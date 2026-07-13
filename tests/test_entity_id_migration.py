"""Tests for entity ID normalization and generation."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from homeassistant.helpers import entity_registry as er

from custom_components.hikvision_axpro.const import DOMAIN
from custom_components.hikvision_axpro.entity_id import (
    collapse_duplicate_token_runs,
    has_invalid_object_id_chars,
    normalized_object_id,
    object_id_name_remainder,
)

from .conftest import setup_entry

pytestmark = pytest.mark.usefixtures("auto_enable_custom_integrations")


def entity_id_of(hass, domain, unique_id) -> str:
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(domain, DOMAIN, unique_id)
    assert entity_id, f"entity {unique_id} not found"
    return entity_id


def test_slugify_helper_produces_valid_object_ids() -> None:
    """Helper always returns a valid non-empty object_id."""
    assert normalized_object_id("Alarm-Bypass 8") == "alarm_bypass_8"
    assert normalized_object_id("Villa 2", fallback=None) == "villa_2"

    non_ascii = normalized_object_id("Český Název 你好", fallback="Zone 7")
    assert non_ascii
    assert re.fullmatch(r"[a-z0-9_]+", non_ascii)

    fallback_only = normalized_object_id("***", fallback="axpro-bypass-1")
    assert re.fullmatch(r"[a-z0-9_]+", fallback_only)

    deterministic = normalized_object_id("***", fallback="")
    assert deterministic
    assert re.fullmatch(r"[a-z0-9_]+", deterministic)

    assert has_invalid_object_id_chars("binary_sensor.villa_2_alarm-bypass-8")
    assert not has_invalid_object_id_chars("binary_sensor.villa_2_alarm_bypass_8")


def test_object_id_name_remainder_strips_device_overlap() -> None:
    """The entity-name part loses the words shared with the device name."""
    # Device "Villa 1" + entity "Villa 1 Alarm Panel" => villa_1_alarm_panel
    assert object_id_name_remainder("Villa 1", "Villa 1 Alarm Panel") == "alarm panel"
    # Partial overlap on the boundary
    assert object_id_name_remainder("Living Alarm", "Alarm panel") == "panel"
    # No overlap: entity name is returned untouched
    assert object_id_name_remainder("Front door", "Alarm") == "Alarm"
    # Entity name fully covered by the device name
    assert object_id_name_remainder("Villa 1", "Villa 1") is None
    assert object_id_name_remainder("Front door", None) is None


def test_collapse_duplicate_token_runs() -> None:
    """Adjacent duplicated multi-word runs are collapsed, once or repeated."""
    assert (
        collapse_duplicate_token_runs("villa_1_villa_1_alarm_panel")
        == "villa_1_alarm_panel"
    )
    assert (
        collapse_duplicate_token_runs("villa_1_villa_1_villa_1_alarm")
        == "villa_1_alarm"
    )
    assert collapse_duplicate_token_runs("front_door_alarm") == "front_door_alarm"
    # Single repeated words and numeric collision suffixes are ambiguous
    # and deliberately left alone.
    assert collapse_duplicate_token_runs("garage_garage_alarm") == "garage_garage_alarm"
    assert collapse_duplicate_token_runs("bypass_2_2") == "bypass_2_2"


def test_no_entity_class_sets_entity_id_directly() -> None:
    """Entity classes must not assign self.entity_id directly."""
    base = Path(__file__).resolve().parents[1] / "custom_components" / "hikvision_axpro"
    for rel in ("binary_sensor.py", "sensor.py", "switch.py", "alarm_control_panel.py"):
        content = (base / rel).read_text(encoding="utf-8")
        assert "self.entity_id =" not in content


async def test_generated_ids_follow_default_ha_name_composition(hass):
    """Entity IDs follow HA default device-name + entity-name composition."""
    from unittest.mock import patch

    from .conftest import MockAxPro, zone_payload

    mock = MockAxPro(
        zones=[
            zone_payload(1, name="Living Alarm"),
            zone_payload(2, name="Front door"),
        ]
    )
    with patch("hikaxpro.HikAxPro", return_value=mock):
        await setup_entry(hass)

    # Zone "Living Alarm" + entity "Alarm" => living_alarm_alarm.
    assert (
        entity_id_of(hass, "binary_sensor", "00:11:22:33:44:55-alarm-1")
        == "binary_sensor.living_alarm_alarm"
    )
    # Non-overlapping names keep the standard composition too.
    assert (
        entity_id_of(hass, "binary_sensor", "00:11:22:33:44:55-alarm-2")
        == "binary_sensor.front_door_alarm"
    )


async def test_reload_keeps_existing_entity_ids(hass, panel):
    """Reload keeps existing entity IDs unchanged."""
    entry = await setup_entry(hass)
    registry = er.async_get(hass)

    before = {
        reg_entry.unique_id: reg_entry.entity_id
        for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id)
        if reg_entry.unique_id
    }

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    after = {
        reg_entry.unique_id: reg_entry.entity_id
        for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id)
        if reg_entry.unique_id
    }

    assert before == after


async def test_regenerate_entity_id_follows_default_ha_behavior(hass, panel):
    """Regenerate uses HA default naming behavior from registry metadata."""
    await setup_entry(hass)
    registry = er.async_get(hass)

    ready_entry = registry.entities[
        entity_id_of(hass, "binary_sensor", "00:11:22:33:44:55-ready-to-arm-away")
    ]
    panel_entry = registry.entities[
        entity_id_of(hass, "alarm_control_panel", "00:11:22:33:44:55")
    ]

    regenerated_ready = registry.async_regenerate_entity_id(ready_entry)
    assert regenerated_ready == "binary_sensor.axpro_ready_to_arm_away"

    regenerated_panel = registry.async_regenerate_entity_id(panel_entry)
    assert regenerated_panel == "alarm_control_panel.axpro"
