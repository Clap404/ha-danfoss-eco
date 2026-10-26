"""Async bleak client for Danfoss eTRV.

Wraps a `BleakClient` with the device's conventions:
  - the PIN must be written to uuids.PIN right after connecting; a TRV with
    a PIN enabled refuses a wrong one, any other TRV accepts anything
  - most characteristics are XXTEA-encrypted with a 16-byte secret key
  - the secret key is plaintext and only readable in pairing mode

Connections go through `bleak_retry_connector.establish_connection`, as Home
Assistant requires, so callers pass a `BLEDevice` from HA's bluetooth API.
"""

from __future__ import annotations

import logging

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from . import crypto, uuids
from .errors import Errors
from .schedule import Schedule
from .settings import Settings
from .values import Battery, CurrentTime, Name, PinCode, Temperature

_LOGGER = logging.getLogger(__name__)

CONNECT_ATTEMPTS = 4
ATT_WRITE_NOT_PERMITTED = 3


class WrongPinError(Exception):
    """The TRV has a PIN enabled and rejected the one we sent."""


def _is_write_not_permitted(exc: BaseException) -> bool:
    """The TRV refuses a wrong PIN with ATT error 3, however the backend wraps it."""
    err: BaseException | None = exc
    while err is not None:
        # ESPHome proxy: BleakError caused by aioesphomeapi's BluetoothGATTAPIError.
        gatt_error = getattr(getattr(err, "error", None), "error", None)
        if gatt_error == ATT_WRITE_NOT_PERMITTED:
            return True
        # Local BlueZ adapter.
        if getattr(err, "dbus_error", None) == "org.bluez.Error.NotPermitted":
            return True
        err = err.__cause__
    return False


def convert_pin(pin: bytes) -> bytes:
    """The app treats the PIN as a decimal integer written big-endian: "1234" → 0x000004D2."""
    text = pin.decode("ascii").strip() or "0"
    return int(text).to_bytes(4, "big")


class ETRVClient:
    """One BLE session. Caller connects and disconnects."""

    def __init__(self, device: BLEDevice, secret_key: bytes | None, pin: bytes) -> None:
        self._device = device
        self._secret = secret_key
        self._pin = pin
        self._client: BleakClientWithServiceCache | None = None

    def set_secret_key(self, secret_key: bytes) -> None:
        self._secret = secret_key

    # --- connection lifecycle -------------------------------------------------

    async def connect(self) -> None:
        if self._client is not None and self._client.is_connected:
            return
        self._client = await establish_connection(
            BleakClientWithServiceCache,
            self._device,
            self._device.name or self._device.address,
            max_attempts=CONNECT_ATTEMPTS,
        )
        try:
            await self._client.write_gatt_char(uuids.PIN, convert_pin(self._pin), response=True)
        except Exception as exc:
            await self.disconnect()
            if _is_write_not_permitted(exc):
                raise WrongPinError(f"{self._device.address} rejected the PIN") from exc
            raise

    async def disconnect(self) -> None:
        if self._client is None:
            return
        try:
            await self._client.disconnect()
        except BleakError:
            _LOGGER.debug("disconnect: client already gone", exc_info=True)
        self._client = None

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

    # --- characteristics ------------------------------------------------------

    async def read_battery(self) -> Battery:
        return Battery.parse(await self._read_raw(uuids.BATTERY_LEVEL))

    async def read_temperature(self) -> Temperature:
        return Temperature.parse(await self._read_encrypted(uuids.TEMPERATURE))

    async def write_setpoint(self, set_point: float) -> None:
        await self._write_encrypted(uuids.TEMPERATURE, Temperature.pack_setpoint(set_point))

    async def read_settings(self) -> Settings:
        return Settings.parse(await self._read_encrypted(uuids.SETTINGS))

    async def write_settings(self, settings: Settings) -> None:
        await self._write_encrypted(uuids.SETTINGS, settings.pack())

    async def read_name(self) -> Name:
        return Name.parse(await self._read_encrypted(uuids.NAME))

    async def write_name(self, name: Name) -> None:
        await self._write_encrypted(uuids.NAME, name.pack())

    async def read_current_time(self) -> CurrentTime:
        return CurrentTime.parse(await self._read_encrypted(uuids.CURRENT_TIME))

    async def write_current_time(self, ct: CurrentTime) -> None:
        await self._write_encrypted(uuids.CURRENT_TIME, ct.pack())

    async def read_errors(self) -> Errors:
        return Errors.parse(await self._read_encrypted(uuids.ERRORS))

    async def write_errors(self, errors: Errors) -> None:
        """Write the fault word back; every bit not set in it is acknowledged."""
        await self._write_encrypted(uuids.ERRORS, errors.pack())

    async def read_schedule(self) -> Schedule:
        chars = [await self._read_encrypted(uuid) for uuid in uuids.SCHEDULE]
        return Schedule.parse((chars[0], chars[1], chars[2]))

    async def write_schedule(self, schedule: Schedule) -> None:
        for uuid, payload in zip(uuids.SCHEDULE, schedule.pack(), strict=True):
            await self._write_encrypted(uuid, payload)

    async def read_pin_code(self) -> PinCode:
        return PinCode.parse(await self._read_encrypted(uuids.PIN_CODE))

    async def write_pin_code(self, pin_code: PinCode) -> None:
        await self._write_encrypted(uuids.PIN_CODE, pin_code.pack())

    async def read_secret_key(self) -> bytes:
        """Plaintext read; only succeeds while the device is in pairing mode."""
        return (await self._read_raw(uuids.SECRET_KEY))[:16]

    async def read_device_information(self) -> dict[str, str]:
        """Static strings; missing characteristics are skipped rather than failing the read."""
        info: dict[str, str] = {}
        for name, uuid in uuids.DEVICE_INFORMATION.items():
            try:
                value = (
                    (await self._read_raw(uuid)).decode("utf-8", errors="replace").strip("\x00 ")
                )
            except Exception as exc:
                _LOGGER.debug("DIS read %s failed: %s", name, exc)
                continue
            if value:
                info[name] = value
        return info
