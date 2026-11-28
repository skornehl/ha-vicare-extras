"""The repair that hands the entity IDs of taken-over values to the core (needs the test harness)."""

from __future__ import annotations

import json

import pytest

from homeassistant.components.repairs import DOMAIN as REPAIRS_DOMAIN
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import (
    area_registry as ar,
    entity_registry as er,
    issue_registry as ir,
    label_registry as lr,
)
from homeassistant.setup import async_setup_component

from custom_components.vicare_extras.const import DOMAIN
from custom_components.vicare_extras.core_overlap import ISSUE_CORE_TAKEOVER

from .test_init_hass import IDENT, _setup

CORE_KEYS = frozenset(
    {
        ("binary_sensor", "boiler_internal_pump_active"),
        ("sensor", "boiler_target_temperature"),
    }
)
PUMP = "binary_sensor.vitodens_internal_pump"
TARGET = "sensor.vitodens_boiler_target_temperature"
CORE_PUMP = "binary_sensor.vitodens_internal_pump_2"
CORE_TARGET = "sensor.vitodens_boiler_target_temperature_2"


@pytest.fixture(autouse=True)
def core_with_both(monkeypatch):
    """A core that defines both values, whatever Home Assistant is installed."""

    async def core_keys(hass):
        return CORE_KEYS

    monkeypatch.setattr("custom_components.vicare_extras.async_core_keys", core_keys)


async def _start(hass, *, pump_disabled: bool = False):
    """Extras entities from an older version, and the core entities that took them over."""
    entry, _, _, registry = await _setup(hass, {})
    vicare = hass.config_entries.async_entries("vicare")[0]
    registry.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        f"{IDENT}-extras-boiler_pump_internal",
        config_entry=entry,
        suggested_object_id="vitodens_internal_pump",
        disabled_by=er.RegistryEntryDisabler.USER if pump_disabled else None,
    )
    registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{IDENT}-extras-boiler_target_temperature",
        config_entry=entry,
        suggested_object_id="vitodens_boiler_target_temperature",
    )
    for domain, key, object_id in (
        ("binary_sensor", "boiler_internal_pump_active", "vitodens_internal_pump_2"),
        ("sensor", "boiler_target_temperature", "vitodens_boiler_target_temperature_2"),
    ):
        registry.async_get_or_create(
            domain, "vicare", f"{IDENT}-{key}", config_entry=vicare, suggested_object_id=object_id
        )
    return entry, registry


async def _load(hass, entry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _issue(hass):
    return ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_CORE_TAKEOVER)


def _moves(hass) -> dict[str, dict]:
    return {move["old"]: move for move in json.loads(_issue(hass).data["moves"])}


async def _start_fix(hass):
    assert await async_setup_component(hass, REPAIRS_DOMAIN, {})
    manager = hass.data[REPAIRS_DOMAIN]["flow_manager"]
    result = await manager.async_init(DOMAIN, context={"issue_id": ISSUE_CORE_TAKEOVER})
    return manager, result


async def test_takeover_raises_the_repair(extras_hass) -> None:
    hass = extras_hass
    entry, registry = await _start(hass)
    await _load(hass, entry)

    # The Extras entities are gone, the repair names their core entities.
    assert registry.async_get(PUMP) is None
    assert registry.async_get(TARGET) is None
    issue = _issue(hass)
    assert issue.is_fixable and issue.is_persistent
    assert issue.severity is ir.IssueSeverity.WARNING
    moves = _moves(hass)
    assert set(moves) == {PUMP, TARGET}
    assert moves[PUMP]["core"] == CORE_PUMP
    assert moves[PUMP]["unique_id"] == f"{IDENT}-boiler_internal_pump_active"
    assert moves[TARGET]["core"] == CORE_TARGET
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_a_disabled_extras_entity_gets_no_repair(extras_hass) -> None:
    hass = extras_hass
    entry, registry = await _start(hass, pump_disabled=True)
    await _load(hass, entry)
    assert registry.async_get(PUMP) is None
    assert set(_moves(hass)) == {TARGET}
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_no_takeover_no_repair(extras_hass, monkeypatch) -> None:
    hass = extras_hass

    async def old_core(hass):
        return frozenset()

    monkeypatch.setattr("custom_components.vicare_extras.async_core_keys", old_core)
    entry, registry = await _start(hass)
    await _load(hass, entry)
    assert registry.async_get(PUMP) is not None
    assert _issue(hass) is None
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_an_open_repair_keeps_its_earlier_moves(extras_hass) -> None:
    hass = extras_hass
    earlier = {
        "old": "sensor.vitodens_holiday",
        "domain": "binary_sensor",
        "unique_id": f"{IDENT}-holiday_active",
        "core": "binary_sensor.vitodens_holiday_2",
        "name": None,
        "icon": None,
        "area_id": None,
        "labels": [],
    }
    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_CORE_TAKEOVER,
        data={"moves": json.dumps([earlier])},
        is_fixable=True,
        is_persistent=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_CORE_TAKEOVER,
    )
    entry, _ = await _start(hass)
    await _load(hass, entry)
    assert set(_moves(hass)) == {PUMP, TARGET, "sensor.vitodens_holiday"}
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_hand_on_gives_the_core_entities_the_old_ids(extras_hass) -> None:
    hass = extras_hass
    entry, registry = await _start(hass)
    area = ar.async_get(hass).async_create("Keller")
    label = lr.async_get(hass).async_create("Heizung")
    registry.async_update_entity(PUMP, name="Heizungspumpe", area_id=area.id, labels={label.label_id})
    # The core entity has its own name already: that one stays.
    registry.async_update_entity(CORE_TARGET, name="Kesselsoll")
    await _load(hass, entry)

    manager, result = await _start_fix(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"
    placeholders = result["description_placeholders"]
    assert f"- `{PUMP}` → `{CORE_PUMP}`" in placeholders["moves"]
    assert f"- `{TARGET}` → `{CORE_TARGET}`" in placeholders["moves"]
    assert placeholders["old_used_by"] == "-"
    assert placeholders["core_used_by"] == "-"

    result = await manager.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    pump = registry.async_get(PUMP)
    assert pump.platform == "vicare"
    assert pump.unique_id == f"{IDENT}-boiler_internal_pump_active"
    assert pump.name == "Heizungspumpe"
    assert pump.area_id == area.id
    assert pump.labels == {label.label_id}
    target = registry.async_get(TARGET)
    assert target.platform == "vicare"
    assert target.name == "Kesselsoll"
    assert registry.async_get(CORE_PUMP) is None
    assert _issue(hass) is None
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_the_repair_lists_automations_and_scripts(extras_hass, monkeypatch) -> None:
    hass = extras_hass
    entry, _ = await _start(hass)
    await _load(hass, entry)
    uses = {
        PUMP: ["automation.pumpe_melden"],
        CORE_TARGET: ["script.kessel_pruefen"],
    }
    monkeypatch.setattr(
        "custom_components.vicare_extras.repairs.automations_with_entity",
        lambda hass, entity_id: [e for e in uses.get(entity_id, []) if e.startswith("automation.")],
    )
    monkeypatch.setattr(
        "custom_components.vicare_extras.repairs.scripts_with_entity",
        lambda hass, entity_id: [e for e in uses.get(entity_id, []) if e.startswith("script.")],
    )
    _, result = await _start_fix(hass)
    assert result["description_placeholders"]["old_used_by"] == "`automation.pumpe_melden`"
    assert result["description_placeholders"]["core_used_by"] == "`script.kessel_pruefen`"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_a_move_that_cannot_be_done_stays_in_the_repair(extras_hass) -> None:
    hass = extras_hass
    entry, registry = await _start(hass)
    await _load(hass, entry)
    # Meanwhile another entity took the old ID of the pump.
    registry.async_get_or_create(
        "binary_sensor", "demo", "other", suggested_object_id="vitodens_internal_pump"
    )
    assert registry.async_get(PUMP).platform == "demo"

    manager, result = await _start_fix(hass)
    result = await manager.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_all_moved"
    assert PUMP in result["description_placeholders"]["skipped"]
    assert TARGET not in result["description_placeholders"]["skipped"]

    # The target was handed on, the pump stays in the repair.
    assert registry.async_get(TARGET).platform == "vicare"
    assert set(_moves(hass)) == {PUMP}
    assert registry.async_get(CORE_PUMP) is not None
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_a_core_entity_that_is_gone_is_skipped(extras_hass) -> None:
    hass = extras_hass
    entry, registry = await _start(hass)
    await _load(hass, entry)
    registry.async_remove(CORE_PUMP)
    registry.async_remove(CORE_TARGET)

    manager, result = await _start_fix(hass)
    result = await manager.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_all_moved"
    assert set(_moves(hass)) == {PUMP, TARGET}
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_an_empty_repair_aborts(extras_hass) -> None:
    hass = extras_hass
    # Home Assistant only asks loaded integrations for their fix flow.
    hass.config.components.add("vicare")
    assert await async_setup_component(hass, DOMAIN, {})
    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_CORE_TAKEOVER,
        data={"moves": "[]"},
        is_fixable=True,
        is_persistent=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_CORE_TAKEOVER,
    )
    _, result = await _start_fix(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "nothing_to_move"


async def test_removing_the_integration_drops_the_repair(extras_hass) -> None:
    hass = extras_hass
    entry, _ = await _start(hass)
    await _load(hass, entry)
    assert _issue(hass) is not None
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert _issue(hass) is None
