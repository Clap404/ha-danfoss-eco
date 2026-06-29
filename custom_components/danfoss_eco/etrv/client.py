"""Async bleak client for Danfoss eTRV.

Wraps a `BleakClient` with the device's conventions:
  - 4-byte ASCII PIN must be written to UUID_PIN immediately after connect
  - most characteristics are XXTEA-encrypted with a 16-byte secret key
  - secret_key char is plaintext and only readable while device is in
    pairing mode (LED blinking after long button press)

This module is pure protocol — no Home Assistant imports — so it can be
exercised from a CLI probe script against real hardware.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError

from ..const import (
    UUID_BATTERY_LEVEL,
    UUID_CURRENT_TIME,
    UUID_ERRORS,
    UUID_NAME,
    UUID_PIN,
    UUID_SCHEDULE_1,
    UUID_SCHEDULE_2,
    UUID_SCHEDULE_3,
    UUID_SECRET_KEY,
    UUID_SETTINGS,
    UUID_TEMPERATURE,
)
from . import crypto
from .properties import (
    Battery,
    CurrentTime,
    Errors,
    Name,
    Schedule,
    SecretKey,
    Settings,
    Temperature,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_PIN = b"0000"
CONNECT_TIMEOUT = 30.0
CONNECT_ATTEMPTS = 4


class ETRVClient:
    """Stateful BLE session. Caller is responsible for connect/disconnect.

    Typical use:
        async with ETRVClient(ble_device, secret_key) as c:
            t = await c.read_temperature()
            await c.write_temperature(Temperature(set_point=21.0, room=t.room))
    """

    def __init__(
        self,
        device_or_address: BLEDevice | str,
        secret_key: bytes | None,
        pin: bytes = DEFAULT_PIN,
    ) -> None:
        self._target = device_or_address
        self._secret = secret_key
        self._pin = pin
        self._client: BleakClient | None = None
        self._pin_sent = False

    # --- connection lifecycle -------------------------------------------------

    async def __aenter__(self) -> "ETRVClient":
        await self.connect()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.disconnect()

    async def connect(self) -> None:
        if self._client is not None and self._client.is_connected:
            return
        last: Exception | None = None
        for attempt in range(1, CONNECT_ATTEMPTS + 1):
            client = BleakClient(self._target, timeout=CONNECT_TIMEOUT)
            self._client = client
            try:
                await client.connect()
                await self._send_pin()
                return
            except (BleakError, asyncio.TimeoutError) as exc:
                last = exc
                _LOGGER.debug("connect attempt %d/%d failed: %s", attempt, CONNECT_ATTEMPTS, exc)
                try:
                    await client.disconnect()
                except Exception:  # noqa: BLE001
                    pass
                self._client = None
                self._pin_sent = False
                await asyncio.sleep(1.0 * attempt)
        assert last is not None
        raise last

    async def disconnect(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            except BleakError:
                _LOGGER.debug("disconnect: client already gone", exc_info=True)
            self._client = None
            self._pin_sent = False

    async def _send_pin(self) -> None:
        if self._pin_sent:
            return
        assert self._client is not None
        await self._client.write_gatt_char(UUID_PIN, self._pin, response=True)
        self._pin_sent = True

    # --- low-level read/write -------------------------------------------------

    async def _read_raw(self, uuid: str) -> bytes:
        assert self._client is not None
        return bytes(await self._client.read_gatt_char(uuid))

    async def _read_encrypted(self, uuid: str) -> bytes:
        if self._secret is None:
            raise RuntimeError("secret_key required for encrypted reads")
        return crypto.decrypt(await self._read_raw(uuid), self._secret)

    async def _write_encrypted(self, uuid: str, payload: bytes) -> None:
        if self._secret is None:
            raise RuntimeError("secret_key required for encrypted writes")
        assert self._client is not None
        await self._client.write_gatt_char(
            uuid, crypto.encrypt(payload, self._secret), response=True
        )

    # --- typed property accessors --------------------------------------------

    async def read_battery(self) -> Battery:
        return Battery.parse(await self._read_raw(UUID_BATTERY_LEVEL))

    async def read_temperature(self) -> Temperature:
        return Temperature.parse(await self._read_encrypted(UUID_TEMPERATURE))

    async def write_temperature(self, temp: Temperature) -> None:
        await self._write_encrypted(UUID_TEMPERATURE, temp.pack())

    async def write_setpoint(self, set_point: float) -> None:
        """Set the target temperature without reading the room temperature first."""
        await self._write_encrypted(
            UUID_TEMPERATURE, Temperature.pack_setpoint_only(set_point)
        )

    async def read_settings(self) -> Settings:
        return Settings.parse(await self._read_encrypted(UUID_SETTINGS))

    async def write_settings(self, settings: Settings) -> None:
        await self._write_encrypted(UUID_SETTINGS, settings.pack())

    async def read_name(self) -> Name:
        return Name.parse(await self._read_encrypted(UUID_NAME))

    async def write_name(self, name: Name) -> None:
        await self._write_encrypted(UUID_NAME, name.pack())

    async def read_current_time(self) -> CurrentTime:
        return CurrentTime.parse(await self._read_encrypted(UUID_CURRENT_TIME))

    async def write_current_time(self, ct: CurrentTime) -> None:
        await self._write_encrypted(UUID_CURRENT_TIME, ct.pack())

    async def read_errors(self) -> Errors:
        return Errors.parse(await self._read_encrypted(UUID_ERRORS))

    async def read_schedule(self) -> Schedule:
        c1 = await self._read_encrypted(UUID_SCHEDULE_1)
        c2 = await self._read_encrypted(UUID_SCHEDULE_2)
        c3 = await self._read_encrypted(UUID_SCHEDULE_3)
        return Schedule.parse(c1, c2, c3)

    async def write_schedule(self, sched: Schedule) -> None:
        c1, c2, c3 = sched.pack()
        await self._write_encrypted(UUID_SCHEDULE_1, c1)
        await self._write_encrypted(UUID_SCHEDULE_2, c2)
        await self._write_encrypted(UUID_SCHEDULE_3, c3)

    async def read_secret_key(self) -> SecretKey:
        """Plaintext read — only succeeds while the device is in pairing mode."""
        return SecretKey.parse(await self._read_raw(UUID_SECRET_KEY))


@asynccontextmanager
async def open_client(
    device_or_address: BLEDevice | str,
    secret_key: bytes | None,
    pin: bytes = DEFAULT_PIN,
) -> AsyncIterator[ETRVClient]:
    """Convenience: open a one-shot session."""
    client = ETRVClient(device_or_address, secret_key, pin)
    await client.connect()
    try:
        yield client
    finally:
        await client.disconnect()


async def _retry(coro_factory, attempts: int = 3, delay: float = 0.5):
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            return await coro_factory()
        except BleakError as exc:
            last_exc = exc
            _LOGGER.debug("BLE op failed (%d/%d): %s", i + 1, attempts, exc)
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc
