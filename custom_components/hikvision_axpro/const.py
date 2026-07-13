"""Constants for the hikvision_axpro integration."""

from typing import Final

DOMAIN: Final[str] = "hikvision_axpro"

DATA_COORDINATOR: Final[str] = "hikaxpro"

DATA_BYPASS_MANAGER: Final[str] = "bypass_manager"

USE_CODE_ARMING: Final[str] = "use_code_arming"

ALLOW_SUBSYSTEMS: Final[str] = "allow_subsystems"

ENABLE_DEBUG_OUTPUT: Final[str] = "debug"

# Zone bypass feature
CONF_AUTO_BYPASS_MODES: Final[str] = "auto_bypass_modes"
CONF_BYPASS_REENABLE_DEBOUNCE: Final[str] = "bypass_reenable_debounce"
CONF_CLEAR_ALL_ON_DISARM: Final[str] = "clear_all_bypasses_on_disarm"

DEFAULT_BYPASS_REENABLE_DEBOUNCE: Final[int] = 10

# Arming modes, aligned with the Home Assistant arm actions
ARM_MODE_HOME: Final[str] = "home"
ARM_MODE_AWAY: Final[str] = "away"
ARM_MODE_VACATION: Final[str] = "vacation"
ARM_MODES: Final[list[str]] = [ARM_MODE_HOME, ARM_MODE_AWAY, ARM_MODE_VACATION]

# Services
SERVICE_BYPASS_ZONE: Final[str] = "bypass_zone"
SERVICE_UNBYPASS_ZONE: Final[str] = "unbypass_zone"
SERVICE_CLEAR_ALL_BYPASSES: Final[str] = "clear_all_bypasses"

# Events
EVENT_BYPASS_APPLIED: Final[str] = f"{DOMAIN}_bypass_applied"
EVENT_BYPASS_REMOVED: Final[str] = f"{DOMAIN}_bypass_removed"
EVENT_ARMING_BLOCKED: Final[str] = f"{DOMAIN}_arming_blocked"

# Dispatcher signal fired when the per-zone bypassable configuration changes
SIGNAL_BYPASS_CONFIG_UPDATED: Final[str] = f"{DOMAIN}_bypass_config_updated"

# Repair issue id for firmware without bypass support
ISSUE_BYPASS_UNSUPPORTED: Final[str] = "bypass_unsupported"


# Sensor entity description constants
ENTITY_DESC_KEY_BATTERY: Final[str] = "battery"
ENTITY_DESC_KEY_MAGNET_PRESENCE: Final[str] = "magnet_presence"
ENTITY_DESC_KEY_MAGNET_SHOCK: Final[str] = "magnet_shock"
ENTITY_DESC_KEY_MAGNET_TILT: Final[str] = "magnet_tilt"
ENTITY_DESC_KEY_SIGNAL_STRENGTH: Final[str] = "signal_strength"
ENTITY_DESC_KEY_HUMIDITY: Final[str] = "humidity"
ENTITY_DESC_KEY_TEMPERATURE: Final[str] = "temperature"
