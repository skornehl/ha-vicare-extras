"""Services: holiday programs plus a generic, validated command service."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .commands import async_execute
from .const import DOMAIN, VICARE_DOMAIN
from .schedules import async_register_schedule_services

HOLIDAY_PROGRAMS = {
    "holiday": "heating.operating.programs.holiday",
    "holiday_at_home": "heating.operating.programs.holidayAtHome",
}

_DEVICE = vol.Optional("device_id")
SCHEMA_SET_HOLIDAY = vol.Schema(
    {
        vol.Required("program"): vol.In(HOLIDAY_PROGRAMS),
        vol.Required("start"): cv.date,
        vol.Required("end"): cv.date,
        _DEVICE: cv.string,
    }
)
SCHEMA_CHANGE_END = vol.Schema(
    {
        vol.Required("program"): vol.In(HOLIDAY_PROGRAMS),
        vol.Required("end"): cv.date,
        _DEVICE: cv.string,
    }
)
SCHEMA_CANCEL = vol.Schema(
    {vol.Required("program"): vol.In(HOLIDAY_PROGRAMS), _DEVICE: cv.string}
)
SCHEMA_EXECUTE = vol.Schema(
    {
        vol.Required("feature"): cv.string,
        vol.Required("command"): cv.string,
        vol.Optional("params", default={}): dict,
        _DEVICE: cv.string,
    }
)


def _coordinator(hass: HomeAssistant) -> Any:
    entries = [
        e for e in hass.config_entries.async_loaded_entries(DOMAIN) if hasattr(e, "runtime_data")
    ]
    if not entries:
        raise ServiceValidationError("ViCare Extras ist nicht geladen")
    return entries[0].runtime_data


def _identifier(hass: HomeAssistant, coordinator: Any, feature: str, device_id: str | None) -> str:
    if device_id:
        device = dr.async_get(hass).async_get(device_id)
        if device is None:
            raise ServiceValidationError(f"Unbekanntes Gerät {device_id}")
        for domain, ident in device.identifiers:
            if domain == VICARE_DOMAIN and ident in coordinator.data:
                return ident
        raise ServiceValidationError(f"{device.name} ist kein ViCare-Gerät")
    matches = [i for i, d in coordinator.data.items() if feature in d.features]
    if not matches:
        raise ServiceValidationError(f"Kein ViCare-Gerät meldet '{feature}'")
    return matches[0]


async def _run(
    hass: HomeAssistant,
    call: ServiceCall,
    feature: str,
    command: str,
    params: dict,
    optimistic: dict | None = None,
) -> None:
    coordinator = _coordinator(hass)
    ident = _identifier(hass, coordinator, feature, call.data.get("device_id"))
    await async_execute(hass, coordinator, ident, feature, command, params, optimistic)


def async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, "execute_command"):
        return

    async def set_holiday(call: ServiceCall) -> None:
        start: date = call.data["start"]
        end: date = call.data["end"]
        if end < start:
            raise ServiceValidationError("Das Ende liegt vor dem Beginn")
        await _run(
            hass,
            call,
            HOLIDAY_PROGRAMS[call.data["program"]],
            "schedule",
            {"start": start.isoformat(), "end": end.isoformat()},
            {"start": start.isoformat(), "end": end.isoformat()},
        )

    async def change_holiday_end(call: ServiceCall) -> None:
        feature = HOLIDAY_PROGRAMS[call.data["program"]]
        end = call.data["end"].isoformat()
        coordinator = _coordinator(hass)
        ident = _identifier(hass, coordinator, feature, call.data.get("device_id"))
        fdata = coordinator.data[ident].features.get(feature) or {}
        executable = ((fdata.get("commands") or {}).get("changeEndDate") or {}).get(
            "isExecutable", True
        )
        start = ((fdata.get("properties") or {}).get("start") or {}).get("value")
        if not executable and start:
            # The API only allows changeEndDate while the program is running;
            # for a scheduled (not yet started) one, re-schedule it with the
            # same start and the new end instead.
            if end < start:
                raise ServiceValidationError("Das neue Ende liegt vor dem Beginn")
            await async_execute(
                hass, coordinator, ident, feature, "schedule",
                {"start": start, "end": end}, {"start": start, "end": end},
            )
            return
        await async_execute(
            hass, coordinator, ident, feature, "changeEndDate", {"end": end}, {"end": end}
        )

    async def cancel_holiday(call: ServiceCall) -> None:
        await _run(
            hass,
            call,
            HOLIDAY_PROGRAMS[call.data["program"]],
            "unschedule",
            {},
            {"active": False, "start": "", "end": ""},
        )

    async def execute_command(call: ServiceCall) -> None:
        await _run(hass, call, call.data["feature"], call.data["command"], call.data["params"])

    hass.services.async_register(DOMAIN, "set_holiday", set_holiday, SCHEMA_SET_HOLIDAY)
    hass.services.async_register(DOMAIN, "change_holiday_end", change_holiday_end, SCHEMA_CHANGE_END)
    hass.services.async_register(DOMAIN, "cancel_holiday", cancel_holiday, SCHEMA_CANCEL)
    hass.services.async_register(DOMAIN, "execute_command", execute_command, SCHEMA_EXECUTE)
    async_register_schedule_services(
        hass, SimpleNamespace(_coordinator=_coordinator, _identifier=_identifier)
    )
