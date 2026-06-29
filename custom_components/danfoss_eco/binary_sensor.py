"""Binary sensors for individual error flags + config bits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import ETRVCoordinator, ETRVState
from .entity import ETRVEntity
from .etrv.properties import ConfigBit, ErrorFlag


@dataclass(frozen=True, kw_only=True)
class ETRVBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: Callable[[ETRVState], bool | None]


def _error(flag: ErrorFlag) -> Callable[[ETRVState], bool | None]:
    return lambda s: s.errors.has(flag) if s.errors else None


def _cfg(bit: ConfigBit) -> Callable[[ETRVState], bool | None]:
    return lambda s: s.settings.get_bit(bit) if s.settings else None


BINARY_SENSORS: tuple[ETRVBinarySensorDescription, ...] = (
    # Errors of interest as standalone problem sensors
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
        value_fn=lambda s: (
            s.errors.has(ErrorFlag.LOW_BATTERY) or s.errors.has(ErrorFlag.CRITICAL_BATTERY)
            if s.errors
            else None
        ),
    ),
    ETRVBinarySensorDescription(
        key="error_sensor",
        translation_key="error_sensor",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: (
            (s.errors.has(ErrorFlag.SENSOR_FRONT) or s.errors.has(ErrorFlag.SENSOR_VALVE))
            if s.errors
            else None
        ),
    ),
    ETRVBinarySensorDescription(
        key="error_clock_invalid",
        translation_key="error_clock_invalid",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_error(ErrorFlag.INVALID_CLOCK),
    ),
    # Config flags as plain on/off sensors
    ETRVBinarySensorDescription(
        key="adaptable_regulation",
        translation_key="adaptable_regulation",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_cfg(ConfigBit.ADAPTABLE_REGULATION),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ETRVCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(ETRVBinarySensor(coordinator, d) for d in BINARY_SENSORS)


class ETRVBinarySensor(ETRVEntity, BinarySensorEntity):
    entity_description: ETRVBinarySensorDescription

    def __init__(
        self,
        coordinator: ETRVCoordinator,
        description: ETRVBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        if data is None:
            return None
        return self.entity_description.value_fn(data)
