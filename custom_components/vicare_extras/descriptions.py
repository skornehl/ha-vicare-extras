"""Which entities to create from a device's feature payload.

Two layers:
1. Curated specs: translated names, units, device classes, enabled by default
   where useful (heat production, malfunction, messages, hygiene, holiday,
   schedules, burner stop reasons, ...).
2. Generic specs: every remaining enabled feature property that the core
   integration does not already expose becomes a disabled diagnostic
   entity, so "everything the API offers" is reachable without code
   changes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
import json
import re
from typing import Any

from homeassistant.util import dt as dt_util

from .const import (
    BURNER_RUNTIME,
    BURNER_STOP_DEFAULT_ON,
    BURNER_STOP_REASONS,
    MAX_LIST_ATTRIBUTE,
    UNITS,
    is_covered,
    is_excluded,
)
from .coordinator import ExtrasDevice
from .core_overlap import NO_OVERLAP, Overlap
from .schedules import format_day

# Features represented by writable entities (switch/number/select/time).
WRITABLE_FEATURES = {
    "heating.dhw.hygiene",
    "heating.dhw.hygiene.trigger",
    "heating.dhw.temperature.hygiene",
}

Feature = dict[str, Any]


def prop(feature: Feature, name: str) -> Any:
    """Return the raw value of one property, or None."""
    p = (feature.get("properties") or {}).get(name)
    return p.get("value") if isinstance(p, dict) else None


def prop_unit(feature: Feature, name: str) -> str | None:
    p = (feature.get("properties") or {}).get(name)
    unit = p.get("unit") if isinstance(p, dict) else None
    return UNITS.get(unit, unit) if unit else None


@dataclass(frozen=True)
class Spec:
    """One entity to create."""

    key: str  # unique-id suffix
    platform: str  # "sensor" | "binary_sensor"
    name: str
    feature: str
    value_fn: Callable[[Feature], Any]
    attrs_fn: Callable[[Feature], dict[str, Any]] | None = None
    unit: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    enabled_default: bool = True
    diagnostic: bool = False
    # Translated entity name (translations/*.json, entity.<platform>.<key>);
    # `name` is the English fallback and the name of generic diagnostics.
    # Icons come from icons.json by the same key.
    translation_key: str | None = None
    placeholders: dict[str, str] | None = None


def _value(name: str) -> Callable[[Feature], Any]:
    return lambda f: prop(f, name)


def _active(f: Feature) -> Any:
    return prop(f, "active")


# Attributes above ~16 KiB are not recorded; stay well below.
_MAX_ATTRIBUTE_JSON = 8192


def bounded(value: Any) -> tuple[Any, bool]:
    """Return (value, truncated) with lists cut to MAX_LIST_ATTRIBUTE items.

    Anything still too large for the recorder is dropped (None, True).
    """
    truncated = False
    if isinstance(value, list) and len(value) > MAX_LIST_ATTRIBUTE:
        value, truncated = value[:MAX_LIST_ATTRIBUTE], True
    try:
        size = len(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return None, True
    if size > _MAX_ATTRIBUTE_JSON:
        return None, True
    return value, truncated


def _entries_attrs(f: Feature) -> dict[str, Any]:
    entries, truncated = bounded(prop(f, "entries"))
    attrs: dict[str, Any] = {"entries": entries}
    if truncated:
        total = prop(f, "entries")
        attrs["entries_total"] = len(total) if isinstance(total, list) else None
    return attrs


def _estimate_note(f: Feature) -> dict[str, Any]:
    return {"note": "Viessmann estimate: gas m³ × 10 kWh/m³, not measured"}


def _count_entries(f: Feature) -> int | None:
    entries = prop(f, "entries")
    return len(entries) if isinstance(entries, list) else None


def _schedule_attrs(f: Feature) -> dict[str, Any]:
    return {"entries": prop(f, "entries")}


def _schedule_today(f: Feature) -> str | None:
    entries = prop(f, "entries")
    if not isinstance(entries, dict):
        return None
    day = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][dt_util.now().weekday()]
    return format_day(entries.get(day) or [])


def _holiday_attrs(f: Feature) -> dict[str, Any]:
    return {"start": prop(f, "start") or None, "end": prop(f, "end") or None}


def _curated(dev: ExtrasDevice) -> list[Spec]:
    feats = dev.features
    specs: list[Spec] = []

    def has(name: str) -> bool:
        return name in feats and feats[name].get("isEnabled", True)

    # --- heat produced (kWh). Viessmann derives this as gas m³ x 10 kWh/m³,
    # it is an estimate, not a measurement (see _gas_energy).
    for circuit, label in (("heating", "heating"), ("dhw", "hot water")):
        fname = f"heating.heat.production.summary.{circuit}"
        if not has(fname):
            continue
        for p, plabel, total, on in (
            ("currentDay", "today", True, True),
            ("lastSevenDays", "last 7 days", False, False),
            ("currentMonth", "this month", True, True),
            ("lastMonth", "last month", False, False),
            ("currentYear", "this year", True, True),
            ("lastYear", "last year", False, False),
        ):
            specs.append(
                Spec(
                    key=f"heat_production_{circuit}_{p}",
                    platform="sensor",
                    name=f"Heat produced {label} {plabel}",
                    feature=fname,
                    value_fn=_value(p),
                    unit="kWh",
                    device_class="energy" if total else None,
                    state_class="total_increasing" if total else None,
                    enabled_default=on,
                    attrs_fn=_estimate_note,
                )
            )

    # --- hydraulic separator: the core entity exists but PyViCare only has
    # the getter on the fuel-cell class, so it stays empty on gas boilers.
    fname = "heating.sensors.temperature.hydraulicSeparator"
    if has(fname):
        specs.append(
            Spec(
                key="hydraulic_separator_temperature",
                platform="sensor",
                name="Hydraulic separator temperature",
                feature=fname,
                value_fn=_value("value"),
                unit="°C",
                device_class="temperature",
                state_class="measurement",
            )
        )

    # --- target supply temperature per heating circuit. The core integration
    # only calls PyViCare's getTargetTemperature() on the device object
    # (GLOBAL_SENSORS), where it has no circuit, so on gas boilers the
    # circuit's flow set point is never exposed. Same for the internal pump.
    for fname in sorted(feats):
        m = re.fullmatch(r"heating\.circuits\.([^.]+)\.temperature", fname)
        if not m or not has(fname):
            continue
        specs.append(
            Spec(
                key=f"circuit_{m.group(1)}_target_supply_temperature",
                platform="sensor",
                name=f"Target supply temperature circuit {m.group(1)}",
                feature=fname,
                value_fn=_value("value"),
                unit="°C",
                device_class="temperature",
                state_class="measurement",
            )
        )

    # --- internal boiler pump: running and its set output in percent
    fname = "heating.boiler.pumps.internal"
    if has(fname):
        specs.append(
            Spec(
                key="boiler_pump_internal",
                platform="binary_sensor",
                name="Internal boiler pump",
                feature=fname,
                value_fn=lambda f: None if prop(f, "status") is None else prop(f, "status") == "on",
                device_class="running",
            )
        )
    fname = "heating.boiler.pumps.internal.target"
    if has(fname):
        specs.append(
            Spec(
                key="boiler_pump_internal_target",
                platform="sensor",
                name="Boiler pump target output",
                feature=fname,
                value_fn=_value("value"),
                unit="%",
                state_class="measurement",
            )
        )

    # --- malfunction / locks
    for fname, key, name, dclass in (
        ("device.lock.malfunction", "malfunction", "Malfunction", "problem"),
        ("device.lock.external", "device_external_lock", "External lock device", None),
        ("heating.external.lock", "heating_external_lock", "External lock heating", None),
    ):
        if has(fname):
            specs.append(
                Spec(
                    key=key,
                    platform="binary_sensor",
                    name=name,
                    feature=fname,
                    value_fn=_active,
                    device_class=dclass,
                )
            )

    # --- messages (status / service / info)
    for kind, name in (
        ("status", "Status messages"),
        ("service", "Service messages"),
        ("info", "Info messages"),
    ):
        fname = f"device.messages.{kind}.raw"
        if has(fname):
            specs.append(
                Spec(
                    key=f"messages_{kind}",
                    platform="sensor",
                    name=name,
                    feature=fname,
                    value_fn=_count_entries,
                    attrs_fn=_entries_attrs,
                    state_class="measurement",
                )
            )

    # --- burner stops (heating + dhw): one counter per stop reason, plus the
    # burner runtime over the same stops. "AllStops" counts since the counter
    # reset; "LastStops" covers the most recent stops (46 on a Vitodens 200),
    # not a time window, so those values can fall.
    for prefix, label, rt_label, default_on in (
        ("device.timeseries.burner.stops", "Burner stops", "Burner runtime", True),
        ("device.timeseries.dhw.burner.stops", "Burner stops hot water", "Burner runtime hot water", False),
    ):
        if not has(prefix):
            continue
        props = feats[prefix].get("properties") or {}
        key_base = f"{prefix.split('.')[2]}_stops"
        windows = (
            ("AllStops", "total", "total_increasing"),
            ("LastStops", "last stops", None),
        )
        for reason, rlabel in BURNER_STOP_REASONS.items():
            for suffix, slabel, sclass in windows:
                pname = f"{reason}{suffix}"
                if pname not in props:
                    continue
                specs.append(
                    Spec(
                        key=f"{key_base}_{reason}_{suffix.lower()}",
                        platform="sensor",
                        name=f"{label}: {rlabel} ({slabel})",
                        feature=prefix,
                        value_fn=_value(pname),
                        state_class=sclass,
                        enabled_default=default_on
                        and suffix == "AllStops"
                        and reason in BURNER_STOP_DEFAULT_ON,
                        diagnostic=True,
                    )
                )
        # `time{All,Last}Stops`: burner runtime in seconds, not a stop reason.
        # The key keeps "time" so existing entities keep their unique_id.
        for suffix, slabel, sclass in windows:
            pname = f"{BURNER_RUNTIME}{suffix}"
            if pname not in props:
                continue
            specs.append(
                Spec(
                    key=f"{key_base}_{BURNER_RUNTIME}_{suffix.lower()}",
                    platform="sensor",
                    name=f"{rt_label} ({slabel})",
                    feature=prefix,
                    value_fn=_value(pname),
                    unit="s",
                    device_class="duration",
                    state_class=sclass,
                    enabled_default=False,
                    diagnostic=True,
                )
            )

    # --- DHW hygiene (legionella protection): enabled/temperature/time are
    # writable entities (switch, number, select, time); only "running" is
    # read-only here.
    if has("heating.dhw.hygiene"):
        specs.append(
            Spec(
                key="dhw_hygiene_active",
                platform="binary_sensor",
                name="Legionella protection running",
                feature="heating.dhw.hygiene",
                value_fn=_active,
                device_class="running",
            )
        )

    # --- holiday programs
    for fname, key, name in (
        ("heating.operating.programs.holiday", "holiday", "Holiday"),
        ("heating.operating.programs.holidayAtHome", "holiday_at_home", "Holiday at home"),
    ):
        if has(fname):
            specs.append(
                Spec(
                    key=key,
                    platform="binary_sensor",
                    name=name,
                    feature=fname,
                    value_fn=_active,
                    attrs_fn=_holiday_attrs,
                )
            )

    # --- schedules and programs per circuit
    for fname in sorted(feats):
        m = re.fullmatch(r"heating\.circuits\.(\d+)\.heating\.schedule", fname)
        if m and has(fname):
            specs.append(
                Spec(
                    key=f"circuit_{m[1]}_schedule",
                    platform="binary_sensor",
                    name=f"Schedule circuit {m[1]}",
                    feature=fname,
                    value_fn=_active,
                    attrs_fn=_schedule_attrs,
                )
            )
        m = re.fullmatch(
            r"heating\.circuits\.(\d+)\.operating\.programs\."
            r"(forcedLastFromSchedule|reducedEnergySaving|summerEco)",
            fname,
        )
        if m and has(fname):
            label = {
                "forcedLastFromSchedule": "Forced operation by schedule",
                "reducedEnergySaving": "Energy saving mode",
                "summerEco": "Summer eco",
            }[m[2]]
            specs.append(
                Spec(
                    key=f"circuit_{m[1]}_program_{m[2]}",
                    platform="binary_sensor",
                    name=f"{label} circuit {m[1]}",
                    feature=fname,
                    value_fn=_active,
                    attrs_fn=(lambda f: {"reason": prop(f, "reason"), "demand": prop(f, "demand")})
                    if m[2] == "reducedEnergySaving"
                    else None,
                )
            )
    for fname in sorted(feats):
        m = re.fullmatch(r"heating\.circuits\.(\d+)\.heating\.schedule", fname)
        label = f"circuit {m[1]}" if m else {
            "heating.dhw.schedule": "hot water",
            "heating.dhw.pumps.circulation.schedule": "circulation",
        }.get(fname)
        if label and has(fname):
            specs.append(
                Spec(
                    key=f"{_slug(fname)}_today",
                    platform="sensor",
                    name=f"Schedule {label} today",
                    feature=fname,
                    value_fn=_schedule_today,
                )
            )
    for fname, key, name in (
        ("heating.dhw.schedule", "dhw_schedule", "Schedule hot water"),
        (
            "heating.dhw.pumps.circulation.schedule",
            "circulation_schedule",
            "Schedule circulation",
        ),
    ):
        if has(fname):
            specs.append(
                Spec(
                    key=key,
                    platform="binary_sensor",
                    name=name,
                    feature=fname,
                    value_fn=_active,
                    attrs_fn=_schedule_attrs,
                )
            )

    # --- boiler target temperature
    if has("heating.boiler.temperature"):
        specs.append(
            Spec(
                key="boiler_target_temperature",
                platform="sensor",
                name="Boiler target temperature",
                feature="heating.boiler.temperature",
                value_fn=_value("value"),
                unit="°C",
                device_class="temperature",
                state_class="measurement",
            )
        )
    return specs


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _generic(dev: ExtrasDevice, handled: set[str]) -> list[Spec]:
    specs: list[Spec] = []
    for fname, feat in sorted(dev.features.items()):
        if not feat.get("isEnabled", True) or fname in handled:
            continue
        if is_covered(fname) or is_excluded(fname):
            continue
        for pname, p in (feat.get("properties") or {}).items():
            if not isinstance(p, dict):
                continue
            ptype = p.get("type")
            name = f"{fname} {pname}"
            key = f"x_{_slug(fname)}_{_slug(pname)}"
            if ptype == "boolean":
                specs.append(
                    Spec(
                        key=key,
                        platform="binary_sensor",
                        name=name,
                        feature=fname,
                        value_fn=_value(pname),
                        enabled_default=False,
                        diagnostic=True,
                    )
                )
                continue
            unit = prop_unit(feat, pname)
            if ptype == "number":
                dclass = {"°C": "temperature", "bar": "pressure"}.get(unit or "")
                specs.append(
                    Spec(
                        key=key,
                        platform="sensor",
                        name=name,
                        feature=fname,
                        value_fn=_value(pname),
                        unit=unit,
                        device_class=dclass,
                        state_class="measurement" if dclass else None,
                        enabled_default=False,
                        diagnostic=True,
                    )
                )
            elif ptype in ("array", "object", "Schedule") or isinstance(
                p.get("value"), (list, dict)
            ):
                specs.append(
                    Spec(
                        key=key,
                        platform="sensor",
                        name=name,
                        feature=fname,
                        value_fn=(lambda f, n=pname: len(prop(f, n) or [])),
                        attrs_fn=(lambda f, n=pname: _value_attrs(prop(f, n))),
                        enabled_default=False,
                        diagnostic=True,
                    )
                )
            else:
                specs.append(
                    Spec(
                        key=key,
                        platform="sensor",
                        name=name,
                        feature=fname,
                        value_fn=(lambda f, n=pname: _as_text(prop(f, n))),
                        enabled_default=False,
                        diagnostic=True,
                    )
                )
    return specs


def _value_attrs(value: Any) -> dict[str, Any]:
    value, truncated = bounded(value)
    return {"value": value, "truncated": True} if truncated else {"value": value}


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text[:255]


GAS_UNIT = "m³"


def _gas_kwh(feature: Feature, name: str, calorific: float) -> float | None:
    """m³ x calorific value - only when the API reports the value in m³."""
    value = prop(feature, name)
    if value is None or prop_unit(feature, name) != GAS_UNIT:
        return None
    return round(value * calorific, 2)


def _gas_energy(dev: ExtrasDevice, calorific: float | None) -> list[Spec]:
    """Gas energy in kWh = m³ x calorific value (from the gas bill).

    Note: Viessmann's own "heat production" is NOT measured - it is exactly
    gas m³ x 10 kWh/m³ (verified on a Vitodens 200, 2025 and 2026 data), so
    an efficiency ratio would always be constant. This is the real energy
    input instead.

    Only created for values the API reports in m³. Devices that already
    report gas in kWh get nothing here: the core integration shows those, and
    multiplying them would give values ~11x too high.
    """
    if not calorific:
        return []
    specs: list[Spec] = []
    for circuit, label in (("heating", "heating"), ("dhw", "hot water")):
        fname = f"heating.gas.consumption.summary.{circuit}"
        feature = dev.features.get(fname)
        if feature is None or not feature.get("isEnabled", True):
            continue
        for p, plabel, total, on in (
            ("currentDay", "today", True, True),
            ("currentMonth", "this month", True, True),
            ("lastMonth", "last month", False, False),
            ("currentYear", "this year", True, True),
            ("lastYear", "last year", False, False),
        ):
            if prop_unit(feature, p) != GAS_UNIT:
                continue
            specs.append(
                Spec(
                    key=f"gas_energy_{circuit}_{p}",
                    platform="sensor",
                    name=f"Gas energy {label} {plabel}",
                    feature=fname,
                    value_fn=(lambda f, p=p: _gas_kwh(f, p, calorific)),
                    attrs_fn=(lambda f, p=p: {"gas_m3": prop(f, p), "calorific_value": calorific}),
                    unit="kWh",
                    device_class="energy" if total else None,
                    state_class="total_increasing" if total else None,
                    enabled_default=on,
                )
            )
    return specs


_CIRCUIT_KEY = re.compile(r"circuit_(\d+)_(.+)")
_SCHEDULE_TODAY = {
    "heating_dhw_schedule_today": "schedule_today_dhw",
    "heating_dhw_pumps_circulation_schedule_today": "schedule_today_circulation",
}


def _translated(spec: Spec) -> Spec:
    """Give a curated spec its translation key; circuit numbers become a placeholder."""
    m = _CIRCUIT_KEY.fullmatch(spec.key)
    if m:
        return replace(spec, translation_key=f"circuit_{m[2].lower()}", placeholders={"circuit": m[1]})
    m = re.fullmatch(r"heating_circuits_(\d+)_heating_schedule_today", spec.key)
    if m:
        return replace(spec, translation_key="schedule_today_circuit", placeholders={"circuit": m[1]})
    return replace(spec, translation_key=_SCHEDULE_TODAY.get(spec.key, spec.key.lower()))


def build_specs(
    dev: ExtrasDevice, calorific: float | None = None, overlap: Overlap = NO_OVERLAP
) -> list[Spec]:
    """Return all entity specs for one device.

    Entities the core integration already provides for this device (overlap,
    see core_overlap.py) are left out, and their features are kept out of the
    generic diagnostics too.
    """
    skip, core_features = overlap
    curated = [
        _translated(s)
        for s in _curated(dev) + _gas_energy(dev, calorific)
        if s.key not in skip
    ]
    handled = (
        {s.feature for s in curated if not s.key.startswith("gas_energy_")}
        | WRITABLE_FEATURES
        | core_features
    )
    return curated + _generic(dev, handled)
