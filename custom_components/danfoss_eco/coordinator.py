"""DataUpdateCoordinator for a single Danfoss eTRV.

One coordinator per device. Each poll opens a BLE session, reads all
characteristics we care about, then disconnects (the device is sleepy and
keeping a long-lived connection is unreliable).

Every BLE session, poll or write, takes a lock shared by all eTRVs: proxies
handle concurrent connection attempts badly, so one device connects at a time.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_PIN,
    CONF_SECRET_KEY,
    DEFAULT_PIN,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .etrv.client import ETRVClient
from .etrv.properties import (
    Battery,
    ConfigBit,
    CurrentTime,
    ErrorFlag,
    Errors,
    Name,
    Schedule,
    Settings,
    Temperature,
)

_LOGGER = logging.getLogger(__name__)

WRITE_DEBOUNCE_S = 5.0
# Resync the device clock once it drifts past this. The device has no crystal
# trim and loses a minute or so a week; the app sidesteps the problem by
# rewriting the clock on every connect.
CLOCK_DRIFT_TOLERANCE_S = 90.0
BLE_LOCK_KEY = f"{DOMAIN}_ble_lock"


def _raw_utc_offset_seconds() -> int:
    """Standard (non-DST) UTC offset in seconds.

    Matches the app's ``TimeZone.getRawOffset()``: the device adds the DST hour
    itself from the daylight_saving config bit, so sending the DST-inclusive
    offset would double-count it for half the year.
    """
    return -time.timezone


@dataclass
class ETRVState:
    battery: Battery | None
    temperature: Temperature | None
    settings: Settings | None
    errors: Errors | None
    current_time: CurrentTime | None
    schedule: Schedule | None
    rssi: int | None


class ETRVCoordinator(DataUpdateCoordinator[ETRVState]):
    """Polls one eTRV via Bluetooth."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
    ) -> None:
        self.entry = entry
        self.address: str = entry.data["address"]
        secret_hex: str = entry.data[CONF_SECRET_KEY]
        self._secret = bytes.fromhex(secret_hex)
        self._ble_lock: asyncio.Lock = hass.data.setdefault(BLE_LOCK_KEY, asyncio.Lock())
        self._pending_setpoint: float | None = None
        self._pending_settings: Settings | None = None
        self._flush_handle: asyncio.TimerHandle | None = None
        self._flush_task: asyncio.Task | None = None
        # Device Information Service values are static; read once, then cache.
        self.device_information: dict[str, str] = {}

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}-{self.address}",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )

    # --- polling -------------------------------------------------------------

    def _open_client(self) -> ETRVClient:
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if ble_device is None:
            raise UpdateFailed(
                f"{self.address} not seen by any connectable Bluetooth adapter or proxy"
            )
        # PIN is read from the entry each time so an options-flow change applies
        # without reloading the entry.
        pin: str = self.entry.data.get(CONF_PIN, DEFAULT_PIN)
        return ETRVClient(ble_device, secret_key=self._secret, pin=pin.encode("ascii"))

    async def _async_update_data(self) -> ETRVState:
        # Flush any pending debounced writes first so the poll reads post-write state.
        await self._flush_now()
        try:
            return await self._read_all()
        except Exception as exc:  # noqa: BLE001
            raise UpdateFailed(f"poll failed: {exc}") from exc

    async def _read_all(self) -> ETRVState:
        async with self._ble_lock:
            client = self._open_client()
            await client.connect()
            try:
                battery = await client.read_battery()
                temperature = await client.read_temperature()
                settings = await client.read_settings()
                try:
                    errors = await client.read_errors()
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.debug("errors read failed: %s", exc)
                    errors = None
                try:
                    current_time = await client.read_current_time()
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.debug("current_time read failed: %s", exc)
                    current_time = None
                try:
                    schedule = await client.read_schedule()
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.debug("schedule read failed: %s", exc)
                    schedule = None
                current_time, errors = await self._repair_clock(client, current_time, errors)
                # Device Information Service is static — read it only once.
                if not self.device_information:
                    self.device_information = await client.read_device_information()
            finally:
                await client.disconnect()

        rssi = self._latest_rssi()
        return ETRVState(
            battery=battery,
            temperature=temperature,
            settings=settings,
            errors=errors,
            current_time=current_time,
            schedule=schedule,
            rssi=rssi,
        )

    async def _repair_clock(
        self, client: ETRVClient, current_time: CurrentTime | None, errors: Errors | None
    ) -> tuple[CurrentTime | None, Errors | None]:
        """Set the clock and acknowledge E10 when either is out of shape.

        The device refuses to run SCHEDULE mode while INVALID_CLOCK is latched,
        and the flag stays latched after the clock is set until the fault word is
        written back without it. Runs inside the poll's existing session, so it
        costs no extra connection.
        """
        clock_unset = current_time is not None and current_time.time is None
        drifted = False
        if current_time is not None and current_time.time is not None:
            drift = abs((datetime.now(tz=timezone.utc) - current_time.time).total_seconds())
            drifted = drift > CLOCK_DRIFT_TOLERANCE_S
        latched = errors is not None and errors.has(ErrorFlag.INVALID_CLOCK)
        if not (clock_unset or drifted or latched):
            return current_time, errors

        try:
            if clock_unset or drifted:
                now = datetime.now(tz=timezone.utc)
                await client.write_current_time(
                    CurrentTime(time=now, offset_seconds=_raw_utc_offset_seconds())
                )
                current_time = await client.read_current_time()
            if errors is not None and latched:
                # Acknowledge only E10 — every other bit is preserved so real
                # faults stay visible.
                await client.write_errors(errors.without(ErrorFlag.INVALID_CLOCK))
                errors = await client.read_errors()
            _LOGGER.debug(
                "clock repaired (unset=%s drifted=%s e10=%s)", clock_unset, drifted, latched
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("clock repair failed: %s", exc)
        return current_time, errors

    def _latest_rssi(self) -> int | None:
        info = bluetooth.async_last_service_info(self.hass, self.address, connectable=True)
        return info.rssi if info else None

    def device_wall_clock(self) -> datetime:
        """The device's local wall-clock time right now — what its schedule runs on.

        The device derives local time as UTC + the stored raw offset, plus one
        DST hour when the daylight_saving config bit is set (see CurrentTime).
        Falls back to this machine's timezone if the clock characteristic has
        never been read; the weekly clock repair keeps drift under 90s.
        """
        data = self.data
        if data and data.current_time is not None:
            offset = data.current_time.offset_seconds
            if data.settings is not None and data.settings.get_bit(ConfigBit.DAYLIGHT_SAVING):
                offset += 3600
            return datetime.now(tz=timezone.utc).astimezone(timezone(timedelta(seconds=offset)))
        return datetime.now().astimezone()

    def scheduled_setpoint(self) -> float | None:
        """The setpoint the device's stored schedule calls for right now.

        Computed locally from the last-polled schedule — no device contact.
        """
        data = self.data
        if not data or not data.schedule:
            return None
        return data.schedule.setpoint_at(self.device_wall_clock())

    # --- writes --------------------------------------------------------------

    async def _run_session(self, body) -> None:
        """Open a connection, run `body(client)`, disconnect.

        We assume the device accepted the writes as long as the BLE op did not
        raise — no read-back, no automatic poll afterwards. The next scheduled
        poll will reconcile if the optimistic state is wrong.
        """
        async with self._ble_lock:
            client = self._open_client()
            await client.connect()
            try:
                await body(client)
            finally:
                await client.disconnect()

    def _optimistic_update(self, **changes) -> None:
        """Patch coordinator.data in place and notify entities, without polling."""
        if self.data is None:
            return
        self.async_set_updated_data(replace(self.data, **changes))

    async def async_write_target_temperature(self, target: float) -> None:
        self._pending_setpoint = target
        if self.data and self.data.temperature:
            self._optimistic_update(temperature=replace(self.data.temperature, set_point=target))
        self._arm_flush()

    async def async_write_settings(self, settings: Settings) -> None:
        self._pending_settings = settings
        self._optimistic_update(settings=settings)
        self._arm_flush()

    async def async_write_schedule(self, schedule: Schedule) -> None:
        """Write the weekly schedule (3 characteristics) in one session.

        Not debounced — schedule edits are infrequent and come as a whole.
        """

        async def body(client):
            await client.write_schedule(schedule)

        await self._run_session(body)
        self._optimistic_update(schedule=schedule)

    # --- debounced flush -----------------------------------------------------

    def _has_pending(self) -> bool:
        return self._pending_setpoint is not None or self._pending_settings is not None

    def _flush_running(self) -> bool:
        return self._flush_task is not None and not self._flush_task.done()

    def _cancel_flush_timer(self) -> None:
        if self._flush_handle is not None:
            self._flush_handle.cancel()
            self._flush_handle = None

    def _arm_flush(self) -> None:
        """Schedule (or reschedule) the debounced flush."""
        self._cancel_flush_timer()
        self._flush_handle = self.hass.loop.call_later(WRITE_DEBOUNCE_S, self._launch_flush)

    def _launch_flush(self) -> None:
        self._flush_handle = None
        if not self._flush_running():
            self._flush_task = self.hass.async_create_task(self._flush_pending())

    async def _flush_pending(self) -> None:
        """Write pending values, looping so writes queued mid-session are not lost."""
        while self._has_pending():
            setpoint, settings = self._pending_setpoint, self._pending_settings
            self._pending_setpoint = self._pending_settings = None

            async def body(client, setpoint=setpoint, settings=settings):
                if setpoint is not None:
                    await client.write_setpoint(setpoint)
                if settings is not None:
                    await client.write_settings(settings)

            try:
                await self._run_session(body)
            except Exception as exc:  # noqa: BLE001
                # Nobody awaits this task. Log loudly and poll so the optimistic
                # state does not keep showing a write that never landed.
                _LOGGER.error(
                    "flush_writes failed (setpoint=%s settings=%s): %s",
                    setpoint,
                    "yes" if settings is not None else "no",
                    exc,
                )
                # Spawned, not awaited: the refresh calls _flush_now, which awaits this task.
                self.hass.async_create_task(self.async_request_refresh())

    async def _flush_now(self) -> None:
        """Send pending writes now, including any queued behind an in-flight flush."""
        while self._has_pending() or self._flush_running():
            self._cancel_flush_timer()
            self._launch_flush()
            await self._flush_task

    def async_drop_pending(self) -> None:
        """Drop any pending debounced writes without sending. Used on unload."""
        self._cancel_flush_timer()
        self._pending_setpoint = self._pending_settings = None

    async def async_write_name(self, name: str) -> None:
        async def body(client):
            await client.write_name(Name(name=name))

        await self._run_session(body)

    async def async_sync_clock(self) -> None:
        async def body(client):
            now = datetime.now(tz=timezone.utc)
            await client.write_current_time(
                CurrentTime(time=now, offset_seconds=_raw_utc_offset_seconds())
            )
            # E10 latches: setting the clock does not clear it, and while it is
            # set the device will not stay in SCHEDULE mode. Ack it here so a
            # manual sync is enough to unblock schedule mode.
            errors = await client.read_errors()
            if errors.has(ErrorFlag.INVALID_CLOCK):
                await client.write_errors(errors.without(ErrorFlag.INVALID_CLOCK))

        await self._run_session(body)
        await self.async_request_refresh()
