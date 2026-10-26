"""Config flow: discover an eTRV, pair with it, store its secret key.

The secret key is only readable while the TRV is in setup mode, entered by a
short button press and advertised in its local name. Like the official app,
the pair step waits for that advert, then connects and reads the key once.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import logging
import re
from typing import Any

from bleak.backends.device import BLEDevice
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothCallbackMatcher,
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
)
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.device_registry import format_mac
import voluptuous as vol

from .const import CONF_PIN, CONF_SECRET_KEY, DEFAULT_PIN, DOMAIN
from .coordinator import BLE_LOCK_KEY
from .etrv import ETRVClient, WrongPinError
from .options_flow import DanfossEcoOptionsFlow, valid_pin

_LOGGER = logging.getLogger(__name__)

SETUP_MODE_TIMEOUT_S = 60


# Advertised name is "<status 0-7>;<MAC minus first char>;<user name>" (older firmware: no status)
_ADV_NAME = re.compile(r"^(?:(?P<status>[0-7]);)?0:04:2F:[0-9A-F:]+;(?P<name>.*)$", re.IGNORECASE)
_STATUS_PIN_SET = 0b001
_STATUS_SETUP_MODE = 0b100


def _is_etrv(adv: BluetoothServiceInfoBleak) -> bool:
    return bool(adv.name and _ADV_NAME.match(adv.name))


def _display_name(adv: BluetoothServiceInfoBleak) -> str:
    match = _ADV_NAME.match(adv.name or "")
    name = match["name"].strip() if match else ""
    return f"{name or 'eTRV'} ({adv.address})"


def _status(adv: BluetoothServiceInfoBleak) -> int | None:
    match = _ADV_NAME.match(adv.name or "")
    return None if match is None or match["status"] is None else int(match["status"])


def _pin_set(adv: BluetoothServiceInfoBleak) -> bool:
    """Whether the TRV advertises an enabled PIN; assume so when it does not say."""
    status = _status(adv)
    return status is None or bool(status & _STATUS_PIN_SET)


def _in_setup_mode(adv: BluetoothServiceInfoBleak) -> bool:
    status = _status(adv)
    return status is not None and bool(status & _STATUS_SETUP_MODE)


def _pin_schema() -> vol.Schema:
    return vol.Schema(
        {vol.Required(CONF_PIN): vol.All(cv.string, vol.Length(min=4, max=4))},
        extra=vol.REMOVE_EXTRA,
    )


async def _read_secret_once(ble_device: BLEDevice, pin: str) -> tuple[bytes, str]:
    client = ETRVClient(ble_device, secret_key=None, pin=pin.encode("ascii"))
    await client.connect()
    try:
        secret = await client.read_secret_key()
        client.set_secret_key(secret)
        try:
            name = (await client.read_name()).name.strip()
        except Exception as exc:
            _LOGGER.debug("name read failed: %s", exc)
            name = ""
        return secret, name
    finally:
        await client.disconnect()


class DanfossEcoConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return DanfossEcoOptionsFlow()

    def __init__(self) -> None:
        self._address = ""
        self._name = ""
        self._ask_pin = True
        self._discovered: dict[str, BluetoothServiceInfoBleak] = {}

    async def _select(self, info: BluetoothServiceInfoBleak) -> ConfigFlowResult:
        await self.async_set_unique_id(format_mac(info.address))
        self._abort_if_unique_id_configured()
        self._address = info.address
        self._name = _display_name(info)
        self._ask_pin = _pin_set(info)
        self.context["title_placeholders"] = {"name": self._name}
        return await self.async_step_pair()

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """HA's Bluetooth integration spotted a matching advertisement."""
        if not _is_etrv(discovery_info):
            return self.async_abort(reason="not_supported")
        return await self._select(discovery_info)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """User clicked Add Integration: list discovered, unconfigured eTRVs."""
        if user_input is not None:
            return await self._select(self._discovered[user_input[CONF_ADDRESS]])

        configured = self._async_current_ids()
        self._discovered = {
            info.address: info
            for info in bluetooth.async_discovered_service_info(self.hass)
            if _is_etrv(info) and format_mac(info.address) not in configured
        }
        if not self._discovered:
            return self.async_abort(reason="no_devices_found")
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {address: _display_name(info) for address, info in self._discovered.items()}
                    )
                }
            ),
        )

    async def async_step_pair(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask the user to short-press the TRV button, then read the secret key."""
        assert self._address, "pair step needs a selected device"
        errors: dict[str, str] = {}

        if user_input is not None:
            pin = (user_input.get(CONF_PIN) or DEFAULT_PIN).strip()
            paired: tuple[bytes, str] | None = None
            if not valid_pin(pin):
                errors[CONF_PIN] = "invalid_pin"
            else:
                try:
                    paired = await self._try_read_secret(self._address, pin)
                except WrongPinError:
                    # The advert can lag behind a PIN just set in the app.
                    self._ask_pin = True
                    errors[CONF_PIN] = "wrong_pin"
                else:
                    if paired is None:
                        errors["base"] = "pairing_failed"
            if paired is not None:
                secret, device_name = paired
                return self.async_create_entry(
                    title=f"{device_name} ({self._address})" if device_name else self._name,
                    data={
                        CONF_ADDRESS: self._address,
                        CONF_SECRET_KEY: secret.hex(),
                        CONF_PIN: pin,
                    },
                )

        return self.async_show_form(
            step_id="pair",
            data_schema=_pin_schema() if self._ask_pin else vol.Schema({}),
            description_placeholders={"name": self._name},
            errors=errors,
        )

    async def _try_read_secret(self, address: str, pin: str) -> tuple[bytes, str] | None:
        """Wait for the TRV to advertise setup mode, then read the secret key.

        Returns the key and the name stored on the TRV ("" if unreadable).
        """
        try:
            info = await bluetooth.async_process_advertisements(
                self.hass,
                _in_setup_mode,
                BluetoothCallbackMatcher(address=address, connectable=True),
                BluetoothScanningMode.ACTIVE,
                SETUP_MODE_TIMEOUT_S,
            )
        except TimeoutError:
            _LOGGER.warning("%s never advertised setup mode", address)
            return None
        try:
            return await _read_secret_once(info.device, pin)
        except WrongPinError:
            raise
        except Exception as exc:
            _LOGGER.warning("Reading the secret key of %s failed: %s", address, exc)
            return None

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The TRV rejected the stored PIN."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            pin = user_input[CONF_PIN].strip()
            if not valid_pin(pin):
                errors[CONF_PIN] = "invalid_pin"
            elif (error := await self._check_pin(entry, pin)) is not None:
                errors[CONF_PIN if error == "wrong_pin" else "base"] = error
            else:
                return self.async_update_reload_and_abort(entry, data_updates={CONF_PIN: pin})

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_pin_schema(),
            description_placeholders={"name": entry.title},
            errors=errors,
        )

    async def _check_pin(self, entry: ConfigEntry, pin: str) -> str | None:
        """Connect with `pin`. Returns a form error, None if the TRV accepted it."""
        address: str = entry.data[CONF_ADDRESS]
        ble_device = bluetooth.async_ble_device_from_address(self.hass, address, connectable=True)
        if ble_device is None:
            return "not_connectable"
        client = ETRVClient(
            ble_device,
            secret_key=bytes.fromhex(entry.data[CONF_SECRET_KEY]),
            pin=pin.encode("ascii"),
        )
        lock: asyncio.Lock = self.hass.data.setdefault(BLE_LOCK_KEY, asyncio.Lock())
        async with lock:
            try:
                await client.connect()
            except WrongPinError:
                return "wrong_pin"
            except Exception as exc:
                _LOGGER.warning("PIN check on %s failed: %s", address, exc)
                return "connect_failed"
            finally:
                await client.disconnect()
        return None
