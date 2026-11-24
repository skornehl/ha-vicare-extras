"""Tests for the Weiche between ViCare Extras and the core vicare integration."""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from vicare_extras import core_overlap as co
from vicare_extras.coordinator import ExtrasDevice
from vicare_extras.descriptions import build_specs

PUMP_FEATURES = {
    "heating.boiler.pumps.internal": {
        "properties": {"status": {"type": "string", "value": "on"}}
    },
    "heating.boiler.pumps.internal.target": {
        "properties": {"value": {"type": "number", "value": 55, "unit": "percent"}}
    },
    "heating.boiler.pumps.internal.current": {
        "properties": {"value": {"type": "number", "value": 50, "unit": "percent"}}
    },
}

CORE_WITH_PUMP = frozenset(
    {
        ("binary_sensor", "boiler_internal_pump_active"),
        ("sensor", "boiler_internal_pump_target_modulation"),
        ("sensor", "boiler_internal_pump_modulation"),
    }
)


def _device() -> ExtrasDevice:
    return ExtrasDevice("gateway_device", "E3_Vitodens_200", dict(PUMP_FEATURES))


def _keys(core_keys) -> set[str]:
    return {spec.key for spec in build_specs(_device(), None, core_keys)}


async def test_description_keys_only_takes_entity_descriptions() -> None:
    """Only objects with a key and a translation_key count."""
    module = SimpleNamespace(
        SENSORS=(
            SimpleNamespace(key="a", translation_key="a"),
            SimpleNamespace(key="b", translation_key=None),
        ),
        OTHER=[SimpleNamespace(key="c")],  # no translation_key: not a description
        NAMES=("x", "y"),
        SINGLE=SimpleNamespace(key="d", translation_key="d"),  # not in a list
    )
    assert co.description_keys(module) == {"a", "b"}


async def test_taken_over_without_core_keys() -> None:
    assert co.taken_over(frozenset()) == (set(), set())


async def test_taken_over_with_pump_in_core() -> None:
    skip, features = co.taken_over(CORE_WITH_PUMP)
    assert skip == {"boiler_pump_internal", "boiler_pump_internal_target"}
    assert features == {
        "heating.boiler.pumps.internal",
        "heating.boiler.pumps.internal.target",
        "heating.boiler.pumps.internal.current",
    }


async def test_taken_over_partial_core() -> None:
    """A core that only has the binary sensor skips only that entity."""
    skip, features = co.taken_over(frozenset({("binary_sensor", "boiler_internal_pump_active")}))
    assert skip == {"boiler_pump_internal"}
    assert features == {"heating.boiler.pumps.internal"}


async def test_build_specs_old_core_keeps_pump_entities() -> None:
    keys = _keys(frozenset())
    assert {"boiler_pump_internal", "boiler_pump_internal_target"} <= keys
    # .current has no curated spec and stays a generic diagnostic.
    assert "x_heating_boiler_pumps_internal_current_value" in keys


async def test_build_specs_new_core_skips_pump_entities() -> None:
    keys = _keys(CORE_WITH_PUMP)
    assert "boiler_pump_internal" not in keys
    assert "boiler_pump_internal_target" not in keys
    # Nothing comes back through the generic layer either.
    assert not any("pumps_internal" in key for key in keys)


async def test_build_specs_unrelated_core_keys_change_nothing() -> None:
    assert _keys(frozenset({("sensor", "outside_temperature")})) == _keys(frozenset())


class _FakeHass:
    async def async_add_import_executor_job(self, func, *args):
        return func(*args)


async def test_async_core_keys_reads_installed_core() -> None:
    """Against the installed Home Assistant: known core keys are found."""
    pytest.importorskip("homeassistant.components.vicare.sensor")
    keys = await co.async_core_keys(_FakeHass())
    assert ("sensor", "outside_temperature") in keys
    assert ("binary_sensor", "circulationpump_active") in keys


async def test_async_core_keys_without_core(monkeypatch) -> None:
    def fail(name):
        raise ImportError(name)

    monkeypatch.setattr(co.importlib, "import_module", fail)
    assert await co.async_core_keys(_FakeHass()) == frozenset()


async def test_remove_stale_entities(monkeypatch) -> None:
    removed: list[str] = []
    entries = {
        ("binary_sensor", "vicare_extras", "dev1-extras-boiler_pump_internal"): "binary_sensor.pump",
        ("sensor", "vicare_extras", "dev1-extras-boiler_pump_internal_target"): "sensor.pump_target",
        ("sensor", "vicare_extras", "dev1-extras-other"): "sensor.other",
    }
    registry = SimpleNamespace(
        async_get_entity_id=lambda platform, domain, uid: entries.get((platform, domain, uid)),
        async_remove=removed.append,
    )
    monkeypatch.setattr(co.er, "async_get", lambda hass: registry)
    co.remove_stale_entities(None, ["dev1"], {"boiler_pump_internal", "boiler_pump_internal_target"})
    assert sorted(removed) == ["binary_sensor.pump", "sensor.pump_target"]


async def test_remove_stale_entities_nothing_to_skip(monkeypatch) -> None:
    monkeypatch.setattr(co.er, "async_get", lambda hass: pytest.fail("registry must not be touched"))
    co.remove_stale_entities(None, ["dev1"], set())
