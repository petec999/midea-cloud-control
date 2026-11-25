"""Read-only sensor entities from the ordinary state response."""
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfTemperature, EntityCategory

from .const import DOMAIN
from .entity import MideaCloudEntity


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        MideaTemperature(coordinator, "indoor_temperature"),
        MideaTemperature(coordinator, "outdoor_temperature"),
        MideaErrorCode(coordinator, "error_code"),
    ])


class MideaTemperature(MideaCloudEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        return self.value


class MideaErrorCode(MideaCloudEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        return self.value
