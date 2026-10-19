"""Switches backed by Settings config bits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory

from .entity import ETRVDescribedEntity, ETRVDescription, config_bit, platform_setup
from .etrv import ConfigBit


@dataclass(frozen=True, kw_only=True)
class ETRVSwitchDescription(SwitchEntityDescription, ETRVDescription[bool]):
    bit: ConfigBit


def _switch(key: str, bit: ConfigBit) -> ETRVSwitchDescription:
    return ETRVSwitchDescription(
        key=key,
        translation_key=key,
        entity_category=EntityCategory.CONFIG,
        bit=bit,
        value_fn=config_bit(bit),
    )


SWITCHES = (
    _switch("adaptable_regulation", ConfigBit.ADAPTABLE_REGULATION),
    _switch("child_lock", ConfigBit.CHILD_LOCK),
    _switch("display_flip", ConfigBit.DISPLAY_FLIP),
    _switch("slow_regulation", ConfigBit.SLOW_REGULATION),
    _switch("vertical_installation", ConfigBit.VERTICAL_INSTALLATION),
)


class ETRVSwitch(ETRVDescribedEntity[bool], SwitchEntity):
    entity_description: ETRVSwitchDescription

    @property
    def is_on(self) -> bool:
        return self._value()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._write(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write(False)

    async def _write(self, value: bool) -> None:
        settings = self.coordinator.settings.with_bit(self.entity_description.bit, value)
        await self.coordinator.async_write_settings(settings)


async_setup_entry = platform_setup(lambda c: (ETRVSwitch(c, d) for d in SWITCHES))
