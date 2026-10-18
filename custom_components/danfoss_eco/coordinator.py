"""DataUpdateCoordinator for a single Danfoss eTRV.

Each poll opens a BLE session, reads every characteristic we care about, then
disconnects: the device is sleepy and long-lived connections are unreliable.

Every session, poll or write, takes a lock shared by all eTRVs. Proxies
handle concurrent connection attempts badly, so one device connects at a time.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import logging

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import clock
from .const import CONF_PIN, CONF_SECRET_KEY, DEFAULT_PIN, DEFAULT_SCAN_INTERVAL, DOMAIN
from .debounce import DebouncedWrites
from .etrv import (
    Battery,
    CurrentTime,
    Errors,
    ETRVClient,
    Name,
    Schedule,
    Settings,
    Temperature,
)

_LOGGER = logging.getLogger(__name__)

WRITE_DEBOUNCE_S = 5.0
SCAN_INTERVAL = timedelta(seconds=DEFAULT_SCAN_INTERVAL)
FIRST_POLL_RETRY = timedelta(minutes=5)
BLE_LOCK_KEY = f"{DOMAIN}_ble_lock"


@dataclass
class ETRVState:
    battery: Battery
    temperature: Temperature
    settings: Settings
    errors: Errors | None
    current_time: CurrentTime | None
    schedule: Schedule | None
    rssi: int | None


class ETRVCoordinator(DataUpdateCoordinator[ETRVState]):
    """Polls one eTRV via Bluetooth and batches writes to it."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        self.address: str = entry.data[CONF_ADDRESS]
        self.mac = format_mac(self.address)
        self._secret = bytes.fromhex(entry.data[CONF_SECRET_KEY])
        self._ble_lock: asyncio.Lock = hass.data.setdefault(BLE_LOCK_KEY, asyncio.Lock())
        self._writes = DebouncedWrites(hass, self._flush_writes, WRITE_DEBOUNCE_S)
        # Device Information Service values are static; read once, then cached.
        self._device_information: dict[str, str] = {}
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}-{self.address}",
            update_interval=SCAN_INTERVAL,
        )

    @property
    def has_data(self) -> bool:
        """False until the first poll succeeds."""
        return self.data is not None  # pyright: ignore[reportUnnecessaryComparison]

    # --- state shortcuts -----------------------------------------------------

    @property
    def settings(self) -> Settings:
        return self.data.settings

    @property
    def temperature(self) -> Temperature:
        return self.data.temperature

    @property
    def schedule(self) -> Schedule | None:
        return self.data.schedule

    def device_wall_clock(self) -> datetime:
        return clock.device_wall_clock(self.data.current_time, self.settings)

    def scheduled_setpoint(self) -> float | None:
        """What the stored schedule calls for right now, computed locally."""
        return self.schedule.setpoint_at(self.device_wall_clock()) if self.schedule else None

    # --- polling -------------------------------------------------------------

    def _open_client(self) -> ETRVClient:
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if ble_device is None:
            raise UpdateFailed(
                f"{self.address} not seen by any connectable Bluetooth adapter or proxy"
            )
        # Read each time so a PIN change in Configure applies without a reload.
        pin: str = self.entry.data.get(CONF_PIN, DEFAULT_PIN)
        return ETRVClient(ble_device, secret_key=self._secret, pin=pin.encode("ascii"))

    async def _async_update_data(self) -> ETRVState:
        # Flush pending writes first so the poll reads post-write state.
        await self._writes.flush_now()
        try:
            state = await self._read_all()
        except Exception as exc:
            if not self.has_data:
                self.update_interval = FIRST_POLL_RETRY
            raise UpdateFailed(f"poll failed: {exc}") from exc
        self.update_interval = SCAN_INTERVAL
        return state

    async def _read_all(self) -> ETRVState:
        async with self._ble_lock:
            client = self._open_client()
            await client.connect()
            try:
                battery = await client.read_battery()
                temperature = await client.read_temperature()
                settings = await client.read_settings()
                errors = await self._optional("errors read", client.read_errors(), None)
                current_time = await self._optional(
                    "current_time read", client.read_current_time(), None
                )
                schedule = await self._optional("schedule read", client.read_schedule(), None)
                current_time, errors = await self._optional(
                    "clock repair",
                    clock.async_repair_clock(client, current_time, errors),
                    (current_time, errors),
                )
                if not self._device_information:
                    self._device_information = await client.read_device_information()
                    self._update_device_registry()
            finally:
                await client.disconnect()

        info = bluetooth.async_last_service_info(self.hass, self.address, connectable=True)
        return ETRVState(
            battery=battery,
            temperature=temperature,
            settings=settings,
            errors=errors,
            current_time=current_time,
            schedule=schedule,
            rssi=info.rssi if info else None,
        )

    def _update_device_registry(self) -> None:
        registry = dr.async_get(self.hass)
        device = registry.async_get_device(identifiers={(DOMAIN, self.mac)})
        if device is None:
            return
        dis = self._device_information
        registry.async_update_device(
            device.id,
            manufacturer=dis.get("manufacturer", "Danfoss"),
            model=dis.get("model_number", "Eco (eTRV)"),
            sw_version=dis.get("firmware_revision") or dis.get("software_revision"),
            hw_version=dis.get("hardware_revision"),
            serial_number=dis.get("serial_number"),
        )

    async def _optional[T, D](self, name: str, coro: Awaitable[T], default: D) -> T | D:
        """Await `coro`; on failure warn and return `default` so the poll still succeeds."""
        try:
            return await coro
        except Exception as exc:
            _LOGGER.warning("%s: %s failed: %s", self.address, name, exc)
            return default

    # --- writes --------------------------------------------------------------

    async def _run_session(self, body: Callable[[ETRVClient], Awaitable[None]]) -> None:
        """Connect, run `body(client)`, disconnect.

        A write is assumed accepted if the BLE op did not raise: no read-back,
        no poll afterwards. The next poll reconciles if optimism was wrong.
        """
        async with self._ble_lock:
            client = self._open_client()
            await client.connect()
            try:
                await body(client)
            finally:
                await client.disconnect()

    # Writes update coordinator.data optimistically so entities reflect them at once.

    async def async_write_target_temperature(self, target: float) -> None:
        self._writes.queue(setpoint=target)
        temperature = replace(self.temperature, set_point=target)
        self.async_set_updated_data(replace(self.data, temperature=temperature))

    async def async_write_settings(self, settings: Settings) -> None:
        self._writes.queue(settings=settings)
        self.async_set_updated_data(replace(self.data, settings=settings))

    async def _flush_writes(self, pending: dict[str, float | Settings]) -> None:
        async def body(client: ETRVClient) -> None:
            setpoint, settings = pending.get("setpoint"), pending.get("settings")
            if isinstance(setpoint, float):
                await client.write_setpoint(setpoint)
            if isinstance(settings, Settings):
                await client.write_settings(settings)

        try:
            await self._run_session(body)
        except Exception as exc:
            # Nobody awaits the flush task. Log loudly and poll so the optimistic
            # state does not keep showing a write that never landed.
            _LOGGER.error("%s: write of %s failed: %s", self.address, sorted(pending), exc)
            self.hass.async_create_task(self.async_request_refresh())

    def async_drop_pending(self) -> None:
        """Forget pending debounced writes. Used on unload."""
        self._writes.drop()

    async def async_write_schedule(self, schedule: Schedule) -> None:
        """Not debounced: schedule edits are rare and come as a whole."""
        await self._run_session(lambda client: client.write_schedule(schedule))
        self.async_set_updated_data(replace(self.data, schedule=schedule))

    async def async_write_name(self, name: str) -> None:
        await self._run_session(lambda client: client.write_name(Name(name=name)))

    async def async_sync_clock(self) -> None:
        async def body(client: ETRVClient) -> None:
            await clock.async_repair_clock(client, None, await client.read_errors(), force=True)

        await self._run_session(body)
        await self.async_request_refresh()
