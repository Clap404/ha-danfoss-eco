"""Diagnostic sensors for Danfoss eTRV."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import ETRVCoordinator, ETRVState
from .entity import ETRVEntity


@dataclass(frozen=True, kw_only=True)
class ETRVSensorDescription(SensorEntityDescription):
    value_fn: Callable[[ETRVState], float | int | str | None]


SENSORS: tuple[ETRVSensorDescription, ...] = (
    ETRVSensorDescription(
        key="battery",
        translation_key="battery",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.battery.level if s.battery else None,
    ),
    ETRVSensorDescription(
        key="rssi",
        translation_key="rssi",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.rssi,
    ),
    ETRVSensorDescription(
        key="error_flags",
        translation_key="error_flags",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: (
            (", ".join(f.name for f in s.errors.active_flags) or "ok") if s.errors else None
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ETRVCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(ETRVSensor(coordinator, d) for d in SENSORS)


class ETRVSensor(ETRVEntity, SensorEntity):
    entity_description: ETRVSensorDescription

    def __init__(self, coordinator: ETRVCoordinator, description: ETRVSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | int | str | None:
        data = self.coordinator.data
        if data is None:
            return None
        return self.entity_description.value_fn(data)
