"""Parsed structures for Danfoss eTRV BLE characteristics.

Wire format is little-endian after XXTEA decryption. Temperature bytes are
half-degrees (raw / 2 = °C). Timestamps are unix epoch (int32).

Each `parse()` takes the decrypted bytes from the device; each `pack()`
returns the bytes to send back. Field semantics are ported verbatim from
libetrv (my_etrv2mqtt) to stay compatible with already-paired devices.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import IntEnum
from typing import ClassVar


def _b2t(raw: int) -> float:
    return raw * 0.5


def _t2b(temp: float) -> int:
    return int(round(temp * 2))


class ScheduleMode(IntEnum):
    MANUAL = 0
    SCHEDULED = 1
    VACATION = 3
    HOLD = 5


class ConfigBit(IntEnum):
    """Bit positions in `Settings.config_bits` (byte 0).

    Bits are stored in reverse order on the wire — the values below are the
    *raw* bit positions in the byte we receive (after XXTEA decrypt). Mapping
    cross-checked against the official Danfoss Android app.
    """

    ADAPTABLE_REGULATION = 0  # forecast-based regulation
    DAYLIGHT_SAVING = 1
    VERTICAL_INSTALLATION = 2  # thermostat oriented vertically
    DISPLAY_FLIP = 3
    SLOW_REGULATION = 4
    CALIBRATED = 5
    VALVE_INSTALLED = 6  # 0 when valve is detached from the radiator
    CHILD_LOCK = 7


@dataclass
class Battery:
    level: int  # 0-100 %

    @classmethod
    def parse(cls, data: bytes) -> "Battery":
        return cls(level=data[0])


@dataclass
class Temperature:
    """Characteristic 0x2d — 8 bytes encrypted."""

    set_point: float
    room: float

    _FMT: ClassVar[str] = "<BB6x"

    @classmethod
    def parse(cls, data: bytes) -> "Temperature":
        sp, rt = struct.unpack(cls._FMT, data[: struct.calcsize(cls._FMT)])
        return cls(set_point=_b2t(sp), room=_b2t(rt))

    def pack(self) -> bytes:
        return struct.pack(self._FMT, _t2b(self.set_point), _t2b(self.room))

    @staticmethod
    def pack_setpoint_only(set_point: float) -> bytes:
        """Write-only helper. The room byte is read-only on the device; the
        official Danfoss app sends just [setpoint, 0] padded to 8 bytes for
        XXTEA alignment, with no preceding read."""
        return struct.pack("<BB6x", _t2b(set_point), 0)


@dataclass
class Settings:
    """Characteristic 0x2a — 16 bytes encrypted."""

    config_bits: int
    temperature_min: float
    temperature_max: float
    frost_protection_temperature: float
    schedule_mode: ScheduleMode
    vacation_temperature: float
    vacation_from: datetime | None
    vacation_to: datetime | None

    _FMT: ClassVar[str] = "<BBBBBBii2x"

    @staticmethod
    def _ts_to_dt(ts: int) -> datetime | None:
        return None if ts == 0 else datetime.fromtimestamp(ts, tz=timezone.utc)

    @staticmethod
    def _dt_to_ts(dt: datetime | None) -> int:
        if dt is None:
            return 0
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())

    @classmethod
    def parse(cls, data: bytes) -> "Settings":
        cfg, t_min, t_max, frost, mode, vac_t, vf, vt = struct.unpack(
            cls._FMT, data[: struct.calcsize(cls._FMT)]
        )
        return cls(
            config_bits=cfg,
            temperature_min=_b2t(t_min),
            temperature_max=_b2t(t_max),
            frost_protection_temperature=_b2t(frost),
            schedule_mode=ScheduleMode(mode),
            vacation_temperature=_b2t(vac_t),
            vacation_from=cls._ts_to_dt(vf),
            vacation_to=cls._ts_to_dt(vt),
        )

    def pack(self) -> bytes:
        return struct.pack(
            self._FMT,
            self.config_bits,
            _t2b(self.temperature_min),
            _t2b(self.temperature_max),
            _t2b(self.frost_protection_temperature),
            int(self.schedule_mode),
            _t2b(self.vacation_temperature),
            self._dt_to_ts(self.vacation_from),
            self._dt_to_ts(self.vacation_to),
        )

    def get_bit(self, bit: ConfigBit) -> bool:
        return bool(self.config_bits & (1 << int(bit)))

    def set_bit(self, bit: ConfigBit, value: bool) -> None:
        mask = 1 << int(bit)
        self.config_bits = (self.config_bits | mask) if value else (self.config_bits & ~mask)


@dataclass
class Name:
    """Characteristic 0x30 — 16 bytes encrypted, null-padded ASCII."""

    name: str

    @classmethod
    def parse(cls, data: bytes) -> "Name":
        return cls(name=data.rstrip(b"\x00").decode("utf-8", errors="replace"))

    def pack(self) -> bytes:
        # App pre-encryption buffer is 15 bytes; XXTEA pads to 16 (4-byte aligned).
        return self.name.encode("utf-8")[:15].ljust(16, b"\x00")


@dataclass
class CurrentTime:
    """Characteristic 0x36 — 8 bytes encrypted. time_local is wall-clock seconds
    since epoch in the device's local tz; time_offset is tz offset in seconds."""

    time: datetime | None
    offset_seconds: int

    _FMT: ClassVar[str] = "<ii"

    @classmethod
    def parse(cls, data: bytes) -> "CurrentTime":
        local, offset = struct.unpack(cls._FMT, data[: struct.calcsize(cls._FMT)])
        if local == 0:
            return cls(time=None, offset_seconds=offset)
        tz = timezone(timedelta(seconds=offset))
        return cls(time=datetime.fromtimestamp(local, tz=tz), offset_seconds=offset)

    def pack(self) -> bytes:
        if self.time is None:
            return struct.pack(self._FMT, 0, self.offset_seconds)
        offset = self.offset_seconds
        utc_off = self.time.utcoffset()
        if utc_off is not None:
            offset = int(utc_off.total_seconds())
        # local wall-clock as seconds-since-epoch (device convention)
        local = int(self.time.timestamp()) + offset
        return struct.pack(self._FMT, local, offset)


@dataclass
class DaySchedule:
    """Three intervals where the home_temperature applies. Outside intervals
    the away_temperature applies. Each value is minutes since midnight,
    must be a multiple of 30 (the device resolution).
    """

    p1_start: int = 0
    p1_end: int = 0
    p2_start: int = 0
    p2_end: int = 0
    p3_start: int = 0
    p3_end: int = 0

    @classmethod
    def parse(cls, data: bytes) -> "DaySchedule":
        if len(data) != 6:
            raise ValueError("DaySchedule needs 6 bytes")
        return cls(*(b * 30 for b in data))

    def pack(self) -> bytes:
        vals = (self.p1_start, self.p1_end, self.p2_start, self.p2_end, self.p3_start, self.p3_end)
        return bytes(v // 30 for v in vals)


@dataclass
class Schedule:
    """Full weekly schedule. Day indices: 0=Sun..6=Sat (Danfoss convention).

    Pack/parse split across three BLE chars:
      - char1 (20 bytes): home_temp, away_temp, Mon, Tue, Wed
      - char2 (12 bytes): Thu, Fri
      - char3 (12 bytes): Sat, Sun
    """

    home_temperature: float
    away_temperature: float
    # index 0=Sun, 1=Mon, ..., 6=Sat
    days: list[DaySchedule]

    @staticmethod
    def _default_days() -> list[DaySchedule]:
        return [DaySchedule() for _ in range(7)]

    @classmethod
    def empty(cls) -> "Schedule":
        return cls(home_temperature=21.0, away_temperature=17.0, days=cls._default_days())

    @classmethod
    def parse(cls, char1: bytes, char2: bytes, char3: bytes) -> "Schedule":
        if len(char1) < 20 or len(char2) < 12 or len(char3) < 12:
            raise ValueError("schedule chars too short")
        home = char1[0] / 2.0
        away = char1[1] / 2.0
        days: list[DaySchedule | None] = [None] * 7
        # char1[2..]: Mon, Tue, Wed
        for i, day_idx in enumerate((1, 2, 3)):
            off = 2 + i * 6
            days[day_idx] = DaySchedule.parse(char1[off : off + 6])
        # char2: Thu, Fri
        for i, day_idx in enumerate((4, 5)):
            off = i * 6
            days[day_idx] = DaySchedule.parse(char2[off : off + 6])
        # char3: Sat, Sun
        for i, day_idx in enumerate((6, 0)):
            off = i * 6
            days[day_idx] = DaySchedule.parse(char3[off : off + 6])
        return cls(home_temperature=home, away_temperature=away, days=[d or DaySchedule() for d in days])

    def pack(self) -> tuple[bytes, bytes, bytes]:
        c1 = bytearray(20)
        c1[0] = _t2b(self.home_temperature)
        c1[1] = _t2b(self.away_temperature)
        for i, day_idx in enumerate((1, 2, 3)):
            c1[2 + i * 6 : 2 + i * 6 + 6] = self.days[day_idx].pack()
        c2 = bytearray(12)
        for i, day_idx in enumerate((4, 5)):
            c2[i * 6 : i * 6 + 6] = self.days[day_idx].pack()
        c3 = bytearray(12)
        for i, day_idx in enumerate((6, 0)):
            c3[i * 6 : i * 6 + 6] = self.days[day_idx].pack()
        return bytes(c1), bytes(c2), bytes(c3)


@dataclass
class SecretKey:
    """Characteristic 0x3f — 16 bytes raw (NOT encrypted)."""

    key: bytes

    @classmethod
    def parse(cls, data: bytes) -> "SecretKey":
        return cls(key=bytes(data[:16]))


class ErrorFlag(IntEnum):
    """Bit-to-error-code mapping (bit N set ⇒ error code N+1).

    From the official Danfoss app's BufferedThermostat.java.
    """

    SENSOR_FRONT = 0       # E1: front temperature sensor
    SENSOR_VALVE = 1       # E2: valve / center temperature sensor
    MEMORY = 2             # E3
    HARDWARE = 3           # E4
    E5 = 4                 # E5 (unlabeled in app)
    MOTOR_JAMMED = 5       # E6
    COMMS_MODULE = 6       # E7
    INVALID_COMMS = 7      # E8
    VALVE_NOT_CLOSING = 8  # E9
    INVALID_CLOCK = 9      # E10: battery was replaced
    E11 = 10
    RADIO_COMMS = 11       # E12
    ENCODER_JAMMED = 12    # E13
    LOW_BATTERY = 13       # E14
    CRITICAL_BATTERY = 14  # E15


@dataclass
class Errors:
    """Characteristic 0x39 — 16-bit sticky-fault bitfield.

    The Danfoss app reads it as a single int16. Our local byte order matches
    the libetrv chunk-reversal pipeline (little-endian after decrypt).
    """

    raw: int

    @classmethod
    def parse(cls, data: bytes) -> "Errors":
        return cls(raw=int.from_bytes(data[:2], "little"))

    @property
    def any(self) -> bool:
        return self.raw != 0

    def has(self, flag: ErrorFlag) -> bool:
        return bool(self.raw & (1 << int(flag)))

    @property
    def active_flags(self) -> list[ErrorFlag]:
        return [f for f in ErrorFlag if self.has(f)]
