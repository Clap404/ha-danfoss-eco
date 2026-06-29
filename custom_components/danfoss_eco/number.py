"""Configurable temperature setpoints (min, max, frost, vacation)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Awaitable, Callable

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import ETRVCoordinator, ETRVState
from .entity import ETRVEntity
from .etrv.properties import Settings


@dataclass(frozen=True, kw_only=True)
class ETRVSettingsNumberDescription(NumberEntityDescription):
    value_fn: Callable[[ETRVState], float | None]
    set_fn: Callable[[Settings, float], Settings]


SETTINGS_NUMBERS: tuple[ETRVSettingsNumberDescription, ...] = (
    ETRVSettingsNumberDescription(
        key="temperature_min",
        translation_key="temperature_min",
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=4.0,
        native_max_value=14.0,
        native_step=0.5,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: s.settings.temperature_min if s.settings else None,
        set_fn=lambda settings, v: replace(settings, temperature_min=v),
    ),
    ETRVSettingsNumberDescription(
        key="temperature_max",
        translation_key="temperature_max",
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=14.0,
        native_max_value=35.0,
        native_step=0.5,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: s.settings.temperature_max if s.settings else None,
        set_fn=lambda settings, v: replace(settings, temperature_max=v),
    ),
    ETRVSettingsNumberDescription(
        key="frost_protection_temperature",
        translation_key="frost_protection_temperature",
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=4.0,
        native_max_value=10.0,
        native_step=0.5,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: s.settings.frost_protection_temperature if s.settings else None,
        set_fn=lambda settings, v: replace(settings, frost_protection_temperature=v),
    ),
    ETRVSettingsNumberDescription(
        key="vacation_temperature",
        translation_key="vacation_temperature",
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=4.0,
        native_max_value=28.0,
        native_step=0.5,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: s.settings.vacation_temperature if s.settings else None,
        set_fn=lambda settings, v: replace(settings, vacation_temperature=v),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ETRVCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(ETRVSettingsNumber(coordinator, d) for d in SETTINGS_NUMBERS)


class ETRVSettingsNumber(ETRVEntity, NumberEntity):
    entity_description: ETRVSettingsNumberDescription

    def __init__(
        self,
        coordinator: ETRVCoordinator,
        description: ETRVSettingsNumberDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        if data is None:
            return None
        return self.entity_description.value_fn(data)

    async def async_set_native_value(self, value: float) -> None:
        data = self.coordinator.data
        if data is None or data.settings is None:
            return
        new_settings = self.entity_description.set_fn(data.settings, value)
        await self.coordinator.async_write_settings(new_settings)
