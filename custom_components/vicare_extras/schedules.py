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
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .commands import async_execute
from .const import DOMAIN

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$|^24:00$")


def schedule_feature(key: str) -> str:
    """Map a service-level schedule key to its feature name."""
    if key == "dhw":
        return "heating.dhw.schedule"
    if key == "circulation":
        return "heating.dhw.pumps.circulation.schedule"
    m = re.fullmatch(r"heating(?:_circuit_(\d+))?", key)
    if m:
        return f"heating.circuits.{m[1] or 1}.heating.schedule"
    raise ServiceValidationError(f"Unbekanntes Zeitprogramm '{key}'")


SCHEDULE_KEY = vol.All(cv.string, vol.Match(r"^(dhw|circulation|heating(_circuit_\d+)?)$"))
ENTRY = vol.Schema(
    {
        vol.Required("start"): vol.Match(_TIME),
        vol.Required("end"): vol.Match(_TIME),
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
                raise ServiceValidationError(
                    f"Bitte 'mode' angeben, möglich: {', '.join(modes)}"
                )
        out.append({"start": e["start"], "end": e["end"], "mode": mode})
    for i, e in enumerate(out):
        e["position"] = i
    return out


def format_day(entries: list[dict[str, Any]]) -> str:
    """Human readable: '05:30–06:30, 17:00–22:00 (comfort)'."""
    if not entries:
        return "keine Zeiten"
    parts = []
    for e in sorted(entries, key=lambda x: x.get("start", "")):
        mode = e.get("mode")
        suffix = f" ({mode})" if mode not in (None, "on", "normal") else ""
        parts.append(f"{e.get('start')}–{e.get('end')}{suffix}")
    return ", ".join(parts)


def async_register_schedule_services(hass: HomeAssistant, helpers: Any) -> None:
    """Register schedule services. `helpers` provides _coordinator/_identifier."""

    def _resolve(call: ServiceCall) -> tuple[Any, str, str, dict[str, Any]]:
        feature = schedule_feature(call.data["schedule"])
        coordinator = helpers._coordinator(hass)  # noqa: SLF001
        ident = helpers._identifier(hass, coordinator, feature, call.data.get("device_id"))  # noqa: SLF001
        fdata = coordinator.data[ident].features.get(feature)
        if fdata is None:
            raise ServiceValidationError(f"Das Gerät meldet kein Zeitprogramm '{feature}'")
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
