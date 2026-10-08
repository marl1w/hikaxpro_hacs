"""Tests for the fast (area + zone) and slow (peripherals + host) poll tiers."""

from __future__ import annotations

from datetime import timedelta

import pytest

from homeassistant.helpers import entity_registry as er

from custom_components.hikvision_axpro.const import DOMAIN

from .conftest import get_coordinator, setup_entry

pytestmark = pytest.mark.usefixtures("auto_enable_custom_integrations")

ZONES = "/status/zones"
AREAS = "/status/subSystems"
PERIPHERALS = "/status/exDevStatus"
HOST = "/status/host"


def requested(panel, path: str) -> bool:
    return any(path in endpoint for endpoint in panel.requests)


async def poll(hass, entry):
    await get_coordinator(hass, entry).async_refresh()
    await hass.async_block_till_done()


async def test_fast_polls_skip_peripherals_until_slow_interval(hass, panel, freezer):
    """Zones and areas are polled every time, peripherals once a minute."""
    entry = await setup_entry(hass, scan_interval=2)
    assert requested(panel, PERIPHERALS)  # setup fetches everything once

    panel.requests.clear()
    freezer.tick(timedelta(seconds=2))
    await poll(hass, entry)
    assert requested(panel, ZONES)
    assert requested(panel, AREAS)
    assert not requested(panel, PERIPHERALS)
    assert not requested(panel, HOST)

    panel.requests.clear()
    freezer.tick(timedelta(seconds=60))
    await poll(hass, entry)
    assert requested(panel, ZONES)
    assert requested(panel, PERIPHERALS)
    assert requested(panel, HOST)


async def test_fast_poll_picks_up_zone_change(hass, panel, freezer):
    """A short-lived PIR trigger is seen on the next fast poll."""
    entry = await setup_entry(hass, scan_interval=2)
    coordinator = get_coordinator(hass, entry)

    panel.set_zone(2, status="trigger")
    freezer.tick(timedelta(seconds=2))
    await poll(hass, entry)
    assert coordinator.zones[2].status.value == "trigger"


async def test_long_scan_interval_polls_everything(hass, panel, freezer):
    """A scan interval above the slow interval keeps today's behaviour."""
    entry = await setup_entry(hass, scan_interval=120)

    panel.requests.clear()
    freezer.tick(timedelta(seconds=120))
    await poll(hass, entry)
    assert requested(panel, ZONES)
    assert requested(panel, PERIPHERALS)


async def test_relay_command_forces_peripheral_refresh(hass, panel, freezer):
    """Switching a relay re-reads peripherals on the next poll."""
    entry = await setup_entry(hass, scan_interval=2)
    coordinator = get_coordinator(hass, entry)

    assert await coordinator.relay_on(1)
    panel.requests.clear()
    freezer.tick(timedelta(seconds=2))
    await poll(hass, entry)
    assert requested(panel, PERIPHERALS)

    panel.requests.clear()
    freezer.tick(timedelta(seconds=2))
    await poll(hass, entry)
    assert not requested(panel, PERIPHERALS)


async def test_peripheral_failure_does_not_block_zone_updates(hass, panel, freezer):
    """A failing exDevStatus keeps zones updating and retries a minute later."""
    entry = await setup_entry(hass, scan_interval=2)
    coordinator = get_coordinator(hass, entry)

    panel.ex_dev_status_fail = True
    panel.set_zone(2, status="trigger")
    freezer.tick(timedelta(seconds=60))
    await poll(hass, entry)
    assert coordinator.last_update_success
    assert coordinator.zones[2].status.value == "trigger"

    panel.requests.clear()
    freezer.tick(timedelta(seconds=2))
    await poll(hass, entry)
    assert not requested(panel, PERIPHERALS)

    panel.ex_dev_status_fail = False
    freezer.tick(timedelta(seconds=60))
    await poll(hass, entry)
    assert requested(panel, PERIPHERALS)


@pytest.mark.parametrize(
    "change", [{"alarm": True}, {"arming": "away"}], ids=["alarm", "keypad_arm"]
)
async def test_panel_side_area_change_forces_peripheral_refresh(
    hass, panel, freezer, change
):
    """An alarm or arming done on the panel re-reads sirens and relays at once."""
    entry = await setup_entry(hass, scan_interval=2)
    freezer.tick(timedelta(seconds=2))
    await poll(hass, entry)

    panel.area(1).update(change)
    panel.requests.clear()
    freezer.tick(timedelta(seconds=2))
    await poll(hass, entry)
    assert requested(panel, PERIPHERALS)

    panel.requests.clear()
    freezer.tick(timedelta(seconds=2))
    await poll(hass, entry)
    assert not requested(panel, PERIPHERALS)


async def test_arm_from_ha_refreshes_once_and_updates_entity(hass, panel):
    """Arming from HA fetches the area status once and shows it right away."""
    await setup_entry(hass, scan_interval=2)
    registry = er.async_get(hass)
    panel_id = registry.async_get_entity_id(
        "alarm_control_panel", DOMAIN, "001122334455"
    )

    panel.requests.clear()
    await hass.services.async_call(
        "alarm_control_panel",
        "alarm_arm_away",
        {"entity_id": panel_id},
        blocking=True,
    )
    assert sum(AREAS in endpoint for endpoint in panel.requests) == 1
    assert hass.states.get(panel_id).state == "armed_away"
