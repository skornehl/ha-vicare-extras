"""Number platform for ViCare Extras."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .hygiene import F_TEMP, HygieneTemperature, hygiene_devices


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(HygieneTemperature(coordinator, dev) for dev in hygiene_devices(coordinator, F_TEMP))
