"""Which entities to create from a device's feature payload.

Two layers:
1. Curated specs: German names, units, device classes, enabled by default
   where useful (heat production, malfunction, messages, hygiene, holiday,
   schedules, burner stop reasons, ...).
2. Generic specs: every remaining enabled feature property that the core
   integration does not already expose becomes a disabled diagnostic
   entity, so "everything the API offers" is reachable without code
   changes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
import re
from typing import Any

from homeassistant.util import dt as dt_util

from .const import (
    BURNER_STOP_DEFAULT_ON,
    BURNER_STOP_REASONS,
    UNITS,
    is_covered,
    is_excluded,
)
from .coordinator import ExtrasDevice
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
    icon: str | None = None
    extra: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)
    # Translated entity name (translations/*.json, entity.<platform>.<key>);
    # `name` stays as the fallback and for the generic diagnostics.
    translation_key: str | None = None
    placeholders: dict[str, str] | None = None


def _value(name: str) -> Callable[[Feature], Any]:
    return lambda f: prop(f, name)


def _active(f: Feature) -> Any:
    return prop(f, "active")


def _entries_attrs(f: Feature) -> dict[str, Any]:
    return {"entries": prop(f, "entries")}


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
    for circuit, label in (("heating", "Heizung"), ("dhw", "Warmwasser")):
        fname = f"heating.heat.production.summary.{circuit}"
        if not has(fname):
            continue
        for p, plabel, total, on in (
            ("currentDay", "heute", True, True),
            ("lastSevenDays", "letzte 7 Tage", False, False),
            ("currentMonth", "diesen Monat", True, True),
            ("lastMonth", "letzten Monat", False, False),
            ("currentYear", "dieses Jahr", True, True),
            ("lastYear", "letztes Jahr", False, False),
        ):
            specs.append(
                Spec(
                    key=f"heat_production_{circuit}_{p}",
                    platform="sensor",
                    name=f"Erzeugte Wärme {label} {plabel}",
                    feature=fname,
                    value_fn=_value(p),
                    unit="kWh",
                    device_class="energy" if total else None,
                    state_class="total_increasing" if total else None,
                    enabled_default=on,
                    icon="mdi:fire",
                    attrs_fn=lambda f: {"hinweis": "Viessmann-Schätzung: Gas m³ × 10 kWh/m³, nicht gemessen"},
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
                name="Temperatur hydraulische Weiche",
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
                name=f"Vorlauf-Solltemperatur Heizkreis {m.group(1)}",
                feature=fname,
                value_fn=_value("value"),
                unit="°C",
                device_class="temperature",
                state_class="measurement",
                icon="mdi:thermometer-chevron-up",
            )
        )

    # --- internal boiler pump: running and its set output in percent
    fname = "heating.boiler.pumps.internal"
    if has(fname):
        specs.append(
            Spec(
                key="boiler_pump_internal",
                platform="binary_sensor",
                name="Interne Kesselpumpe",
                feature=fname,
                value_fn=lambda f: None if prop(f, "status") is None else prop(f, "status") == "on",
                device_class="running",
                icon="mdi:pump",
            )
        )
    fname = "heating.boiler.pumps.internal.target"
    if has(fname):
        specs.append(
            Spec(
                key="boiler_pump_internal_target",
                platform="sensor",
                name="Kesselpumpe Sollleistung",
                feature=fname,
                value_fn=_value("value"),
                unit="%",
                state_class="measurement",
                icon="mdi:pump",
            )
        )

    # --- malfunction / locks
    for fname, key, name, dclass in (
        ("device.lock.malfunction", "malfunction", "Störung", "problem"),
        ("device.lock.external", "device_external_lock", "Externe Sperre Gerät", None),
        ("heating.external.lock", "heating_external_lock", "Externe Sperre Heizung", None),
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
                    icon="mdi:lock-alert" if dclass is None else None,
                )
            )

    # --- messages (status / service / info)
    for kind, name in (
        ("status", "Statusmeldungen"),
        ("service", "Wartungsmeldungen"),
        ("info", "Infomeldungen"),
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
                    icon="mdi:message-alert-outline",
                )
            )

    # --- burner stop reasons (heating + dhw), counters
    for prefix, label, default_on in (
        ("device.timeseries.burner.stops", "Brennerstopps", True),
        ("device.timeseries.dhw.burner.stops", "Brennerstopps WW", False),
    ):
        if not has(prefix):
            continue
        for reason, rlabel in BURNER_STOP_REASONS.items():
            for suffix, slabel, sclass in (
                ("AllStops", "gesamt", "total_increasing"),
                ("LastStops", "zuletzt", None),
            ):
                pname = f"{reason}{suffix}"
                if pname not in (feats[prefix].get("properties") or {}):
                    continue
                specs.append(
                    Spec(
                        key=f"{prefix.split('.')[2]}_stops_{reason}_{suffix.lower()}",
                        platform="sensor",
                        name=f"{label}: {rlabel} ({slabel})",
                        feature=prefix,
                        value_fn=_value(pname),
                        state_class=sclass,
                        enabled_default=default_on
                        and suffix == "AllStops"
                        and reason in BURNER_STOP_DEFAULT_ON,
                        diagnostic=True,
                        icon="mdi:fire-off",
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
                name="Legionellenschutz läuft",
                feature="heating.dhw.hygiene",
                value_fn=_active,
                device_class="running",
            )
        )

    # --- holiday programs
    for fname, key, name in (
        ("heating.operating.programs.holiday", "holiday", "Urlaub"),
        ("heating.operating.programs.holidayAtHome", "holiday_at_home", "Urlaub zu Hause"),
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
                    icon="mdi:palm-tree",
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
                    name=f"Zeitprogramm Heizkreis {m[1]}",
                    feature=fname,
                    value_fn=_active,
                    attrs_fn=_schedule_attrs,
                    icon="mdi:calendar-week",
                )
            )
        m = re.fullmatch(
            r"heating\.circuits\.(\d+)\.operating\.programs\."
            r"(forcedLastFromSchedule|reducedEnergySaving|summerEco)",
            fname,
        )
        if m and has(fname):
            label = {
                "forcedLastFromSchedule": "Zwangsbetrieb nach Zeitplan",
                "reducedEnergySaving": "Energiesparbetrieb",
                "summerEco": "Sommer-Eco",
            }[m[2]]
            specs.append(
                Spec(
                    key=f"circuit_{m[1]}_program_{m[2]}",
                    platform="binary_sensor",
                    name=f"{label} Heizkreis {m[1]}",
                    feature=fname,
                    value_fn=_active,
                    attrs_fn=(lambda f: {"reason": prop(f, "reason"), "demand": prop(f, "demand")})
                    if m[2] == "reducedEnergySaving"
                    else None,
                    icon="mdi:leaf",
                )
            )
    for fname in sorted(feats):
        m = re.fullmatch(r"heating\.circuits\.(\d+)\.heating\.schedule", fname)
        label = f"Heizkreis {m[1]}" if m else {
            "heating.dhw.schedule": "Warmwasser",
            "heating.dhw.pumps.circulation.schedule": "Zirkulation",
        }.get(fname)
        if label and has(fname):
            specs.append(
                Spec(
                    key=f"{_slug(fname)}_today",
                    platform="sensor",
                    name=f"Zeitprogramm {label} heute",
                    feature=fname,
                    value_fn=_schedule_today,
                    icon="mdi:calendar-today",
                )
            )
    for fname, key, name in (
        ("heating.dhw.schedule", "dhw_schedule", "Zeitprogramm Warmwasser"),
        (
            "heating.dhw.pumps.circulation.schedule",
            "circulation_schedule",
            "Zeitprogramm Zirkulation",
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
                    icon="mdi:calendar-week",
                )
            )

    # --- boiler target temperature
    if has("heating.boiler.temperature"):
        specs.append(
            Spec(
                key="boiler_target_temperature",
                platform="sensor",
                name="Kessel-Solltemperatur",
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
                        attrs_fn=(lambda f, n=pname: {"value": prop(f, n)}),
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


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text[:255]


def _gas_energy(dev: ExtrasDevice, calorific: float | None) -> list[Spec]:
    """Gas energy in kWh = m³ x calorific value (from the gas bill).

    Note: Viessmann's own "heat production" is NOT measured - it is exactly
    gas m³ x 10 kWh/m³ (verified on a Vitodens 200, 2025 and 2026 data), so
    an efficiency ratio would always be constant. This is the real energy
    input instead.
    """
    if not calorific:
        return []
    specs: list[Spec] = []
    for circuit, label in (("heating", "Heizung"), ("dhw", "Warmwasser")):
        fname = f"heating.gas.consumption.summary.{circuit}"
        if fname not in dev.features:
            continue
        for p, plabel, total, on in (
            ("currentDay", "heute", True, True),
            ("currentMonth", "diesen Monat", True, True),
            ("lastMonth", "letzten Monat", False, False),
            ("currentYear", "dieses Jahr", True, True),
            ("lastYear", "letztes Jahr", False, False),
        ):
            specs.append(
                Spec(
                    key=f"gas_energy_{circuit}_{p}",
                    platform="sensor",
                    name=f"Gasenergie {label} {plabel}",
                    feature=fname,
                    value_fn=(
                        lambda f, p=p: None
                        if prop(f, p) is None
                        else round(prop(f, p) * calorific, 2)
                    ),
                    attrs_fn=(lambda f, p=p: {"gas_m3": prop(f, p), "calorific_value": calorific}),
                    unit="kWh",
                    device_class="energy" if total else None,
                    state_class="total_increasing" if total else None,
                    enabled_default=on,
                    icon="mdi:fire",
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


def build_specs(dev: ExtrasDevice, calorific: float | None = None) -> list[Spec]:
    """Return all entity specs for one device."""
    curated = [_translated(s) for s in _curated(dev) + _gas_energy(dev, calorific)]
    handled = {s.feature for s in curated if not s.key.startswith("gas_energy_")} | WRITABLE_FEATURES
    return curated + _generic(dev, handled)
