"""Writable switches backed by Settings bits."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import ETRVCoordinator
from .entity import ETRVEntity
from .etrv.properties import ConfigBit


@dataclass(frozen=True, kw_only=True)
class ETRVSwitchDescription(SwitchEntityDescription):
    bit: ConfigBit


SWITCHES: tuple[ETRVSwitchDescription, ...] = (
    ETRVSwitchDescription(
        key="child_lock",
        translation_key="child_lock",
        entity_category=EntityCategory.CONFIG,
        bit=ConfigBit.CHILD_LOCK,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ETRVCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(ETRVSwitch(coordinator, d) for d in SWITCHES)


class ETRVSwitch(ETRVEntity, SwitchEntity):
    entity_description: ETRVSwitchDescription

    def __init__(self, coordinator: ETRVCoordinator, description: ETRVSwitchDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        settings = self.coordinator.data.settings if self.coordinator.data else None
        if settings is None:
            return None
        return settings.get_bit(self.entity_description.bit)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._write(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write(False)

    async def _write(self, value: bool) -> None:
        data = self.coordinator.data
        if data is None or data.settings is None:
            return
        new_settings = replace(data.settings)
        new_settings.set_bit(self.entity_description.bit, value)
        await self.coordinator.async_write_settings(new_settings)
