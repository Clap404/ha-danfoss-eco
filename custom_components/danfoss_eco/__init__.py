"""Danfoss Eco BLE integration."""

from __future__ import annotations

import logging

from homeassistant.const import Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .coordinator import ETRVCoordinator
from .entity import ETRVConfigEntry
from .services import async_register_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.CLIMATE,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SWITCH,
]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ETRVConfigEntry) -> bool:
    coordinator = ETRVCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(
        hass.bus.async_listen(
            dr.EVENT_DEVICE_REGISTRY_UPDATED, _device_rename_listener(hass, coordinator)
        )
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ETRVConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entry.runtime_data.async_drop_pending()
    return unload_ok


def _device_rename_listener(hass: HomeAssistant, coordinator: ETRVCoordinator):
    """Push HA device renames to the name stored on the TRV."""

    @callback
    def _handle(event: Event[dr.EventDeviceRegistryUpdatedData]) -> None:
        data = event.data
        if data["action"] != "update" or "name_by_user" not in data["changes"]:
            return
        device = dr.async_get(hass).async_get(data["device_id"])
        if device is None or coordinator.entry.entry_id not in device.config_entries:
            return
        name = device.name_by_user or device.name or coordinator.entry.title
        hass.async_create_task(_write_name(coordinator, name))

    return _handle


async def _write_name(coordinator: ETRVCoordinator, name: str) -> None:
    try:
        await coordinator.async_write_name(name)
    except Exception as exc:
        _LOGGER.warning("writing name %r to %s failed: %s", name, coordinator.address, exc)
