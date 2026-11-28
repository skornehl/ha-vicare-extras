"""Weiche: skip what the installed core `vicare` integration already provides.

Some values started in ViCare Extras and later moved into the core
integration (for example the internal boiler pump, home-assistant/core#193054,
HA 2026.12). An Extras entity is only left out when both hold:

1. the loaded `vicare` integration (core or a custom copy) defines the entity
   description key (read from its platform modules), and
2. the core integration really created that entity for the device and it is
   enabled, that is the entity registry has a `vicare` entity with the core
   unique_id on it that is not disabled. A core entity that is disabled (many
   diagnostic ones are by default) leaves the Extras entity in place: better
   a duplicate than a missing value. Once it is enabled, the next start of
   Extras leaves its own out.

Then the curated Extras entity is not created and its feature is not offered
as a generic diagnostic either, so nothing shows up twice. With an older core,
or a device the core does not support the value for, everything stays.

An Extras entity that was in use is removed from the entity registry and
listed in the repair issue `core_takeover`. Its fix (repairs.py) gives the
core entity the old entity ID. Generic diagnostics are not listed: their
properties do not map to one core entity, and they are disabled by default.

A value of a heating circuit (CORE_TAKEOVER_CIRCUITS) is checked per circuit:
the core names its entity `<device>-<key>-<circuit>`, and only the circuits
it created are left out of Extras.

Limitation: description keys are collected from all description tuples of a
platform module, not per description group (device, circuit, burner, ...).
That is harmless because of check 2: the registry decides per device.

Every entry names the core pull request that adds the value; the README lists
them in "Values that moved into the core integration". An entry for a pull
request that is not merged yet changes nothing until a Home Assistant release
has the description key and creates the entity.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
import json
import logging
import re
from types import ModuleType
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.loader import async_get_integration

from .const import DOMAIN, VICARE_DOMAIN

_LOGGER = logging.getLogger(__name__)

# Repair issue for Extras entities the core took over, fixed in repairs.py.
ISSUE_CORE_TAKEOVER = "core_takeover"
DOCUMENTATION_URL = "https://github.com/skornehl/ha-vicare-extras"

CORE_PLATFORMS = ("sensor", "binary_sensor")

# Curated Extras key -> (core platform, core description key, features the
# core now reads). The features are also kept out of the generic layer.
CORE_TAKEOVER: dict[str, tuple[str, str, tuple[str, ...]]] = {
    # home-assistant/core#193054 (merged, HA 2026.12)
    "boiler_pump_internal": (
        "binary_sensor",
        "boiler_internal_pump_active",
        ("heating.boiler.pumps.internal",),
    ),
    "boiler_pump_internal_target": (
        "sensor",
        "boiler_internal_pump_target_modulation",
        ("heating.boiler.pumps.internal.target",),
    ),
    # home-assistant/core#189621 (merged, HA 2026.12): the start and end are
    # sensors of their own in the core.
    "holiday": (
        "binary_sensor",
        "holiday_active",
        ("heating.operating.programs.holiday",),
    ),
    "holiday_at_home": (
        "binary_sensor",
        "holiday_at_home_active",
        ("heating.operating.programs.holidayAtHome",),
    ),
    # home-assistant/core#194939 (merged, HA 2027.1). Switching it, its
    # temperature and its start stay writable entities of Extras (hygiene.py).
    "dhw_hygiene_active": (
        "binary_sensor",
        "dhw_hygiene_active",
        ("heating.dhw.hygiene",),
    ),
    # home-assistant/core#194940 (merged, HA 2027.1)
    "boiler_target_temperature": (
        "sensor",
        "boiler_target_temperature",
        ("heating.boiler.temperature",),
    ),
    # home-assistant/core#194930 (open). Its service message binary sensor only
    # says whether there is one, so the Extras count with the entries stays.
    "malfunction": (
        "binary_sensor",
        "malfunction_lock_active",
        ("device.lock.malfunction",),
    ),
    "device_external_lock": (
        "binary_sensor",
        "external_lock_active",
        ("device.lock.external",),
    ),
}

# home-assistant/core#195089 (open): the total of each heating stop reason, one
# core sensor per reason, `burner_stops_<reason in snake case>`. The Extras
# window of the last stops and the burner runtime stay, and so do the stops
# during hot water heating. The feature stays with Extras for them.
CORE_BURNER_STOP_REASONS = (
    "normalStop",
    "flameLossDuringOperation",
    "flueGasTemperatureSensor",
    "ionizationAlignment",
    "noGasAtStart",
    "noIgnitionAtStart",
    "noIonizationAtStart",
    "safetyShutdown",
    "safetyShutdownErrorShutdown",
    "safetyTime",
    "stabilisationProcess",
    "temperatureMonitor",
    "temperatureMonitorIonizationComparison",
    "undervoltage",
)
CORE_TAKEOVER.update(
    {
        f"burner_stops_{reason}_allstops": (
            "sensor",
            "burner_stops_" + re.sub(r"(?<!^)(?=[A-Z])", "_", reason).lower(),
            (),
        )
        for reason in CORE_BURNER_STOP_REASONS
    }
)

# Per heating circuit: Extras key and features with "{circuit}" -> (core
# platform, core description key). The core creates one entity per circuit.
CORE_TAKEOVER_CIRCUITS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    # home-assistant/core#192754 (open)
    "circuit_{circuit}_target_supply_temperature": (
        "sensor",
        "target_supply_temperature",
        ("heating.circuits.{circuit}.temperature",),
    ),
}

# Features without a curated Extras entity that the core took over; they
# only matter for the generic diagnostics.
CORE_TAKEOVER_GENERIC: dict[tuple[str, str], tuple[str, ...]] = {
    ("sensor", "boiler_internal_pump_modulation"): ("heating.boiler.pumps.internal.current",),
}

CoreKeys = frozenset[tuple[str, str]]
# (curated Extras keys to skip, features the core covers) for one device
Overlap = tuple[frozenset[str], frozenset[str]]
NO_OVERLAP: Overlap = (frozenset(), frozenset())


def description_keys(module: ModuleType) -> set[str]:
    """Return the keys of all entity descriptions a platform module defines."""
    keys: set[str] = set()
    for value in vars(module).values():
        if not isinstance(value, (tuple, list)):
            continue
        for item in value:
            key = getattr(item, "key", None)
            # Entity descriptions carry a key and a translation_key attribute.
            if isinstance(key, str) and hasattr(item, "translation_key"):
                keys.add(key)
    return keys


async def async_core_keys(hass: HomeAssistant) -> CoreKeys:
    """Return (platform, key) for every description of the loaded vicare integration.

    Uses the integration Home Assistant actually loaded, so a custom `vicare`
    copy in custom_components is read instead of the built-in one. Any error
    means "nothing taken over": better a duplicate than a missing value.
    """
    found: set[tuple[str, str]] = set()
    try:
        integration = await async_get_integration(hass, VICARE_DOMAIN)
    except Exception as err:  # noqa: BLE001 - never block the setup
        _LOGGER.warning("Cannot resolve the vicare integration: %s", err)
        return frozenset()
    for platform in CORE_PLATFORMS:
        try:
            module = await integration.async_get_platform(platform)
        except Exception as err:  # noqa: BLE001 - old/custom cores may lack a platform
            _LOGGER.warning(
                "Cannot read vicare.%s (%s), assuming it provides none of the Extras values",
                platform,
                err,
            )
            continue
        found |= {(platform, key) for key in description_keys(module)}
    return frozenset(found)


def taken_over(
    core_keys: CoreKeys,
    created: Callable[[str, str], bool] | None = None,
    circuits: Callable[[str, str], Iterable[str]] | None = None,
) -> Overlap:
    """Return (curated Extras keys to skip, features the core now covers).

    `created(platform, key)` tells whether the core created that entity for
    the device; None means "assume it did" (only the description keys count).
    `circuits(platform, key)` gives the heating circuits the core created an
    entity of `key` for; None means none (without the registry a circuit is
    never left out).
    """

    def provided(platform: str, key: str) -> bool:
        return (platform, key) in core_keys and (created is None or created(platform, key))

    skip: set[str] = set()
    features: set[str] = set()
    for extras_key, (platform, core_key, feats) in CORE_TAKEOVER.items():
        if provided(platform, core_key):
            skip.add(extras_key)
            features.update(feats)
    for (platform, core_key), feats in CORE_TAKEOVER_GENERIC.items():
        if provided(platform, core_key):
            features.update(feats)
    for extras_key, (platform, core_key, feats) in CORE_TAKEOVER_CIRCUITS.items():
        if (platform, core_key) not in core_keys or circuits is None:
            continue
        for circuit in circuits(platform, core_key):
            skip.add(extras_key.format(circuit=circuit))
            features.update(feat.format(circuit=circuit) for feat in feats)
    return frozenset(skip), frozenset(features)


def _enabled_core_entries(
    registry: er.EntityRegistry, platform: str, unique_id: str
) -> Iterable[er.RegistryEntry]:
    """Yield the enabled core entities whose unique ID is `unique_id` or starts with it and "-"."""
    prefix = f"{unique_id}-"
    for entry in registry.entities.values():
        if (
            entry.platform == VICARE_DOMAIN
            and entry.domain == platform
            and getattr(entry, "disabled_by", None) is None
            and (entry.unique_id == unique_id or entry.unique_id.startswith(prefix))
        ):
            yield entry


def core_entity_exists(
    registry: er.EntityRegistry, identifier: str, platform: str, key: str
) -> bool:
    """Return True if the core vicare integration has `key` enabled for the device.

    Core unique IDs are `<identifier>-<key>` for device-level values and
    `<identifier>-<key>-<component id>` for circuits, burners, ...
    A disabled core entity does not count.
    """
    return any(_enabled_core_entries(registry, platform, f"{identifier}-{key}"))


def core_components(
    registry: er.EntityRegistry, identifier: str, platform: str, key: str
) -> set[str]:
    """Return the component IDs (circuits, burners, ...) the core has `key` enabled for.

    The core unique ID of such an entity is `<identifier>-<key>-<component id>`.
    """
    prefix = f"{identifier}-{key}-"
    return {
        entry.unique_id.removeprefix(prefix)
        for entry in _enabled_core_entries(registry, platform, f"{identifier}-{key}")
        if entry.unique_id.startswith(prefix)
    }


def device_overlap(hass: HomeAssistant, core_keys: CoreKeys, identifier: str) -> Overlap:
    """Return the overlap for one device, checked against the entity registry."""
    if not core_keys:
        return NO_OVERLAP
    registry = er.async_get(hass)
    return taken_over(
        core_keys,
        lambda platform, key: core_entity_exists(registry, identifier, platform, key),
        lambda platform, key: core_components(registry, identifier, platform, key),
    )


def takeover_targets(
    registry: er.EntityRegistry, identifier: str, core_keys: CoreKeys
) -> dict[str, tuple[str, str]]:
    """Return Extras unique ID -> (domain, unique ID) of the core entity that took it over.

    Only enabled core entities count, as in core_entity_exists(). A value
    the core created more than once for the device (several burners, ...)
    has no single target and is left out.
    """
    targets: dict[str, tuple[str, str]] = {}
    for extras_key, (platform, core_key, _) in CORE_TAKEOVER.items():
        if (platform, core_key) not in core_keys:
            continue
        unique_id = f"{identifier}-{core_key}"
        entries = list(_enabled_core_entries(registry, platform, unique_id))
        exact = [entry for entry in entries if entry.unique_id == unique_id]
        if len(exact or entries) == 1:
            targets[f"{identifier}-extras-{extras_key}"] = (platform, (exact or entries)[0].unique_id)
    for extras_key, (platform, core_key, _) in CORE_TAKEOVER_CIRCUITS.items():
        if (platform, core_key) not in core_keys:
            continue
        for circuit in core_components(registry, identifier, platform, core_key):
            targets[f"{identifier}-extras-{extras_key.format(circuit=circuit)}"] = (
                platform,
                f"{identifier}-{core_key}-{circuit}",
            )
    return targets


def remove_orphaned_entities(
    hass: HomeAssistant,
    entry_id: str,
    expected: dict[str, set[str]],
) -> list[er.RegistryEntry]:
    """Remove registry entries of this config entry that are no longer produced.

    `expected` maps a device identifier to the unique IDs the integration
    creates for it right now. Only devices in `expected` are touched; callers
    must pass only devices whose data is currently present, never decide on
    an empty or failed snapshot. Returns the removed registry entries.
    """
    registry = er.async_get(hass)
    removed: list[er.RegistryEntry] = []
    prefixes = {identifier: f"{identifier}-extras-" for identifier in expected}
    for entity in list(er.async_entries_for_config_entry(registry, entry_id)):
        for identifier, prefix in prefixes.items():
            if entity.unique_id.startswith(prefix) and entity.unique_id not in expected[identifier]:
                _LOGGER.info(
                    "%s is no longer provided (taken over by the core ViCare "
                    "integration, option changed or feature gone), removing it",
                    entity.entity_id,
                )
                registry.async_remove(entity.entity_id)
                removed.append(entity)
                break
    return removed


def async_report_takeover(
    hass: HomeAssistant,
    removed: Iterable[er.RegistryEntry],
    targets: dict[str, tuple[str, str]],
) -> list[dict[str, Any]]:
    """Raise the repair issue for removed Extras entities the core took over.

    One issue for the integration lists every move: the removed Extras entity
    ID and the core entity that has the value now. Its fix flow (repairs.py)
    gives each core entity the old entity ID, so automations, scripts,
    dashboards and the history keep working. Moves of an issue that is still
    open are kept. Disabled Extras entities are left out (nothing used them),
    and so are core entities of another domain, which cannot take the ID.
    Returns the new moves.
    """
    registry = er.async_get(hass)
    new: list[dict[str, Any]] = []
    for entity in removed:
        target = targets.get(entity.unique_id)
        if target is None or entity.disabled_by is not None:
            continue
        domain, unique_id = target
        if entity.domain != domain:
            continue
        new.append(
            {
                "old": entity.entity_id,
                "domain": domain,
                "unique_id": unique_id,
                "core": registry.async_get_entity_id(domain, VICARE_DOMAIN, unique_id),
                # Settings the user gave the Extras entity, handed on by the fix.
                "name": entity.name,
                "icon": entity.icon,
                "area_id": entity.area_id,
                "labels": sorted(entity.labels),
            }
        )
    if not new:
        return new
    issues = ir.async_get(hass)
    existing = issues.async_get_issue(DOMAIN, ISSUE_CORE_TAKEOVER)
    moves = {move["old"]: move for move in issue_moves(existing.data if existing else None)}
    moves.update({move["old"]: move for move in new})
    async_create_takeover_issue(hass, list(moves.values()))
    return new


def issue_moves(data: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return the moves stored in the data of the repair issue."""
    try:
        moves = json.loads((data or {}).get("moves") or "[]")
    except (TypeError, ValueError):
        return []
    return [move for move in moves if isinstance(move, dict) and move.get("old")]


def async_create_takeover_issue(hass: HomeAssistant, moves: list[dict[str, Any]]) -> None:
    """Create or replace the repair issue with these moves."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_CORE_TAKEOVER,
        # Issue data only holds scalars, so the moves are stored as JSON.
        data={"moves": json.dumps(moves)},
        is_fixable=True,
        is_persistent=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_CORE_TAKEOVER,
        learn_more_url=f"{DOCUMENTATION_URL}#values-that-moved-into-the-core-integration",
    )


def format_moves(moves: Iterable[dict[str, Any]], core: dict[str, str | None] | None = None) -> str:
    """Return one Markdown line per move: old Extras entity ID -> core entity ID.

    `core` maps the old entity ID to the current core entity ID; without it
    the core entity ID stored with the move is shown.
    """
    lines = []
    for move in moves:
        target = (core or {}).get(move["old"], move.get("core"))
        lines.append(f"- `{move['old']}` → `{target or move['unique_id']}`")
    return "\n".join(lines)


def unique_ids(identifier: str, keys: Iterable[str]) -> set[str]:
    """Return the Extras unique IDs for the given keys of one device."""
    return {f"{identifier}-extras-{key}" for key in keys}
