"""Constants for the Midea Cloud Control integration."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.climate import (
    FAN_AUTO, FAN_HIGH, FAN_LOW, FAN_MEDIUM,
    SWING_BOTH, SWING_HORIZONTAL, SWING_OFF, SWING_VERTICAL, HVACMode,
)

DOMAIN = "midea_cloud_control"
PLATFORMS = ["climate", "sensor", "binary_sensor", "switch"]
CONF_SCAN_INTERVAL = "scan_interval"

CONF_CLOUD = "cloud"
CONF_APPLIANCE_ID = "appliance_id"
CONF_APPLIANCE_NAME = "appliance_name"

DEFAULT_SCAN_INTERVAL = timedelta(seconds=30)

FAN_SILENT = "silent"
FAN_MAX = "max"

# Midea operational mode <-> Home Assistant HVAC mode.
# Power is a separate flag, so HVACMode.OFF is handled outside this map.
MODE_TO_HVAC: dict[int, HVACMode] = {
    1: HVACMode.AUTO,
    2: HVACMode.COOL,
    3: HVACMode.DRY,
    4: HVACMode.HEAT,
    5: HVACMode.FAN_ONLY,
}
HVAC_TO_MODE = {v: k for k, v in MODE_TO_HVAC.items()}

FAN_TO_SPEED: dict[str, int] = {
    FAN_SILENT: 20,
    FAN_LOW: 40,
    FAN_MEDIUM: 60,
    FAN_HIGH: 80,
    FAN_MAX: 100,
    FAN_AUTO: 102,
}
SPEED_TO_FAN = {v: k for k, v in FAN_TO_SPEED.items()}

SWING_TO_MODE: dict[str, int] = {
    SWING_OFF: 0,
    SWING_HORIZONTAL: 3,
    SWING_VERTICAL: 12,
    SWING_BOTH: 15,
}
MODE_TO_SWING = {v: k for k, v in SWING_TO_MODE.items()}

MIN_TEMP = 17.0
MAX_TEMP = 30.0
