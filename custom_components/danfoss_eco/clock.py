"""The device clock: what its schedule runs on, and keeping it right.

The device has no crystal trim and loses about a minute a week. The app
sidesteps that by rewriting the clock on every connect; we rewrite it when
it drifts past CLOCK_DRIFT_TOLERANCE_S, inside the poll's existing session.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
import logging
import time

from .etrv import ConfigBit, CurrentTime, ErrorFlag, Errors, ETRVClient, Settings

_LOGGER = logging.getLogger(__name__)

CLOCK_DRIFT_TOLERANCE_S = 90.0


def raw_utc_offset_seconds() -> int:
    """Standard (non-DST) UTC offset, like the app's TimeZone.getRawOffset().

    The device adds the DST hour itself from the daylight_saving config bit,
    so sending the DST-inclusive offset would double-count it half the year.
    """
    return -time.timezone


def device_wall_clock(current_time: CurrentTime | None, settings: Settings) -> datetime:
    """The device's local time right now: UTC + stored offset + DST hour if enabled.

    Falls back to this machine's timezone if the clock has never been read.
    """
    if current_time is None:
        return datetime.now().astimezone()
    offset = current_time.offset_seconds
    if settings.get_bit(ConfigBit.DAYLIGHT_SAVING):
        offset += 3600
    return datetime.now(tz=UTC).astimezone(timezone(timedelta(seconds=offset)))


async def async_repair_clock(
    client: ETRVClient,
    current_time: CurrentTime | None,
    errors: Errors | None,
    *,
    force: bool = False,
) -> tuple[CurrentTime | None, Errors | None]:
    """Set the clock if unset, drifted or `force`, then acknowledge E10 if latched.

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
    if force or unset or drifted:
        await client.write_current_time(
            CurrentTime(time=now, offset_seconds=raw_utc_offset_seconds())
        )
        current_time = await client.read_current_time()
        _LOGGER.debug("clock set (unset=%s drifted=%s forced=%s)", unset, drifted, force)
    if errors is not None and errors.has(ErrorFlag.INVALID_CLOCK):
        await client.write_errors(errors.without(ErrorFlag.INVALID_CLOCK))
        errors = await client.read_errors()
    return current_time, errors
