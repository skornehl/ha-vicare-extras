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
    if not hasattr(dev, "serial"):
        # ViCare up to HA 2024.6 (still used by old manually installed copies,
        # issue #1) had no per-device serial and identified the device by the
        # gateway serial alone.
        return gateway_serial
    if dev.serial is not None:
        return f"{gateway_serial}_{dev.serial.replace('-', '_')}"
    return f"{gateway_serial}_{dev.config.getId()}"


def _read_device(dev: Any) -> ExtrasDevice:
    try:
        api = dev.api
        service = api.service
        model = dev.config.getModel().replace("_", " ")
        identifier = _device_identifier(dev)
    except AttributeError as err:
        raise CoreInternalsChanged(str(err)) from err
    # PyViCare >= 2.63 keeps the accessor on the device and one cache per
    # gateway (filtered per device by _extract_entities). Older PyViCare,
    # as bundled by custom ViCare integrations, keeps it on the service and
    # one cache per device (issue #1: "'HeatPump' object has no attribute
    # 'accessor'").
    accessor = getattr(api, "accessor", None) or getattr(service, "accessor", None)

    snapshot = ExtrasDevice(identifier=identifier, model=model)
    # PyViCare <= 2.60 stores the cache as `self.__cache`, which Python
    # name-mangles to `_ViCareCachedService__cache`; `_cache` exists from 2.61.
    cache = getattr(service, "_cache", None)
    if cache is None:
        cache = getattr(service, "_ViCareCachedService__cache", None)
    if not cache:
        return snapshot
    entities = None
    if accessor is not None and hasattr(service, "_extract_entities"):
        try:
            entities = service._extract_entities(cache, accessor)  # noqa: SLF001
        except Exception:  # noqa: BLE001 - fall back to the raw payload
            entities = None
    if entities is None:
        entities = cache.get("data", [])
        device_id = getattr(accessor, "device_id", None)
        if device_id is None:
            try:
                device_id = dev.config.getId()
            except AttributeError:
                device_id = None
        if device_id is not None:
            entities = [
                f for f in entities
                if not isinstance(f, dict) or str(f.get("deviceId", device_id)) == str(device_id)
            ]
    snapshot.features = {
        f["feature"]: f for f in entities if isinstance(f, dict) and "feature" in f
    }
    snapshot.cache_time = getattr(service, "_cacheTime", None) or getattr(
        service, "_ViCareCachedService__cacheTime", None
    )
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
        self.core_devices = {}

    core_devices: dict[str, Any]

    async def _async_update_data(self) -> dict[str, ExtrasDevice]:
        vicare = _vicare_entry(self.hass)
        if vicare is None:
            raise UpdateFailed("ViCare integration is not loaded")
        try:
            devices = vicare.runtime_data.devices
            data: dict[str, ExtrasDevice] = {}
            core: dict[str, Any] = {}
            for dev in devices:
                snap = _read_device(dev)
                core[snap.identifier] = dev
                previous = (self.data or {}).get(snap.identifier)
                if not snap.features and previous is not None:
                    # PyViCare empties its cache after every write command
                    # until the core coordinator fetches again - keep the
                    # last known values instead of going unavailable.
                    snap = previous
                data[snap.identifier] = snap
            self.core_devices = core
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
