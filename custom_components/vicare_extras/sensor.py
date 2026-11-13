"""Sensor platform for ViCare Extras."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_CALORIFIC_VALUE
from .coordinator import ExtrasDevice, ViCareExtrasCoordinator
from .descriptions import build_specs
from .entity import ViCareExtrasEntity, device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ViCareExtrasCoordinator = entry.runtime_data
    calorific = entry.options.get(CONF_CALORIFIC_VALUE)
    async_add_entities(
        ViCareExtrasSensor(coordinator, dev, spec)
        for dev in coordinator.data.values()
        for spec in build_specs(dev, calorific)
        if spec.platform == "sensor"
    )
    async_add_entities(
        ViCareExtrasApiDataSensor(coordinator, dev)
        for dev in coordinator.data.values()
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


class ViCareExtrasApiDataSensor(
    CoordinatorEntity[ViCareExtrasCoordinator], SensorEntity
):
    """When the core integration last fetched this device's data from the API.

    Replaces the former per-entity attribute api_data_from: one entity that
    changes per poll instead of every entity of the device.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "api_data_from"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: ViCareExtrasCoordinator, device: ExtrasDevice
    ) -> None:
        super().__init__(coordinator)
        self._identifier = device.identifier
        self._attr_unique_id = f"{device.identifier}-extras-api_data_from"
        self._attr_device_info = device_info(device)

    @property
    def _cache_time(self) -> datetime | None:
        dev = (self.coordinator.data or {}).get(self._identifier)
        return dev.cache_time if dev else None

    @property
    def available(self) -> bool:
        return super().available and self._cache_time is not None

    @property
    def native_value(self) -> datetime | None:
        cache_time = self._cache_time
        # PyViCare stores a naive datetime.now() (local time of the HA
        # process); astimezone() attaches that local zone. Aware values are
        # passed through unchanged in meaning.
        return cache_time.astimezone() if cache_time else None
