"""Optional control switches; enable individually after model verification."""
from homeassistant.components.switch import SwitchEntity
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .entity import MideaCloudEntity


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        MideaCloudSwitch(coordinator, field)
        for field in ("eco", "turbo", "sleep", "display_on")
    ])


class MideaCloudSwitch(MideaCloudEntity, SwitchEntity):
    # A decoded flag is not evidence that a particular model supports writes.
    _attr_entity_registry_enabled_default = False

    @property
    def is_on(self):
        return self.value

    async def _set(self, enabled):
        if not self.available:
            raise HomeAssistantError("Current switch state unavailable")
        # ECO is documented for cooling, not heating. Turbo support varies.
        if enabled and self.field == "eco" and (
            not self.coordinator.data.power_on or self.coordinator.data.operational_mode != 2
        ):
            raise HomeAssistantError("Enable ECO only while cooling")
        if self.field in ("eco", "turbo", "sleep") and not self.coordinator.data.power_on:
            raise HomeAssistantError("Turn the air conditioner on first")
        await self.coordinator.async_set_switch(self.field, enabled)

    async def async_turn_on(self, **kwargs):
        await self._set(True)

    async def async_turn_off(self, **kwargs):
        await self._set(False)
