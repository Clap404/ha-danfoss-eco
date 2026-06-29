"""DataUpdateCoordinator for a single Danfoss eTRV.

One coordinator per device. Each poll opens a BLE session, reads all
characteristics we care about, then disconnects (the device is sleepy and
keeping a long-lived connection is unreliable).

Writes (target temp, mode, etc.) take an `asyncio.Lock` so they don't race
with a poll, and trigger a refresh once done.
"""

from __future__ import annotations

import asyncio
import logging
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
    CurrentTime,
    Errors,
    Settings,
    Temperature,
)

_LOGGER = logging.getLogger(__name__)

RETRY_ATTEMPTS = 3
RETRY_BACKOFF_S = 2.0


async def _with_retry(name: str, op):
    """Run `op()` (a coro factory) up to RETRY_ATTEMPTS times with backoff."""
    last_exc: Exception | None = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            return await op()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            _LOGGER.debug("%s attempt %d/%d failed: %s", name, attempt, RETRY_ATTEMPTS, exc)
            if attempt < RETRY_ATTEMPTS:
                await asyncio.sleep(RETRY_BACKOFF_S * attempt)
    assert last_exc is not None
    raise last_exc


@dataclass
class ETRVState:
    battery: Battery | None
    temperature: Temperature | None
    settings: Settings | None
    errors: Errors | None
    current_time: CurrentTime | None
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
        pin_str: str = entry.data.get(CONF_PIN, DEFAULT_PIN)
        self._pin = pin_str.encode("ascii")
        self._write_lock = asyncio.Lock()

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
        target = ble_device or self.address
        return ETRVClient(target, secret_key=self._secret, pin=self._pin)

    async def _async_update_data(self) -> ETRVState:
        async with self._write_lock:
            try:
                return await _with_retry("poll", self._read_all)
            except Exception as exc:  # noqa: BLE001
                raise UpdateFailed(f"poll failed after retries: {exc}") from exc

    async def _read_all(self) -> ETRVState:
        client = self._open_client()
        await client.connect()  # retries handled by ETRVClient + outer _with_retry
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
        finally:
            await client.disconnect()

        rssi = self._latest_rssi()
        return ETRVState(
            battery=battery,
            temperature=temperature,
            settings=settings,
            errors=errors,
            current_time=current_time,
            rssi=rssi,
        )

    def _latest_rssi(self) -> int | None:
        info = bluetooth.async_last_service_info(self.hass, self.address, connectable=True)
        return info.rssi if info else None

    # --- writes --------------------------------------------------------------

    async def _run_session(self, name: str, body) -> None:
        """Open a connection, run `body(client)`, disconnect. Retries on failure.

        We assume the device accepted the writes as long as the BLE op did not
        raise — no read-back, no automatic poll afterwards. The next scheduled
        poll will reconcile if the optimistic state is wrong.
        """

        async def op():
            client = self._open_client()
            await client.connect()
            try:
                await body(client)
            finally:
                await client.disconnect()

        async with self._write_lock:
            await _with_retry(name, op)

    def _optimistic_update(self, **changes) -> None:
        """Patch coordinator.data in place and notify entities, without polling."""
        if self.data is None:
            return
        self.async_set_updated_data(replace(self.data, **changes))

    async def async_write_target_temperature(self, target: float) -> None:
        await self._run_session("write_setpoint", lambda c: c.write_setpoint(target))
        if self.data and self.data.temperature:
            self._optimistic_update(temperature=replace(self.data.temperature, set_point=target))

    async def async_write_settings(self, settings: Settings) -> None:
        await self._run_session("write_settings", lambda c: c.write_settings(settings))
        self._optimistic_update(settings=settings)

    async def async_sync_clock(self) -> None:
        async def body(client):
            now = datetime.now(tz=timezone.utc).astimezone()
            utcoffset = now.utcoffset()
            offset = int(utcoffset.total_seconds()) if utcoffset else 0
            await client.write_current_time(CurrentTime(time=now, offset_seconds=offset))

        await self._run_session("sync_clock", body)
        # Clock state is reflected by errors char's INVALID_CLOCK flag; let the
        # next poll surface that change.
