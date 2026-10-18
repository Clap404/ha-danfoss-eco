"""Single-value characteristics: battery, temperature, name, clock.

Each `parse()` takes the decrypted bytes from the device; each `pack()`
returns the bytes to send back. Multi-byte integers are big-endian, matching
the app's ByteBuffer reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import struct
from typing import ClassVar

from .codec import datetime_from_epoch, epoch_from_datetime, temp_from_byte, temp_to_byte


@dataclass
class Battery:
    level: int  # 0-100 %

    @classmethod
    def parse(cls, data: bytes) -> Battery:
        return cls(level=data[0])


@dataclass
class Temperature:
    """8 bytes: setpoint, room temperature, 6 bytes padding."""

    set_point: float
    room: float

    _FMT: ClassVar[str] = "<BB6x"

    @classmethod
    def parse(cls, data: bytes) -> Temperature:
        sp, rt = struct.unpack(cls._FMT, data[: struct.calcsize(cls._FMT)])
        return cls(set_point=temp_from_byte(sp), room=temp_from_byte(rt))

    @classmethod
    def pack_setpoint(cls, set_point: float) -> bytes:
        """The room byte is read-only; the app writes [setpoint, 0] with no prior read."""
        return struct.pack(cls._FMT, temp_to_byte(set_point), 0)


@dataclass
class Name:
    """16 bytes, null-padded UTF-8."""

    name: str

    @classmethod
    def parse(cls, data: bytes) -> Name:
        return cls(name=data.rstrip(b"\x00").decode("utf-8", errors="replace"))

    def pack(self) -> bytes:
        # The app's pre-encryption buffer is 15 bytes; XXTEA pads to 16.
        raw = self.name.encode("utf-8")[:15].decode("utf-8", errors="ignore").encode("utf-8")
        return raw.ljust(16, b"\x00")


@dataclass
class CurrentTime:
    """8 bytes: int32 UTC unix epoch, int32 timezone offset in seconds.

    The device derives local time as epoch + offset. `time` is None while the
    clock is unset (fresh batteries).
    """

    time: datetime | None
    offset_seconds: int

    _FMT: ClassVar[str] = ">ii"

    @classmethod
    def parse(cls, data: bytes) -> CurrentTime:
        epoch, offset = struct.unpack(cls._FMT, data[: struct.calcsize(cls._FMT)])
        return cls(time=datetime_from_epoch(epoch), offset_seconds=offset)

    def pack(self) -> bytes:
        return struct.pack(self._FMT, epoch_from_datetime(self.time), self.offset_seconds)
