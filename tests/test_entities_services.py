"""Tests for services, bypassable switch and ready sensor."""

from __future__ import annotations

from datetime import timedelta

import pytest

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.hikvision_axpro.const import (
    CONF_AUTO_BYPASS_MODES,
    DOMAIN,
    SERVICE_BYPASS_ZONE,
    SERVICE_CLEAR_ALL_BYPASSES,
    SERVICE_UNBYPASS_ZONE,
)

from .conftest import get_coordinator, get_manager, setup_entry

pytestmark = pytest.mark.usefixtures("auto_enable_custom_integrations")


def entity_id_of(hass, domain, unique_id) -> str:
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(domain, DOMAIN, unique_id)
    assert entity_id, f"entity {unique_id} not found"
    return entity_id


# ---------------------------------------------------------------------------
# Services


async def test_bypass_and_unbypass_services(hass, panel):
    """Bypass/unbypass a zone by targeting its bypass sensor."""
    entry = await setup_entry(hass)
    manager = get_manager(hass, entry)
    bypass_sensor = entity_id_of(
        hass, "binary_sensor", "00:11:22:33:44:55-bypass-1"
    )

    await hass.services.async_call(
        DOMAIN, SERVICE_BYPASS_ZONE, {"entity_id": bypass_sensor}, blocking=True
    )
    await hass.async_block_till_done()
    assert panel.zones[1]["bypassed"] is True
    # User decision #5: service bypasses are owned by the integration.
    assert manager.owns_zone(1)
    assert manager.bypass_reason(1) == "service"

    await hass.services.async_call(
        DOMAIN, SERVICE_UNBYPASS_ZONE, {"entity_id": bypass_sensor}, blocking=True
    )
    await hass.async_block_till_done()
    assert panel.zones[1]["bypassed"] is False
    assert not manager.owns_zone(1)


async def test_bypass_service_failure_raises(hass, panel):
    """A failed service bypass surfaces an explicit error."""
    await setup_entry(hass)
    panel.fail_bypass_zones.add(1)
    bypass_sensor = entity_id_of(
        hass, "binary_sensor", "00:11:22:33:44:55-bypass-1"
    )

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN, SERVICE_BYPASS_ZONE, {"entity_id": bypass_sensor}, blocking=True
        )


async def test_clear_all_bypasses_service(hass, panel):
    """Clear all removes every bypass regardless of owner."""
    entry = await setup_entry(hass)
    panel.zones[1]["bypassed"] = True  # external
    panel.zones[2]["bypassed"] = True  # external
    panel_entity = entity_id_of(hass, "alarm_control_panel", "00:11:22:33:44:55")

    await hass.services.async_call(
        DOMAIN,
        SERVICE_CLEAR_ALL_BYPASSES,
        {"entity_id": panel_entity},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert panel.zones[1]["bypassed"] is False
    assert panel.zones[2]["bypassed"] is False


# ---------------------------------------------------------------------------
# Bypassable switch


async def test_bypassable_switch_defaults_off_and_toggles(hass, panel):
    entry = await setup_entry(hass)
    manager = get_manager(hass, entry)
    switch = entity_id_of(
        hass, "switch", "00:11:22:33:44:55-bypassable-away-1"
    )

    assert hass.states.get(switch).state == "off"

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": switch}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(switch).state == "on"
    assert manager.is_bypassable(1, "away")

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": switch}, blocking=True
    )
    await hass.async_block_till_done()
    assert not manager.is_bypassable(1, "away")


async def test_bypassable_switches_are_independent_per_mode(hass, panel):
    entry = await setup_entry(hass)
    manager = get_manager(hass, entry)
    away = entity_id_of(hass, "switch", "00:11:22:33:44:55-bypassable-away-1")
    home = entity_id_of(hass, "switch", "00:11:22:33:44:55-bypassable-home-1")
    vacation = entity_id_of(
        hass, "switch", "00:11:22:33:44:55-bypassable-vacation-1"
    )

    assert hass.states.get(away).state == "off"
    assert hass.states.get(home).state == "off"
    assert hass.states.get(vacation).state == "off"

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": home}, blocking=True
    )
    await hass.async_block_till_done()

    assert manager.is_bypassable(1, "home")
    assert not manager.is_bypassable(1, "away")
    assert not manager.is_bypassable(1, "vacation")


async def test_bypassable_switches_created_only_for_allowed_modes(hass, panel):
    """Disallowed modes do not expose a per-zone bypassable switch."""
    await setup_entry(hass, **{CONF_AUTO_BYPASS_MODES: ["away"]})
    registry = er.async_get(hass)

    assert registry.async_get_entity_id(
        "switch", DOMAIN, "00:11:22:33:44:55-bypassable-away-1"
    )
    assert not registry.async_get_entity_id(
        "switch", DOMAIN, "00:11:22:33:44:55-bypassable-home-1"
    )
    assert not registry.async_get_entity_id(
        "switch", DOMAIN, "00:11:22:33:44:55-bypassable-vacation-1"
    )


async def test_bypassable_flag_persists_across_reload(hass, panel):
    """The flag survives a restart (storage-backed)."""
    entry = await setup_entry(hass)
    manager = get_manager(hass, entry)
    await manager.async_set_bypassable(1, True, "home")
    # Flush the delayed save.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
    await hass.async_block_till_done()

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    reloaded = get_manager(hass, entry)
    assert reloaded.is_bypassable(1, "home")
    assert not reloaded.is_bypassable(1, "away")
    switch = entity_id_of(hass, "switch", "00:11:22:33:44:55-bypassable-home-1")
    assert hass.states.get(switch).state == "on"


async def test_legacy_bypass_storage_migrates_on_startup(hass, panel):
    """Existing v1 storage must load after the per-mode schema upgrade."""
    storage_key = "hikvision_axpro.test-entry.bypass"
    legacy_store = Store[dict](hass, 1, storage_key)
    await legacy_store.async_save(
        {
            "bypassable_zones": {"1": True},
            "owned_bypasses": {},
            "last_auto_bypass": None,
        }
    )

    entry = await setup_entry(hass)
    manager = get_manager(hass, entry)

    assert manager.is_bypassable(1, "home")
    assert not manager.is_bypassable(1, "away")
    assert not manager.is_bypassable(1, "vacation")


async def test_bypassable_switch_disabled_when_panel_forbids(hass, panel):
    """'Forbid bypass on arming' (panel config) => switch off and not editable."""
    panel.zone_configs = [
        {"id": 1, "zoneName": "Front door", "armNoBypassEnabled": True},
        {"id": 2, "zoneName": "Curtain hall", "armNoBypassEnabled": False},
    ]
    entry = await setup_entry(hass)
    manager = get_manager(hass, entry)

    forbidden = entity_id_of(
        hass, "switch", "00:11:22:33:44:55-bypassable-away-1"
    )
    normal = entity_id_of(hass, "switch", "00:11:22:33:44:55-bypassable-away-2")
    assert hass.states.get(forbidden).state == "unavailable"
    assert hass.states.get(normal).state == "off"

    # Turning it on is ignored: the panel-side setting wins.
    await manager.async_set_bypassable(1, True, "away")
    assert not manager.is_bypassable(1, "away")
    assert hass.states.get(forbidden).state == "unavailable"


async def test_panel_forbid_change_picked_up_on_hourly_refresh(hass, panel):
    """Enabling the panel setting takes effect at the hourly config refresh."""
    entry = await setup_entry(hass)
    coordinator = get_coordinator(hass, entry)
    manager = get_manager(hass, entry)
    await manager.async_set_bypassable(1, True, "away")
    assert manager.is_bypassable(1, "away")
    switch = entity_id_of(hass, "switch", "00:11:22:33:44:55-bypassable-away-1")

    panel.zone_configs = [
        {"id": 1, "zoneName": "Front door", "armNoBypassEnabled": True}
    ]

    # Within the hour the config is not re-fetched: nothing changes yet.
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert manager.is_bypassable(1, "away")

    # Backdate the last fetch so the hourly refresh is due.
    coordinator._last_zone_config_fetch -= timedelta(hours=2)
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert not manager.is_bypassable(1, "away")
    assert hass.states.get(switch).state == "unavailable"


async def test_stale_bypassable_switch_removed_on_zone_type_change(hass, panel):
    """A switch left over from a zone type change is removed on reload."""
    entry = await setup_entry(hass)
    registry = er.async_get(hass)
    for mode in ("away", "home", "vacation"):
        assert registry.async_get_entity_id(
            "switch", DOMAIN, f"00:11:22:33:44:55-bypassable-{mode}-1"
        )

    panel.zones[1]["zoneType"] = "Delay"  # no longer eligible
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    for mode in ("away", "home", "vacation"):
        assert not registry.async_get_entity_id(
            "switch", DOMAIN, f"00:11:22:33:44:55-bypassable-{mode}-1"
        )


async def test_bypassable_flag_cleared_for_ineligible_zone_type(hass, panel):
    """A stored flag is dropped when the zone type becomes ineligible."""
    entry = await setup_entry(hass)
    manager = get_manager(hass, entry)
    await manager.async_set_bypassable(1, True, "away")
    await manager.async_set_bypassable(1, True, "home")
    assert manager.is_bypassable(1, "away")
    assert manager.is_bypassable(1, "home")

    panel.zones[1]["zoneType"] = "Delay"
    await get_coordinator(hass, entry).async_refresh()
    await hass.async_block_till_done()

    assert not manager.is_bypassable(1, "away")
    assert not manager.is_bypassable(1, "home")


async def test_no_bypassable_switch_for_excluded_zone_types(hass, panel):
    """No config switch for zone types that are never auto-bypassed."""
    panel.zones[3] = {
        "id": 3,
        "name": "Smoke",
        "status": "online",
        "tamperEvident": False,
        "bypassed": False,
        "armed": False,
        "alarm": False,
        "subSystemNo": 1,
        "zoneType": "Fire",
    }
    await setup_entry(hass)
    registry = er.async_get(hass)
    assert registry.async_get_entity_id(
        "switch", DOMAIN, "00:11:22:33:44:55-bypassable-away-1"
    )
    assert not registry.async_get_entity_id(
        "switch", DOMAIN, "00:11:22:33:44:55-bypassable-away-3"
    )


# ---------------------------------------------------------------------------
# Ready-to-arm sensor


async def test_ready_sensor_reflects_evaluation(hass, panel):
    entry = await setup_entry(hass)
    sensor = entity_id_of(
        hass, "binary_sensor", "00:11:22:33:44:55-ready-to-arm-away"
    )
    assert hass.states.get(sensor).state == "on"

    panel.set_zone(2, status="offline")  # non-bypassable fault
    await get_coordinator(hass, entry).async_refresh()
    await hass.async_block_till_done()

    state = hass.states.get(sensor)
    assert state.state == "off"
    assert [z["zone_id"] for z in state.attributes["blocking_zones"]] == [2]
    assert state.attributes["areas"]["1"]["ready"] is False


async def test_ready_sensor_updates_on_flag_toggle_without_poll(hass, panel):
    """Toggling a bypassable switch re-evaluates immediately."""
    panel.set_zone(1, magnetOpenStatus=True)
    entry = await setup_entry(hass)
    manager = get_manager(hass, entry)
    sensor = entity_id_of(
        hass, "binary_sensor", "00:11:22:33:44:55-ready-to-arm-away"
    )
    await get_coordinator(hass, entry).async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(sensor).state == "off"

    await manager.async_set_bypassable(1, True, "away")
    await hass.async_block_till_done()

    state = hass.states.get(sensor)
    assert state.state == "on"
    assert [z["zone_id"] for z in state.attributes["zones_to_bypass"]] == [1]


async def test_ready_sensor_uses_mode_specific_switch(hass, panel):
    panel.set_zone(1, magnetOpenStatus=True)
    entry = await setup_entry(hass)
    manager = get_manager(hass, entry)
    await manager.async_set_bypassable(1, True, "home")
    await get_coordinator(hass, entry).async_refresh()
    await hass.async_block_till_done()

    away = entity_id_of(hass, "binary_sensor", "00:11:22:33:44:55-ready-to-arm-away")
    home = entity_id_of(hass, "binary_sensor", "00:11:22:33:44:55-ready-to-arm-home")

    assert hass.states.get(away).state == "off"
    assert hass.states.get(home).state == "on"


async def test_ready_sensor_created_for_every_mode(hass, panel):
    """One ready sensor per arming mode, regardless of auto-bypass config."""
    await setup_entry(hass, **{CONF_AUTO_BYPASS_MODES: []})
    for unique_id in (
        "00:11:22:33:44:55-ready-to-arm-away",
        "00:11:22:33:44:55-ready-to-arm-home",
        "00:11:22:33:44:55-ready-to-arm-vacation",
    ):
        entity_id_of(hass, "binary_sensor", unique_id)


async def test_ready_sensor_applies_mode_gate_before_switch_state(hass, panel):
    """A disallowed mode ignores bypassable flags and still blocks."""
    panel.set_zone(1, magnetOpenStatus=True)
    entry = await setup_entry(hass, **{CONF_AUTO_BYPASS_MODES: ["away"]})
    manager = get_manager(hass, entry)
    await manager.async_set_bypassable(1, True, "home")
    await get_coordinator(hass, entry).async_refresh()
    await hass.async_block_till_done()

    home = entity_id_of(hass, "binary_sensor", "00:11:22:33:44:55-ready-to-arm-home")
    assert hass.states.get(home).state == "off"


async def test_home_ready_sensor_ignores_stay_bypassed_zone(hass, panel):
    """A stay-bypassed faulted zone blocks away but not home."""
    panel.set_zone(2, status="offline", stayAway=True)
    entry = await setup_entry(hass)
    await get_coordinator(hass, entry).async_refresh()
    await hass.async_block_till_done()

    away = entity_id_of(hass, "binary_sensor", "00:11:22:33:44:55-ready-to-arm-away")
    home = entity_id_of(hass, "binary_sensor", "00:11:22:33:44:55-ready-to-arm-home")
    assert hass.states.get(away).state == "off"
    assert hass.states.get(home).state == "on"
    assert hass.states.get(home).attributes["evaluated_mode"] == "home"


async def test_panel_diagnostic_attributes(hass, panel):
    """The panel exposes bypassed zones and owned bypasses."""
    entry = await setup_entry(hass)
    manager = get_manager(hass, entry)
    await manager.async_set_bypassable(1, True)
    panel.set_zone(1, magnetOpenStatus=True)
    await get_coordinator(hass, entry).async_arm_away()
    # Entity states update on the next poll cycle.
    await get_coordinator(hass, entry).async_refresh()
    await hass.async_block_till_done()

    panel_entity = entity_id_of(hass, "alarm_control_panel", "00:11:22:33:44:55")
    attrs = hass.states.get(panel_entity).attributes
    assert [z["zone_id"] for z in attrs["bypassed_zones"]] == [1]
    assert "1" in {str(k) for k in attrs["owned_bypasses"]}
    assert attrs["last_auto_bypass"] is not None

    bypass_sensor = entity_id_of(
        hass, "binary_sensor", "00:11:22:33:44:55-bypass-1"
    )
    sensor_attrs = hass.states.get(bypass_sensor).attributes
    assert sensor_attrs["bypass_owner"] == "integration"
    assert sensor_attrs["bypass_reason"] == "auto_arm"
