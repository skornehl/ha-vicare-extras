"""Binary sensor platform for ViCare Extras."""

from __future__ import annotations

from collections.abc import Iterable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ExtrasDevice, ViCareExtrasConfigEntry, ViCareExtrasCoordinator
from .descriptions import Spec
from .entity import ViCareExtrasEntity, async_add_dynamic_entities, device_specs

# Read-only: values come from the coordinator.
PARALLEL_UPDATES = 0


def _entities(coordinator: ViCareExtrasCoordinator, dev: ExtrasDevice) -> Iterable[Entity]:
    for spec in device_specs(coordinator, dev):
        if spec.platform == "binary_sensor":
            yield ViCareExtrasBinarySensor(coordinator, dev, spec)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ViCareExtrasConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_dynamic_entities(entry, async_add_entities, _entities)


class ViCareExtrasBinarySensor(ViCareExtrasEntity, BinarySensorEntity):
    """A binary sensor for one boolean feature property."""

    def __init__(
        self, coordinator: ViCareExtrasCoordinator, device: ExtrasDevice, spec: Spec
    ) -> None:
        super().__init__(coordinator, device, spec)
        if spec.device_class:
            self._attr_device_class = BinarySensorDeviceClass(spec.device_class)

    @property
    def is_on(self) -> bool | None:
        value = self._raw_value()
        return None if value is None else bool(value)
