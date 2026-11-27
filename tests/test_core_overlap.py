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


# The values of the core pull requests in CORE_TAKEOVER and CORE_TAKEOVER_CIRCUITS,
# as a gas boiler with two heating circuits reports them.
TAKEOVER_FEATURES = {
    **PUMP_FEATURES,
    "heating.operating.programs.holiday": {
        "properties": {
            "active": {"type": "boolean", "value": False},
            "start": {"type": "string", "value": ""},
            "end": {"type": "string", "value": ""},
        }
    },
    "heating.operating.programs.holidayAtHome": {
        "properties": {
            "active": {"type": "boolean", "value": False},
            "start": {"type": "string", "value": ""},
            "end": {"type": "string", "value": ""},
        }
    },
    "heating.dhw.hygiene": {
        "properties": {
            "enabled": {"type": "boolean", "value": True},
            "active": {"type": "boolean", "value": False},
        }
    },
    "heating.boiler.temperature": {
        "properties": {"value": {"type": "number", "value": 54, "unit": "celsius"}}
    },
    "device.lock.malfunction": {"properties": {"active": {"type": "boolean", "value": False}}},
    "device.lock.external": {"properties": {"active": {"type": "boolean", "value": False}}},
    "heating.circuits.0.temperature": {
        "properties": {"value": {"type": "number", "value": 42, "unit": "celsius"}}
    },
    "heating.circuits.1.temperature": {
        "properties": {"value": {"type": "number", "value": 35, "unit": "celsius"}}
    },
}

CORE_WITH_EVERYTHING = frozenset(
    {(platform, key) for platform, key, _ in co.CORE_TAKEOVER.values()}
    | {(platform, key) for platform, key, _ in co.CORE_TAKEOVER_CIRCUITS.values()}
)


def _takeover_keys(core_keys, created=None, circuits=None) -> set[str]:
    dev = ExtrasDevice("gateway_device", "E3_Vitodens_200", dict(TAKEOVER_FEATURES))
    return {spec.key for spec in build_specs(dev, None, co.taken_over(core_keys, created, circuits))}


async def test_every_takeover_names_a_curated_entity() -> None:
    """A key in the mapping that Extras does not create would never skip anything."""
    keys = _takeover_keys(frozenset())
    assert set(co.CORE_TAKEOVER) <= keys
    for template in co.CORE_TAKEOVER_CIRCUITS:
        assert {template.format(circuit=c) for c in ("0", "1")} <= keys


async def test_a_new_core_takes_over_everything_it_created() -> None:
    keys = _takeover_keys(CORE_WITH_EVERYTHING, circuits=lambda platform, key: {"0", "1"})
    assert not keys & set(co.CORE_TAKEOVER)
    assert not any(key.endswith("_target_supply_temperature") for key in keys)
    # The features do not come back as generic diagnostics, one per property.
    feats = [f for _, _, fs in co.CORE_TAKEOVER.values() for f in fs]
    feats += [f.format(circuit=c) for _, _, fs in co.CORE_TAKEOVER_CIRCUITS.values() for f in fs for c in ("0", "1")]
    for feat in feats:
        generic = {f"x_{feat.replace('.', '_')}_{name}" for name in TAKEOVER_FEATURES[feat]["properties"]}
        assert not keys & generic, feat
    # The pump's current output has no core entity in this core: it stays generic.
    assert "x_heating_boiler_pumps_internal_current_value" in keys


async def test_circuits_are_taken_over_one_by_one() -> None:
    """The core created circuit 0 only: circuit 1 keeps its Extras entity."""
    core_keys = frozenset({("sensor", "target_supply_temperature")})
    skip, features = co.taken_over(core_keys, None, lambda platform, key: {"0"})
    assert skip == {"circuit_0_target_supply_temperature"}
    assert features == {"heating.circuits.0.temperature"}
    keys = _takeover_keys(core_keys, circuits=lambda platform, key: {"0"})
    assert "circuit_0_target_supply_temperature" not in keys
    assert "circuit_1_target_supply_temperature" in keys
    # Without the registry no circuit is left out.
    assert co.taken_over(core_keys) == (frozenset(), frozenset())


async def test_core_components() -> None:
    registry = _Registry(
        [
            _entry("sensor.c0", "vicare", "gw_dev-target_supply_temperature-0"),
            _entry("sensor.c1", "vicare", "gw_dev-target_supply_temperature-1"),
            _entry("sensor.other", "vicare", "gw_other-target_supply_temperature-0"),
            _entry("sensor.extras", "vicare_extras", "gw_dev-target_supply_temperature-2"),
            _entry("sensor.device", "vicare", "gw_dev-target_supply_temperature"),
        ]
    )
    assert co.core_components(registry, "gw_dev", "sensor", "target_supply_temperature") == {"0", "1"}
    assert co.core_components(registry, "gw_dev", "binary_sensor", "target_supply_temperature") == set()


async def test_device_overlap_takes_over_the_circuits_in_the_registry(monkeypatch) -> None:
    registry = _Registry([_entry("sensor.c1", "vicare", "gw_dev-target_supply_temperature-1")])
    monkeypatch.setattr(co.er, "async_get", lambda hass: registry)
    skip, features = co.device_overlap(None, frozenset({("sensor", "target_supply_temperature")}), "gw_dev")
    assert skip == {"circuit_1_target_supply_temperature"}
    assert features == {"heating.circuits.1.temperature"}


async def test_the_readme_links_every_takeover_to_its_core_pull_request() -> None:
    """README, "Values that moved into the core integration": one row per Extras key."""
    from pathlib import Path

    readme = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")
    rows = [line for line in readme.splitlines() if line.startswith("| ") and "home-assistant/core/pull/" in line]
    for key in [*co.CORE_TAKEOVER, *co.CORE_TAKEOVER_CIRCUITS]:
        shown = key.replace("{circuit}", "N")
        assert any(f"`{shown}`" in row for row in rows), f"README has no row with a core PR link for {key}"
