"""Editing the weekly schedule from user input.

Shared by the `set_schedule` service and the Configure dialog so both accept
and render the same format. A day is comma-separated comfort periods, each
``HH:MM-HH:MM`` on a 30-minute grid, e.g. ``06:00-08:30, 16:30-22:30`` (max
3). An end of ``00:00`` means end of day. An empty string clears the day.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import logging

from .etrv import DaySchedule, Schedule
from .etrv.schedule import MINUTES_PER_DAY

_LOGGER = logging.getLogger(__name__)

# Mon..Sun for display; values are the device's day index (0=Sun .. 6=Sat).
DAY_TO_INDEX: dict[str, int] = {
    "monday": 1,
    "tuesday": 2,
    "wednesday": 3,
    "thursday": 4,
    "friday": 5,
    "saturday": 6,
    "sunday": 0,
}


def _to_minutes(value: str, *, is_end: bool) -> int:
    value = value.strip()
    try:
        hh, mm = value.split(":")
        minutes = int(hh) * 60 + int(mm)
    except ValueError as exc:
        raise ValueError(f"invalid time '{value}', expected HH:MM") from exc
    if is_end and minutes == 0:
        return MINUTES_PER_DAY
    return minutes


def _format_minutes(minutes: int) -> str:
    if minutes == MINUTES_PER_DAY:
        return "00:00"
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_day(text: str) -> DaySchedule:
    """Parse a day string into a validated DaySchedule. Raises ValueError."""
    periods: list[tuple[int, int]] = []
    for chunk in (c.strip() for c in text.split(",") if c.strip()):
        start, sep, end = chunk.partition("-")
        if not sep:
            raise ValueError(f"period '{chunk}' must look like 06:00-08:30")
        periods.append((_to_minutes(start, is_end=False), _to_minutes(end, is_end=True)))
    day = DaySchedule(periods)
    day.validate()
    return day


def format_day(day: DaySchedule) -> str:
    return ", ".join(f"{_format_minutes(s)}-{_format_minutes(e)}" for s, e in day.periods)


def patch_schedule(
    base: Schedule | None,
    *,
    home_temperature: float | None,
    away_temperature: float | None,
    days: Mapping[str, DaySchedule],
    device: str,
) -> Schedule:
    """Copy `base` with the given fields replaced; days not given keep their program.

    Without a base (schedule never read from the device) the missing days are
    written empty, which is what a user editing a fresh thermostat expects.
    """
    if base is None:
        _LOGGER.warning(
            "%s: schedule not yet read from device, days not given are written empty", device
        )
    schedule = deepcopy(base) if base is not None else Schedule.empty()
    if home_temperature is not None:
        schedule.home_temperature = float(home_temperature)
    if away_temperature is not None:
        schedule.away_temperature = float(away_temperature)
    for name, day in days.items():
        schedule.days[DAY_TO_INDEX[name]] = day
    return schedule
