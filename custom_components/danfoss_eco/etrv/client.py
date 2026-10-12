"""Async bleak client for Danfoss eTRV.

Wraps a `BleakClient` with the device's conventions:
  - 4-byte ASCII PIN must be written to UUID_PIN immediately after connect
  - most characteristics are XXTEA-encrypted with a 16-byte secret key
  - secret_key char is plaintext and only readable while device is in
    pairing mode (LED blinking after long button press)

Connections go through `bleak_retry_connector.establish_connection`, as Home
Assistant requires, so callers must pass a `BLEDevice` from HA's bluetooth API.
"""
from __future__ import annotations

import logging

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from ..const import (
    UUID_BATTERY_LEVEL,
    UUID_CURRENT_TIME,
    UUID_ERRORS,
    UUID_FIRMWARE_REVISION,
    UUID_HARDWARE_REVISION,
    UUID_MANUFACTURER,
    UUID_MODEL_NUMBER,
    UUID_NAME,
    UUID_PIN,
    UUID_SCHEDULE_1,
    UUID_SCHEDULE_2,
    UUID_SCHEDULE_3,
    UUID_SECRET_KEY,
    UUID_SERIAL_NUMBER,
    UUID_SETTINGS,
    UUID_SOFTWARE_REVISION,
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
CONNECT_ATTEMPTS = 4


def convert_pin(pin: bytes) -> bytes:
    """Encode a 4-digit ASCII PIN the way the device expects it.

    The official app (CharacteristicWriter.convertPin) treats the PIN as a
    decimal integer and writes it as a 4-byte big-endian value — e.g. "1234"
    → 0x000004D2, "0000" → 0x00000000. This is *not* the ASCII bytes. PINs
    other than the factory default would silently fail with the old encoding.
    """
    text = pin.decode("ascii").strip() or "0"
    return int(text).to_bytes(4, "big")


class ETRVClient:
    """Stateful BLE session. Caller is responsible for connect/disconnect.

    Typical use:
        async with ETRVClient(ble_device, secret_key) as c:
            t = await c.read_temperature()
            await c.write_temperature(Temperature(set_point=21.0, room=t.room))
    """

    def __init__(
        self,
        device: BLEDevice,
        secret_key: bytes | None,
        pin: bytes = DEFAULT_PIN,
    ) -> None:
        self._device = device
        self._secret = secret_key
        self._pin = pin
        self._client: BleakClientWithServiceCache | None = None
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
        self._client = await establish_connection(
            BleakClientWithServiceCache,
            self._device,
            self._device.name or self._device.address,
            max_attempts=CONNECT_ATTEMPTS,
        )
        try:
            await self._send_pin()
        except Exception:
            await self.disconnect()
            raise

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
        await self._client.write_gatt_char(UUID_PIN, convert_pin(self._pin), response=True)
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

    async def write_errors(self, errors: Errors) -> None:
        """Write the fault word back, acknowledging every bit not set in it.

        Mirrors the app's composeErrorCode / writeAlertsToThermostat: the
        device latches faults and only drops one when the word is written back
        without it.
        """
        await self._write_encrypted(UUID_ERRORS, errors.pack())

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

    # --- Device Information Service (standard 0x180A, plaintext strings) ------

    async def _read_string(self, uuid: str) -> str:
        return bytes(await self._read_raw(uuid)).decode("utf-8", errors="replace").strip("\x00 ")

    async def read_device_information(self) -> dict[str, str]:
        """Read the standard Device Information Service characteristics.

        Values are static, so callers typically read this once and cache it.
        Missing characteristics are skipped rather than failing the whole read.
        """
        fields = {
            "firmware_revision": UUID_FIRMWARE_REVISION,
            "software_revision": UUID_SOFTWARE_REVISION,
            "hardware_revision": UUID_HARDWARE_REVISION,
            "serial_number": UUID_SERIAL_NUMBER,
            "model_number": UUID_MODEL_NUMBER,
            "manufacturer": UUID_MANUFACTURER,
        }
        info: dict[str, str] = {}
        for name, uuid in fields.items():
            try:
                value = await self._read_string(uuid)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("DIS read %s failed: %s", name, exc)
                continue
            if value:
                info[name] = value
        return info
