"""Configurable temperature limits stored in Settings."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import EntityCategory, UnitOfTemperature

from .entity import ETRVDescribedEntity, ETRVDescription, platform_setup
from .etrv import Settings


@dataclass(frozen=True, kw_only=True)
class ETRVNumberDescription(NumberEntityDescription, ETRVDescription[float]):
    set_fn: Callable[[Settings, float], Settings]


def _temperature(field: str, low: float, high: float) -> ETRVNumberDescription:
    """A Settings temperature field exposed as a number box."""
    return ETRVNumberDescription(
        key=field,
        translation_key=field,
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=low,
        native_max_value=high,
        native_step=0.5,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda s: getattr(s.settings, field),
        set_fn=lambda settings, value: replace(settings, **{field: value}),
    )


NUMBERS = (
    _temperature("temperature_min", 4.0, 14.0),
    _temperature("temperature_max", 14.0, 35.0),
    _temperature("frost_protection_temperature", 4.0, 10.0),
    _temperature("vacation_temperature", 4.0, 28.0),
)


class ETRVNumber(ETRVDescribedEntity[float], NumberEntity):
    entity_description: ETRVNumberDescription

    @property
    def native_value(self) -> float:
        return self._value()

    async def async_set_native_value(self, value: float) -> None:
        settings = self.entity_description.set_fn(self.coordinator.settings, value)
        await self.coordinator.async_write_settings(settings)


async_setup_entry = platform_setup(lambda c: (ETRVNumber(c, d) for d in NUMBERS))
