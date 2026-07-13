"""Binary Sensors.

Hikvision binary sensors.
"""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import (
    device_registry as dr,
    entity_platform,
    entity_registry as er,
)
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import HikAxProDataUpdateCoordinator
from .const import (
    ARM_MODES,
    DATA_COORDINATOR,
    DOMAIN,
    SERVICE_BYPASS_ZONE,
    SERVICE_UNBYPASS_ZONE,
    SIGNAL_BYPASS_CONFIG_UPDATED,
)
from .hik_device import HikDevice
from .model import DetectorType, Zone, detector_model_to_name

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up a Hikvision ax pro alarm control panel based on a config entry."""

    coordinator: HikAxProDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        DATA_COORDINATOR
    ]
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_BYPASS_ZONE,
        {},
        "async_bypass",
    )
    platform.async_register_entity_service(
        SERVICE_UNBYPASS_ZONE,
        {},
        "async_unbypass",
    )
    devices = []
    await coordinator.async_request_refresh()
    device_registry = dr.async_get(hass)

    if coordinator.zone_status is not None:
        for zone in coordinator.zone_status.zone_list:
            zone_config = coordinator.devices.get(zone.zone.id)
            detector_type: DetectorType | None
            if zone_config is not None:
                _LOGGER.debug("Adding device with zone config: %s", zone)
                _LOGGER.debug("+ config: %s", zone_config)
                detector_type = zone_config.detector_type
                device_registry.async_get_or_create(
                    config_entry_id=entry.entry_id,
                    # connections={},
                    identifiers={
                        (DOMAIN, str(entry.entry_id) + "-" + str(zone_config.id))
                    },
                    manufacturer="HikVision"
                    if zone.zone.model is not None
                    else "Unknown",
                    # suggested_area=zone.zone.,
                    name=zone_config.zone_name,
                    via_device=(DOMAIN, str(coordinator.mac)),
                    model=detector_model_to_name(zone.zone.model)
                    if zone.zone.model is not None
                    else detector_type,
                    sw_version=zone.zone.version,
                )
            else:
                _LOGGER.debug("Zone config empty")
                _LOGGER.debug("Adding device: %s", zone)
                detector_type = zone.zone.detector_type
                device_registry.async_get_or_create(
                    config_entry_id=entry.entry_id,
                    # connections={},
                    identifiers={
                        (DOMAIN, str(entry.entry_id) + "-" + str(zone.zone.id))
                    },
                    manufacturer="HikVision"
                    if zone.zone.model is not None
                    else "Unknown",
                    # suggested_area=zone.zone.,
                    name=zone.zone.name,
                    via_device=(DOMAIN, str(coordinator.mac)),
                    model=detector_model_to_name(zone.zone.model)
                    if zone.zone.model is not None
                    else detector_type,
                    sw_version=zone.zone.version,
                )

            _LOGGER.debug(
                "Compare %s is %s == %s",
                detector_type,
                detector_type is DetectorType.MAGNET_SHOCK_DETECTOR,
                detector_type == DetectorType.MAGNET_SHOCK_DETECTOR,
            )
            # Specific entity
            if (
                detector_type == DetectorType.WIRELESS_EXTERNAL_MAGNET_DETECTOR
                and zone.zone.magnet_open_status is not None
            ):
                devices.append(
                    HikWirelessExtMagnetDetector(coordinator, zone.zone, entry.entry_id)
                )
            if (
                detector_type
                in (
                    DetectorType.DOOR_MAGNETIC_CONTACT_DETECTOR,
                    DetectorType.SLIM_MAGNETIC_CONTACT,
                )
                and zone.zone.magnet_open_status is not None
            ):
                devices.append(
                    HikMagneticContactDetector(coordinator, zone.zone, entry.entry_id)
                )
            if (
                detector_type is DetectorType.MAGNET_SHOCK_DETECTOR
                and zone.zone.magnet_shock_current_status is not None
            ):
                if zone.zone.magnet_shock_current_status.magnet_tilt_status is not None:
                    devices.append(
                        HikMagnetTiltDetector(coordinator, zone.zone, entry.entry_id)
                    )
                if zone.zone.magnet_shock_current_status.magnet_open_status is not None:
                    devices.append(
                        HikMagnetOpenDetector(coordinator, zone.zone, entry.entry_id)
                    )
                if (
                    zone.zone.magnet_shock_current_status.magnet_shock_status
                    is not None
                ):
                    devices.append(
                        HikMagnetShockDetector(coordinator, zone.zone, entry.entry_id)
                    )
            if zone.zone.tamper_evident is not None:
                devices.append(
                    HikTamperDetection(coordinator, zone.zone, entry.entry_id)
                )
            if zone.zone.bypassed is not None:
                devices.append(
                    HikBypassDetection(coordinator, zone.zone, entry.entry_id)
                )
            if zone.zone.armed is not None:
                devices.append(HikArmedInfo(coordinator, zone.zone, entry.entry_id))
            if zone.zone.alarm is not None:
                devices.append(HikAlarmInfo(coordinator, zone.zone, entry.entry_id))
            if zone.zone.stay_away is not None:
                devices.append(HikStayAwayInfo(coordinator, zone.zone, entry.entry_id))
            if zone.zone.is_via_repeater is not None:
                devices.append(
                    HikIsViaRepeaterInfo(coordinator, zone.zone, entry.entry_id)
                )
    # One ready-to-arm sensor per arming mode, always created: they are
    # advisory readiness signals for automations, independent of which
    # modes have auto-bypass enabled.
    devices.extend(
        HikReadyToArmSensor(coordinator, entry.entry_id, mode) for mode in ARM_MODES
    )
    # The mode-less id belonged to the single, away-only sensor of
    # earlier builds and no mode maps to it anymore.
    entity_registry = er.async_get(hass)
    if stale_id := entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, f"{coordinator.mac}-ready-to-arm"
    ):
        entity_registry.async_remove(stale_id)
    _LOGGER.debug("setting up - sensors: %s", ",".join(x.name for x in devices))
    async_add_entities(devices, False)


class HikWirelessExtMagnetDetector(CoordinatorEntity, HikDevice, BinarySensorEntity):
    """Representation of Hikvision external magnet detector."""

    coordinator: HikAxProDataUpdateCoordinator

    def __init__(
        self, coordinator: HikAxProDataUpdateCoordinator, zone: Zone, entry_id: str
    ) -> None:
        """Create the entity with a DataUpdateCoordinator."""
        super().__init__(coordinator)
        self.zone = zone
        self._ref_id = entry_id
        self._attr_unique_id = f"{self.coordinator.mac}-magnet-{zone.id}"
        self._attr_icon = "mdi:magnet"
        self._device_class = BinarySensorDeviceClass.SAFETY
        self._attr_has_entity_name = True

    @property
    def name(self) -> str | None:
        return "Magnet presence"

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if self.coordinator.zones and self.coordinator.zones[self.zone.id]:
            value = self.coordinator.zones[self.zone.id].magnet_open_status
            if value is True:
                self._attr_is_on = value
                self._attr_available = True
                self._attr_icon = "mdi:magnet-on"
            elif value is False:
                self._attr_is_on = value
                self._attr_available = True
                self._attr_icon = "mdi:magnet"
            else:
                self._attr_is_on = None
                self._attr_state = None
                self._attr_available = False
                self._attr_icon = "mdi:help"
        else:
            self._attr_is_on = None
            self._attr_state = None
            self._attr_available = False
            self._attr_icon = "mdi:help"
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        if self.coordinator.zones and self.coordinator.zones[self.zone.id]:
            value = self.coordinator.zones[self.zone.id].magnet_open_status
            if value is True or value is False:
                return value
        return None


class HikMagneticContactDetector(CoordinatorEntity, HikDevice, BinarySensorEntity):
    """Representation of Hikvision external magnet detector."""

    coordinator: HikAxProDataUpdateCoordinator

    def __init__(
        self, coordinator: HikAxProDataUpdateCoordinator, zone: Zone, entry_id: str
    ) -> None:
        """Create the entity with a DataUpdateCoordinator."""
        super().__init__(coordinator)
        self.zone = zone
        self._ref_id = entry_id
        self._attr_unique_id = f"{self.coordinator.mac}-magnet-{zone.id}"
        self._device_class = BinarySensorDeviceClass.SAFETY
        self._attr_has_entity_name = True

    @property
    def name(self) -> str | None:
        return "Magnet presence"

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if self.coordinator.zones and self.coordinator.zones[self.zone.id]:
            value = self.coordinator.zones[self.zone.id].magnet_open_status
            if value is True:
                self._attr_is_on = value
                self._attr_available = True
                self._attr_icon = "mdi:magnet-on"
            elif value is False:
                self._attr_is_on = value
                self._attr_available = True
                self._attr_icon = "mdi:magnet"
            else:
                self._attr_is_on = None
                self._attr_state = None
                self._attr_available = False
                self._attr_icon = "mdi:help"
        else:
            self._attr_is_on = None
            self._attr_state = None
            self._attr_available = False
            self._attr_icon = "mdi:help"
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        if self.coordinator.zones and self.coordinator.zones[self.zone.id]:
            value = self.coordinator.zones[self.zone.id].magnet_open_status
            if value is True or value is False:
                return value
        return None


class HikMagnetShockDetector(CoordinatorEntity, HikDevice, BinarySensorEntity):
    """Representation of Hikvision external magnet detector."""

    coordinator: HikAxProDataUpdateCoordinator

    def __init__(
        self, coordinator: HikAxProDataUpdateCoordinator, zone: Zone, entry_id: str
    ) -> None:
        """Create the entity with a DataUpdateCoordinator."""
        super().__init__(coordinator)
        self.zone = zone
        self._ref_id = entry_id
        self._attr_unique_id = f"{self.coordinator.mac}-magnet-shock-{zone.id}"
        self._device_class = BinarySensorDeviceClass.SAFETY
        self._attr_has_entity_name = True

    @property
    def name(self) -> str | None:
        return "Magnet shock detection"

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if (
            self.coordinator.zones
            and self.coordinator.zones[self.zone.id]
            and self.coordinator.zones[self.zone.id].magnet_shock_current_status
        ):
            value = self.coordinator.zones[
                self.zone.id
            ].magnet_shock_current_status.magnet_shock_status
            if value is True:
                self._attr_is_on = value
                self._attr_available = True
                self._attr_icon = "mdi:magnet-on"
            elif value is False:
                self._attr_is_on = value
                self._attr_available = True
                self._attr_icon = "mdi:magnet"
            else:
                self._attr_is_on = None
                self._attr_available = False
                self._attr_icon = "mdi:help"
        else:
            self._attr_is_on = None
            self._attr_available = False
            self._attr_icon = "mdi:help"
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        if (
            self.coordinator.zones
            and self.coordinator.zones[self.zone.id]
            and self.coordinator.zones[self.zone.id].magnet_shock_current_status
        ):
            return self.coordinator.zones[
                self.zone.id
            ].magnet_shock_current_status.magnet_shock_status
        else:
            return None


class HikMagnetOpenDetector(CoordinatorEntity, HikDevice, BinarySensorEntity):
    """Representation of Hikvision external magnet detector."""

    coordinator: HikAxProDataUpdateCoordinator

    def __init__(
        self, coordinator: HikAxProDataUpdateCoordinator, zone: Zone, entry_id: str
    ) -> None:
        """Create the entity with a DataUpdateCoordinator."""
        super().__init__(coordinator)
        self.zone = zone
        self._ref_id = entry_id
        self._attr_unique_id = f"{self.coordinator.mac}-magnet-open-{zone.id}"
        self._device_class = BinarySensorDeviceClass.SAFETY
        self._attr_has_entity_name = True

    @property
    def name(self) -> str | None:
        return "Magnet open detection"

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if (
            self.coordinator.zones
            and self.coordinator.zones[self.zone.id]
            and self.coordinator.zones[self.zone.id].magnet_shock_current_status
        ):
            value = self.coordinator.zones[
                self.zone.id
            ].magnet_shock_current_status.magnet_open_status
            if value is True:
                self._attr_is_on = value
                self._attr_available = True
                self._attr_icon = "mdi:magnet-on"
            elif value is False:
                self._attr_is_on = value
                self._attr_available = True
                self._attr_icon = "mdi:magnet"
            else:
                self._attr_is_on = None
                self._attr_available = False
                self._attr_icon = "mdi:help"
        else:
            self._attr_is_on = None
            self._attr_available = False
            self._attr_icon = "mdi:help"
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        if (
            self.coordinator.zones
            and self.coordinator.zones[self.zone.id]
            and self.coordinator.zones[self.zone.id].magnet_shock_current_status
        ):
            return self.coordinator.zones[
                self.zone.id
            ].magnet_shock_current_status.magnet_open_status
        else:
            return None


class HikMagnetTiltDetector(CoordinatorEntity, HikDevice, BinarySensorEntity):
    """Representation of Hikvision external magnet detector."""

    coordinator: HikAxProDataUpdateCoordinator

    def __init__(
        self, coordinator: HikAxProDataUpdateCoordinator, zone: Zone, entry_id: str
    ) -> None:
        """Create the entity with a DataUpdateCoordinator."""
        super().__init__(coordinator)
        self.zone = zone
        self._ref_id = entry_id
        self._attr_unique_id = f"{self.coordinator.mac}-magnet-tilt-{zone.id}"
        self._device_class = BinarySensorDeviceClass.SAFETY
        self._attr_has_entity_name = True

    @property
    def name(self) -> str | None:
        return "Magnet tilt detection"

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if (
            self.coordinator.zones
            and self.coordinator.zones[self.zone.id]
            and self.coordinator.zones[self.zone.id].magnet_shock_current_status
        ):
            value = self.coordinator.zones[
                self.zone.id
            ].magnet_shock_current_status.magnet_tilt_status
            if value is True:
                self._attr_is_on = value
                self._attr_available = True
                self._attr_icon = "mdi:magnet-on"
            elif value is False:
                self._attr_is_on = value
                self._attr_available = True
                self._attr_icon = "mdi:magnet"
            else:
                self._attr_is_on = None
                self._attr_available = False
                self._attr_icon = "mdi:help"
        else:
            self._attr_is_on = None
            self._attr_available = False
            self._attr_icon = "mdi:help"
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        if (
            self.coordinator.zones
            and self.coordinator.zones[self.zone.id]
            and self.coordinator.zones[self.zone.id].magnet_shock_current_status
        ):
            return self.coordinator.zones[
                self.zone.id
            ].magnet_shock_current_status.magnet_tilt_status
        else:
            return None


class HikTamperDetection(CoordinatorEntity, HikDevice, BinarySensorEntity):
    """Representation of Hikvision tamper detection."""

    coordinator: HikAxProDataUpdateCoordinator

    def __init__(
        self, coordinator: HikAxProDataUpdateCoordinator, zone: Zone, entry_id: str
    ) -> None:
        """Create the entity with a DataUpdateCoordinator."""
        super().__init__(coordinator)
        self.zone = zone
        self._ref_id = entry_id
        self._attr_unique_id = f"{self.coordinator.mac}-tamper-{zone.id}"
        self._attr_icon = "mdi:electric-switch"
        self._device_class = BinarySensorDeviceClass.TAMPER
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_has_entity_name = True

    @property
    def name(self) -> str | None:
        return "Tamper"

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
        if self.coordinator.zones and self.coordinator.zones[self.zone.id]:
            value = self.coordinator.zones[self.zone.id].tamper_evident
            self._attr_is_on = value
            self._attr_available = True
        else:
            self._attr_state = None
            self._attr_available = False

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        if self.coordinator.zones and self.coordinator.zones[self.zone.id]:
            return self.coordinator.zones[self.zone.id].tamper_evident
        else:
            return False


class HikBypassDetection(CoordinatorEntity, HikDevice, BinarySensorEntity):
    """Representation of Hikvision bypass detection."""

    coordinator: HikAxProDataUpdateCoordinator

    def __init__(
        self, coordinator: HikAxProDataUpdateCoordinator, zone: Zone, entry_id: str
    ) -> None:
        """Create the entity with a DataUpdateCoordinator."""
        super().__init__(coordinator)
        self.zone = zone
        self._ref_id = entry_id
        self._attr_unique_id = f"{self.coordinator.mac}-bypass-{zone.id}"
        self._attr_icon = "mdi:alarm-light-off"
        self._device_class = BinarySensorDeviceClass.SAFETY
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_has_entity_name = True

    @property
    def name(self) -> str | None:
        return "Bypass"

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
        if self.coordinator.zones and self.coordinator.zones[self.zone.id]:
            value = self.coordinator.zones[self.zone.id].bypassed
            self._attr_is_on = value
            self._attr_available = True
        else:
            self._attr_is_on = None
            self._attr_available = False

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        if self.coordinator.zones and self.coordinator.zones[self.zone.id]:
            return self.coordinator.zones[self.zone.id].bypassed
        else:
            return False

    @property
    def extra_state_attributes(self):
        """Expose owner and reason of the current bypass"""
        manager = self.coordinator.bypass_manager
        if manager is None or not self.is_on:
            return None
        owned = manager.owns_zone(self.zone.id)
        return {
            "bypass_owner": "integration" if owned else "external",
            "bypass_reason": manager.bypass_reason(self.zone.id),
        }

    async def async_bypass(self):
        """Bypass this zone (service handler)."""
        if self.coordinator.bypass_manager is not None:
            await self.coordinator.bypass_manager.async_bypass_zone(self.zone.id)

    async def async_unbypass(self):
        """Restore this zone (service handler)."""
        if self.coordinator.bypass_manager is not None:
            await self.coordinator.bypass_manager.async_unbypass_zone(self.zone.id)


class HikReadyToArmSensor(CoordinatorEntity, BinarySensorEntity):
    """Whether arming in one mode would currently succeed.

    On when no zone is in fault or every faulted zone is marked
    bypassable; off when at least one faulted zone would block arming.
    One sensor exists per arming mode, independent of the auto-bypass
    configuration. Advisory: computed from polled data with the same
    evaluation used at arm time; the authoritative check runs
    synchronously when arming.
    """

    coordinator: HikAxProDataUpdateCoordinator

    def __init__(
        self, coordinator: HikAxProDataUpdateCoordinator, entry_id: str, mode: str
    ) -> None:
        """Create the entity with a DataUpdateCoordinator."""
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._mode = mode
        self._attr_unique_id = f"{coordinator.mac}-ready-to-arm-{mode}"
        self._attr_icon = "mdi:shield-check"
        self._attr_has_entity_name = True

    async def async_added_to_hass(self) -> None:
        """Re-evaluate immediately when a bypassable flag is toggled."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_BYPASS_CONFIG_UPDATED, self._config_updated
            )
        )

    @callback
    def _config_updated(self, entry_id: str) -> None:
        if entry_id == self._entry_id:
            self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()

    @property
    def name(self) -> str | None:
        return f"Ready to arm {self._mode}"

    @property
    def device_info(self) -> DeviceInfo:
        """Attach to the panel device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.mac)},
            manufacturer="Hikvision - Ax Pro",
            model=self.coordinator.device_model,
            name=self.coordinator.device_name,
        )

    @property
    def is_on(self) -> bool | None:
        """Return true when arming would succeed under the logic."""
        manager = self.coordinator.bypass_manager
        if manager is None or self.coordinator.zones is None:
            return None
        return manager.current_readiness(self._mode).ready

    @property
    def extra_state_attributes(self):
        """Expose the evaluation details."""
        manager = self.coordinator.bypass_manager
        if manager is None:
            return None
        result = manager.current_readiness(self._mode)
        return {
            "blocking_zones": result.blocking_zones,
            "zones_to_bypass": result.zones_to_bypass,
            "areas": {
                str(area): breakdown for area, breakdown in result.per_area.items()
            },
            "evaluated_mode": self._mode,
            "advisory": True,
        }


class HikArmedInfo(CoordinatorEntity, HikDevice, BinarySensorEntity):
    """Representation of Hikvision armed status."""

    coordinator: HikAxProDataUpdateCoordinator

    def __init__(
        self, coordinator: HikAxProDataUpdateCoordinator, zone: Zone, entry_id: str
    ) -> None:
        """Create the entity with a DataUpdateCoordinator."""
        super().__init__(coordinator)
        self.zone = zone
        self._ref_id = entry_id
        self._attr_unique_id = f"{self.coordinator.mac}-armed-{zone.id}"
        self._attr_icon = "mdi:lock"
        self._device_class = BinarySensorDeviceClass.LOCK
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_has_entity_name = True

    @property
    def name(self) -> str | None:
        return "Armed"

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
        if self.coordinator.zones and self.coordinator.zones[self.zone.id]:
            value = self.coordinator.zones[self.zone.id].armed
            self._attr_is_on = value
            self._attr_available = True
        else:
            self._attr_is_on = None
            self._attr_available = False

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        if self.coordinator.zones and self.coordinator.zones[self.zone.id]:
            return self.coordinator.zones[self.zone.id].armed
        else:
            return False


class HikAlarmInfo(CoordinatorEntity, HikDevice, BinarySensorEntity):
    """Representation of Hikvision alarm status."""

    coordinator: HikAxProDataUpdateCoordinator

    def __init__(
        self, coordinator: HikAxProDataUpdateCoordinator, zone: Zone, entry_id: str
    ) -> None:
        """Create the entity with a DataUpdateCoordinator."""
        super().__init__(coordinator)
        self.zone = zone
        self._ref_id = entry_id
        self._attr_unique_id = f"{self.coordinator.mac}-alarm-{zone.id}"
        self._attr_icon = "mdi:alarm-light"
        self._device_class = BinarySensorDeviceClass.LOCK
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_has_entity_name = True

    @property
    def name(self) -> str | None:
        return "Alarm"

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
        if self.coordinator.zones and self.coordinator.zones[self.zone.id]:
            value = self.coordinator.zones[self.zone.id].alarm
            self._attr_is_on = value
            self._attr_available = True
        else:
            self._attr_is_on = None
            self._attr_available = False

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        if self.coordinator.zones and self.coordinator.zones[self.zone.id]:
            return self.coordinator.zones[self.zone.id].alarm
        else:
            return False


class HikStayAwayInfo(CoordinatorEntity, HikDevice, BinarySensorEntity):
    """Representation of Hikvision Stay away status."""

    coordinator: HikAxProDataUpdateCoordinator

    def __init__(
        self, coordinator: HikAxProDataUpdateCoordinator, zone: Zone, entry_id: str
    ) -> None:
        """Create the entity with a DataUpdateCoordinator."""
        super().__init__(coordinator)
        self.zone = zone
        self._ref_id = entry_id
        self._attr_unique_id = f"{self.coordinator.mac}-stayaway-{zone.id}"
        self._attr_icon = "mdi:shield-lock-outline"
        self._device_class = BinarySensorDeviceClass.LOCK
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_has_entity_name = True

    @property
    def name(self) -> str | None:
        return "Stay away"

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
        if self.coordinator.zones and self.coordinator.zones[self.zone.id]:
            value = self.coordinator.zones[self.zone.id].stay_away
            self._attr_is_on = value
            self._attr_available = True
        else:
            self._attr_is_on = None
            self._attr_available = False

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        if self.coordinator.zones and self.coordinator.zones[self.zone.id]:
            return self.coordinator.zones[self.zone.id].stay_away
        else:
            return False


class HikIsViaRepeaterInfo(CoordinatorEntity, HikDevice, BinarySensorEntity):
    """Representation of Hikvision is via repeater status."""

    coordinator: HikAxProDataUpdateCoordinator

    def __init__(
        self, coordinator: HikAxProDataUpdateCoordinator, zone: Zone, entry_id: str
    ) -> None:
        """Create the entity with a DataUpdateCoordinator."""
        super().__init__(coordinator)
        self.zone = zone
        self._ref_id = entry_id
        self._attr_unique_id = f"{self.coordinator.mac}-isviarepeater-{zone.id}"
        self._attr_icon = "mdi:google-circles-extended"
        self._device_class = BinarySensorDeviceClass.CONNECTIVITY
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_has_entity_name = True

    @property
    def name(self) -> str | None:
        return "Is via repeater"

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
        if self.coordinator.zones and self.coordinator.zones[self.zone.id]:
            value = self.coordinator.zones[self.zone.id].is_via_repeater
            self._attr_is_on = value
            self._attr_available = True
        else:
            self._attr_is_on = None
            self._attr_available = False

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        if self.coordinator.zones and self.coordinator.zones[self.zone.id]:
            return self.coordinator.zones[self.zone.id].is_via_repeater
        else:
            return False


class HikBinaryBatteryInfo(CoordinatorEntity, HikDevice, BinarySensorEntity):
    """Representation of Hikvision binary battery info."""

    coordinator: HikAxProDataUpdateCoordinator

    def __init__(
        self, coordinator: HikAxProDataUpdateCoordinator, zone: Zone, entry_id: str
    ) -> None:
        """Create the entity with a DataUpdateCoordinator."""
        super().__init__(coordinator)
        self.zone = zone
        self._ref_id = entry_id
        self._attr_unique_id = f"{self.coordinator.mac}-battery-low-{zone.id}"
        self._attr_icon = "mdi:battery"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_has_entity_name = True

    @property
    def name(self) -> str | None:
        return "Battery low"

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
        if (
            self.coordinator.zones
            and self.coordinator.zones[self.zone.id]
            and self.coordinator.zones[self.zone.id].charge is not None
        ):
            value = self.coordinator.zones[self.zone.id].charge == "lowPower"
            self._attr_is_on = value
            self._attr_available = True
        else:
            self._attr_is_on = None
            self._attr_available = False

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        if (
            self.coordinator.zones
            and self.coordinator.zones[self.zone.id]
            and self.coordinator.zones[self.zone.id].charge is not None
        ):
            return self.coordinator.zones[self.zone.id].charge == "lowPower"
        else:
            return False
