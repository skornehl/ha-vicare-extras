"""Device selection of the services (no Home Assistant instance needed)."""

from __future__ import annotations

import pytest

from homeassistant.exceptions import ServiceValidationError

from custom_components.vicare_extras.services import resolve_device

from .common import FakeCoordinator, device, feature

HOLIDAY = "heating.operating.programs.holiday"


def _supports(name: str):
    return lambda features: name in features


async def test_single_device_is_used() -> None:
    boiler = device([feature(HOLIDAY)], "gw_boiler")
    other = device([feature("heating.dhw.schedule")], "gw_other")
    coordinator = FakeCoordinator({"gw_boiler": boiler, "gw_other": other})
    assert resolve_device(None, coordinator, _supports(HOLIDAY), HOLIDAY, None) == "gw_boiler"


async def test_several_devices_need_device_id() -> None:
    coordinator = FakeCoordinator(
        {"a": device([feature(HOLIDAY)], "a"), "b": device([feature(HOLIDAY)], "b")}
    )
    with pytest.raises(ServiceValidationError) as err:
        resolve_device(None, coordinator, _supports(HOLIDAY), HOLIDAY, None)
    assert err.value.translation_key == "multiple_devices"
    assert err.value.translation_placeholders == {"feature": HOLIDAY, "count": "2"}


async def test_no_device() -> None:
    coordinator = FakeCoordinator({"a": device([], "a")})
    with pytest.raises(ServiceValidationError) as err:
        resolve_device(None, coordinator, _supports(HOLIDAY), HOLIDAY, None)
    assert err.value.translation_key == "no_device_with_feature"
