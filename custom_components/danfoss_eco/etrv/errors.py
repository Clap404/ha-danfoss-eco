"""The error-code characteristic: a 16-bit latched fault word."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import struct


class ErrorFlag(IntEnum):
    """Bit N set means error code E(N+1). From the app's BufferedThermostat."""

    SENSOR_FRONT = 0
    SENSOR_VALVE = 1
    MEMORY = 2
    HARDWARE = 3
    E5 = 4
    MOTOR_JAMMED = 5
    COMMS_MODULE = 6
    INVALID_COMMS = 7
    VALVE_NOT_CLOSING = 8
    INVALID_CLOCK = 9  # E10: batteries were replaced
    E11 = 10
    RADIO_COMMS = 11
    ENCODER_JAMMED = 12
    LOW_BATTERY = 13
    CRITICAL_BATTERY = 14


@dataclass
class Errors:
    """Faults latch: a bit stays set until the word is written back without it.

    Big-endian, verified on hardware: an unset clock reads 0x0200 (E10). A
    latched INVALID_CLOCK keeps the device out of SCHEDULE mode even after the
    clock is set, so acknowledging it matters.
    """

    raw: int

    @classmethod
    def parse(cls, data: bytes) -> Errors:
        return cls(raw=int.from_bytes(data[:2], "big"))

    def pack(self) -> bytes:
        # XXTEA needs at least 8 bytes; the app zero-pads the same way.
        return struct.pack(">H", self.raw & 0xFFFF).ljust(8, b"\x00")

    def has(self, *flags: ErrorFlag) -> bool:
        return any(self.raw & (1 << flag) for flag in flags)

    def without(self, *flags: ErrorFlag) -> Errors:
        """This word with `flags` cleared: the payload that acknowledges them."""
        mask = sum(1 << flag for flag in flags)
        return Errors(raw=self.raw & ~mask)

    @property
    def active_flags(self) -> list[ErrorFlag]:
        return [flag for flag in ErrorFlag if self.has(flag)]
