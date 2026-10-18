"""Diagnostic sensors."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, SIGNAL_STRENGTH_DECIBELS_MILLIWATT, EntityCategory

from .entity import ETRVDescribedEntity, ETRVDescription, platform_setup


@dataclass(frozen=True, kw_only=True)
class ETRVSensorDescription(SensorEntityDescription, ETRVDescription[int | str | None]):
    pass


SENSORS = (
    ETRVSensorDescription(
        key="battery",
        translation_key="battery",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.battery.level,
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
            ", ".join(f.name for f in s.errors.active_flags) or "ok" if s.errors else None
        ),
    ),
)


class ETRVSensor(ETRVDescribedEntity[int | str | None], SensorEntity):
    entity_description: ETRVSensorDescription

    @property
    def native_value(self) -> int | str | None:
        return self._value()


async_setup_entry = platform_setup(lambda c: (ETRVSensor(c, d) for d in SENSORS))
