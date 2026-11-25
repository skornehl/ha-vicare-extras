"""Tests for the Weiche between ViCare Extras and the core vicare integration."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.vicare_extras import core_overlap as co
from custom_components.vicare_extras.coordinator import ExtrasDevice
from custom_components.vicare_extras.descriptions import build_specs

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


def _keys(core_keys, created=None) -> set[str]:
    return {spec.key for spec in build_specs(_device(), None, co.taken_over(core_keys, created))}


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
    assert co.taken_over(frozenset()) == (frozenset(), frozenset())


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


async def test_build_specs_keeps_entities_the_core_did_not_create() -> None:
    """New core, but this device has no core pump entity: Extras keeps its own."""
    assert _keys(CORE_WITH_PUMP, lambda platform, key: False) == _keys(frozenset())


async def test_taken_over_only_what_the_core_created() -> None:
    created = {("binary_sensor", "boiler_internal_pump_active")}
    skip, features = co.taken_over(CORE_WITH_PUMP, lambda p, k: (p, k) in created)
    assert skip == {"boiler_pump_internal"}
    assert features == {"heating.boiler.pumps.internal"}


class _Registry:
    def __init__(self, entries):
        self.entities = {e.entity_id: e for e in entries}
        self.removed: list[str] = []

    def async_get_entity_id(self, domain, platform, unique_id):
        for e in self.entities.values():
            if (e.domain, e.platform, e.unique_id) == (domain, platform, unique_id):
                return e.entity_id
        return None

    def async_remove(self, entity_id):
        self.removed.append(entity_id)


def _entry(entity_id, platform, unique_id):
    return SimpleNamespace(
        entity_id=entity_id, domain=entity_id.split(".")[0], platform=platform, unique_id=unique_id
    )


async def test_core_entity_exists() -> None:
    registry = _Registry(
        [
            _entry("binary_sensor.pump", "vicare", "gw_dev-boiler_internal_pump_active"),
            _entry("sensor.burner", "vicare", "gw_dev-burner_modulation-0"),
            _entry("sensor.extras", "vicare_extras", "gw_dev-boiler_internal_pump_modulation"),
        ]
    )
    assert co.core_entity_exists(registry, "gw_dev", "binary_sensor", "boiler_internal_pump_active")
    assert co.core_entity_exists(registry, "gw_dev", "sensor", "burner_modulation")  # component
    assert not co.core_entity_exists(registry, "gw_other", "binary_sensor", "boiler_internal_pump_active")
    # Only the core platform counts, and the platform domain must match.
    assert not co.core_entity_exists(registry, "gw_dev", "sensor", "boiler_internal_pump_modulation")
    assert not co.core_entity_exists(registry, "gw_dev", "sensor", "boiler_internal_pump_active")


async def test_device_overlap_uses_registry(monkeypatch) -> None:
    registry = _Registry([_entry("binary_sensor.pump", "vicare", "gw_dev-boiler_internal_pump_active")])
    monkeypatch.setattr(co.er, "async_get", lambda hass: registry)
    skip, features = co.device_overlap(None, CORE_WITH_PUMP, "gw_dev")
    assert skip == {"boiler_pump_internal"}
    assert features == {"heating.boiler.pumps.internal"}
    assert co.device_overlap(None, CORE_WITH_PUMP, "gw_other") == co.NO_OVERLAP
    assert co.device_overlap(None, frozenset(), "gw_dev") == co.NO_OVERLAP


class _Integration:
    def __init__(self, modules=None, error=None):
        self.modules = modules or {}
        self.error = error

    async def async_get_platform(self, platform):
        if self.error:
            raise self.error
        return self.modules[platform]


async def test_async_core_keys_reads_loaded_integration(monkeypatch) -> None:
    sensor = SimpleNamespace(S=(SimpleNamespace(key="outside_temperature", translation_key="x"),))
    binary = SimpleNamespace(B=[SimpleNamespace(key="boiler_internal_pump_active", translation_key="y")])

    async def get(hass, domain):
        assert domain == "vicare"
        return _Integration({"sensor": sensor, "binary_sensor": binary})

    monkeypatch.setattr(co, "async_get_integration", get)
    assert await co.async_core_keys(None) == {
        ("sensor", "outside_temperature"),
        ("binary_sensor", "boiler_internal_pump_active"),
    }


async def test_async_core_keys_survives_import_errors(monkeypatch) -> None:
    async def broken_platform(hass, domain):
        return _Integration(error=SyntaxError("broken custom vicare"))

    monkeypatch.setattr(co, "async_get_integration", broken_platform)
    assert await co.async_core_keys(None) == frozenset()

    async def no_integration(hass, domain):
        raise RuntimeError("not found")

    monkeypatch.setattr(co, "async_get_integration", no_integration)
    assert await co.async_core_keys(None) == frozenset()


async def test_async_core_keys_reads_installed_core() -> None:
    """Against the installed Home Assistant: the real description keys are found."""
    vicare_sensor = pytest.importorskip("homeassistant.components.vicare.sensor")
    vicare_binary = pytest.importorskip("homeassistant.components.vicare.binary_sensor")
    assert "outside_temperature" in co.description_keys(vicare_sensor)
    assert "circulationpump_active" in co.description_keys(vicare_binary)


async def test_remove_orphaned_entities(monkeypatch) -> None:
    entries = [
        _entry("sensor.keep", "vicare_extras", "dev1-extras-keep"),
        _entry("sensor.pump_current", "vicare_extras", "dev1-extras-x_heating_boiler_pumps_internal_current_value"),
        _entry("sensor.gone_dev", "vicare_extras", "dev2-extras-anything"),
        _entry("sensor.similar", "vicare_extras", "dev10-extras-other"),
    ]
    registry = _Registry(entries)
    monkeypatch.setattr(co.er, "async_get", lambda hass: registry)
    monkeypatch.setattr(co.er, "async_entries_for_config_entry", lambda reg, entry_id: entries)
    removed = co.remove_orphaned_entities(None, "entry", {"dev1": {"dev1-extras-keep"}})
    # dev2 / dev10 have no current data here: never touched.
    assert removed == ["sensor.pump_current"]
    assert registry.removed == ["sensor.pump_current"]


async def test_unique_ids() -> None:
    assert co.unique_ids("dev", ["a", "b"]) == {"dev-extras-a", "dev-extras-b"}
