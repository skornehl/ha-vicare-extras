"""ViCare Extras: the Viessmann API features the core `vicare` integration
does not expose, read from the core integration's own cache.

No login and no polling of its own - see coordinator.py for why. Writes
(commands.py) cost one API call each plus one refresh.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .coordinator import ViCareExtrasCoordinator
from .services import async_register_services

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
]

type ViCareExtrasConfigEntry = ConfigEntry[ViCareExtrasCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: ViCareExtrasConfigEntry) -> bool:
    coordinator = ViCareExtrasCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    if not any(dev.features for dev in coordinator.data.values()):
        # Core integration loaded but has not fetched yet - entities are
        # created from the payload, so wait for it.
        raise ConfigEntryNotReady("ViCare has not fetched any data yet")
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ViCareExtrasConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
