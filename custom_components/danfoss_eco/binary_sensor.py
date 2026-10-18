"""Binary sensors for error flags and config bits."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory

from .coordinator import ETRVState
from .entity import ETRVDescribedEntity, ETRVDescription, config_bit, platform_setup
from .etrv import ConfigBit, ErrorFlag


@dataclass(frozen=True, kw_only=True)
class ETRVBinarySensorDescription(BinarySensorEntityDescription, ETRVDescription[bool | None]):
    pass


def _error(*flags: ErrorFlag) -> Callable[[ETRVState], bool | None]:
    return lambda s: s.errors.has(*flags) if s.errors else None


BINARY_SENSORS = (
    ETRVBinarySensorDescription(
        key="error_motor_jammed",
        translation_key="error_motor_jammed",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_error(ErrorFlag.MOTOR_JAMMED),
    ),
    ETRVBinarySensorDescription(
        key="error_low_battery",
        translation_key="error_low_battery",
        device_class=BinarySensorDeviceClass.BATTERY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_error(ErrorFlag.LOW_BATTERY, ErrorFlag.CRITICAL_BATTERY),
    ),
    ETRVBinarySensorDescription(
        key="error_sensor",
        translation_key="error_sensor",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_error(ErrorFlag.SENSOR_FRONT, ErrorFlag.SENSOR_VALVE),
    ),
    ETRVBinarySensorDescription(
        key="error_clock_invalid",
        translation_key="error_clock_invalid",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_error(ErrorFlag.INVALID_CLOCK),
    ),
    ETRVBinarySensorDescription(
        key="adaptable_regulation",
        translation_key="adaptable_regulation",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=config_bit(ConfigBit.ADAPTABLE_REGULATION),
    ),
)


class ETRVBinarySensor(ETRVDescribedEntity[bool | None], BinarySensorEntity):
    entity_description: ETRVBinarySensorDescription

    @property
    def is_on(self) -> bool | None:
        return self._value()


async_setup_entry = platform_setup(lambda c: (ETRVBinarySensor(c, d) for d in BINARY_SENSORS))
