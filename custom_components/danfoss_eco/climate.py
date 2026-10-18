"""Climate entity.

Device `Mode` → HA:
  - MANUAL   → HEAT  (target_temperature drives the valve)
  - SCHEDULE → AUTO  (device runs its stored weekly schedule)
  - VACATION / PAUSE → preset over the HVAC mode they return to

HVAC OFF is MANUAL at the frost-protection temperature.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import ATTR_HVAC_MODE, ClimateEntityFeature, HVACMode
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_HALVES, UnitOfTemperature
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_point_in_utc_time

from .coordinator import ETRVCoordinator
from .entity import ETRVEntity, platform_setup
from .etrv import Mode, Settings

PRESET_NONE = "none"
PRESET_VACATION = "vacation"
PRESET_PAUSE = "pause"

_MODE_TO_HVAC = {Mode.MANUAL: HVACMode.HEAT, Mode.SCHEDULE: HVACMode.AUTO}
_HVAC_TO_MODE = {
    HVACMode.OFF: Mode.MANUAL,
    HVACMode.HEAT: Mode.MANUAL,
    HVACMode.AUTO: Mode.SCHEDULE,
}
_MODE_TO_PRESET = {Mode.VACATION: PRESET_VACATION, Mode.PAUSE: PRESET_PAUSE}
_PRESET_TO_MODE = {v: k for k, v in _MODE_TO_PRESET.items()}
_PRESETS = [PRESET_NONE, *_PRESET_TO_MODE]


def _base_mode(settings: Settings) -> Mode:
    """The non-overlay mode: current mode, or what vacation/pause returns to."""
    return settings.mode if settings.mode in _MODE_TO_HVAC else settings.return_mode


class ETRVClimate(ETRVEntity, ClimateEntity):
    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_precision = PRECISION_HALVES
    _attr_target_temperature_step = 0.5
    _attr_hvac_modes = list(_HVAC_TO_MODE)  # noqa: RUF012
    _attr_preset_modes = _PRESETS
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.TURN_ON
    )

    def __init__(self, coordinator: ETRVCoordinator) -> None:
        super().__init__(coordinator, "climate")
        self._boundary_unsub: Callable[[], None] | None = None

    # --- schedule boundary tracking ------------------------------------------
    # In SCHEDULE mode the device changes its setpoint on period boundaries.
    # Recompute the displayed setpoint at the next one so it stays truthful
    # between the hourly polls.

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._track_next_boundary()

    async def async_will_remove_from_hass(self) -> None:
        self._cancel_boundary()
        await super().async_will_remove_from_hass()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._track_next_boundary()
        super()._handle_coordinator_update()

    def _cancel_boundary(self) -> None:
        if self._boundary_unsub is not None:
            self._boundary_unsub()
            self._boundary_unsub = None

    def _track_next_boundary(self) -> None:
        self._cancel_boundary()
        schedule = self.coordinator.schedule
        if self.coordinator.settings.mode != Mode.SCHEDULE or schedule is None:
            return
        next_at = schedule.next_transition(self.coordinator.device_wall_clock())
        if next_at is not None:
            self._boundary_unsub = async_track_point_in_utc_time(
                self.hass, self._boundary_reached, next_at.astimezone(UTC)
            )

    @callback
    def _boundary_reached(self, _now: datetime) -> None:
        self._boundary_unsub = None
        self._track_next_boundary()
        self.async_write_ha_state()

    # --- state ---------------------------------------------------------------

    @property
    def current_temperature(self) -> float:
        return self.coordinator.temperature.room

    @property
    def target_temperature(self) -> float:
        # In SCHEDULE mode the manual setpoint from the last poll may be stale.
        if self.coordinator.settings.mode == Mode.SCHEDULE:
            scheduled = self.coordinator.scheduled_setpoint()
            if scheduled is not None:
                return scheduled
        return self.coordinator.temperature.set_point

    @property
    def min_temp(self) -> float:
        return self.coordinator.settings.temperature_min

    @property
    def max_temp(self) -> float:
        return self.coordinator.settings.temperature_max

    @property
    def hvac_mode(self) -> HVACMode:
        s, t = self.coordinator.settings, self.coordinator.temperature
        at_frost = abs(t.set_point - s.frost_protection_temperature) < 0.25
        if s.mode == Mode.MANUAL and s.frost_protection_temperature and at_frost:
            return HVACMode.OFF
        return _MODE_TO_HVAC[_base_mode(s)]

    @property
    def preset_mode(self) -> str:
        return _MODE_TO_PRESET.get(self.coordinator.settings.mode, PRESET_NONE)

    # --- commands ------------------------------------------------------------

    async def async_set_temperature(self, **kwargs: Any) -> None:
        # HA routes hvac_mode through here rather than calling async_set_hvac_mode.
        if (hvac_mode := kwargs.get(ATTR_HVAC_MODE)) is not None:
            await self.async_set_hvac_mode(HVACMode(hvac_mode))
        if (temp := kwargs.get(ATTR_TEMPERATURE)) is not None:
            await self.coordinator.async_write_target_temperature(float(temp))

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        settings = self.coordinator.settings
        mode = _HVAC_TO_MODE[hvac_mode]
        await self.coordinator.async_write_settings(replace(settings, mode=mode, return_mode=mode))
        if hvac_mode == HVACMode.OFF:
            await self.coordinator.async_write_target_temperature(
                settings.frost_protection_temperature
            )

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        settings = self.coordinator.settings
        base = _base_mode(settings)
        if preset_mode == PRESET_NONE:
            new = replace(settings, mode=base)
        else:
            new = replace(settings, mode=_PRESET_TO_MODE[preset_mode], return_mode=base)
        await self.coordinator.async_write_settings(new)


async_setup_entry = platform_setup(lambda c: [ETRVClimate(c)])
