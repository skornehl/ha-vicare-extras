"""Services: holiday programs, schedules and a generic, validated command service.

Registered once in async_setup (not per config entry). The holiday and
schedule services are normal user actions, like the entities of this
integration. `execute_command` can send any command, so it is admin-only and
additionally has to be allowed in the integration options.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from types import SimpleNamespace
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.service import async_register_admin_service

from .commands import async_execute, validation_error
from .const import CONF_ALLOW_RAW_COMMANDS, DOMAIN, VICARE_DOMAIN
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


def coordinator(hass: HomeAssistant) -> Any:
    """Return the coordinator of the loaded ViCare Extras entry."""
    entries = [
        e for e in hass.config_entries.async_loaded_entries(DOMAIN) if hasattr(e, "runtime_data")
    ]
    if not entries:
        raise validation_error("not_loaded")
    return entries[0].runtime_data


def resolve_device(
    hass: HomeAssistant,
    coord: Any,
    supports: Callable[[dict[str, dict[str, Any]]], bool],
    label: str,
    device_id: str | None,
) -> str:
    """Return the identifier of the device a service call targets.

    With a device_id that device is used. Without one exactly one device may
    support the call; with several the caller has to choose.
    """
    if device_id:
        device = dr.async_get(hass).async_get(device_id)
        if device is None:
            raise validation_error("unknown_device_id", device_id=device_id)
        for domain, ident in device.identifiers:
            if domain == VICARE_DOMAIN and ident in coord.data:
                return ident
        raise validation_error("not_vicare_device", device=device.name_by_user or device.name)
    matches = [i for i, d in coord.data.items() if supports(d.features)]
    if not matches:
        stale = [i for i, d in coord.data.items() if not d.features]
        if stale:
            # The device may support it, its data is just not current.
            raise validation_error("no_current_data", device=", ".join(stale))
        raise validation_error("no_device_with_feature", feature=label)
    if len(matches) > 1:
        raise validation_error("multiple_devices", feature=label, count=len(matches))
    return matches[0]


def _feature_resolver(feature: str) -> Callable[[dict[str, dict[str, Any]]], bool]:
    return lambda features: feature in features


async def _run(
    hass: HomeAssistant,
    call: ServiceCall,
    feature: str,
    command: str,
    params: dict,
    optimistic: dict | None = None,
) -> None:
    coord = coordinator(hass)
    ident = resolve_device(hass, coord, _feature_resolver(feature), feature, call.data.get("device_id"))
    await async_execute(hass, coord, ident, feature, command, params, optimistic)


def async_register_services(hass: HomeAssistant) -> None:
    """Register all services of the integration (once, from async_setup)."""

    async def set_holiday(call: ServiceCall) -> None:
        start: date = call.data["start"]
        end: date = call.data["end"]
        if end < start:
            raise validation_error("holiday_end_before_start")
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
        coord = coordinator(hass)
        ident = resolve_device(
            hass, coord, _feature_resolver(feature), feature, call.data.get("device_id")
        )
        fdata = coord.data[ident].features.get(feature) or {}
        executable = ((fdata.get("commands") or {}).get("changeEndDate") or {}).get(
            "isExecutable", True
        )
        start = ((fdata.get("properties") or {}).get("start") or {}).get("value")
        if not executable and start:
            # The API only allows changeEndDate while the program is running;
            # for a scheduled (not yet started) one, re-schedule it with the
            # same start and the new end instead.
            if end < start:
                raise validation_error("holiday_end_before_start")
            await async_execute(
                hass, coord, ident, feature, "schedule",
                {"start": start, "end": end}, {"start": start, "end": end},
            )
            return
        await async_execute(
            hass, coord, ident, feature, "changeEndDate", {"end": end}, {"end": end}
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
        coord = coordinator(hass)
        if not coord.config_entry.options.get(CONF_ALLOW_RAW_COMMANDS, False):
            raise validation_error("raw_commands_disabled")
        await _run(hass, call, call.data["feature"], call.data["command"], call.data["params"])

    hass.services.async_register(DOMAIN, "set_holiday", set_holiday, SCHEMA_SET_HOLIDAY)
    hass.services.async_register(
        DOMAIN, "change_holiday_end", change_holiday_end, SCHEMA_CHANGE_END
    )
    hass.services.async_register(DOMAIN, "cancel_holiday", cancel_holiday, SCHEMA_CANCEL)
    async_register_admin_service(hass, DOMAIN, "execute_command", execute_command, SCHEMA_EXECUTE)
    async_register_schedule_services(
        hass, SimpleNamespace(coordinator=coordinator, resolve_device=resolve_device)
    )
