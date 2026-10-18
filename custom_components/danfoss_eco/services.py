"""Domain-level services for Danfoss Eco."""

from __future__ import annotations

import logging
from copy import deepcopy

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .const import DOMAIN
from .coordinator import ETRVCoordinator
from .etrv.properties import Schedule
from .schedule_text import DAY_TO_INDEX, parse_day

_LOGGER = logging.getLogger(__name__)

SYNC_CLOCK_SCHEMA = vol.Schema({vol.Required("device_id"): cv.string})


def _day_schema(value):
    """Validate a day string, returning a DaySchedule (or raising vol.Invalid)."""
    try:
        return parse_day(cv.string(value))
    except ValueError as exc:
        raise vol.Invalid(str(exc)) from exc


SET_SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Optional("home_temperature"): vol.All(vol.Coerce(float), vol.Range(min=4, max=35)),
        vol.Optional("away_temperature"): vol.All(vol.Coerce(float), vol.Range(min=4, max=35)),
        **{vol.Optional(day): _day_schema for day in DAY_TO_INDEX},
    }
)


def _coordinator_for_device(hass: HomeAssistant, device_id: str) -> ETRVCoordinator:
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise vol.Invalid(f"unknown device_id {device_id}")
    for entry_id in device.config_entries:
        coord = hass.data.get(DOMAIN, {}).get(entry_id)
        if coord is not None:
            return coord
    raise vol.Invalid(f"device {device_id} is not a Danfoss Eco device")


@callback
def async_register_services(hass: HomeAssistant) -> None:
    async def handle_sync_clock(call: ServiceCall) -> None:
        coord = _coordinator_for_device(hass, call.data["device_id"])
        await coord.async_sync_clock()

    async def handle_set_schedule(call: ServiceCall) -> None:
        coord = _coordinator_for_device(hass, call.data["device_id"])
        # Start from the last-read schedule so callers can patch a single day.
        base = coord.data.schedule if coord.data else None
        if base is None:
            _LOGGER.warning(
                "%s: schedule not yet read from device, days not given are written empty",
                coord.address,
            )
        schedule = deepcopy(base) if base is not None else Schedule.empty()
        if "home_temperature" in call.data:
            schedule.home_temperature = call.data["home_temperature"]
        if "away_temperature" in call.data:
            schedule.away_temperature = call.data["away_temperature"]
        for day, index in DAY_TO_INDEX.items():
            if day in call.data:
                schedule.days[index] = call.data[day]
        await coord.async_write_schedule(schedule)

    hass.services.async_register(DOMAIN, "sync_clock", handle_sync_clock, schema=SYNC_CLOCK_SCHEMA)
    hass.services.async_register(
        DOMAIN, "set_schedule", handle_set_schedule, schema=SET_SCHEDULE_SCHEMA
    )
