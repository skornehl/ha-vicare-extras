"""Button platform for ViCare Extras (DHW hygiene)."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ViCareExtrasConfigEntry
from .entity import async_add_dynamic_entities
from .hygiene import F_HYGIENE, HygieneStartButton

# Writes go to the cloud API one at a time.
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ViCareExtrasConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_dynamic_entities(
        entry,
        async_add_entities,
        lambda coordinator, dev: [HygieneStartButton(coordinator, dev)] if F_HYGIENE in dev.features else [],
    )
