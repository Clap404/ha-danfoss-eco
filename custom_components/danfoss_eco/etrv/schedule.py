"""The weekly schedule, spread over three characteristics."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import ClassVar

from .codec import temp_from_byte, temp_to_byte

MINUTES_PER_DAY = 1440
SLOT_MINUTES = 30
MAX_PERIODS = 3


@dataclass
class DaySchedule:
    """Comfort periods as (start, end) minutes since midnight, on a 30-minute grid.

    Inside a period the home temperature applies, outside it the away
    temperature. On the wire a day is 6 bytes: three (start, end) pairs in
    half-hour units, unused pairs zero.
    """

    periods: list[tuple[int, int]] = field(default_factory=list[tuple[int, int]])

    @classmethod
    def parse(cls, data: bytes) -> DaySchedule:
        if len(data) != 6:
            raise ValueError("DaySchedule needs 6 bytes")
        minutes = [b * SLOT_MINUTES for b in data]
        pairs = zip(minutes[::2], minutes[1::2], strict=True)
        return cls([(start, end) for start, end in pairs if (start, end) != (0, 0)])

    def validate(self) -> None:
        """Enforce the device's constraints (mirrors WeekdaySchedule.throwIfInvalid)."""
        if len(self.periods) > MAX_PERIODS:
            raise ValueError(f"at most {MAX_PERIODS} periods per day")
        for i, (start, end) in enumerate(self.periods, start=1):
            for label, value in (("start", start), ("end", end)):
                if not 0 <= value <= MINUTES_PER_DAY:
                    raise ValueError(f"period {i} {label}={value} not in 0..{MINUTES_PER_DAY}")
                if value % SLOT_MINUTES:
                    raise ValueError(
                        f"period {i} {label}={value} not on the {SLOT_MINUTES}-minute grid"
                    )
            if start >= end:
                raise ValueError(f"period {i}: start ({start}) must be before end ({end})")

    def pack(self) -> bytes:
        self.validate()
        flat = [m for period in self.periods for m in period]
        flat += [0] * (2 * MAX_PERIODS - len(flat))
        return bytes(m // SLOT_MINUTES for m in flat)

    def is_home_at(self, minutes: int) -> bool:
        return any(start <= minutes < end for start, end in self.periods)


@dataclass
class Schedule:
    """Full weekly schedule. Day indices follow the device: 0=Sun .. 6=Sat."""

    home_temperature: float
    away_temperature: float
    days: list[DaySchedule]

    # (characteristic index, byte offset, day index) of each 6-byte day block.
    _LAYOUT: ClassVar[tuple[tuple[int, int, int], ...]] = (
        (0, 2, 1),
        (0, 8, 2),
        (0, 14, 3),  # char1: Mon, Tue, Wed after the two temps
        (1, 0, 4),
        (1, 6, 5),  # char2: Thu, Fri
        (2, 0, 6),
        (2, 6, 0),  # char3: Sat, Sun
    )
    _SIZES: ClassVar[tuple[int, int, int]] = (20, 12, 12)

    @classmethod
    def empty(cls) -> Schedule:
        return cls(
            home_temperature=21.0, away_temperature=17.0, days=[DaySchedule() for _ in range(7)]
        )

    @staticmethod
    def day_index(local_time: datetime) -> int:
        return (local_time.weekday() + 1) % 7

    def setpoint_at(self, local_time: datetime) -> float:
        day = self.days[self.day_index(local_time)]
        minutes = local_time.hour * 60 + local_time.minute
        return self.home_temperature if day.is_home_at(minutes) else self.away_temperature

    def next_transition(self, local_time: datetime) -> datetime | None:
        """When the setpoint next changes, strictly after `local_time`.

        Scans a full week so a single weekly period is still found. None if
        the schedule never changes the setpoint.
        """
        best: datetime | None = None
        for delta in range(8):
            midnight = (local_time + timedelta(days=delta)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            for period in self.days[self.day_index(midnight)].periods:
                for minute in period:
                    at = midnight + timedelta(minutes=minute)
                    if at > local_time and (best is None or at < best):
                        best = at
        return best

    @classmethod
    def parse(cls, chars: tuple[bytes, bytes, bytes]) -> Schedule:
        if any(len(c) < size for c, size in zip(chars, cls._SIZES, strict=True)):
            raise ValueError("schedule chars too short")
        schedule = cls.empty()
        schedule.home_temperature = temp_from_byte(chars[0][0])
        schedule.away_temperature = temp_from_byte(chars[0][1])
        for c, off, day_idx in cls._LAYOUT:
            schedule.days[day_idx] = DaySchedule.parse(chars[c][off : off + 6])
        return schedule

    def pack(self) -> tuple[bytes, bytes, bytes]:
        chars = tuple(bytearray(size) for size in self._SIZES)
        chars[0][0] = temp_to_byte(self.home_temperature)
        chars[0][1] = temp_to_byte(self.away_temperature)
        for c, off, day_idx in self._LAYOUT:
            chars[c][off : off + 6] = self.days[day_idx].pack()
        return bytes(chars[0]), bytes(chars[1]), bytes(chars[2])
