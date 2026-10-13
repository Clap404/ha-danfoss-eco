"""Climate entity for Danfoss eTRV.

Maps the device's operating `Mode` to HA HVAC modes:
  - MANUAL   → HEAT       (target_temperature controls the valve)
  - SCHEDULE → AUTO       (device runs its internal weekly schedule)
  - VACATION → preset "vacation" over the return mode's HVAC mode
  - PAUSE    → preset "pause"    over the return mode's HVAC mode

Frost protection is exposed as HVAC OFF (switches the device to MANUAL and
applies the frost_protection_temperature).
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
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
from .etrv.properties import Mode, Settings

PRESET_NONE = "none"
PRESET_VACATION = "vacation"
PRESET_PAUSE = "pause"

_MODE_TO_HVAC = {
    Mode.MANUAL: HVACMode.HEAT,
    Mode.SCHEDULE: HVACMode.AUTO,
}


def _hvac_for_mode(mode: Mode, return_mode: Mode) -> HVACMode:
    """The HVAC mode to show. For vacation/pause, show the underlying mode."""
    if mode in (Mode.VACATION, Mode.PAUSE):
        return _MODE_TO_HVAC.get(return_mode, HVACMode.AUTO)
    return _MODE_TO_HVAC.get(mode, HVACMode.HEAT)


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
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.AUTO]
    _attr_preset_modes = [PRESET_NONE, PRESET_VACATION, PRESET_PAUSE]
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
        # Frost-protection setpoint while in MANUAL → treat as OFF
        t = self.coordinator.data.temperature
        if (
            s.mode == Mode.MANUAL
            and t
            and s.frost_protection_temperature
            and abs(t.set_point - s.frost_protection_temperature) < 0.25
        ):
            return HVACMode.OFF
        return _hvac_for_mode(s.mode, s.return_mode)

    @property
    def preset_mode(self) -> str | None:
        s = self.coordinator.data.settings if self.coordinator.data else None
        if not s:
            return None
        if s.mode == Mode.VACATION:
            return PRESET_VACATION
        if s.mode == Mode.PAUSE:
            return PRESET_PAUSE
        return PRESET_NONE

    # --- commands ------------------------------------------------------------

    @staticmethod
    def _base_mode(settings: Settings) -> Mode:
        """The non-overlay mode (what vacation/pause returns to)."""
        if settings.mode in (Mode.MANUAL, Mode.SCHEDULE):
            return settings.mode
        return settings.return_mode if settings.return_mode in (Mode.MANUAL, Mode.SCHEDULE) else Mode.MANUAL

    async def async_set_temperature(self, **kwargs: Any) -> None:
        # HA passes hvac_mode through to async_set_temperature (it does not
        # call async_set_hvac_mode), so handle it here or set_temperature
        # with only hvac_mode becomes a no-op.
        hvac_mode = kwargs.get(ATTR_HVAC_MODE)
        if hvac_mode is not None:
            await self.async_set_hvac_mode(HVACMode(hvac_mode))
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
            settings.mode = Mode.MANUAL
            settings.return_mode = Mode.MANUAL
            await self.coordinator.async_write_settings(settings)
            await self.coordinator.async_write_target_temperature(
                settings.frost_protection_temperature
            )
            return
        if hvac_mode == HVACMode.HEAT:
            settings.mode = Mode.MANUAL
            settings.return_mode = Mode.MANUAL
        elif hvac_mode == HVACMode.AUTO:
            settings.mode = Mode.SCHEDULE
            settings.return_mode = Mode.SCHEDULE
        else:
            return
        await self.coordinator.async_write_settings(settings)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        data = self.coordinator.data
        if not data or not data.settings:
            return
        settings = data.settings
        if preset_mode == PRESET_NONE:
            settings.mode = self._base_mode(settings)
        elif preset_mode == PRESET_VACATION:
            settings.return_mode = self._base_mode(settings)
            settings.mode = Mode.VACATION
        elif preset_mode == PRESET_PAUSE:
            settings.return_mode = self._base_mode(settings)
            settings.mode = Mode.PAUSE
        else:
            return
        await self.coordinator.async_write_settings(settings)
