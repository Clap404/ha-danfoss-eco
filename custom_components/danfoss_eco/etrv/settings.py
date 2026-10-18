"""The Settings characteristic: operating mode, config bits, temperature limits."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import IntEnum
import logging
import struct
from typing import ClassVar

from .codec import datetime_from_epoch, epoch_from_datetime, temp_from_byte, temp_to_byte

_LOGGER = logging.getLogger(__name__)


class Mode(IntEnum):
    """Operating mode.

    The device packs the mode and the mode to return to after vacation/pause
    into one byte:

        0 MANUAL          1 SCHEDULE
        2 VACATION→MANUAL 3 VACATION→SCHEDULE
        4 PAUSE→MANUAL    5 PAUSE→SCHEDULE

    Verified against the app's parseSettings/composeSettings.
    """

    MANUAL = 0
    SCHEDULE = 1
    VACATION = 2
    PAUSE = 4

    @staticmethod
    def from_byte(b: int) -> Mode:
        if b not in _BYTE_TO_MODE:
            _LOGGER.warning("unknown mode byte 0x%02x, treating as MANUAL", b)
        return _BYTE_TO_MODE.get(b, Mode.MANUAL)

    @staticmethod
    def return_from_byte(b: int) -> Mode:
        """The mode the device reverts to when leaving vacation/pause."""
        return Mode.SCHEDULE if b % 2 else Mode.MANUAL

    @staticmethod
    def to_byte(mode: Mode, return_mode: Mode) -> int:
        if mode in (Mode.VACATION, Mode.PAUSE):
            return int(mode) + (return_mode == Mode.SCHEDULE)
        return int(mode)


_BYTE_TO_MODE = {
    0: Mode.MANUAL,
    1: Mode.SCHEDULE,
    2: Mode.VACATION,
    3: Mode.VACATION,
    4: Mode.PAUSE,
    5: Mode.PAUSE,
}


class ConfigBit(IntEnum):
    """Bit positions in `Settings.config_bits`, as received after decryption."""

    ADAPTABLE_REGULATION = 0  # forecast-based regulation
    DAYLIGHT_SAVING = 1
    VERTICAL_INSTALLATION = 2
    DISPLAY_FLIP = 3
    SLOW_REGULATION = 4
    CALIBRATED = 5
    VALVE_INSTALLED = 6
    CHILD_LOCK = 7


@dataclass
class Settings:
    """16 bytes: config bits, min, max, frost, mode, vacation temp, two vacation timestamps."""

    config_bits: int
    temperature_min: float
    temperature_max: float
    frost_protection_temperature: float
    mode: Mode
    return_mode: Mode
    vacation_temperature: float
    vacation_from: datetime | None
    vacation_to: datetime | None

    _FMT: ClassVar[str] = ">BBBBBBii2x"

    @classmethod
    def parse(cls, data: bytes) -> Settings:
        cfg, t_min, t_max, frost, mode_byte, vac_t, vf, vt = struct.unpack(
            cls._FMT, data[: struct.calcsize(cls._FMT)]
        )
        return cls(
            config_bits=cfg,
            temperature_min=temp_from_byte(t_min),
            temperature_max=temp_from_byte(t_max),
            frost_protection_temperature=temp_from_byte(frost),
            mode=Mode.from_byte(mode_byte),
            return_mode=Mode.return_from_byte(mode_byte),
            vacation_temperature=temp_from_byte(vac_t),
            vacation_from=datetime_from_epoch(vf),
            vacation_to=datetime_from_epoch(vt),
        )

    def pack(self) -> bytes:
        return struct.pack(
            self._FMT,
            self.config_bits,
            temp_to_byte(self.temperature_min),
            temp_to_byte(self.temperature_max),
            temp_to_byte(self.frost_protection_temperature),
            Mode.to_byte(self.mode, self.return_mode),
            temp_to_byte(self.vacation_temperature),
            epoch_from_datetime(self.vacation_from),
            epoch_from_datetime(self.vacation_to),
        )

    def get_bit(self, bit: ConfigBit) -> bool:
        return bool(self.config_bits & (1 << bit))

    def with_bit(self, bit: ConfigBit, value: bool) -> Settings:
        mask = 1 << bit
        bits = self.config_bits | mask if value else self.config_bits & ~mask
        return replace(self, config_bits=bits)
