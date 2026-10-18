"""Domain-level services: sync_clock and set_schedule."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv, device_registry as dr
import voluptuous as vol

from .const import DOMAIN
from .coordinator import ETRVCoordinator
from .etrv import DaySchedule
from .schedule_form import DAY_TO_INDEX, parse_day, patch_schedule


def _day_schema(value: object) -> DaySchedule:
    try:
        return parse_day(cv.string(value))
    except ValueError as exc:
        raise vol.Invalid(str(exc)) from exc


_TEMPERATURE = vol.All(vol.Coerce(float), vol.Range(min=4, max=35))

SYNC_CLOCK_SCHEMA = vol.Schema({vol.Required("device_id"): cv.string})
SET_SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Optional("home_temperature"): _TEMPERATURE,
        vol.Optional("away_temperature"): _TEMPERATURE,
        **{vol.Optional(day): _day_schema for day in DAY_TO_INDEX},
    }
)


def _coordinator_for_device(hass: HomeAssistant, device_id: str) -> ETRVCoordinator:
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise vol.Invalid(f"unknown device_id {device_id}")
    for entry_id in device.config_entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry and entry.domain == DOMAIN and entry.state is ConfigEntryState.LOADED:
            return entry.runtime_data
    raise vol.Invalid(f"device {device_id} is not a loaded Danfoss Eco device")


@callback
def async_register_services(hass: HomeAssistant) -> None:
    async def handle_sync_clock(call: ServiceCall) -> None:
        await _coordinator_for_device(hass, call.data["device_id"]).async_sync_clock()

    async def handle_set_schedule(call: ServiceCall) -> None:
        coord = _coordinator_for_device(hass, call.data["device_id"])
        schedule = patch_schedule(
            coord.schedule,
            home_temperature=call.data.get("home_temperature"),
            away_temperature=call.data.get("away_temperature"),
            days={day: call.data[day] for day in DAY_TO_INDEX if day in call.data},
            device=coord.address,
        )
        await coord.async_write_schedule(schedule)

    hass.services.async_register(DOMAIN, "sync_clock", handle_sync_clock, schema=SYNC_CLOCK_SCHEMA)
    hass.services.async_register(
        DOMAIN, "set_schedule", handle_set_schedule, schema=SET_SCHEDULE_SCHEMA
    )
