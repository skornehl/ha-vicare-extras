"""Sensor platform for ViCare Extras."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
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
        ViCareExtrasSensor(coordinator, dev, spec)
        for dev in coordinator.data.values()
        for spec in build_specs(dev)
        if spec.platform == "sensor"
    )


class ViCareExtrasSensor(ViCareExtrasEntity, SensorEntity):
    """A sensor for one feature property."""

    def __init__(self, coordinator, device, spec) -> None:
        super().__init__(coordinator, device, spec)
        self._attr_native_unit_of_measurement = spec.unit
        if spec.device_class:
            self._attr_device_class = SensorDeviceClass(spec.device_class)
        if spec.state_class:
            self._attr_state_class = SensorStateClass(spec.state_class)

    @property
    def native_value(self) -> Any:
        value = self._raw_value()
        if isinstance(value, bool):
            return str(value).lower()
        return value
