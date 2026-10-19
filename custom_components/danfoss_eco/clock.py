"""The device clock: what its schedule runs on, and keeping it right.

The device has no crystal trim and loses about a minute a week. The app
sidesteps that by rewriting the clock on every connect; we rewrite it when
it drifts past CLOCK_DRIFT_TOLERANCE_S, inside the poll's existing session.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
import logging

from .etrv import ConfigBit, CurrentTime, ErrorFlag, Errors, ETRVClient, Settings

_LOGGER = logging.getLogger(__name__)

CLOCK_DRIFT_TOLERANCE_S = 90.0


def local_utc_offset_seconds() -> int:
    """Full local UTC offset, DST hour included: the poll rewrites it when it changes."""
    offset = datetime.now().astimezone().utcoffset()
    return int(offset.total_seconds()) if offset else 0


async def async_disable_daylight_saving(client: ETRVClient, settings: Settings) -> Settings:
    """Clear the daylight_saving bit: the firmware knows one DST rule, so we send the offset."""
    if not settings.get_bit(ConfigBit.DAYLIGHT_SAVING):
        return settings
    settings = settings.with_bit(ConfigBit.DAYLIGHT_SAVING, False)
    await client.write_settings(settings)
    _LOGGER.debug("daylight_saving bit cleared")
    return settings


def device_wall_clock(current_time: CurrentTime | None) -> datetime:
    """The device's local time right now: UTC + stored offset.

    Falls back to this machine's timezone if the clock has never been read.
    """
    if current_time is None:
        return datetime.now().astimezone()
    offset = timezone(timedelta(seconds=current_time.offset_seconds))
    return datetime.now(tz=UTC).astimezone(offset)


async def async_repair_clock(
    client: ETRVClient,
    current_time: CurrentTime | None,
    errors: Errors | None,
    *,
    force: bool = False,
) -> tuple[CurrentTime | None, Errors | None]:
    """Set the clock if unset, drifted, offset stale or `force`, then acknowledge E10 if latched.

    The device refuses SCHEDULE mode while INVALID_CLOCK is latched, and the
    flag stays latched after the clock is set until the fault word is written
    back without it. Only that bit is cleared, so real faults stay visible.
    """
    now = datetime.now(tz=UTC)
    unset = current_time is not None and current_time.time is None
    drifted = (
        current_time is not None
        and current_time.time is not None
        and abs((now - current_time.time).total_seconds()) > CLOCK_DRIFT_TOLERANCE_S
    )
    offset = local_utc_offset_seconds()
    stale_offset = current_time is not None and current_time.offset_seconds != offset
    if force or unset or drifted or stale_offset:
        await client.write_current_time(CurrentTime(time=now, offset_seconds=offset))
        current_time = await client.read_current_time()
        _LOGGER.debug(
            "clock set (unset=%s drifted=%s stale_offset=%s forced=%s)",
            unset,
            drifted,
            stale_offset,
            force,
        )
    if errors is not None and errors.has(ErrorFlag.INVALID_CLOCK):
        await client.write_errors(errors.without(ErrorFlag.INVALID_CLOCK))
        errors = await client.read_errors()
    return current_time, errors
