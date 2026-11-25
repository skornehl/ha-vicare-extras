"""Weiche: skip what the installed core `vicare` integration already provides.

Some values started in ViCare Extras and later moved into the core
integration (for example the internal boiler pump, home-assistant/core#193054,
HA 2026.12). An Extras entity is only left out when both hold:

1. the loaded `vicare` integration (core or a custom copy) defines the entity
   description key (read from its platform modules), and
2. the core integration really created that entity for the device, that is
   the entity registry has a `vicare` entity with the core unique_id on it.

Then the curated Extras entity is not created and its feature is not offered
as a generic diagnostic either, so nothing shows up twice. With an older core,
or a device the core does not support the value for, everything stays.

Limitation: description keys are collected from all description tuples of a
platform module, not per description group (device, circuit, burner, ...).
That is harmless because of check 2: the registry decides per device.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
import logging
from types import ModuleType

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.loader import async_get_integration

from .const import VICARE_DOMAIN

_LOGGER = logging.getLogger(__name__)

CORE_PLATFORMS = ("sensor", "binary_sensor")

# Curated Extras key -> (core platform, core description key, features the
# core now reads). The features are also kept out of the generic layer.
CORE_TAKEOVER: dict[str, tuple[str, str, tuple[str, ...]]] = {
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
    core_keys: CoreKeys, created: Callable[[str, str], bool] | None = None
) -> Overlap:
    """Return (curated Extras keys to skip, features the core now covers).

    `created(platform, key)` tells whether the core created that entity for
    the device; None means "assume it did" (only the description keys count).
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
    return frozenset(skip), frozenset(features)


def core_entity_exists(
    registry: er.EntityRegistry, identifier: str, platform: str, key: str
) -> bool:
    """Return True if the core vicare integration registered `key` for the device.

    Core unique IDs are `<identifier>-<key>` for device-level values and
    `<identifier>-<key>-<component id>` for circuits, burners, ...
    """
    unique_id = f"{identifier}-{key}"
    if registry.async_get_entity_id(platform, VICARE_DOMAIN, unique_id):
        return True
    prefix = f"{unique_id}-"
    return any(
        entry.platform == VICARE_DOMAIN
        and entry.domain == platform
        and entry.unique_id.startswith(prefix)
        for entry in registry.entities.values()
    )


def device_overlap(hass: HomeAssistant, core_keys: CoreKeys, identifier: str) -> Overlap:
    """Return the overlap for one device, checked against the entity registry."""
    if not core_keys:
        return NO_OVERLAP
    registry = er.async_get(hass)
    return taken_over(
        core_keys,
        lambda platform, key: core_entity_exists(registry, identifier, platform, key),
    )


def remove_orphaned_entities(
    hass: HomeAssistant,
    entry_id: str,
    expected: dict[str, set[str]],
) -> list[str]:
    """Remove registry entries of this config entry that are no longer produced.

    `expected` maps a device identifier to the unique IDs the integration
    creates for it right now. Only devices in `expected` are touched; callers
    must pass only devices whose data is currently present, never decide on
    an empty or failed snapshot. Returns the removed entity IDs.
    """
    registry = er.async_get(hass)
    removed: list[str] = []
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
                removed.append(entity.entity_id)
                break
    return removed


def unique_ids(identifier: str, keys: Iterable[str]) -> set[str]:
    """Return the Extras unique IDs for the given keys of one device."""
    return {f"{identifier}-extras-{key}" for key in keys}
