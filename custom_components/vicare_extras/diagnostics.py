"""Diagnostics for ViCare Extras: the feature payload with private data removed."""

from __future__ import annotations

import re
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .coordinator import ViCareExtrasConfigEntry, max_age

REDACTED = "**REDACTED**"

# Keys anywhere in the payload: serials, ids, URLs (they contain installation
# id and gateway serial), network and personal data, coordinates.
TO_REDACT = {
    "serial",
    "serialNumber",
    "gatewayId",
    "gatewaySerial",
    "installationId",
    "deviceId",
    "uri",
    "macAddress",
    "mac",
    "ssid",
    "ip",
    "ipAddress",
    "email",
    "latitude",
    "longitude",
}

# Features whose whole property block is private.
_PRIVATE_FEATURES = re.compile(
    r"(serial|identification|houseLocation|wifi|zigbee|\.name$|gateway\.devices|lan\.)",
    re.IGNORECASE,
)


def _redact_feature(name: str, feature: dict[str, Any]) -> dict[str, Any]:
    data = async_redact_data(feature, TO_REDACT)
    if _PRIVATE_FEATURES.search(name) and isinstance(data.get("properties"), dict):
        data["properties"] = {key: REDACTED for key in data["properties"]}
    return data


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ViCareExtrasConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for the config entry."""
    coordinator = entry.runtime_data
    devices = []
    for index, (identifier, device) in enumerate(sorted((coordinator.data or {}).items()), 1):
        core = getattr(coordinator.core_devices.get(identifier), "coordinator", None)
        skip, core_features = coordinator.overlap(identifier)
        devices.append(
            {
                # The identifier contains the gateway serial.
                "device": f"device_{index}",
                "model": device.model,
                "cache_time": device.cache_time.isoformat() if device.cache_time else None,
                "data_present": bool(device.features),
                "stale_after_seconds": max_age(core).total_seconds(),
                "core_last_update_success": getattr(core, "last_update_success", None),
                "skipped_for_core": sorted(skip),
                "features_covered_by_core": sorted(core_features),
                "feature_count": len(device.features),
                "features": {
                    name: _redact_feature(name, feature)
                    for name, feature in sorted(device.features.items())
                },
            }
        )
    return {
        "options": dict(entry.options),
        "core_description_keys": sorted(f"{p}.{k}" for p, k in coordinator.core_keys),
        "last_update_success": coordinator.last_update_success,
        "devices": devices,
    }
