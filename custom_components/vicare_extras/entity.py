"""Base entity for ViCare Extras."""

from __future__ import annotations

from typing import Any

from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import VICARE_DOMAIN
from .coordinator import ExtrasDevice, ViCareExtrasCoordinator
from .descriptions import Spec


class ViCareExtrasEntity(CoordinatorEntity[ViCareExtrasCoordinator]):
    """An entity backed by one feature of one ViCare device."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: ViCareExtrasCoordinator, device: ExtrasDevice, spec: Spec
    ) -> None:
        super().__init__(coordinator)
        self.spec = spec
        self._identifier = device.identifier
        self._attr_unique_id = f"{device.identifier}-extras-{spec.key}"
        if spec.translation_key:
            # Name from translations/<language>.json; new entity IDs come from
            # the English name, so YAML stays shareable across languages.
            self._attr_translation_key = spec.translation_key
            self._attr_translation_placeholders = spec.placeholders or {}
        else:
            self._attr_name = spec.name
        self._attr_icon = spec.icon
        self._attr_entity_registry_enabled_default = spec.enabled_default
        if spec.diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        # Same identifier as the core integration, so these entities show up
        # on the existing ViCare device instead of a new one.
        self._attr_device_info = DeviceInfo(
            identifiers={(VICARE_DOMAIN, device.identifier)},
            name=device.model,
            manufacturer="Viessmann",
            model=device.model,
        )

    @property
    def _feature(self) -> dict[str, Any] | None:
        dev = (self.coordinator.data or {}).get(self._identifier)
        return dev.features.get(self.spec.feature) if dev else None

    @property
    def available(self) -> bool:
        return super().available and self._feature is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        feature = self._feature
        attrs: dict[str, Any] = {"feature": self.spec.feature}
        if feature is not None and self.spec.attrs_fn is not None:
            attrs.update(self.spec.attrs_fn(feature))
        dev = (self.coordinator.data or {}).get(self._identifier)
        if dev and dev.cache_time:
            attrs["api_data_from"] = dev.cache_time.isoformat()
        return attrs

    def _raw_value(self) -> Any:
        feature = self._feature
        return None if feature is None else self.spec.value_fn(feature)
