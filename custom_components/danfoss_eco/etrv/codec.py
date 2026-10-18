"""Scalar conversions shared by the characteristic codecs."""

from __future__ import annotations

from datetime import UTC, datetime


def temp_from_byte(raw: int) -> float:
    """Device temperatures are half-degrees."""
    return raw * 0.5


def temp_to_byte(temp: float) -> int:
    return round(temp * 2)


def datetime_from_epoch(epoch: int) -> datetime | None:
    """int32 unix seconds; 0 means unset."""
    return None if epoch == 0 else datetime.fromtimestamp(epoch, tz=UTC)


def epoch_from_datetime(dt: datetime | None) -> int:
    if dt is None:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp())
