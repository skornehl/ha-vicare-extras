"""Weiche: skip what the installed core `vicare` integration already provides.

Some values started in ViCare Extras and later moved into the core
integration (for example the internal boiler pump, home-assistant/core#193054,
expected in HA 2026.12). Which core version is installed decides at setup:
if the core platform module defines the matching entity description key,
the curated Extras entity is not created and its feature is not offered as a
generic diagnostic either, so nothing shows up twice. With an older core
everything stays as before.

The check reads the description keys from the core platform modules, so it
does not depend on whether the core entities were already created when
Extras sets up.
"""

from __future__ import annotations

import importlib
import logging
from types import ModuleType

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN

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
    """Return (platform, key) for every description of the installed core vicare."""
    found: set[tuple[str, str]] = set()
    for platform in CORE_PLATFORMS:
        try:
            module = await hass.async_add_import_executor_job(
                importlib.import_module, f"homeassistant.components.vicare.{platform}"
            )
        except ImportError:
            continue
        found |= {(platform, key) for key in description_keys(module)}
    return frozenset(found)


def taken_over(core_keys: CoreKeys) -> tuple[set[str], set[str]]:
    """Return (curated Extras keys to skip, features the core now covers)."""
    skip: set[str] = set()
    features: set[str] = set()
    for extras_key, (platform, core_key, feats) in CORE_TAKEOVER.items():
        if (platform, core_key) in core_keys:
            skip.add(extras_key)
            features.update(feats)
    for core, feats in CORE_TAKEOVER_GENERIC.items():
        if core in core_keys:
            features.update(feats)
    return skip, features


def remove_stale_entities(hass: HomeAssistant, identifiers: list[str], skip: set[str]) -> None:
    """Drop registry entries of Extras entities the core has taken over.

    They would otherwise stay behind as restored, unavailable entities.
    """
    if not skip:
        return
    registry = er.async_get(hass)
    for identifier in identifiers:
        for key in skip:
            for platform in CORE_PLATFORMS:
                entity_id = registry.async_get_entity_id(
                    platform, DOMAIN, f"{identifier}-extras-{key}"
                )
                if entity_id:
                    _LOGGER.info(
                        "%s is now provided by the core ViCare integration, removing %s",
                        key,
                        entity_id,
                    )
                    registry.async_remove(entity_id)
