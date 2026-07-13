"""The hikvision_axpro integration."""

import asyncio
from asyncio import timeout
import contextlib
from datetime import datetime, timedelta
import logging

import hikaxpro
import xmltodict

from homeassistant.components.alarm_control_panel import (
    SCAN_INTERVAL,
    AlarmControlPanelState,
)
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_CODE_FORMAT,
    CONF_CODE,
    CONF_ENABLED,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    SERVICE_RELOAD,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
import homeassistant.helpers.device_registry as dr
import homeassistant.helpers.entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .bypass_manager import BypassManager
from .bypass_store import BypassStore
from .const import (
    ALLOW_SUBSYSTEMS,
    ARM_MODE_AWAY,
    ARM_MODE_HOME,
    ARM_MODE_VACATION,
    DATA_BYPASS_MANAGER,
    DATA_COORDINATOR,
    DOMAIN,
    ENABLE_DEBUG_OUTPUT,
    ISSUE_BYPASS_UNSUPPORTED,
    USE_CODE_ARMING,
)
from .isapi_bypass import AxProBypassClient
from .model import (
    Arming,
    ExDevStatusResponse,
    JSONResponseStatus,
    OutputConfList,
    OutputStatusFull,
    RelayStatusSearchResponse,
    RelaySwitchConf,
    SubSys,
    SubSystemResponse,
    Zone,
    ZoneConfig,
    ZonesConf,
    ZonesResponse,
)

PLATFORMS: list[Platform] = [
    Platform.ALARM_CONTROL_PANEL,
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.SWITCH,
]
_LOGGER = logging.getLogger(__name__)

# The zone configuration (names, types, panel-side bypass restrictions)
# changes rarely; refresh it hourly instead of on every poll.
ZONE_CONFIG_REFRESH_INTERVAL = timedelta(hours=1)


def _filter_enabled(n: SubSys) -> bool:
    return n.enabled


async def async_setup(hass: HomeAssistant, config: ConfigEntry):
    """Set up the hikvision_axpro integration component."""
    hass.data.setdefault(DOMAIN, {})

    async def _handle_reload(service):
        """Handle reload service call."""
        _LOGGER.info("Service %s.reload called: reloading integration", DOMAIN)

        current_entries = hass.config_entries.async_entries(DOMAIN)

        reload_tasks = [
            hass.config_entries.async_reload(entry.entry_id)
            for entry in current_entries
        ]

        await asyncio.gather(*reload_tasks)

    async def _handle_purge(service):
        """Handle purge of unwanted entitites."""
        _LOGGER.info("Service %s.purge called: destroying old entities", DOMAIN)
        dregistry: dr.DeviceRegistry = dr.async_get(hass)
        eregistry: er.EntityRegistry = er.async_get(hass)

        current_entries = hass.config_entries.async_entries(DOMAIN)
        for config in current_entries:
            devices = dregistry.devices.get_devices_for_config_entry_id(config.entry_id)
            entities: list[er.RegistryEntry] = []
            for device in devices:
                device_ent = eregistry.entities.get_entries_for_device_id(
                    device.id, True
                )
                entities.extend(device_ent)

            invalid_binary_sensors_as_sensor_unique_id_parts = [
                "-magnet-",
                "-magnet-shock-",
                "-magnet-open-",
                "-magnet-tilt-",
                "-tamper-",
                "-bypass-",
                "-armed-",
                "-alarm-",
                "-stayaway-",
                "-isviarepeater-",
                "-battery-low-",
            ]
            for entity in entities:
                if entity.domain == SENSOR_DOMAIN and any(
                    sub_string in entity.unique_id
                    for sub_string in invalid_binary_sensors_as_sensor_unique_id_parts
                ):
                    _LOGGER.info("Service %s.purge: removing entity", entity.entity_id)
                    eregistry.async_remove(entity.entity_id)

    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_RELOAD,
        _handle_reload,
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        "purge",
        _handle_purge,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up hikvision_axpro from a config entry."""
    host = entry.data[CONF_HOST]
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    use_code = entry.data[CONF_ENABLED]
    code_format = entry.data[ATTR_CODE_FORMAT]
    code = entry.data[CONF_CODE]
    use_code_arming = entry.data[USE_CODE_ARMING]
    use_sub_systems = entry.data.get(ALLOW_SUBSYSTEMS, False)
    axpro = hikaxpro.HikAxPro(
        host, username, password, user_level=hikaxpro.USER_LEVEL_ADMIN_OPERATOR
    )
    update_interval: float = entry.data.get(
        CONF_SCAN_INTERVAL, SCAN_INTERVAL.total_seconds()
    )

    if entry.data.get(ENABLE_DEBUG_OUTPUT):
        with contextlib.suppress(Exception):
            axpro.set_logging_level(logging.DEBUG)

    try:
        async with timeout(10):
            mac = await hass.async_add_executor_job(axpro.get_interface_mac_address, 1)
    except (TimeoutError, ConnectionError) as ex:
        raise ConfigEntryNotReady from ex

    coordinator = HikAxProDataUpdateCoordinator(
        hass,
        axpro,
        mac,
        use_code,
        code_format,
        use_code_arming,
        code,
        update_interval,
        use_sub_systems,
    )
    try:
        async with timeout(10):
            await hass.async_add_executor_job(coordinator.init_device)
    except (TimeoutError, ConnectionError) as ex:
        raise ConfigEntryNotReady from ex

    bypass_client = AxProBypassClient(axpro)
    bypass_store = BypassStore(hass, entry.entry_id)
    await bypass_store.async_load()
    bypass_manager = BypassManager(hass, entry, coordinator, bypass_client, bypass_store)
    # A firmware that reports the per-zone bypass state supports the
    # bypass control endpoint; detected from already-fetched data.
    bypass_supported = any(
        zone.bypassed is not None for zone in (coordinator.zones or {}).values()
    )
    bypass_manager.bypass_supported = bypass_supported
    coordinator.bypass_manager = bypass_manager
    bypass_manager.async_sync_bypassable_flags()
    if not bypass_supported:
        _LOGGER.warning(
            "Panel %s does not report zone bypass states; "
            "bypass features are disabled",
            coordinator.device_name,
        )
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"{ISSUE_BYPASS_UNSUPPORTED}_{entry.entry_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_BYPASS_UNSUPPORTED,
            translation_placeholders={"device": coordinator.device_name or ""},
        )

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        DATA_COORDINATOR: coordinator,
        DATA_BYPASS_MANAGER: bypass_manager,
    }

    entry.async_on_unload(entry.add_update_listener(update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        data = hass.data[DOMAIN].pop(entry.entry_id)
        manager: BypassManager | None = data.get(DATA_BYPASS_MANAGER)
        if manager is not None:
            manager.async_unload()

    return unload_ok


async def update_listener(hass: HomeAssistant, config_entry: ConfigEntry):
    """Update listener."""
    await hass.config_entries.async_reload(config_entry.entry_id)


class HikAxProDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching ax pro data."""

    axpro: hikaxpro.HikAxPro
    bypass_manager: BypassManager | None = None
    zone_status: ZonesResponse | None
    zones: dict[int, Zone] | None = None
    device_info: dict | None = None
    device_model: str | None = None
    device_name: str | None = None
    sub_systems: dict[int, SubSys] = {}
    """ Zones aka devices """
    devices: dict[int, ZoneConfig] = {}
    relays: dict[int, RelaySwitchConf] = {}
    relays_status: dict[int, OutputStatusFull] = {}
    use_sub_systems: bool

    def __init__(
        self,
        hass: HomeAssistant,
        axpro: hikaxpro.HikAxPro,
        mac,
        use_code,
        code_format,
        use_code_arming,
        code,
        update_interval: float,
        use_sub_systems=False,
    ) -> None:
        """Initialize global data updater and AXPro API."""
        self.axpro = axpro
        self.state = None
        self.zone_status = None
        self.host = axpro.host
        self.mac = mac
        self.use_code = use_code
        self.code_format = code_format
        self.use_code_arming = use_code_arming
        self.code = code
        self.use_sub_systems = use_sub_systems
        self._last_zone_config_fetch: datetime | None = None
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=update_interval),
        )

    def _get_device_info(self):
        endpoint = self.axpro.build_url(
            f"http://{self.host}" + hikaxpro.consts.Endpoints.SystemDeviceInfo, False
        )
        response = self.axpro.make_request(endpoint, "GET", None, True)

        if response.status_code != 200:
            raise hikaxpro.errors.UnexpectedResponseCodeError(
                response.status_code, response.text
            )
        _LOGGER.debug(response.text)
        return xmltodict.parse(response.text)

    def init_device(self):
        """Init device information."""
        self.device_info = self._get_device_info()
        self.device_name = self.device_info["DeviceInfo"]["deviceName"]
        self.device_model = self.device_info["DeviceInfo"]["model"]
        _LOGGER.debug(self.device_info)
        self.load_devices()
        self.load_relays()
        self._update_data()

    def load_relays(self):
        """Load relays."""
        devices = self._load_relays()
        if devices is not None:
            self.relays = {}
            for item in devices.list:
                self.relays[item.output.id] = item.output

    def _load_relays(self) -> OutputConfList:
        endpoint = self.axpro.build_url(
            f"http://{self.host}" + hikaxpro.consts.Endpoints.OutputConfig, True
        )
        response = self.axpro.make_request(endpoint, "GET", None, True)

        if response.status_code != 200:
            raise hikaxpro.errors.UnexpectedResponseCodeError(
                response.status_code, response.text
            )
        _LOGGER.debug(response.text)
        return OutputConfList.from_dict(response.json())

    def load_ext_devices_status(self):
        """Load status of external devices."""
        statuses = self._load_ext_devices_status()
        if statuses is not None:
            self.relays_status = {}
            if statuses.ex_dev_status is not None:
                for item in statuses.ex_dev_status.output_list:
                    self.relays_status[item.output.id] = item.output

    def _load_ext_devices_status(self) -> ExDevStatusResponse:
        endpoint = self.axpro.build_url(
            f"http://{self.host}" + "/ISAPI/SecurityCP/status/exDevStatus", True
        )
        response = self.axpro.make_request(endpoint, "GET", None, True)

        if response.status_code != 200:
            raise hikaxpro.errors.UnexpectedResponseCodeError(
                response.status_code, response.text
            )
        _LOGGER.debug(response.text)
        return ExDevStatusResponse.from_dict(response.json())

    def load_devices(self):
        """Load devices from Zone Config."""
        devices = self._load_devices()
        self._last_zone_config_fetch = dt_util.utcnow()
        if devices is not None:
            self.devices = {}
            for item in devices.list:
                self.devices[item.zone.id] = item.zone

    def _load_devices(self) -> ZonesConf:
        endpoint = self.axpro.build_url(
            f"http://{self.host}" + hikaxpro.consts.Endpoints.ZonesConfig, True
        )
        response = self.axpro.make_request(endpoint, "GET", None, True)

        if response.status_code != 200:
            raise hikaxpro.errors.UnexpectedResponseCodeError(
                response.status_code, response.text
            )
        _LOGGER.debug(response.text)
        return ZonesConf.from_dict(response.json())

    def _update_relays_status(self) -> RelayStatusSearchResponse:
        endpoint = self.axpro.build_url(
            f"http://{self.host}" + hikaxpro.consts.Endpoints.OutputStatus, True
        )
        response = self.axpro.make_request(
            endpoint,
            "POST",
            {
                "OutputCond": {
                    "searchID": "homeassistant",
                    "searchResultPosition": 1,
                    "maxResults": 50,
                    "moduleType": "localWired",
                }
            },
            True,
        )

        if response.status_code != 200:
            raise hikaxpro.errors.UnexpectedResponseCodeError(
                response.status_code, response.text
            )
        _LOGGER.debug(response.text)
        return RelayStatusSearchResponse.from_dict(response.json())

    def _update_data(self) -> None:
        """Fetch data from axpro via sync functions."""
        status = AlarmControlPanelState.DISARMED
        status_json = self.axpro.subsystem_status()
        try:
            subsys_resp = SubSystemResponse.from_dict(status_json)
            subsys_arr: list[SubSys] = []
            if subsys_resp is not None and subsys_resp.sub_sys_list is not None:
                subsys_arr = []
                for sublist in subsys_resp.sub_sys_list:
                    subsys_arr.append(sublist.sub_sys)

            subsys_arr = list(filter(_filter_enabled, subsys_arr))
            self.sub_systems = {}
            for subsys in subsys_arr:
                self.sub_systems[subsys.id] = subsys
                if self.use_sub_systems and subsys.id != 1:
                    continue
                if subsys.alarm:
                    status = AlarmControlPanelState.TRIGGERED
                elif subsys.arming == Arming.AWAY:
                    status = AlarmControlPanelState.ARMED_AWAY
                elif subsys.arming == Arming.STAY:
                    status = AlarmControlPanelState.ARMED_HOME
                elif subsys.arming == Arming.VACATION:
                    status = AlarmControlPanelState.ARMED_VACATION
            _LOGGER.debug("SubSystem status: %s", subsys_resp)
        except:
            _LOGGER.warning("Error getting status: %s", status_json)
        _LOGGER.debug("Axpro status: %s", status)
        self.state = status

        zone_response = self.axpro.zone_status()
        zone_status = ZonesResponse.from_dict(zone_response)
        self.zone_status = zone_status
        zones = {}
        for zone in zone_status.zone_list:
            zones[zone.zone.id] = zone.zone
        self.zones = zones
        _LOGGER.debug("Zones: %s", zone_response)
        # Refresh the zone configuration hourly, so panel-side setting
        # changes (e.g. "forbid bypass on arming") are picked up without
        # a reload while keeping the poll cycle light. A failed refresh
        # keeps the previous configuration and is retried next poll.
        if (
            self._last_zone_config_fetch is None
            or dt_util.utcnow() - self._last_zone_config_fetch
            >= ZONE_CONFIG_REFRESH_INTERVAL
        ):
            try:
                self.load_devices()
            except Exception as err:  # noqa: BLE001 - keep polling alive
                _LOGGER.warning(
                    "Zone configuration refresh failed; keeping the previous "
                    "one: %s",
                    err,
                )
        # relays
        devices_status = self._load_ext_devices_status()
        relays_status = {}
        if devices_status.ex_dev_status is not None:
            for item in devices_status.ex_dev_status.output_list:
                relays_status[item.output.id] = item.output
        self.relays_status = relays_status
        _LOGGER.debug("Relay status: %s", relays_status)

    async def _async_update_data(self) -> None:
        """Fetch data from Axpro."""
        try:
            async with timeout(10):
                await self.hass.async_add_executor_job(self._update_data)
        except ConnectionError as error:
            raise UpdateFailed(error) from error
        if self.bypass_manager is not None:
            await self.bypass_manager.async_on_data_refreshed()

    async def _async_arm(self, arm_command, sub_id: int | None, mode: str) -> None:
        """Run the pre-arm bypass flow, then send the arm command."""
        manager = self.bypass_manager
        if manager is not None and manager.arm_lock.locked():
            raise HomeAssistantError(
                "Another arming or bypass operation is already in progress",
                translation_domain=DOMAIN,
                translation_key="arming_in_progress",
            )
        if manager is not None:
            async with manager.arm_lock:
                await manager.async_prepare_arming(sub_id, mode)
                is_success = await self.hass.async_add_executor_job(
                    arm_command, sub_id
                )
        else:
            is_success = await self.hass.async_add_executor_job(arm_command, sub_id)

        if not is_success:
            raise HomeAssistantError(
                "The panel refused the arm command",
                translation_domain=DOMAIN,
                translation_key="arm_refused",
            )
        await self._async_update_data()
        await self.async_request_refresh()

    def _arm_vacation(self, sub_id: int | None):
        """Send the vacation arm command (not exposed by hikaxpro)."""
        sid = "0xffffffff" if sub_id is None else str(sub_id)
        endpoint = self.axpro.build_url(
            f"http://{self.host}/ISAPI/SecurityCP/control/arm/{sid}?ways=vacation",
            True,
        )
        response = self.axpro.make_request(endpoint, "PUT", None, True)
        if response.status_code != 200:
            raise hikaxpro.errors.UnexpectedResponseCodeError(
                response.status_code, response.text
            )
        return response.json()

    async def async_arm_home(self, sub_id: int | None = None):
        """Arm alarm panel in home state."""
        await self._async_arm(self.axpro.arm_home, sub_id, ARM_MODE_HOME)

    async def async_arm_away(self, sub_id: int | None = None):
        """Arm alarm panel in away state."""
        await self._async_arm(self.axpro.arm_away, sub_id, ARM_MODE_AWAY)

    async def async_arm_vacation(self, sub_id: int | None = None):
        """Arm alarm panel in vacation state."""
        await self._async_arm(self._arm_vacation, sub_id, ARM_MODE_VACATION)

    async def async_disarm(self, sub_id: int | None = None):
        """Disarm alarm control panel."""
        is_success = await self.hass.async_add_executor_job(self.axpro.disarm, sub_id)

        if not is_success:
            raise HomeAssistantError(
                "The panel refused the disarm command",
                translation_domain=DOMAIN,
                translation_key="disarm_refused",
            )
        if self.bypass_manager is not None:
            await self.bypass_manager.async_on_disarm(sub_id)
        await self._async_update_data()
        await self.async_request_refresh()

    def _relay_call(self, relay_id: int, is_enabled: bool) -> JSONResponseStatus:
        endpoint = self.axpro.build_url(
            f"http://{self.host}"
            + hikaxpro.consts.Endpoints.OutputControl.replace("{}", str(relay_id)),
            True,
        )
        response = self.axpro.make_request(
            endpoint,
            "PUT",
            {"OutputsCtrl": {"switch": "open" if is_enabled else "close"}},
            True,
        )
        if response.status_code != 200:
            raise hikaxpro.errors.UnexpectedResponseCodeError(
                response.status_code, response.text
            )
        _LOGGER.debug(response.text)
        return JSONResponseStatus.from_dict(response.json())

    async def relay_on(self, relay_id: int):
        """Turn on relay by ID."""
        response: JSONResponseStatus = await self.hass.async_add_executor_job(
            self._relay_call, relay_id, True
        )
        return response.status_code == 1

    async def relay_off(self, relay_id: int):
        """Turn off relay by ID."""
        response: JSONResponseStatus = await self.hass.async_add_executor_job(
            self._relay_call, relay_id, False
        )
        return response.status_code == 1
