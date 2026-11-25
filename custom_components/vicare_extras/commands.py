"""Write path: validated ViCare API commands.

Every command is checked against the definition the API itself ships with
each feature (`commands.<name>.isExecutable` and `params.<p>.constraints`:
min/max/stepping/enum/regEx/...) before anything is sent, so an invalid
value never reaches the boiler. Parameters of a type this module does not
know are rejected. One command = one API call; the new value is shown
optimistically and confirmed by one refresh of the core coordinator
CONFIRM_DELAY seconds later (one more call).
"""

from __future__ import annotations

import asyncio
import copy
import logging
import math
import re
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .const import DOMAIN, is_excluded

_LOGGER = logging.getLogger(__name__)

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MINUTES_PER_DAY = 1440


def validation_error(key: str, **placeholders: Any) -> ServiceValidationError:
    """A translated ServiceValidationError (strings.json: exceptions.<key>)."""
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders={k: str(v) for k, v in placeholders.items()},
    )


def command_error(key: str, **placeholders: Any) -> HomeAssistantError:
    """A translated HomeAssistantError (strings.json: exceptions.<key>)."""
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders={k: str(v) for k, v in placeholders.items()},
    )


def _check_number(name: str, c: dict[str, Any], value: Any) -> Any:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise validation_error("param_not_number", name=name, value=repr(value))
    if "min" in c and value < c["min"]:
        raise validation_error("param_below_min", name=name, value=value, min=c["min"])
    if "max" in c and value > c["max"]:
        raise validation_error("param_above_max", name=name, value=value, max=c["max"])
    step = c.get("stepping")
    if step:
        base = c.get("min", 0)
        q = (value - base) / step
        if not math.isclose(q, round(q), abs_tol=1e-6):
            raise validation_error("param_step", name=name, value=value, step=step, base=base)
    return int(value) if float(value).is_integer() else value


def _check_string(name: str, c: dict[str, Any], value: Any) -> str:
    if not isinstance(value, str):
        raise validation_error("param_not_string", name=name, value=repr(value))
    if "enum" in c and value not in c["enum"]:
        raise validation_error(
            "param_not_allowed", name=name, value=repr(value), options=", ".join(map(str, c["enum"]))
        )
    if "regEx" in c:
        try:
            matched = re.fullmatch(c["regEx"], value)
        except (re.error, TypeError) as err:
            # A pattern Python cannot evaluate: refuse instead of guessing.
            raise validation_error("param_bad_regex", name=name, error=err) from err
        if not matched:
            raise validation_error("param_bad_format", name=name, value=repr(value))
    return value


def _check_param(name: str, spec: dict[str, Any], value: Any) -> Any:
    ptype = spec.get("type")
    c = spec.get("constraints") or {}
    if ptype == "number":
        return _check_number(name, c, value)
    if ptype == "boolean":
        if not isinstance(value, bool):
            raise validation_error("param_not_boolean", name=name, value=repr(value))
        return value
    if ptype == "string":
        return _check_string(name, c, value)
    if ptype == "Schedule":
        return _check_schedule(name, c, value)
    # Unknown parameter type: nothing to validate against, so do not send.
    raise validation_error("param_unsupported_type", name=name, type=ptype)


def _to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    hours, minutes = int(h), int(m)
    if not (0 <= minutes < 60 and 0 <= hours <= 24) or (hours == 24 and minutes):
        raise ValueError(hhmm)
    return hours * 60 + minutes


def _check_schedule(name: str, c: dict[str, Any], value: Any) -> Any:
    if not isinstance(value, dict):
        raise validation_error("schedule_not_dict", name=name)
    max_entries = c.get("maxEntries")
    resolution = c.get("resolution")
    modes = set(c.get("modes") or [])
    for day, entries in value.items():
        if day not in DAYS:
            raise validation_error("schedule_unknown_day", day=day)
        if not isinstance(entries, list):
            raise validation_error("schedule_entries_not_list", day=day)
        if max_entries is not None and len(entries) > max_entries:
            raise validation_error("schedule_too_many", day=day, max=max_entries)
        spans = []
        for e in entries:
            try:
                start, end = _to_minutes(e["start"]), _to_minutes(e["end"])
            except (KeyError, ValueError, AttributeError, TypeError) as err:
                raise validation_error("schedule_bad_entry", day=day, entry=repr(e)) from err
            if start >= MINUTES_PER_DAY:
                raise validation_error("schedule_start_24", day=day)
            if modes and e.get("mode") not in modes:
                raise validation_error(
                    "schedule_bad_mode", day=day, mode=repr(e.get("mode")), modes=", ".join(sorted(modes))
                )
            if resolution and (start % resolution or (end % resolution and end != MINUTES_PER_DAY)):
                raise validation_error("schedule_raster", day=day, resolution=resolution)
            if end <= start:
                raise validation_error("schedule_end_before_start", day=day)
            spans.append((start, end))
        if not c.get("overlapAllowed", True):
            spans.sort()
            for (_, e1), (s2, _) in zip(spans, spans[1:]):
                if s2 < e1:
                    raise validation_error("schedule_overlap", day=day)
    return value


def validate_command(
    feature: dict[str, Any], command: str, params: dict[str, Any]
) -> dict[str, Any]:
    """Validate params against the feature's own command definition."""
    fname = feature.get("feature", "?")
    if is_excluded(fname):
        raise validation_error("feature_blocked", feature=fname)
    commands = feature.get("commands") or {}
    if command not in commands:
        raise validation_error(
            "unknown_command", feature=fname, command=command, commands=", ".join(commands) or "-"
        )
    cdef = commands[command]
    if cdef.get("isExecutable") is False:
        raise validation_error("not_executable", feature=fname, command=command)
    pdefs = cdef.get("params") or {}
    unknown = set(params) - set(pdefs)
    if unknown:
        raise validation_error("unknown_params", command=command, params=", ".join(sorted(unknown)))
    clean: dict[str, Any] = {}
    for pname, pspec in pdefs.items():
        if pname not in params:
            if pspec.get("required"):
                raise validation_error("missing_param", param=pname, command=command)
            continue
        clean[pname] = _check_param(pname, pspec, params[pname])
    return clean


# The API keeps reporting the old value for several seconds after a command
# (measured: an immediate refresh still returned it). So: show the new value
# optimistically right away and confirm with ONE refresh after this delay.
CONFIRM_DELAY = 20
# How long the last snapshot is kept while PyViCare's cache is empty after a
# write (until the confirming fetch has arrived).
HOLD_AFTER_WRITE = CONFIRM_DELAY + 60


async def async_execute(
    hass: HomeAssistant,
    coordinator: Any,
    identifier: str,
    feature_name: str,
    command: str,
    params: dict[str, Any] | None = None,
    optimistic: dict[str, Any] | None = None,
) -> None:
    """Validate and send one command, then update optimistically.

    `optimistic` maps property names of the feature to the value they
    should show until the delayed confirmation refresh.
    """
    dev = (coordinator.data or {}).get(identifier)
    core = getattr(coordinator, "core_devices", {}).get(identifier)
    if dev is None or core is None:
        raise validation_error("unknown_device", device=identifier)
    if not dev.features:
        raise validation_error("no_current_data", device=identifier)
    feature = dev.features.get(feature_name)
    if feature is None:
        raise validation_error("feature_missing", feature=feature_name)
    clean = validate_command(feature, command, params or {})
    _LOGGER.info("ViCare command %s.%s %s", feature_name, command, clean)
    try:
        await hass.async_add_executor_job(core.api.setProperty, feature_name, command, clean)
    except Exception as err:  # noqa: BLE001 - surface any API error to the user
        raise command_error(
            "command_failed", feature=feature_name, command=command, error=err
        ) from err
    # setProperty emptied PyViCare's cache; keep showing the last values.
    hold = getattr(coordinator, "hold", None)
    if hold is not None:
        hold(HOLD_AFTER_WRITE)

    if optimistic:
        patched = copy.deepcopy(feature)
        props = patched.setdefault("properties", {})
        for pname, value in optimistic.items():
            props.setdefault(pname, {})["value"] = value
        dev.features[feature_name] = patched
        coordinator.async_set_updated_data(coordinator.data)

    schedule_confirmation(hass, coordinator, core, identifier, feature_name)


def schedule_confirmation(
    hass: HomeAssistant, coordinator: Any, core: Any, identifier: str, feature_name: str
) -> None:
    """One delayed refresh per core coordinator (gateway), however many writes."""
    core_coordinator = getattr(core, "coordinator", None)
    key = id(core_coordinator) if core_coordinator is not None else identifier
    tasks: dict[Any, asyncio.Task[None]] = coordinator.confirm_tasks
    pending = tasks.get(key)
    if pending is not None and not pending.done():
        pending.cancel()  # several writes in a row -> one confirmation

    async def _confirm() -> None:
        await asyncio.sleep(CONFIRM_DELAY)
        if core_coordinator is not None:
            # Debounced: never a second fetch if one is already due.
            await core_coordinator.async_request_refresh()
        await coordinator.async_request_refresh()

    tasks[key] = coordinator.config_entry.async_create_background_task(
        hass, _confirm(), f"{DOMAIN} confirm {feature_name}"
    )
