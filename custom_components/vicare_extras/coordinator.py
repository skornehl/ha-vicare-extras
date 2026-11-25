"""Reads the core ViCare integration's PyViCare cache - no API calls.

The core `vicare` integration polls the Viessmann API (one bulk call per
gateway every 60 s x gateway count) and stores the full feature payload in
PyViCare's per-service cache. The free API tier allows ~1450 calls/day,
which the core integration already uses up almost completely, so this
integration must never fetch on its own. It only ever reads `service._cache`.

`PyViCareDevice.getProperty()` is deliberately NOT used: it re-fetches
from the API when the cache has expired.

Updates are pushed: a listener on every core coordinator re-reads the cache
right after the core fetched. The own 30 s poll of the cache is only the
fallback (for example for cores without a coordinator).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CORE_DEFAULT_INTERVAL,
    DOMAIN,
    SCAN_INTERVAL,
    STALE_FACTOR,
    STALE_MIN,
    VICARE_DOMAIN,
)
from .core_overlap import NO_OVERLAP, CoreKeys, Overlap, device_overlap

_LOGGER = logging.getLogger(__name__)

ISSUE_INTERNALS = "core_internals_changed"


@dataclass
class ExtrasDevice:
    """Snapshot of one ViCare device's features.

    `features` is empty while the data is missing or stale; `cache_time` then
    still tells when the core integration fetched last.
    """

    identifier: str
    model: str
    features: dict[str, dict[str, Any]] = field(default_factory=dict)
    cache_time: datetime | None = None


class CoreInternalsChanged(Exception):
    """The core vicare integration no longer looks like we expect."""


def vicare_entries(hass: HomeAssistant) -> list[ConfigEntry]:
    """Return all loaded core ViCare config entries."""
    return [
        entry
        for entry in hass.config_entries.async_entries(VICARE_DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]


def all_vicare_entries_loaded(hass: HomeAssistant) -> bool:
    """Return True if every enabled core ViCare entry is loaded."""
    return all(
        entry.state is ConfigEntryState.LOADED
        for entry in hass.config_entries.async_entries(VICARE_DOMAIN)
        if entry.disabled_by is None
    )


def _core_devices(entry: ConfigEntry) -> list[Any]:
    try:
        return list(entry.runtime_data.devices)
    except (AttributeError, TypeError) as err:
        raise CoreInternalsChanged(f"runtime_data.devices: {err}") from err


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
        entities = cache.get("data", []) if isinstance(cache, dict) else []
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


def max_age(core_coordinator: Any) -> timedelta:
    """How old the core's data may get before it counts as stale."""
    interval = getattr(core_coordinator, "update_interval", None)
    if not isinstance(interval, timedelta):
        interval = CORE_DEFAULT_INTERVAL
    return max(interval * STALE_FACTOR, STALE_MIN)


def is_stale(cache_time: datetime | None, limit: timedelta) -> bool:
    """Return True if the fetch at `cache_time` is older than `limit`.

    PyViCare stores a naive datetime.now(); aware values are compared in
    their own zone. Unknown fetch times are not treated as stale.
    """
    if not isinstance(cache_time, datetime):
        return False
    now = datetime.now(cache_time.tzinfo) if cache_time.tzinfo else datetime.now()
    return now - cache_time > limit


def shape_of(data: dict[str, ExtrasDevice]) -> int:
    """Fingerprint of which devices, features and properties exist.

    Platforms only rebuild their entity list when this changes, so new devices
    or features get entities without a reload.
    """
    return hash(
        tuple(
            sorted(
                (
                    identifier,
                    tuple(
                        sorted(
                            (
                                name,
                                bool(feat.get("isEnabled", True)),
                                tuple(sorted(feat.get("properties") or {})),
                            )
                            for name, feat in dev.features.items()
                        )
                    ),
                )
                for identifier, dev in data.items()
            )
        )
    )


class ViCareExtrasCoordinator(DataUpdateCoordinator[dict[str, ExtrasDevice]]):
    """Reads the in-memory PyViCare cache of the core integration."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        # identifier -> core ViCareDevice (has .api for writes, .coordinator)
        self.core_devices: dict[str, Any] = {}
        # (platform, description key) of the loaded vicare, see core_overlap.py
        self.core_keys: CoreKeys = frozenset()
        self.shape: int | None = None
        # pending confirmation refresh per core coordinator, see commands.py
        self.confirm_tasks: dict[Any, asyncio.Task[None]] = {}
        self._overlap: dict[str, Overlap] = {}
        self._hold_until = 0.0
        self._core_listeners: dict[int, tuple[Any, Callable[[], None]]] = {}
        self._stale_logged: set[str] = set()

    # ------------------------------------------------------------ overlap
    def overlap(self, identifier: str) -> Overlap:
        """Return (skip, core features) for one device, computed once."""
        if not self.core_keys:
            return NO_OVERLAP
        if identifier not in self._overlap:
            self._overlap[identifier] = device_overlap(self.hass, self.core_keys, identifier)
        return self._overlap[identifier]

    # ------------------------------------------------------------ writes
    def hold(self, seconds: float) -> None:
        """Keep the last snapshot for `seconds` while the cache is empty.

        PyViCare clears the whole gateway cache after every write until the
        core coordinator fetches again.
        """
        self._hold_until = max(self._hold_until, time.monotonic() + seconds)

    def fresh_device(self, identifier: str) -> ExtrasDevice | None:
        """Return the device if its data is currently present."""
        dev = (self.data or {}).get(identifier)
        return dev if dev is not None and dev.features else None

    # ------------------------------------------------------------ core push
    @callback
    def _handle_core_update(self) -> None:
        self.config_entry.async_create_background_task(
            self.hass, self.async_request_refresh(), f"{DOMAIN} core update"
        )

    @callback
    def _sync_core_listeners(self, core_coordinators: list[Any]) -> None:
        current = {
            id(c): c
            for c in core_coordinators
            if c is not None and callable(getattr(c, "async_add_listener", None))
        }
        for key in [k for k in self._core_listeners if k not in current]:
            self._core_listeners.pop(key)[1]()
        for key, core in current.items():
            if key not in self._core_listeners:
                self._core_listeners[key] = (core, core.async_add_listener(self._handle_core_update))

    @callback
    def async_unsubscribe_core(self) -> None:
        """Remove the listeners from the core coordinators."""
        for _, unsub in self._core_listeners.values():
            unsub()
        self._core_listeners.clear()

    async def async_shutdown(self) -> None:
        """Stop listening to the core integration."""
        self.async_unsubscribe_core()
        await super().async_shutdown()

    # ------------------------------------------------------------ update
    def _settle(
        self, snap: ExtrasDevice, core: Any, previous: ExtrasDevice | None, holding: bool
    ) -> ExtrasDevice:
        """Decide what to show for a freshly read snapshot.

        `core` is the device's core coordinator (or None for old cores).
        """
        limit = max_age(core)
        if snap.features:
            if is_stale(snap.cache_time, limit):
                self._log_stale(snap.identifier, snap.cache_time)
                return ExtrasDevice(snap.identifier, snap.model, {}, snap.cache_time)
            self._stale_logged.discard(snap.identifier)
            return snap
        if previous is None:
            return snap
        if previous.features and (
            holding
            or (
                getattr(core, "last_update_success", True)
                and not is_stale(previous.cache_time, limit)
            )
        ):
            # The cache is empty for a moment after every write (PyViCare
            # clears it) and while the core fetches - keep the last values.
            return previous
        # Core fetch failed or nothing arrived for too long: unavailable, but
        # keep the last fetch time for the "API data from" sensor.
        self._log_stale(snap.identifier, previous.cache_time)
        return ExtrasDevice(snap.identifier, snap.model, {}, previous.cache_time)

    def _log_stale(self, identifier: str, cache_time: datetime | None) -> None:
        if identifier not in self._stale_logged:
            self._stale_logged.add(identifier)
            _LOGGER.warning(
                "No current ViCare data for %s (last fetch %s), entities are unavailable "
                "until the core ViCare integration fetches again",
                identifier,
                cache_time,
            )

    async def _async_update_data(self) -> dict[str, ExtrasDevice]:
        entries = vicare_entries(self.hass)
        if not entries:
            raise UpdateFailed("ViCare integration is not loaded")
        holding = time.monotonic() < self._hold_until
        old = self.data or {}
        data: dict[str, ExtrasDevice] = {}
        core: dict[str, Any] = {}
        try:
            for vicare in entries:
                for dev in _core_devices(vicare):
                    snap = _read_device(dev)
                    data[snap.identifier] = self._settle(
                        snap,
                        getattr(dev, "coordinator", None),
                        old.get(snap.identifier),
                        holding,
                    )
                    core[snap.identifier] = dev
        except CoreInternalsChanged as err:
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
        self.core_devices = core
        self._sync_core_listeners([getattr(dev, "coordinator", None) for dev in core.values()])
        self.shape = shape_of(data)
        return data


type ViCareExtrasConfigEntry = ConfigEntry[ViCareExtrasCoordinator]
