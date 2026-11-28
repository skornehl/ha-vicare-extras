"""ViCare Extras: the Viessmann API features the core `vicare` integration
does not expose, read from the core integration's own cache.

No login and no polling of its own - see coordinator.py for why. Writes
(commands.py) cost one API call each plus one refresh.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.typing import ConfigType

from .const import CONF_ALLOW_RAW_COMMANDS, DOMAIN, VICARE_DOMAIN
from .coordinator import (
    ISSUE_INTERNALS,
    ViCareExtrasConfigEntry,
    ViCareExtrasCoordinator,
    all_vicare_entries_loaded,
)
from .core_overlap import (
    ISSUE_CORE_TAKEOVER,
    async_core_keys,
    async_report_takeover,
    remove_orphaned_entities,
    takeover_targets,
    unique_ids,
)
from .entity import device_specs
from .hygiene import hygiene_keys
from .services import async_register_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the services once; they look up the loaded entry per call."""
    async_register_services(hass)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate older config entries."""
    if entry.version > 1:
        return False
    if entry.minor_version < 2:
        # 1.0.0 made execute_command opt-in. Existing installations keep it
        # allowed so their automations do not break; new ones start blocked.
        hass.config_entries.async_update_entry(
            entry,
            options={CONF_ALLOW_RAW_COMMANDS: True, **entry.options},
            minor_version=2,
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ViCareExtrasConfigEntry) -> bool:
    coordinator = ViCareExtrasCoordinator(hass, entry)
    entry.async_on_unload(coordinator.async_unsubscribe_core)
    await coordinator.async_config_entry_first_refresh()
    if not any(dev.features for dev in coordinator.data.values()):
        # Core integration loaded but has not fetched yet - entities are
        # created from the payload, so wait for it.
        raise ConfigEntryNotReady("ViCare has not fetched any data yet")
    entry.runtime_data = coordinator
    # Weiche: values the core already provides for a device are skipped.
    coordinator.core_keys = await async_core_keys(hass)
    _async_cleanup_registries(hass, entry, coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


def expected_unique_ids(coordinator: ViCareExtrasCoordinator, identifier: str) -> set[str]:
    """Every unique ID the integration creates for a device right now."""
    device = coordinator.data[identifier]
    keys = {spec.key for spec in device_specs(coordinator, device)}
    keys |= hygiene_keys(device)
    keys.add("api_data_from")
    return unique_ids(identifier, keys)


def _async_cleanup_registries(
    hass: HomeAssistant, entry: ViCareExtrasConfigEntry, coordinator: ViCareExtrasCoordinator
) -> None:
    """Remove entities and devices that are no longer provided.

    Entities: only for devices whose data is present right now, never because
    of an empty or failed snapshot. An entity the core took over gets a repair
    issue that hands its entity ID on to the core entity. Devices: only when
    every core ViCare entry is loaded, so a device of a temporarily failing
    entry is kept.
    """
    present = {
        identifier: expected_unique_ids(coordinator, identifier)
        for identifier, dev in coordinator.data.items()
        if dev.features
    }
    entity_registry = er.async_get(hass)
    targets: dict[str, tuple[str, str]] = {}
    for identifier in present:
        targets |= takeover_targets(entity_registry, identifier, coordinator.core_keys)
    removed = remove_orphaned_entities(hass, entry.entry_id, present)
    async_report_takeover(hass, removed, targets)

    if not all_vicare_entries_loaded(hass):
        return
    device_registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        idents = {i for d, i in device.identifiers if d == VICARE_DOMAIN}
        if idents and not idents & set(coordinator.data):
            _LOGGER.info("ViCare device %s is gone, detaching ViCare Extras from it", device.name)
            device_registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ViCareExtrasConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow removing a device the core integration no longer reports."""
    coordinator = getattr(entry, "runtime_data", None)
    known = set(coordinator.data) if coordinator and coordinator.data else set()
    return not any(d == VICARE_DOMAIN and i in known for d, i in device_entry.identifiers)


async def _async_options_updated(hass: HomeAssistant, entry: ViCareExtrasConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ViCareExtrasConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_INTERNALS)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean up the repair issues when the integration is removed."""
    ir.async_delete_issue(hass, DOMAIN, ISSUE_INTERNALS)
    ir.async_delete_issue(hass, DOMAIN, ISSUE_CORE_TAKEOVER)
