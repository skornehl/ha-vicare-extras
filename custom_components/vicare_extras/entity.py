"""Base entity and platform helpers for ViCare Extras."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_CALORIFIC_VALUE, VICARE_DOMAIN
from .coordinator import ExtrasDevice, ViCareExtrasConfigEntry, ViCareExtrasCoordinator
from .descriptions import Spec, build_specs


def device_info(device: ExtrasDevice) -> DeviceInfo:
    """Device info matching the core integration's device.

    Same identifier as the core integration, so these entities show up on the
    existing ViCare device instead of a new one.
    """
    return DeviceInfo(
        identifiers={(VICARE_DOMAIN, device.identifier)},
        name=device.model,
        manufacturer="Viessmann",
        model=device.model,
    )


def device_specs(coordinator: ViCareExtrasCoordinator, device: ExtrasDevice) -> list[Spec]:
    """All read-only entity specs of one device with the current options."""
    calorific = coordinator.config_entry.options.get(CONF_CALORIFIC_VALUE)
    return build_specs(device, calorific, coordinator.overlap(device.identifier))


@callback
def async_add_dynamic_entities(
    entry: ViCareExtrasConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    factory: Callable[[ViCareExtrasCoordinator, ExtrasDevice], Iterable[Entity]],
) -> None:
    """Add entities now and whenever devices or features appear later.

    Rebuilt only when the coordinator's shape (devices, features, properties)
    changes; devices without current data are skipped until they have some.
    """
    coordinator = entry.runtime_data
    known: set[str] = set()
    last_shape: list[int | None] = [None]

    @callback
    def _check() -> None:
        if coordinator.shape is not None and coordinator.shape == last_shape[0]:
            return
        last_shape[0] = coordinator.shape
        new: list[Entity] = []
        for device in list((coordinator.data or {}).values()):
            if not device.features:
                continue
            for entity in factory(coordinator, device):
                if entity.unique_id is not None and entity.unique_id not in known:
                    known.add(entity.unique_id)
                    new.append(entity)
        if new:
            async_add_entities(new)

    _check()
    entry.async_on_unload(coordinator.async_add_listener(_check))


class ViCareExtrasBaseEntity(CoordinatorEntity[ViCareExtrasCoordinator]):
    """Common base: one device, attached to the core ViCare device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ViCareExtrasCoordinator, device: ExtrasDevice, key: str) -> None:
        super().__init__(coordinator)
        self._identifier = device.identifier
        # Stable since 0.1 - existing entities keep their history.
        self._attr_unique_id = f"{device.identifier}-extras-{key}"
        self._attr_device_info = device_info(device)

    def feature(self, name: str) -> dict[str, Any] | None:
        dev = (self.coordinator.data or {}).get(self._identifier)
        return dev.features.get(name) if dev else None


class ViCareExtrasEntity(ViCareExtrasBaseEntity):
    """An entity backed by one feature of one ViCare device."""

    def __init__(
        self, coordinator: ViCareExtrasCoordinator, device: ExtrasDevice, spec: Spec
    ) -> None:
        super().__init__(coordinator, device, spec.key)
        self.spec = spec
        if spec.translation_key:
            # Name from translations/<language>.json, icon from icons.json;
            # new entity IDs come from the English name, so YAML stays
            # shareable across languages.
            self._attr_translation_key = spec.translation_key
            self._attr_translation_placeholders = spec.placeholders or {}
        else:
            self._attr_name = spec.name
        self._attr_entity_registry_enabled_default = spec.enabled_default
        if spec.diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def _feature(self) -> dict[str, Any] | None:
        return self.feature(self.spec.feature)

    @property
    def available(self) -> bool:
        return super().available and self._feature is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        feature = self._feature
        attrs: dict[str, Any] = {"feature": self.spec.feature}
        if feature is not None and self.spec.attrs_fn is not None:
            attrs.update(self.spec.attrs_fn(feature))
        # No per-poll timestamp here: any attribute change fires state_changed
        # and makes the recorder write a new row for every entity on every
        # poll. The cache time has its own sensor (ViCareExtrasApiDataSensor).
        return attrs

    def _raw_value(self) -> Any:
        feature = self._feature
        return None if feature is None else self.spec.value_fn(feature)
