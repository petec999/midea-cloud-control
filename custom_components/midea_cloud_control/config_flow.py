"""Config flow for Midea Cloud Control."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, ConfigEntry, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict, SelectSelector, SelectSelectorConfig, SelectSelectorMode,
)

from .client import SUPPORTED, CloudError, MideaCloudClient
from .const import CONF_APPLIANCE_ID, CONF_APPLIANCE_NAME, CONF_CLOUD, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema({
    vol.Required(CONF_USERNAME): str,
    vol.Required(CONF_PASSWORD): str,
    vol.Required(CONF_CLOUD, default=SUPPORTED[0]): SelectSelector(
        SelectSelectorConfig(options=list(SUPPORTED), mode=SelectSelectorMode.DROPDOWN)
    ),
})


class MideaCloudConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the configuration flow."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return MideaCloudOptionsFlow()

    def __init__(self) -> None:
        self._credentials: dict[str, Any] = {}
        self._appliances: dict[int, dict[str, Any]] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect credentials and enumerate the account's appliances."""
        errors: dict[str, str] = {}

        if user_input is not None:
            client = MideaCloudClient(
                user_input[CONF_USERNAME], user_input[CONF_PASSWORD],
                user_input[CONF_CLOUD], session=async_get_clientsession(self.hass),
            )
            try:
                await client.connect()
                self._appliances = await client.list_appliances()
            except CloudError:
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error connecting to the cloud")
                errors["base"] = "unknown"
            else:
                if not self._appliances:
                    errors["base"] = "no_appliances"
                else:
                    self._credentials = user_input
                    return await self.async_step_appliance()

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_appliance(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick which appliance to add."""
        if user_input is not None:
            appliance_id = int(user_input[CONF_APPLIANCE_ID])
            await self.async_set_unique_id(str(appliance_id))
            self._abort_if_unique_id_configured()

            name = self._appliances[appliance_id].get("name") or f"Midea {appliance_id}"
            return self.async_create_entry(
                title=name,
                data={**self._credentials,
                      CONF_APPLIANCE_ID: appliance_id,
                      CONF_APPLIANCE_NAME: name},
            )

        options = [
            SelectOptionDict(
                value=str(aid),
                label=f"{info.get('name') or 'Unnamed'} ({aid})",
            )
            for aid, info in self._appliances.items()
        ]
        schema = vol.Schema({
            vol.Required(CONF_APPLIANCE_ID): SelectSelector(
                SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST)
            )
        })
        return self.async_show_form(step_id="appliance", data_schema=schema)


class MideaCloudOptionsFlow(OptionsFlow):
    """Configure polling without changing account credentials."""

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        default = self.config_entry.options.get(
            CONF_SCAN_INTERVAL, int(DEFAULT_SCAN_INTERVAL.total_seconds()))
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_SCAN_INTERVAL, default=default):
                    vol.All(vol.Coerce(int), vol.Range(min=30, max=300)),
            }),
        )
