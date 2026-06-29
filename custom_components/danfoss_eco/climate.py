"""Climate entity for Danfoss eTRV.

Maps the device's `schedule_mode` to HA HVAC modes:
  - MANUAL    → HEAT       (target_temperature controls valve)
  - SCHEDULED → HEAT       (device's internal schedule; not editable from HA)
  - VACATION  → HEAT + preset "vacation"
  - HOLD      → HEAT + preset "hold"

Frost protection is exposed as preset "frost" (selecting it switches the
device to MANUAL and applies the frost_protection_temperature).
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_HALVES, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import ETRVCoordinator
from .entity import ETRVEntity
from .etrv.properties import ScheduleMode

PRESET_NONE = "none"
PRESET_VACATION = "vacation"
PRESET_HOLD = "hold"

_MODE_TO_HVAC = {
    ScheduleMode.MANUAL: HVACMode.HEAT,
    ScheduleMode.SCHEDULED: HVACMode.HEAT,
    ScheduleMode.VACATION: HVACMode.HEAT,
    ScheduleMode.HOLD: HVACMode.HEAT,
}

_MODE_TO_PRESET = {
    ScheduleMode.MANUAL: PRESET_NONE,
    ScheduleMode.SCHEDULED: PRESET_NONE,
    ScheduleMode.VACATION: PRESET_VACATION,
    ScheduleMode.HOLD: PRESET_HOLD,
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ETRVCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ETRVClimate(coordinator)])


class ETRVClimate(ETRVEntity, ClimateEntity):
    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_precision = PRECISION_HALVES
    _attr_target_temperature_step = 0.5
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_preset_modes = [PRESET_NONE, PRESET_VACATION, PRESET_HOLD]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.TURN_ON
    )

    def __init__(self, coordinator: ETRVCoordinator) -> None:
        super().__init__(coordinator, "climate")

    # --- state ---------------------------------------------------------------

    @property
    def current_temperature(self) -> float | None:
        t = self.coordinator.data.temperature if self.coordinator.data else None
        return t.room if t else None

    @property
    def target_temperature(self) -> float | None:
        t = self.coordinator.data.temperature if self.coordinator.data else None
        return t.set_point if t else None

    @property
    def min_temp(self) -> float:
        s = self.coordinator.data.settings if self.coordinator.data else None
        return s.temperature_min if s else 6.0

    @property
    def max_temp(self) -> float:
        s = self.coordinator.data.settings if self.coordinator.data else None
        return s.temperature_max if s else 28.0

    @property
    def hvac_mode(self) -> HVACMode | None:
        s = self.coordinator.data.settings if self.coordinator.data else None
        if not s:
            return None
        # Frost-protection setpoint → treat as OFF
        t = self.coordinator.data.temperature
        if (
            t
            and s.frost_protection_temperature
            and abs(t.set_point - s.frost_protection_temperature) < 0.25
        ):
            if s.schedule_mode == ScheduleMode.MANUAL:
                return HVACMode.OFF
        return _MODE_TO_HVAC.get(s.schedule_mode, HVACMode.HEAT)

    @property
    def preset_mode(self) -> str | None:
        s = self.coordinator.data.settings if self.coordinator.data else None
        if not s:
            return None
        return _MODE_TO_PRESET.get(s.schedule_mode, PRESET_NONE)

    # --- commands ------------------------------------------------------------

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        await self.coordinator.async_write_target_temperature(float(temp))

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        data = self.coordinator.data
        if not data or not data.settings:
            return
        settings = data.settings
        if hvac_mode == HVACMode.OFF:
            # Set MANUAL + frost protection temperature
            settings.schedule_mode = ScheduleMode.MANUAL
            await self.coordinator.async_write_settings(settings)
            await self.coordinator.async_write_target_temperature(
                settings.frost_protection_temperature
            )
            return
        if hvac_mode == HVACMode.HEAT:
            settings.schedule_mode = ScheduleMode.MANUAL
        else:
            return
        await self.coordinator.async_write_settings(settings)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        data = self.coordinator.data
        if not data or not data.settings:
            return
        settings = data.settings
        if preset_mode == PRESET_NONE:
            settings.schedule_mode = ScheduleMode.MANUAL
        elif preset_mode == PRESET_VACATION:
            settings.schedule_mode = ScheduleMode.VACATION
        elif preset_mode == PRESET_HOLD:
            settings.schedule_mode = ScheduleMode.HOLD
        else:
            return
        await self.coordinator.async_write_settings(settings)
