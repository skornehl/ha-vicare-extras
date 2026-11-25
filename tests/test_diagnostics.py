"""Diagnostics redact private data."""

from __future__ import annotations

from datetime import datetime
import json
from types import SimpleNamespace

from custom_components.vicare_extras.coordinator import ExtrasDevice
from custom_components.vicare_extras.diagnostics import async_get_config_entry_diagnostics

from .common import feature, num

PRIVATE = ["7633107000000000", "88884444", "SERIAL7777X", "zigbee-0123456789abcdef", "52.52", "13.37", "a@b.c", "aa:bb:cc"]


def _entry():
    uri = "https://api.viessmann.com/iot/v2/features/installations/88884444/gateways/7633107000000000/devices/0/features/x"
    features = {
        "device.serial": {**feature("device.serial", {"value": {"type": "string", "value": "7633107000000000"}}), "uri": uri, "gatewayId": "7633107000000000", "deviceId": "0"},
        "heating.boiler.serial": feature("heating.boiler.serial", {"value": {"type": "string", "value": "SERIAL7777X"}}),
        "heating.operating.programs.houseLocation": feature(
            "heating.operating.programs.houseLocation",
            {"latitude": num(52.52), "longitude": num(13.37)},
        ),
        "device.messages.info.raw": feature(
            "device.messages.info.raw", {"entries": {"type": "array", "value": [{"email": "a@b.c", "macAddress": "aa:bb:cc"}]}}
        ),
        "heating.boiler.temperature": feature("heating.boiler.temperature", {"value": num(50, "celsius")}),
    }
    dev = ExtrasDevice("7633107000000000_zigbee_0123456789abcdef", "E3 Vitodens 200", features, datetime(2026, 1, 1))
    coordinator = SimpleNamespace(
        data={dev.identifier: dev},
        core_devices={},
        core_keys=frozenset({("sensor", "outside_temperature")}),
        last_update_success=True,
        overlap=lambda identifier: (frozenset(), frozenset()),
    )
    return SimpleNamespace(runtime_data=coordinator, options={"calorific_value": 11.5})


async def test_diagnostics_redacts_private_data() -> None:
    result = await async_get_config_entry_diagnostics(None, _entry())
    text = json.dumps(result, default=str)
    for value in PRIVATE:
        assert value not in text, value
    device = result["devices"][0]
    assert device["device"] == "device_1"
    # Ordinary values stay readable.
    assert device["features"]["heating.boiler.temperature"]["properties"]["value"]["value"] == 50
    assert result["options"] == {"calorific_value": 11.5}
