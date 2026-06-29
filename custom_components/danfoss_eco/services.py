"""Domain-level services for Danfoss Eco."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .const import DOMAIN
from .coordinator import ETRVCoordinator

_LOGGER = logging.getLogger(__name__)

SYNC_CLOCK_SCHEMA = vol.Schema({vol.Required("device_id"): cv.string})


def _coordinator_for_device(hass: HomeAssistant, device_id: str) -> ETRVCoordinator:
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise vol.Invalid(f"unknown device_id {device_id}")
    for entry_id in device.config_entries:
        coord = hass.data.get(DOMAIN, {}).get(entry_id)
        if coord is not None:
            return coord
    raise vol.Invalid(f"device {device_id} is not a Danfoss Eco device")


async def async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, "sync_clock"):
        return

    async def handle_sync_clock(call: ServiceCall) -> None:
        coord = _coordinator_for_device(hass, call.data["device_id"])
        await coord.async_sync_clock()

    hass.services.async_register(DOMAIN, "sync_clock", handle_sync_clock, schema=SYNC_CLOCK_SCHEMA)


def async_unload_services(hass: HomeAssistant) -> None:
    if not hass.data.get(DOMAIN):
        if hass.services.has_service(DOMAIN, "sync_clock"):
            hass.services.async_remove(DOMAIN, "sync_clock")
