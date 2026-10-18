"""Options flow (Settings → Devices & Services → Configure): PIN and weekly schedule.

The schedule is edited here so users never need Developer tools. Each day is
a text box of comfort periods like ``06:00-08:30, 16:30-22:30``.
"""

from __future__ import annotations

import logging
from typing import Any, cast

from homeassistant.config_entries import ConfigEntryState, ConfigFlowResult, OptionsFlow
from homeassistant.helpers import selector
import voluptuous as vol

from .const import CONF_PIN, DEFAULT_PIN
from .coordinator import ETRVCoordinator
from .etrv import Schedule
from .schedule_form import DAY_TO_INDEX, format_day, parse_day, patch_schedule

_LOGGER = logging.getLogger(__name__)


def valid_pin(pin: str) -> bool:
    return pin.isdigit() and len(pin) == 4


# HA registers selectors through an untyped registry, which erases their concrete classes.
_TEXT = cast(
    type[selector.Selector[selector.TextSelectorConfig]],
    selector.TextSelector,  # pyright: ignore[reportUnknownMemberType]
)
_TEMPERATURE = cast(
    selector.Selector[selector.NumberSelectorConfig],
    selector.NumberSelector(  # pyright: ignore[reportUnknownMemberType]
        selector.NumberSelectorConfig(
            min=4, max=35, step=0.5, unit_of_measurement="°C", mode=selector.NumberSelectorMode.BOX
        )
    ),
)


class DanfossEcoOptionsFlow(OptionsFlow):
    def _coordinator(self) -> ETRVCoordinator | None:
        entry = self.config_entry
        return entry.runtime_data if entry.state is ConfigEntryState.LOADED else None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        coord = self._coordinator()
        base = coord.schedule if coord else None
        errors: dict[str, str] = {}

        if user_input is not None:
            errors = await self._apply(coord, base, user_input)
            if not errors:
                return self.async_create_entry(title="", data={})

        current = base if base is not None else Schedule.empty()
        defaults: dict[str, str | float] = {
            CONF_PIN: self.config_entry.data.get(CONF_PIN, DEFAULT_PIN),
            "home_temperature": current.home_temperature,
            "away_temperature": current.away_temperature,
            **{day: format_day(current.days[index]) for day, index in DAY_TO_INDEX.items()},
            **(user_input or {}),  # keep what the user just typed on error
        }
        fields: dict[vol.Optional, object] = {vol.Optional(CONF_PIN): _TEXT()}
        if coord is not None:  # schedule editing needs a live coordinator
            fields |= {
                vol.Optional("home_temperature"): _TEMPERATURE,
                vol.Optional("away_temperature"): _TEMPERATURE,
                **{vol.Optional(day): _TEXT() for day in DAY_TO_INDEX},
            }
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(vol.Schema(fields), defaults),
            errors=errors,
        )

    async def _apply(
        self, coord: ETRVCoordinator | None, base: Schedule | None, user_input: dict[str, Any]
    ) -> dict[str, str]:
        """Store the PIN and write the schedule. Returns form errors, empty on success."""
        pin = (user_input.get(CONF_PIN) or "").strip()
        if pin and not valid_pin(pin):
            return {CONF_PIN: "invalid_pin"}
        if pin and pin != self.config_entry.data.get(CONF_PIN):
            self.hass.config_entries.async_update_entry(
                self.config_entry, data={**self.config_entry.data, CONF_PIN: pin}
            )
        if coord is None:
            return {}

        try:
            days = {day: parse_day(user_input[day]) for day in DAY_TO_INDEX if day in user_input}
        except ValueError as exc:
            _LOGGER.warning("invalid schedule input: %s", exc)
            return {"base": "invalid_schedule"}
        schedule = patch_schedule(
            base,
            home_temperature=user_input.get("home_temperature"),
            away_temperature=user_input.get("away_temperature"),
            days=days,
            device=coord.address,
        )
        if schedule == base:
            return {}
        try:
            await coord.async_write_schedule(schedule)
        except Exception as exc:
            _LOGGER.error("schedule write failed: %s", exc)
            return {"base": "write_failed"}
        return {}
