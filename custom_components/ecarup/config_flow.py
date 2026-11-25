"""Config flow for eCarUp."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ECarUpApi, ECarUpApiError, ECarUpAuthError
from .const import CONF_CLIENT_ID, CONF_CLIENT_SECRET, DOMAIN


async def _validate_input(hass: HomeAssistant, data: dict[str, str]) -> None:
    api = ECarUpApi(
        async_get_clientsession(hass), data[CONF_CLIENT_ID], data[CONF_CLIENT_SECRET]
    )
    await api.async_validate()


class ECarUpConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for eCarUp."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await _validate_input(
                    self.hass,
                    {
                        CONF_CLIENT_ID: user_input[CONF_CLIENT_ID].strip(),
                        CONF_CLIENT_SECRET: user_input[CONF_CLIENT_SECRET],
                    },
                )
            except ECarUpAuthError:
                errors["base"] = "invalid_auth"
            except ECarUpApiError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(user_input[CONF_CLIENT_ID].strip())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_NAME].strip(),
                    data={
                        CONF_NAME: user_input[CONF_NAME].strip(),
                        CONF_CLIENT_ID: user_input[CONF_CLIENT_ID].strip(),
                        CONF_CLIENT_SECRET: user_input[CONF_CLIENT_SECRET],
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default="eCarUp"): str,
                vol.Required(CONF_CLIENT_ID): str,
                vol.Required(CONF_CLIENT_SECRET): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start reauthentication when stored credentials stop working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate replacement credentials and update the existing entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            entry: ConfigEntry = self._get_reauth_entry()
            client_id = user_input[CONF_CLIENT_ID].strip()
            try:
                await _validate_input(
                    self.hass,
                    {
                        CONF_CLIENT_ID: client_id,
                        CONF_CLIENT_SECRET: user_input[CONF_CLIENT_SECRET],
                    },
                )
            except ECarUpAuthError:
                errors["base"] = "invalid_auth"
            except ECarUpApiError:
                errors["base"] = "cannot_connect"
            else:
                return await self._async_finish_reauth(entry, user_input)

        schema = vol.Schema(
            {
                vol.Required(CONF_CLIENT_ID): str,
                vol.Required(CONF_CLIENT_SECRET): str,
            }
        )
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=schema, errors=errors
        )

    async def _async_finish_reauth(
        self, entry: ConfigEntry, user_input: dict[str, Any]
    ) -> ConfigFlowResult:
        """Update entry credentials after validating them."""
        client_id = user_input[CONF_CLIENT_ID].strip()
        await self.async_set_unique_id(client_id)
        self._abort_if_unique_id_mismatch(reason="wrong_account")
        return self.async_update_reload_and_abort(
            entry,
            data_updates={
                CONF_CLIENT_ID: client_id,
                CONF_CLIENT_SECRET: user_input[CONF_CLIENT_SECRET],
            },
        )
