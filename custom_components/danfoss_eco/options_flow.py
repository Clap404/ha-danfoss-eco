"""Options flow (Settings → Devices & Services → Configure): PIN and weekly schedule.

Once the TRV has been polled, the PIN fields set the PIN the TRV enforces.
Before that, the PIN field only sets the PIN HA sends when connecting.

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
from .etrv import PinCode, Schedule
from .schedule_form import DAY_TO_INDEX, format_day, parse_day, patch_schedule

_LOGGER = logging.getLogger(__name__)


def valid_pin(pin: str) -> bool:
    return pin.isdigit() and len(pin) == 4


# HA registers selectors through an untyped registry, which erases their concrete classes.
_TEXT = cast(
    type[selector.Selector[selector.TextSelectorConfig]],
    selector.TextSelector,  # pyright: ignore[reportUnknownMemberType]
)
_BOOLEAN = cast(
    type[selector.Selector[selector.BooleanSelectorConfig]],
    selector.BooleanSelector,  # pyright: ignore[reportUnknownMemberType]
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
        if entry.state is not ConfigEntryState.LOADED:
            return None
        coord: ETRVCoordinator = entry.runtime_data
        return coord if coord.has_data else None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        coord = self._coordinator()
        base = coord.schedule if coord else None
        errors: dict[str, str] = {}

        if user_input is not None:
            errors = await self._apply(coord, base, user_input)
            if not errors:
                return self.async_create_entry(title="", data={})

        current = base if base is not None else Schedule.empty()
        pin_code = coord.pin_code if coord else None
        defaults: dict[str, str | float | bool] = {
            CONF_PIN: pin_code.pin
            if pin_code
            else self.config_entry.data.get(CONF_PIN, DEFAULT_PIN),
            "pin_enabled": bool(pin_code and pin_code.enabled),
            "home_temperature": current.home_temperature,
            "away_temperature": current.away_temperature,
            **{day: format_day(current.days[index]) for day, index in DAY_TO_INDEX.items()},
            **(user_input or {}),  # keep what the user just typed on error
        }
        fields: dict[vol.Optional, object] = {}
        if pin_code is not None:
            fields[vol.Optional("pin_enabled")] = _BOOLEAN()
        fields[vol.Optional(CONF_PIN)] = _TEXT()
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
        """Apply the PIN, then the schedule. Returns form errors, empty on success."""
        pin = (user_input.get(CONF_PIN) or "").strip()
        if pin and not valid_pin(pin):
            return {CONF_PIN: "invalid_pin"}
        if coord is None or coord.pin_code is None:
            if pin and pin != self.config_entry.data.get(CONF_PIN):
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data={**self.config_entry.data, CONF_PIN: pin}
                )
        elif errors := await self._apply_pin_code(coord, coord.pin_code, pin, user_input):
            return errors
        if coord is None:
            return {}
        return await self._apply_schedule(coord, base, user_input)

    async def _apply_pin_code(
        self, coord: ETRVCoordinator, current: PinCode, pin: str, user_input: dict[str, Any]
    ) -> dict[str, str]:
        enabled = bool(user_input.get("pin_enabled"))
        if enabled and not pin:
            return {CONF_PIN: "invalid_pin"}
        pin_code = PinCode(pin=pin or current.pin, enabled=enabled)
        if pin_code == current:
            return {}
        try:
            await coord.async_write_pin_code(pin_code)
        except Exception as exc:
            _LOGGER.error("PIN write failed: %s", exc)
            return {"base": "pin_write_failed"}
        return {}

    async def _apply_schedule(
        self, coord: ETRVCoordinator, base: Schedule | None, user_input: dict[str, Any]
    ) -> dict[str, str]:
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
