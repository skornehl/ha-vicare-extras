"""Entity specs: gas energy units, attributes, translations and icons."""

from __future__ import annotations

import json
from pathlib import Path
import re

from custom_components.vicare_extras.descriptions import bounded, build_specs

from .common import device, feature, num

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "vicare_extras"
GAS = "heating.gas.consumption.summary.heating"
PERIODS = ("currentDay", "currentMonth", "lastMonth", "currentYear", "lastYear")


def _gas(unit: str | None) -> dict:
    return feature(GAS, {p: num(10, unit) for p in PERIODS})


def _gas_specs(unit: str | None, calorific: float | None = 11.5):
    return {s.key: s for s in build_specs(device([_gas(unit)]), calorific) if s.key.startswith("gas_energy")}


async def test_gas_energy_from_cubic_meters() -> None:
    specs = _gas_specs("cubicMeter")
    assert len(specs) == 5
    spec = specs["gas_energy_heating_currentDay"]
    assert spec.value_fn(_gas("cubicMeter")) == 115.0
    assert spec.unit == "kWh"
    assert spec.state_class == "total_increasing"


async def test_gas_energy_never_multiplies_kwh() -> None:
    assert _gas_specs("kilowattHour") == {}
    assert _gas_specs(None) == {}
    # Unit changes to kWh after setup: unknown instead of an ~11x value.
    spec = _gas_specs("cubicMeter")["gas_energy_heating_currentDay"]
    assert spec.value_fn(_gas("kilowattHour")) is None


async def test_no_calorific_no_gas_energy() -> None:
    assert _gas_specs("cubicMeter", None) == {}


async def test_attributes_are_english() -> None:
    name = "heating.heat.production.summary.heating"
    feat = feature(name, {"currentDay": num(5, "kilowattHour")})
    spec = next(s for s in build_specs(device([feat])) if s.key == "heat_production_heating_currentDay")
    assert spec.attrs_fn(feat) == {"note": "Viessmann estimate: gas m³ × 10 kWh/m³, not measured"}


async def test_message_entries_are_bounded() -> None:
    entries = [{"errorCode": f"S.{i}", "timestamp": "2026-11-25T00:00:00Z"} for i in range(50)]
    feat = feature("device.messages.status.raw", {"entries": {"type": "array", "value": entries}})
    spec = next(s for s in build_specs(device([feat])) if s.key == "messages_status")
    assert spec.value_fn(feat) == 50
    attrs = spec.attrs_fn(feat)
    assert len(attrs["entries"]) == 20
    assert attrs["entries_total"] == 50


async def test_bounded_drops_huge_values() -> None:
    assert bounded([1, 2]) == ([1, 2], False)
    value, truncated = bounded({"blob": "x" * 20000})
    assert value is None and truncated


def _all_features() -> list[dict]:
    stops = {f"{r}{s}": num() for r in ("normalStop", "time") for s in ("AllStops", "LastStops")}
    schedule = {"active": {"type": "boolean", "value": True}, "entries": {"type": "Schedule", "value": {}}}
    active = {"active": {"type": "boolean", "value": False}}
    names_active = (
        "device.lock.malfunction", "device.lock.external", "heating.external.lock", "heating.dhw.hygiene",
        "heating.operating.programs.holiday", "heating.operating.programs.holidayAtHome",
        "heating.circuits.1.operating.programs.forcedLastFromSchedule",
        "heating.circuits.1.operating.programs.reducedEnergySaving",
        "heating.circuits.1.operating.programs.summerEco",
    )
    return [
        feature("heating.heat.production.summary.dhw", {p: num(1, "kilowattHour") for p in PERIODS}),
        _gas("cubicMeter"),
        feature("heating.sensors.temperature.hydraulicSeparator", {"value": num(40, "celsius")}),
        feature("heating.circuits.1.temperature", {"value": num(40, "celsius")}),
        feature("heating.boiler.pumps.internal", {"status": {"type": "string", "value": "on"}}),
        feature("heating.boiler.pumps.internal.target", {"value": num(50, "percent")}),
        feature("device.messages.info.raw", {"entries": {"type": "array", "value": []}}),
        feature("device.timeseries.burner.stops", stops),
        feature("heating.circuits.1.heating.schedule", schedule),
        feature("heating.dhw.schedule", schedule),
        feature("heating.dhw.pumps.circulation.schedule", schedule),
        feature("heating.boiler.temperature", {"value": num(50, "celsius")}),
        *(feature(n, active) for n in names_active),
    ]


async def test_every_curated_entity_is_translated_and_has_valid_icon() -> None:
    strings = json.loads((COMPONENT / "strings.json").read_text())["entity"]
    icons = json.loads((COMPONENT / "icons.json").read_text())["entity"]
    specs = [s for s in build_specs(device(_all_features()), 11.5) if s.translation_key]
    assert specs
    for spec in specs:
        assert spec.translation_key in strings[spec.platform], spec.translation_key
    for platform, keys in icons.items():
        for key in keys:
            assert key in strings[platform], f"icon for unknown {platform}.{key}"


async def test_every_translation_file_has_all_keys() -> None:
    def flat(d, prefix=""):
        out = set()
        for k, v in d.items():
            out |= flat(v, f"{prefix}{k}.") if isinstance(v, dict) else {prefix + k}
        return out

    reference = flat(json.loads((COMPONENT / "strings.json").read_text()))
    for path in (COMPONENT / "translations").glob("*.json"):
        assert flat(json.loads(path.read_text())) == reference, path.name


async def test_every_raised_exception_is_translated() -> None:
    strings = json.loads((COMPONENT / "strings.json").read_text())["exceptions"]
    used = set()
    for path in COMPONENT.glob("*.py"):
        used |= set(re.findall(r'(?:validation_error|command_error)\(\s*"([a-z0-9_]+)"', path.read_text()))
    assert used
    assert used <= set(strings), used - set(strings)
