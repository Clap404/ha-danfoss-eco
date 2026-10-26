"""Config flow: discover an eTRV, pair with it, store its secret key.

The secret key characteristic is only readable for a few seconds after a
short button press while a client is connecting. So the pair step shows a
prompt, then connects and reads in a loop until success or timeout while the
user presses the button.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from bleak.backends.device import BLEDevice
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.device_registry import format_mac
import voluptuous as vol

from .const import CONF_PIN, CONF_SECRET_KEY, DEFAULT_PIN, DOMAIN
from .etrv import ETRVClient
from .options_flow import DanfossEcoOptionsFlow, valid_pin

_LOGGER = logging.getLogger(__name__)

PAIR_TIMEOUT_S = 30.0
PAIR_POLL_INTERVAL_S = 2.0


# Advertised name is "<status 0-7>;<MAC minus first char>;<user name>" (older firmware: no status)
_ADV_NAME = re.compile(r"^(?:[0-7];)?0:04:2F:[0-9A-F:]+;(?P<name>.*)$", re.IGNORECASE)


def _is_etrv(adv: BluetoothServiceInfoBleak) -> bool:
    return bool(adv.name and _ADV_NAME.match(adv.name))


def _display_name(adv: BluetoothServiceInfoBleak) -> str:
    match = _ADV_NAME.match(adv.name or "")
    name = match["name"].strip() if match else ""
    return f"{name or 'eTRV'} ({adv.address})"


class DanfossEcoConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return DanfossEcoOptionsFlow()

    def __init__(self) -> None:
        self._address = ""
        self._name = ""
        self._discovered: dict[str, str] = {}  # address -> display name

    async def _select(self, address: str, name: str) -> ConfigFlowResult:
        await self.async_set_unique_id(format_mac(address))
        self._abort_if_unique_id_configured()
        self._address = address
        self._name = name or address
        self.context["title_placeholders"] = {"name": self._name}
        return await self.async_step_pair()

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """HA's Bluetooth integration spotted a matching advertisement."""
        if not _is_etrv(discovery_info):
            return self.async_abort(reason="not_supported")
        return await self._select(discovery_info.address, _display_name(discovery_info))

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """User clicked Add Integration: list discovered, unconfigured eTRVs."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            return await self._select(address, self._discovered.get(address, ""))

        configured = self._async_current_ids()
        self._discovered = {
            info.address: _display_name(info)
            for info in bluetooth.async_discovered_service_info(self.hass)
            if _is_etrv(info) and format_mac(info.address) not in configured
        }
        if not self._discovered:
            return self.async_abort(reason="no_devices_found")
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): vol.In(self._discovered)}),
        )

    async def async_step_pair(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask the user to short-press the TRV button, then read the secret key."""
        assert self._address, "pair step needs a selected device"
        errors: dict[str, str] = {}

        if user_input is not None:
            pin = (user_input.get(CONF_PIN) or DEFAULT_PIN).strip()
            ble_device = bluetooth.async_ble_device_from_address(
                self.hass, self._address, connectable=True
            )
            if not valid_pin(pin):
                errors[CONF_PIN] = "invalid_pin"
            elif ble_device is None:
                errors["base"] = "not_connectable"
            elif (paired := await self._try_read_secret(ble_device, pin)) is None:
                errors["base"] = "pairing_failed"
            else:
                secret, device_name = paired
                return self.async_create_entry(
                    title=device_name or self._name,
                    data={
                        CONF_ADDRESS: self._address,
                        CONF_SECRET_KEY: secret.hex(),
                        CONF_PIN: pin,
                    },
                )

        schema = vol.Schema(
            {
                vol.Optional(CONF_PIN, default=DEFAULT_PIN): vol.All(
                    cv.string, vol.Length(min=4, max=4)
                )
            },
            extra=vol.REMOVE_EXTRA,
        )
        return self.async_show_form(
            step_id="pair",
            data_schema=schema,
            description_placeholders={"name": self._name},
            errors=errors,
        )

    async def _try_read_secret(self, ble_device: BLEDevice, pin: str) -> tuple[bytes, str] | None:
        """Connect repeatedly until the secret key is readable, or time out.

        Returns the key and the name stored on the TRV ("" if unreadable).
        """
        deadline = self.hass.loop.time() + PAIR_TIMEOUT_S
        last_exc: Exception | None = None
        while self.hass.loop.time() < deadline:
            client = ETRVClient(ble_device, secret_key=None, pin=pin.encode("ascii"))
            try:
                await client.connect()
                secret = await client.read_secret_key()
                _LOGGER.info("Paired with %s", self._address)
                client.set_secret_key(secret)
                try:
                    name = (await client.read_name()).name.strip()
                except Exception as exc:
                    _LOGGER.debug("name read failed: %s", exc)
                    name = ""
                return secret, name
            except Exception as exc:
                last_exc = exc
                _LOGGER.debug("pair attempt failed: %s", exc)
            finally:
                await client.disconnect()
            await asyncio.sleep(PAIR_POLL_INTERVAL_S)

        _LOGGER.warning("Pairing timed out for %s: %s", self._address, last_exc)
        return None
