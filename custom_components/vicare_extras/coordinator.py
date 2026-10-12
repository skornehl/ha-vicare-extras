"""Reads the core ViCare integration's PyViCare cache - no API calls.

The core `vicare` integration polls the Viessmann API (one call per device
every 60 s x device count) and stores the full feature payload in
PyViCare's per-service cache. The free API tier allows ~1450 calls/day,
which three devices already use up almost completely, so this integration
must never fetch on its own. It only ever reads `service._cache`.

`PyViCareDevice.getProperty()` is deliberately NOT used: it re-fetches
from the API when the cache has expired.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, SCAN_INTERVAL, VICARE_DOMAIN

_LOGGER = logging.getLogger(__name__)

ISSUE_INTERNALS = "core_internals_changed"


@dataclass
class ExtrasDevice:
    """Snapshot of one ViCare device's features."""

    identifier: str
    model: str
    features: dict[str, dict[str, Any]] = field(default_factory=dict)
    cache_time: datetime | None = None


class CoreInternalsChanged(Exception):
    """The core vicare integration no longer looks like we expect."""


def _vicare_entry(hass: HomeAssistant) -> ConfigEntry | None:
    for entry in hass.config_entries.async_entries(VICARE_DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry
    return None


def _device_identifier(dev: Any) -> str:
    """Mirror the core integration's device identifier (vicare/entity.py)."""
    gateway_serial = dev.config.getConfig().serial
    if dev.serial is not None:
        return f"{gateway_serial}_{dev.serial.replace('-', '_')}"
    return f"{gateway_serial}_{dev.config.getId()}"


def _read_device(dev: Any) -> ExtrasDevice:
    try:
        api = dev.api
        service = api.service
        accessor = api.accessor
        model = dev.config.getModel().replace("_", " ")
        identifier = _device_identifier(dev)
    except AttributeError as err:
        raise CoreInternalsChanged(str(err)) from err

    snapshot = ExtrasDevice(identifier=identifier, model=model)
    cache = getattr(service, "_cache", None)
    if not cache:
        return snapshot
    try:
        entities = service._extract_entities(cache, accessor)  # noqa: SLF001
    except Exception:  # noqa: BLE001 - fall back to the raw payload
        entities = cache.get("data", [])
    snapshot.features = {
        f["feature"]: f for f in entities if isinstance(f, dict) and "feature" in f
    }
    snapshot.cache_time = getattr(service, "_cacheTime", None)
    return snapshot


class ViCareExtrasCoordinator(DataUpdateCoordinator[dict[str, ExtrasDevice]]):
    """Polls the in-memory PyViCare cache of the core integration."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )

    async def _async_update_data(self) -> dict[str, ExtrasDevice]:
        vicare = _vicare_entry(self.hass)
        if vicare is None:
            raise UpdateFailed("ViCare integration is not loaded")
        try:
            devices = vicare.runtime_data.devices
            data = {d.identifier: d for d in map(_read_device, devices)}
        except (AttributeError, CoreInternalsChanged) as err:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                ISSUE_INTERNALS,
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key=ISSUE_INTERNALS,
                translation_placeholders={"error": str(err)},
            )
            raise UpdateFailed(f"Core vicare internals changed: {err}") from err
        ir.async_delete_issue(self.hass, DOMAIN, ISSUE_INTERNALS)
        return data
