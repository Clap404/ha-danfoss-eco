"""Config flow for Danfoss Eco.

Two paths:
  - Bluetooth discovery → user clicks button → we read secret_key
  - Manual entry: user pastes MAC + secret_key hex (already-paired devices)

The button-press handshake is the awkward part: the secret_key characteristic
is only readable while the device LED is solid-on, which happens for a few
seconds after a short button press while a BLE client is connecting. So the
flow is: show the prompt, ask user to short-press the button, then attempt
connect + read in a loop until success or timeout.
"""

from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from typing import Any

import voluptuous as vol
from bleak.backends.device import BLEDevice
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv, selector
from homeassistant.helpers.device_registry import format_mac
from .const import (
    CONF_PIN,
    CONF_SECRET_KEY,
    DEFAULT_PIN,
    DOMAIN,
    SERVICE_UUID,
)
from .etrv.client import ETRVClient
from .etrv.properties import Schedule
from .schedule_text import DAY_TO_INDEX, format_day, parse_day

_LOGGER = logging.getLogger(__name__)

PAIR_TIMEOUT_S = 30.0
PAIR_POLL_INTERVAL_S = 2.0


def _is_etrv(adv: BluetoothServiceInfoBleak) -> bool:
    if adv.name and adv.name.endswith("eTRV"):
        return True
    return SERVICE_UUID.lower() in {u.lower() for u in (adv.service_uuids or [])}


class DanfossEcoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Danfoss Eco."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return DanfossEcoOptionsFlow()

    def __init__(self) -> None:
        self._discovered_address: str | None = None
        self._discovered_name: str | None = None
        self._discovered: dict[str, str] = {}  # address -> display name

    # --- entry points --------------------------------------------------------

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Triggered when HA's BT integration spots a matching advertisement."""
        await self.async_set_unique_id(format_mac(discovery_info.address))
        self._abort_if_unique_id_configured()
        if not _is_etrv(discovery_info):
            return self.async_abort(reason="not_supported")
        self._discovered_address = discovery_info.address
        self._discovered_name = discovery_info.name or discovery_info.address
        self.context["title_placeholders"] = {"name": self._discovered_name}
        return await self.async_step_pair()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """User clicked Add Integration. Show a list of discovered eTRVs."""
        if user_input is not None:
            address: str = user_input[CONF_ADDRESS]
            self._discovered_address = address
            self._discovered_name = self._discovered.get(address, address)
            await self.async_set_unique_id(format_mac(address))
            self._abort_if_unique_id_configured()
            return await self.async_step_pair()

        current_addresses = self._async_current_ids()
        self._discovered.clear()
        for info in bluetooth.async_discovered_service_info(self.hass):
            if format_mac(info.address) in current_addresses:
                continue
            if _is_etrv(info):
                self._discovered[info.address] = info.name or info.address

        if not self._discovered:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(self._discovered),
                }
            ),
        )

    # --- pairing -------------------------------------------------------------

    async def async_step_pair(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Prompt user to short-press the TRV button, then read secret_key."""
        assert self._discovered_address is not None
        errors: dict[str, str] = {}

        if user_input is not None:
            pin = (user_input.get(CONF_PIN) or DEFAULT_PIN).strip()
            if not pin.isdigit() or len(pin) != 4:
                errors[CONF_PIN] = "invalid_pin"
                pin = None  # type: ignore[assignment]
            address = self._discovered_address
            ble_device = bluetooth.async_ble_device_from_address(
                self.hass, address, connectable=True
            )
            if ble_device is None:
                errors["base"] = "not_connectable"
            secret = (
                await self._try_read_secret(ble_device, pin) if pin and ble_device else None
            )
            if secret is not None:
                return self.async_create_entry(
                    title=self._discovered_name or address,
                    data={
                        CONF_ADDRESS: address,
                        CONF_SECRET_KEY: secret.hex(),
                        CONF_PIN: pin,
                    },
                )
            if not errors:
                errors["base"] = "pairing_failed"

        schema = vol.Schema(
            {
                vol.Optional(CONF_PIN, default=DEFAULT_PIN): vol.All(
                    cv.string, vol.Length(min=4, max=4)
                ),
            },
            extra=vol.REMOVE_EXTRA,
        )
        return self.async_show_form(
            step_id="pair",
            data_schema=schema,
            description_placeholders={"name": self._discovered_name or ""},
            errors=errors,
        )

    async def _try_read_secret(self, ble_device: BLEDevice, pin: str = DEFAULT_PIN) -> bytes | None:
        """Connect repeatedly until we can read the plaintext secret_key char.

        The user is expected to short-press the TRV button while this is running.
        """
        deadline = self.hass.loop.time() + PAIR_TIMEOUT_S
        last_exc: Exception | None = None
        pin_bytes = pin.encode("ascii")
        while self.hass.loop.time() < deadline:
            client = ETRVClient(ble_device, secret_key=None, pin=pin_bytes)
            try:
                await client.connect()
                sk = await client.read_secret_key()
                _LOGGER.info("Paired with %s", self._discovered_address)
                return sk.key
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                _LOGGER.debug("pair attempt failed: %s", exc)
            finally:
                await client.disconnect()
            await asyncio.sleep(PAIR_POLL_INTERVAL_S)

        _LOGGER.warning("Pairing timed out for %s: %s", self._discovered_address, last_exc)
        return None


class DanfossEcoOptionsFlow(OptionsFlow):
    """Per-device options: PIN and the weekly heating schedule.

    The schedule is edited here (Settings → Devices & Services → Configure) so
    users never need Developer tools. Each day is a text box of comfort periods
    like ``06:00-08:30, 16:30-22:30`` (see :mod:`.schedule_text`).
    """

    # self.config_entry is provided automatically by Home Assistant.

    def _coordinator(self):
        return self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)

    def _schedule_from_input(self, base: Schedule | None, user_input: dict[str, Any]) -> Schedule:
        schedule = deepcopy(base) if base is not None else Schedule.empty()
        if user_input.get("home_temperature") is not None:
            schedule.home_temperature = float(user_input["home_temperature"])
        if user_input.get("away_temperature") is not None:
            schedule.away_temperature = float(user_input["away_temperature"])
        for day, index in DAY_TO_INDEX.items():
            if day in user_input:
                schedule.days[index] = parse_day(user_input[day])  # raises ValueError on bad text
        return schedule

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        coord = self._coordinator()
        base = coord.data.schedule if (coord and coord.data) else None
        errors: dict[str, str] = {}

        if user_input is not None:
            new_pin = (user_input.get(CONF_PIN) or "").strip()
            if (
                new_pin
                and new_pin.isdigit()
                and len(new_pin) == 4
                and new_pin != self.config_entry.data.get(CONF_PIN)
            ):
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data={**self.config_entry.data, CONF_PIN: new_pin},
                )

            if coord is not None and not errors:
                try:
                    schedule = self._schedule_from_input(base, user_input)
                except ValueError as exc:
                    _LOGGER.warning("invalid schedule input: %s", exc)
                    errors["base"] = "invalid_schedule"
                else:
                    # Only touch the device if the schedule actually changed.
                    if base is None or schedule != base:
                        try:
                            await coord.async_write_schedule(schedule)
                        except Exception as exc:  # noqa: BLE001
                            _LOGGER.error("schedule write failed: %s", exc)
                            errors["base"] = "write_failed"

            if not errors:
                return self.async_create_entry(title="", data={})

        current = base if base is not None else Schedule.empty()
        defaults: dict[str, Any] = {
            CONF_PIN: self.config_entry.data.get(CONF_PIN, DEFAULT_PIN),
            "home_temperature": current.home_temperature,
            "away_temperature": current.away_temperature,
            **{day: format_day(current.days[index]) for day, index in DAY_TO_INDEX.items()},
        }
        if user_input is not None:
            defaults.update(user_input)  # keep what the user just typed on error

        temp_selector = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=4, max=35, step=0.5, unit_of_measurement="°C", mode=selector.NumberSelectorMode.BOX
            )
        )
        fields: dict[Any, Any] = {vol.Optional(CONF_PIN): selector.TextSelector()}
        if coord is not None:
            # Schedule editing needs a live coordinator to write to the device.
            fields[vol.Optional("home_temperature")] = temp_selector
            fields[vol.Optional("away_temperature")] = temp_selector
            for day in DAY_TO_INDEX:
                fields[vol.Optional(day)] = selector.TextSelector()
        schema = vol.Schema(fields)
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(schema, defaults),
            errors=errors,
        )
