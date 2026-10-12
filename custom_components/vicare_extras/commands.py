"""Write path: validated ViCare API commands.

Every command is checked against the definition the API itself ships with
each feature (`commands.<name>.isExecutable` and `params.<p>.constraints`:
min/max/stepping/enum/regEx/...) before anything is sent, so an invalid
value never reaches the boiler. One command = one API call; afterwards the
core coordinator is refreshed once (one more call) so the new state shows
up immediately.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .const import is_excluded

_LOGGER = logging.getLogger(__name__)


def _check_param(name: str, spec: dict[str, Any], value: Any) -> Any:
    ptype = spec.get("type")
    c = spec.get("constraints") or {}
    if ptype == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ServiceValidationError(f"'{name}' muss eine Zahl sein, nicht {value!r}")
        if "min" in c and value < c["min"]:
            raise ServiceValidationError(f"'{name}' = {value} ist kleiner als {c['min']}")
        if "max" in c and value > c["max"]:
            raise ServiceValidationError(f"'{name}' = {value} ist größer als {c['max']}")
        step = c.get("stepping")
        if step:
            base = c.get("min", 0)
            q = (value - base) / step
            if not math.isclose(q, round(q), abs_tol=1e-6):
                raise ServiceValidationError(
                    f"'{name}' = {value} passt nicht zur Schrittweite {step} (ab {base})"
                )
        return int(value) if float(value).is_integer() else value
    if ptype == "boolean":
        if not isinstance(value, bool):
            raise ServiceValidationError(f"'{name}' muss true/false sein, nicht {value!r}")
        return value
    if ptype == "string":
        if not isinstance(value, str):
            raise ServiceValidationError(f"'{name}' muss Text sein, nicht {value!r}")
        if "enum" in c and value not in c["enum"]:
            raise ServiceValidationError(
                f"'{name}' = {value!r} ist nicht erlaubt, möglich: {', '.join(c['enum'])}"
            )
        if "regEx" in c and not re.fullmatch(c["regEx"], value):
            raise ServiceValidationError(f"'{name}' = {value!r} hat das falsche Format")
        return value
    if ptype == "Schedule":
        return _check_schedule(name, c, value)
    # Unknown parameter type: pass through, the API validates it again.
    return value


def _to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _check_schedule(name: str, c: dict[str, Any], value: Any) -> Any:
    if not isinstance(value, dict):
        raise ServiceValidationError(f"'{name}' muss ein Zeitprogramm (Tag → Einträge) sein")
    days = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
    max_entries = c.get("maxEntries")
    resolution = c.get("resolution")
    modes = set(c.get("modes") or [])
    for day, entries in value.items():
        if day not in days:
            raise ServiceValidationError(f"Unbekannter Tag '{day}' im Zeitprogramm")
        if not isinstance(entries, list):
            raise ServiceValidationError(f"Tag '{day}': Einträge müssen eine Liste sein")
        if max_entries is not None and len(entries) > max_entries:
            raise ServiceValidationError(f"Tag '{day}': höchstens {max_entries} Einträge erlaubt")
        spans = []
        for e in entries:
            try:
                start, end = _to_minutes(e["start"]), _to_minutes(e["end"])
            except (KeyError, ValueError, AttributeError, TypeError) as err:
                raise ServiceValidationError(
                    f"Tag '{day}': Eintrag {e!r} braucht start und end als HH:MM"
                ) from err
            if modes and e.get("mode") not in modes:
                raise ServiceValidationError(
                    f"Tag '{day}': mode {e.get('mode')!r} nicht erlaubt, möglich: {', '.join(sorted(modes))}"
                )
            if resolution and (start % resolution or (end % resolution and end != 1440)):
                raise ServiceValidationError(
                    f"Tag '{day}': Zeiten müssen im {resolution}-Minuten-Raster liegen"
                )
            if end <= start and e["end"] != "24:00":
                raise ServiceValidationError(f"Tag '{day}': Ende muss nach dem Start liegen")
            spans.append((start, end))
        if not c.get("overlapAllowed", True):
            spans.sort()
            for (s1, e1), (s2, _) in zip(spans, spans[1:]):
                if s2 < e1:
                    raise ServiceValidationError(f"Tag '{day}': Einträge überlappen sich")
    return value


def validate_command(
    feature: dict[str, Any], command: str, params: dict[str, Any]
) -> dict[str, Any]:
    """Validate params against the feature's own command definition."""
    fname = feature.get("feature", "?")
    if is_excluded(fname):
        raise ServiceValidationError(f"Befehle auf '{fname}' sind gesperrt")
    commands = feature.get("commands") or {}
    if command not in commands:
        raise ServiceValidationError(
            f"'{fname}' kennt den Befehl '{command}' nicht, möglich: {', '.join(commands) or '–'}"
        )
    cdef = commands[command]
    if cdef.get("isExecutable") is False:
        raise ServiceValidationError(
            f"'{fname}.{command}' ist im aktuellen Zustand der Anlage nicht ausführbar"
        )
    pdefs = cdef.get("params") or {}
    unknown = set(params) - set(pdefs)
    if unknown:
        raise ServiceValidationError(
            f"Unbekannte Parameter für '{command}': {', '.join(sorted(unknown))}"
        )
    clean: dict[str, Any] = {}
    for pname, pspec in pdefs.items():
        if pname not in params:
            if pspec.get("required"):
                raise ServiceValidationError(f"Parameter '{pname}' fehlt für '{command}'")
            continue
        clean[pname] = _check_param(pname, pspec, params[pname])
    return clean


async def async_execute(
    hass: HomeAssistant,
    coordinator: Any,
    identifier: str,
    feature_name: str,
    command: str,
    params: dict[str, Any] | None = None,
) -> None:
    """Validate and send one command, then refresh core + own data."""
    dev = (coordinator.data or {}).get(identifier)
    core = getattr(coordinator, "core_devices", {}).get(identifier)
    if dev is None or core is None:
        raise ServiceValidationError(f"Unbekanntes ViCare-Gerät {identifier}")
    feature = dev.features.get(feature_name)
    if feature is None:
        raise ServiceValidationError(f"Das Gerät meldet kein Feature '{feature_name}'")
    clean = validate_command(feature, command, params or {})
    _LOGGER.info("ViCare command %s.%s %s", feature_name, command, clean)
    try:
        await hass.async_add_executor_job(core.api.setProperty, feature_name, command, clean)
    except Exception as err:  # noqa: BLE001 - surface any API error to the user
        raise HomeAssistantError(
            f"ViCare hat '{feature_name}.{command}' abgelehnt: {err}"
        ) from err
    if core.coordinator is not None:
        await core.coordinator.async_refresh()
    await coordinator.async_refresh()
