"""Shared entities for cloud telemetry and optional controls."""
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MideaCloudCoordinator


class MideaCloudEntity(CoordinatorEntity[MideaCloudCoordinator]):
    """Use the same device registry entry as the existing climate entity."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, field):
        super().__init__(coordinator)
        self.field = field
        self._attr_unique_id = f"{coordinator.appliance_id}_{field}"
        self._attr_translation_key = field
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(coordinator.appliance_id))},
            name=coordinator.appliance_name,
            manufacturer="Midea",
            model="Air conditioner (cloud control)",
        )

    @property
    def value(self):
        return getattr(self.coordinator.data, self.field, None)

    @property
    def available(self):
        return super().available and self.value is not None
