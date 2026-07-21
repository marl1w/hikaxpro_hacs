from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity

from . import HikAxProDataUpdateCoordinator
from .bypass_manager import is_auto_bypass_eligible
from .const import ARM_MODES, DATA_COORDINATOR, DOMAIN
from .hik_device import HikDevice
from .model import RelaySwitchConf, RelayStatusEnum, OutputStatusFull, Zone
from homeassistant.const import STATE_ON, STATE_OFF, STATE_UNKNOWN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up a Hikvision ax pro alarm control panel based on a config entry."""
    coordinator: HikAxProDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    await coordinator.async_request_refresh()
    device_registry = dr.async_get(hass)
    devices = []
    if coordinator.relays is not None:
        for [switch_id, switch] in coordinator.relays.items():
            _LOGGER.debug("Adding switch with config: %s", switch)
            device_registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                # connections={},
                identifiers={(DOMAIN, str(entry.entry_id) + "-relay-" + str(switch_id))},
                manufacturer="HikVision",
                # suggested_area=zone.zone.,
                name=switch.name,
                via_device=(DOMAIN, str(coordinator.mac)),
            )
            devices.append(HikRelaySwitch(coordinator, switch, entry.entry_id))
    entity_registry = er.async_get(hass)
    allowed_modes = set(
        coordinator.bypass_manager.auto_bypass_modes
        if coordinator.bypass_manager is not None
        else ARM_MODES
    )
    if coordinator.zone_status is not None:
        for zone in coordinator.zone_status.zone_list:
            # Only instant zones get the config switch (the only type
            # the panel offers "forbid bypass on arming" for); all
            # other zone types are never bypassed automatically.
            # Switches left over from a zone type change are removed
            # from the registry.
            if is_auto_bypass_eligible(zone.zone):
                devices.extend(
                    HikZoneBypassableSwitch(
                        coordinator, zone.zone, entry.entry_id, mode
                    )
                    for mode in ARM_MODES
                    if mode in allowed_modes
                )
            for mode in ARM_MODES:
                if is_auto_bypass_eligible(zone.zone) and mode in allowed_modes:
                    continue
                if stale_id := entity_registry.async_get_entity_id(
                    "switch",
                    DOMAIN,
                    f"{coordinator.mac}-bypassable-{mode}-{zone.zone.id}",
                ):
                    entity_registry.async_remove(stale_id)
            # Legacy mode-less entity id used by older builds.
            if stale_id := entity_registry.async_get_entity_id(
                "switch",
                DOMAIN,
                f"{coordinator.mac}-bypassable-{zone.zone.id}",
            ):
                entity_registry.async_remove(stale_id)
    _LOGGER.debug("setting up - switches: %s", devices)
    async_add_entities(devices, False)


class HikZoneBypassableSwitch(CoordinatorEntity, HikDevice, SwitchEntity):
    """Per-zone flag: may the arming logic auto-bypass this zone.

    The state is persisted in the integration storage and defaults to
    off. When the panel config forbids bypassing the zone on arming
    ("armNoBypassEnabled"), the switch is off and not editable: the
    panel-side setting always wins.
    """

    coordinator: HikAxProDataUpdateCoordinator

    def __init__(
        self,
        coordinator: HikAxProDataUpdateCoordinator,
        zone: Zone,
        entry_id: str,
        mode: str,
    ) -> None:
        """Create the entity with a DataUpdateCoordinator."""
        super().__init__(coordinator)
        self.zone = zone
        self._ref_id = entry_id
        self._mode = mode
        self._attr_unique_id = f"{coordinator.mac}-bypassable-{mode}-{zone.id}"
        self._attr_icon = "mdi:shield-off-outline"
        self._attr_entity_category = EntityCategory.CONFIG
        self._attr_has_entity_name = True

    @property
    def name(self) -> str | None:
        return f"Bypassable on {self._mode} arming"

    @property
    def _forbidden_by_panel(self) -> bool:
        manager = self.coordinator.bypass_manager
        return manager is not None and manager.zone_forbids_bypass(self.zone.id)

    @property
    def available(self) -> bool:
        manager = self.coordinator.bypass_manager
        return (
            manager is not None
            and manager.bypass_supported
            and not self._forbidden_by_panel
        )

    @property
    def is_on(self) -> bool | None:
        """Return true if the zone may be auto-bypassed."""
        manager = self.coordinator.bypass_manager
        if manager is None:
            return None
        if self._forbidden_by_panel:
            return False
        return manager.is_bypassable(self.zone.id, self._mode)

    @property
    def extra_state_attributes(self):
        """Expose panel restrictions and owner/reason of a current bypass."""
        manager = self.coordinator.bypass_manager
        if manager is None:
            return None
        attrs = {}
        attrs["arming_mode"] = self._mode
        if self._forbidden_by_panel:
            attrs["forbidden_by_panel_config"] = True
        if manager.owns_zone(self.zone.id):
            attrs["bypass_owner"] = "integration"
            attrs["bypass_reason"] = manager.bypass_reason(self.zone.id)
        return attrs or None

    async def async_turn_on(self, **kwargs):
        """Mark the zone as bypassable on arming."""
        if self.coordinator.bypass_manager is not None:
            await self.coordinator.bypass_manager.async_set_bypassable(
                self.zone.id, True, self._mode
            )
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        """Mark the zone as not bypassable on arming."""
        if self.coordinator.bypass_manager is not None:
            await self.coordinator.bypass_manager.async_set_bypassable(
                self.zone.id, False, self._mode
            )
            self.async_write_ha_state()



class HikRelaySwitch(CoordinatorEntity, SwitchEntity):
    """Representation of Hikvision external magnet detector."""
    coordinator: HikAxProDataUpdateCoordinator

    def __init__(self, coordinator: HikAxProDataUpdateCoordinator, switch: RelaySwitchConf, entry_id: str) -> None:
        """Create the entity with a DataUpdateCoordinator."""
        super().__init__(coordinator)
        self.switch = switch
        self._ref_id = entry_id
        self._attr_unique_id = f"{self.coordinator.mac}-relay-{switch.id}"
        #self._attr_icon = "mdi:switch"
        self._attr_has_entity_name = True
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(self._ref_id) +  "-relay-" + str(switch.id))},
            manufacturer="HikVision",
            # suggested_area=zone.zone.,
            name=switch.name,
            via_device=(DOMAIN, str(coordinator.mac)),
        )

        #entity_description = SwitchEntityDescription(
        #    device_class=SwitchDeviceClass.SWITCH
        #)
        _attr_device_class: SwitchDeviceClass.SWITCH

        status = self.coordinator.relays_status.get(switch.id)
        self._available = status is not None
        if status is not None:
            self._attr_is_on = status.status == RelayStatusEnum.ON

    @property
    def name(self) -> str | None:
        """Main feature entity: use the relay device name only."""
        return None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        status = self.coordinator.relays_status.get(self.switch.id)
        self._available = status is not None
        if status is not None:
            self._attr_is_on = status.status == RelayStatusEnum.ON
        else:
            self._attr_is_on = None
        self.async_write_ha_state()

    async def async_turn_on(self):
        """Turn the entity on."""
        _LOGGER.debug(
            "Sending ON request to SWITCH device %s (%s)",
        )
        try:
            res = await self.coordinator.relay_on(self.switch.id)
            if res:
                self._attr_is_on = True
                self._available = True
                self.async_write_ha_state()
            else:
                self._available = False
                _LOGGER.exception(
                    "Error turn on for switch %s", self.entity_id
                )
        except:
            self._available = False
            _LOGGER.exception(
                "Error turn on for switch %s", self.entity_id
            )

    async def async_turn_off(self):
        """Turn the entity on."""
        _LOGGER.debug(
            "Sending OFF request to SWITCH device %s (%s)",
        )
        try:
            res = await self.coordinator.relay_off(self.switch.id)
            if res:
                self._attr_is_on = False
                self._available = True
                self.async_write_ha_state()
            else:
                self._available = False
                _LOGGER.exception(
                    "Error turn on for switch %s", self.entity_id
                )
        except:
            self._available = False
            _LOGGER.exception(
                "Error turn on for switch %s", self.entity_id
            )
