"""Read-only filter and diagnostic state flags."""
from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.const import EntityCategory

from .const import DOMAIN
from .entity import MideaCloudEntity


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        MideaFilterAlert(coordinator, "filter_alert"),
    ])


class MideaFilterAlert(MideaCloudEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self):
        return self.value
