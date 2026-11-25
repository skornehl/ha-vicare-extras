"""Schedule services (heating circuit, DHW, DHW circulation).

The API only accepts a full week (`setSchedule` with `newSchedule`), so
day-level services read the current week, change the requested days and
send the whole week back. Constraints (max entries, 10-minute raster,
allowed modes, no overlap) come from the feature itself and are checked in
commands.py before anything is sent.
"""

from __future__ import annotations

import copy
import re
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.helpers import config_validation as cv

from .commands import DAYS, async_execute, validation_error
from .const import DOMAIN

# A slot may end at 24:00 but never start there.
_START = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_END = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$|^24:00$")
_CIRCUIT_SCHEDULE = re.compile(r"heating\.circuits\.(\d+)\.heating\.schedule")


def heating_schedules(features: dict[str, dict[str, Any]]) -> list[tuple[int, str]]:
    """Return (circuit, feature) of all enabled heating schedules, lowest first."""
    found = []
    for name, feat in features.items():
        m = _CIRCUIT_SCHEDULE.fullmatch(name)
        if m and feat.get("isEnabled", True):
            found.append((int(m[1]), name))
    return sorted(found)


def schedule_feature(key: str, features: dict[str, dict[str, Any]] | None = None) -> str | None:
    """Map a service-level schedule key to its feature name.

    `heating` without a circuit means the lowest circuit with an enabled
    schedule on that device (None if it has none).
    """
    if key == "dhw":
        return "heating.dhw.schedule"
    if key == "circulation":
        return "heating.dhw.pumps.circulation.schedule"
    if key == "heating":
        found = heating_schedules(features or {})
        return found[0][1] if found else None
    m = re.fullmatch(r"heating_circuit_(\d+)", key)
    if m:
        return f"heating.circuits.{int(m[1])}.heating.schedule"
    raise validation_error("unknown_schedule", schedule=key)


SCHEDULE_KEY = vol.All(cv.string, vol.Match(r"^(dhw|circulation|heating(_circuit_\d+)?)$"))
ENTRY = vol.Schema(
    {
        vol.Required("start"): vol.Match(_START),
        vol.Required("end"): vol.Match(_END),
        vol.Optional("mode"): cv.string,
    }
)
DAYS_LIST = vol.All(cv.ensure_list, [vol.In(DAYS)], vol.Length(min=1))

SCHEMA_SET_DAY = vol.Schema(
    {
        vol.Required("schedule"): SCHEDULE_KEY,
        vol.Required("days"): DAYS_LIST,
        vol.Required("entries"): vol.All(cv.ensure_list, [ENTRY]),
        vol.Optional("device_id"): cv.string,
    }
)
SCHEMA_COPY_DAY = vol.Schema(
    {
        vol.Required("schedule"): SCHEDULE_KEY,
        vol.Required("source_day"): vol.In(DAYS),
        vol.Required("target_days"): DAYS_LIST,
        vol.Optional("device_id"): cv.string,
    }
)
SCHEMA_RESET_DAY = vol.Schema(
    {
        vol.Required("schedule"): SCHEDULE_KEY,
        vol.Required("day"): vol.In(DAYS),
        vol.Optional("device_id"): cv.string,
    }
)
SCHEMA_SCHEDULE_ONLY = vol.Schema(
    {vol.Required("schedule"): SCHEDULE_KEY, vol.Optional("device_id"): cv.string}
)


def _constraints(feature: dict[str, Any]) -> dict[str, Any]:
    cmd = (feature.get("commands") or {}).get("setSchedule") or {}
    return ((cmd.get("params") or {}).get("newSchedule") or {}).get("constraints") or {}


def _current_week(feature: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    entries = ((feature.get("properties") or {}).get("entries") or {}).get("value") or {}
    return {d: copy.deepcopy(entries.get(d, [])) for d in DAYS}


def _normalize(day_entries: list[dict[str, Any]], constraints: dict[str, Any]) -> list[dict[str, Any]]:
    modes = constraints.get("modes") or []
    out = []
    for e in sorted(day_entries, key=lambda x: x["start"]):
        mode = e.get("mode")
        if mode is None:
            if len(modes) == 1:
                mode = modes[0]
            elif "normal" in modes:
                mode = "normal"
            else:
                raise validation_error("mode_required", modes=", ".join(modes))
        out.append({"start": e["start"], "end": e["end"], "mode": mode})
    for i, e in enumerate(out):
        e["position"] = i
    return out


def format_day(entries: list[dict[str, Any]]) -> str:
    """Human readable: '05:30–06:30, 17:00–22:00 (comfort)'.

    An empty day is a plain dash: the state is free text and cannot be
    translated by Home Assistant, so it must not depend on a language.
    """
    if not entries:
        return "–"
    parts = []
    for e in sorted(entries, key=lambda x: x.get("start", "")):
        mode = e.get("mode")
        suffix = f" ({mode})" if mode not in (None, "on", "normal") else ""
        parts.append(f"{e.get('start')}–{e.get('end')}{suffix}")
    return ", ".join(parts)


def async_register_schedule_services(hass: HomeAssistant, helpers: Any) -> None:
    """Register schedule services. `helpers` provides coordinator/resolve_device."""

    def _resolve(call: ServiceCall) -> tuple[Any, str, str, dict[str, Any]]:
        key = call.data["schedule"]
        coordinator = helpers.coordinator(hass)

        def supports(features: dict[str, dict[str, Any]]) -> bool:
            feature = schedule_feature(key, features)
            return feature is not None and feature in features

        label = schedule_feature(key) or "heating.circuits.N.heating.schedule"
        ident = helpers.resolve_device(hass, coordinator, supports, label, call.data.get("device_id"))
        features = coordinator.data[ident].features
        feature = schedule_feature(key, features)
        fdata = features.get(feature) if feature else None
        if feature is None or fdata is None:
            raise validation_error("no_schedule", schedule=key)
        return coordinator, ident, feature, fdata

    async def _send_week(coordinator, ident, feature, week) -> None:
        await async_execute(
            hass, coordinator, ident, feature, "setSchedule", {"newSchedule": week}, {"entries": week}
        )

    async def set_schedule_day(call: ServiceCall) -> None:
        coordinator, ident, feature, fdata = _resolve(call)
        c = _constraints(fdata)
        week = _current_week(fdata)
        new_day = _normalize(call.data["entries"], c)
        for day in call.data["days"]:
            week[day] = copy.deepcopy(new_day)
        await _send_week(coordinator, ident, feature, week)

    async def copy_schedule_day(call: ServiceCall) -> None:
        coordinator, ident, feature, fdata = _resolve(call)
        week = _current_week(fdata)
        source = _normalize(week[call.data["source_day"]], _constraints(fdata))
        for day in call.data["target_days"]:
            week[day] = copy.deepcopy(source)
        await _send_week(coordinator, ident, feature, week)

    async def reset_schedule_day(call: ServiceCall) -> None:
        coordinator, ident, feature, _ = _resolve(call)
        await async_execute(hass, coordinator, ident, feature, "resetDay", {"day": call.data["day"]})

    async def reset_schedule(call: ServiceCall) -> None:
        coordinator, ident, feature, _ = _resolve(call)
        await async_execute(hass, coordinator, ident, feature, "resetSchedule", {})

    async def get_schedule(call: ServiceCall) -> ServiceResponse:
        _, _, feature, fdata = _resolve(call)
        c = _constraints(fdata)
        week = _current_week(fdata)
        return {
            "feature": feature,
            "active": ((fdata.get("properties") or {}).get("active") or {}).get("value"),
            "modes": c.get("modes"),
            "default_mode": c.get("defaultMode"),
            "max_entries_per_day": c.get("maxEntries"),
            "resolution_minutes": c.get("resolution"),
            "entries": week,
            "summary": {d: format_day(week[d]) for d in DAYS},
        }

    # Normal user actions, like the entities; only execute_command is admin-only.
    hass.services.async_register(DOMAIN, "set_schedule_day", set_schedule_day, SCHEMA_SET_DAY)
    hass.services.async_register(DOMAIN, "copy_schedule_day", copy_schedule_day, SCHEMA_COPY_DAY)
    hass.services.async_register(DOMAIN, "reset_schedule_day", reset_schedule_day, SCHEMA_RESET_DAY)
    hass.services.async_register(DOMAIN, "reset_schedule", reset_schedule, SCHEMA_SCHEDULE_ONLY)
    hass.services.async_register(
        DOMAIN,
        "get_schedule",
        get_schedule,
        SCHEMA_SCHEDULE_ONLY,
        supports_response=SupportsResponse.ONLY,
    )
