"""Relays follow the panel: switchable only with the "manual" scenario."""

from __future__ import annotations

import pytest

from homeassistant.helpers import entity_registry as er

from custom_components.hikvision_axpro.const import DOMAIN
from custom_components.hikvision_axpro.model import (
    RelaySwitchConf,
    relay_allows_manual_control,
)

from .conftest import get_coordinator, setup_entry

pytestmark = pytest.mark.usefixtures("auto_enable_custom_integrations")

RELAY_UID = "001122334455-relay-0"


def relay(scenario_type: list[str] | None) -> dict:
    config = {"id": 0, "name": "siren", "related": True}
    if scenario_type is not None:
        config["scenarioType"] = scenario_type
    return config


def entity_id(hass, domain: str) -> str | None:
    return er.async_get(hass).async_get_entity_id(domain, DOMAIN, RELAY_UID)


@pytest.mark.parametrize(
    ("scenario_type", "manual"),
    [
        (["alarm", "disarm", "clearAlarm"], False),
        (["alarm", "manual"], True),
        (["manual"], True),
        ([], False),
        (None, True),
    ],
)
def test_relay_allows_manual_control(scenario_type, manual):
    conf = RelaySwitchConf.from_dict(relay(scenario_type))
    assert relay_allows_manual_control(conf) is manual


async def test_panel_driven_relay_is_read_only(hass, panel):
    """A relay without the manual scenario is a binary sensor, not a switch."""
    panel.output_configs = [relay(["alarm", "disarm", "clearAlarm"])]
    panel.output_status = [{"id": 0, "name": "siren", "status": "off"}]
    entry = await setup_entry(hass)

    assert entity_id(hass, "switch") is None
    sensor = entity_id(hass, "binary_sensor")
    assert sensor is not None
    assert hass.states.get(sensor).state == "off"

    panel.output_status = [{"id": 0, "name": "siren", "status": "on"}]
    coordinator = get_coordinator(hass, entry)
    coordinator.request_slow_refresh()
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(sensor).state == "on"


async def test_manual_relay_stays_a_switch(hass, panel):
    """A relay the panel allows to switch by hand keeps its switch."""
    panel.output_configs = [relay(["alarm", "manual"])]
    panel.output_status = [{"id": 0, "name": "siren", "status": "off"}]
    await setup_entry(hass)

    assert entity_id(hass, "switch") is not None
    assert entity_id(hass, "binary_sensor") is None


async def test_relay_without_scenarios_stays_a_switch(hass, panel):
    """Firmware that does not report scenarioType keeps manual control."""
    panel.output_configs = [relay(None)]
    panel.output_status = [{"id": 0, "name": "siren", "status": "off"}]
    await setup_entry(hass)

    assert entity_id(hass, "switch") is not None
    assert entity_id(hass, "binary_sensor") is None


async def test_stale_switch_is_removed(hass, panel):
    """The switch an older release created for a panel-driven relay goes away."""
    er.async_get(hass).async_get_or_create(
        "switch", DOMAIN, RELAY_UID, suggested_object_id="siren"
    )
    panel.output_configs = [relay(["alarm"])]
    panel.output_status = [{"id": 0, "name": "siren", "status": "off"}]
    await setup_entry(hass)

    assert entity_id(hass, "switch") is None
    assert entity_id(hass, "binary_sensor") is not None
