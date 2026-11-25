"""DHW hygiene entities."""

from __future__ import annotations

from datetime import time

import pytest

from homeassistant.components.number import NumberDeviceClass
from homeassistant.exceptions import ServiceValidationError

from custom_components.vicare_extras.entity import device_info
from custom_components.vicare_extras.hygiene import (
    F_HYGIENE,
    F_TRIGGER,
    HygieneDay,
    HygieneStartTime,
    HygieneSwitch,
    HygieneTemperature,
    hygiene_keys,
)

from .common import FakeCoordinator, device, feature, num


def _trigger(weekdays: list[str], hour: int = 3, minute: int = 0) -> dict:
    return feature(
        F_TRIGGER,
        {
            "startHour": num(hour),
            "startMinute": num(minute),
            "weekdays": {"type": "array", "value": weekdays},
        },
    )


def _setup(*features: dict):
    dev = device(list(features))
    return FakeCoordinator({dev.identifier: dev}), dev


class _Recorder:
    def __init__(self) -> None:
        self.calls: list = []

    async def __call__(self, feature, command, params=None, optimistic=None) -> None:
        self.calls.append((feature, command, params))


async def test_switch_unknown_when_enabled_missing() -> None:
    coordinator, dev = _setup(feature(F_HYGIENE, {"active": {"type": "boolean", "value": False}}))
    assert HygieneSwitch(coordinator, dev).is_on is None
    dev.features[F_HYGIENE]["properties"]["enabled"] = {"type": "boolean", "value": True}
    assert HygieneSwitch(coordinator, dev).is_on is True


@pytest.mark.parametrize(
    ("weekdays", "option"),
    [([], "daily"), (["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], "daily"), (["Sat"], "sat"), (["Mon", "Thu"], None)],
)
async def test_day_option(weekdays, option) -> None:
    coordinator, dev = _setup(_trigger(weekdays))
    assert HygieneDay(coordinator, dev).current_option == option


async def test_time_change_keeps_single_weekday() -> None:
    coordinator, dev = _setup(_trigger(["Sat"]))
    entity = HygieneStartTime(coordinator, dev)
    entity.run = _Recorder()
    await entity.async_set_value(time(4, 30))
    assert entity.run.calls == [
        (F_TRIGGER, "triggerOncePerWeek", {"startHour": 4, "startMinute": 30, "weekday": "Sat"})
    ]


async def test_time_change_on_daily_trigger() -> None:
    coordinator, dev = _setup(_trigger([]))
    entity = HygieneStartTime(coordinator, dev)
    entity.run = _Recorder()
    await entity.async_set_value(time(4, 30))
    assert entity.run.calls == [(F_TRIGGER, "triggerDaily", {"startHour": 4, "startMinute": 30})]


async def test_time_change_never_turns_several_weekdays_into_daily() -> None:
    coordinator, dev = _setup(_trigger(["Mon", "Thu"]))
    entity = HygieneStartTime(coordinator, dev)
    entity.run = _Recorder()
    with pytest.raises(ServiceValidationError) as err:
        await entity.async_set_value(time(4, 30))
    assert err.value.translation_key == "hygiene_multiple_weekdays"
    assert entity.run.calls == []


async def test_disabled_hygiene_blocks_trigger_changes() -> None:
    coordinator, dev = _setup(
        _trigger([]), feature(F_HYGIENE, {"enabled": {"type": "boolean", "value": False}})
    )
    with pytest.raises(ServiceValidationError) as err:
        await HygieneDay(coordinator, dev).async_select_option("mon")
    assert err.value.translation_key == "hygiene_disabled"


async def test_number_and_device_info() -> None:
    temp = feature("heating.dhw.temperature.hygiene", {"value": num(60, "celsius")})
    coordinator, dev = _setup(temp)
    entity = HygieneTemperature(coordinator, dev)
    assert entity.device_class is NumberDeviceClass.TEMPERATURE
    assert entity.native_value == 60
    assert entity.device_info == device_info(dev)
    assert entity.unique_id == f"{dev.identifier}-extras-dhw_hygiene_temperature"


async def test_hygiene_keys() -> None:
    _, dev = _setup(_trigger([]), feature(F_HYGIENE))
    assert hygiene_keys(dev) == {
        "dhw_hygiene_enabled", "dhw_hygiene_activate", "dhw_hygiene_day", "dhw_hygiene_time"
    }
