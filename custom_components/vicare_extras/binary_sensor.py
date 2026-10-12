"""Binary sensor platform for ViCare Extras."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import ViCareExtrasCoordinator
from .descriptions import build_specs
from .entity import ViCareExtrasEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ViCareExtrasCoordinator = entry.runtime_data
    async_add_entities(
        ViCareExtrasBinarySensor(coordinator, dev, spec)
        for dev in coordinator.data.values()
        for spec in build_specs(dev)
        if spec.platform == "binary_sensor"
    )


class ViCareExtrasBinarySensor(ViCareExtrasEntity, BinarySensorEntity):
    """A binary sensor for one boolean feature property."""

    def __init__(self, coordinator, device, spec) -> None:
        super().__init__(coordinator, device, spec)
        if spec.device_class:
            self._attr_device_class = BinarySensorDeviceClass(spec.device_class)

    @property
    def is_on(self) -> bool | None:
        value = self._raw_value()
        return None if value is None else bool(value)
