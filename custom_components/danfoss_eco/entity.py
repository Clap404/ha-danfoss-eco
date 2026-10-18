"""Entity base classes and the per-platform setup helper."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity, EntityDescription
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ETRVCoordinator, ETRVState
from .etrv import ConfigBit

type ETRVConfigEntry = ConfigEntry[ETRVCoordinator]
type SetupEntry = Callable[[HomeAssistant, ETRVConfigEntry, AddEntitiesCallback], Awaitable[None]]


def platform_setup(entities: Callable[[ETRVCoordinator], Iterable[Entity]]) -> SetupEntry:
    """Build a platform's `async_setup_entry` from an entity factory."""

    async def async_setup_entry(
        hass: HomeAssistant, entry: ETRVConfigEntry, async_add_entities: AddEntitiesCallback
    ) -> None:
        async_add_entities(entities(entry.runtime_data))

    return async_setup_entry


class ETRVEntity(CoordinatorEntity[ETRVCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: ETRVCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.mac}_{key}"
        # Manufacturer, model and versions are filled in by the coordinator after its first poll.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.mac)},
            connections={("bluetooth", coordinator.address)},
            name=coordinator.entry.title,
        )

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.has_data


@dataclass(frozen=True, kw_only=True)
class ETRVDescription[V](EntityDescription):
    """Mixed into each platform's description: how to read the state from ETRVState."""

    value_fn: Callable[[ETRVState], V]


def config_bit(bit: ConfigBit) -> Callable[[ETRVState], bool]:
    return lambda s: s.settings.get_bit(bit)


class ETRVDescribedEntity[V](ETRVEntity):
    """Entity whose state is `entity_description.value_fn(coordinator.data)`."""

    entity_description: ETRVDescription[V]

    def __init__(self, coordinator: ETRVCoordinator, description: ETRVDescription[V]) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    def _value(self) -> V:
        return self.entity_description.value_fn(self.coordinator.data)
