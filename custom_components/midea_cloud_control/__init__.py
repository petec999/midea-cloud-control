"""Midea Cloud Control integration.

Controls Midea-protocol appliances via the vendor cloud's transparent-send
endpoint, for devices that cannot be controlled locally because Midea retired
the token service that local control depends on.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import CloudError, MideaCloudClient
from .const import CONF_APPLIANCE_ID, CONF_APPLIANCE_NAME, CONF_CLOUD, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN, PLATFORMS
from .coordinator import MideaCloudCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a config entry."""
    client = MideaCloudClient(
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        entry.data[CONF_CLOUD],
        session=async_get_clientsession(hass),
    )

    try:
        await client.connect()
    except CloudError as err:
        raise ConfigEntryNotReady(f"Could not log in to {entry.data[CONF_CLOUD]}: {err}") from err

    coordinator = MideaCloudCoordinator(
        hass, client,
        int(entry.data[CONF_APPLIANCE_ID]),
        entry.data[CONF_APPLIANCE_NAME],
        timedelta(seconds=entry.options.get(CONF_SCAN_INTERVAL, int(DEFAULT_SCAN_INTERVAL.total_seconds()))),
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
