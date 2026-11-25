"""Validation and execution of ViCare commands."""

from __future__ import annotations

import asyncio

import pytest

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.vicare_extras import commands
from custom_components.vicare_extras.commands import async_execute, validate_command

from .common import FakeCoordinator, FakeCoreCoordinator, FakeHass, core_device, device, feature, num


def _feature(params: dict, executable: bool = True, name: str = "heating.dhw.temperature.hygiene"):
    return feature(
        name,
        {"value": num(60, "celsius")},
        commands={"cmd": {"isExecutable": executable, "params": params}},
    )


def _key(err: pytest.ExceptionInfo) -> str:
    return err.value.translation_key


NUMBER = {"v": {"type": "number", "required": True, "constraints": {"min": 10, "max": 70, "stepping": 5}}}


async def test_number_ok_and_integer_normalised() -> None:
    assert validate_command(_feature(NUMBER), "cmd", {"v": 55.0}) == {"v": 55}


@pytest.mark.parametrize(
    ("value", "key"),
    [(5, "param_below_min"), (75, "param_above_max"), (57, "param_step"), ("60", "param_not_number"), (True, "param_not_number")],
)
async def test_number_rejected(value, key) -> None:
    with pytest.raises(ServiceValidationError) as err:
        validate_command(_feature(NUMBER), "cmd", {"v": value})
    assert _key(err) == key
    assert err.value.translation_domain == "vicare_extras"


async def test_boolean_and_string() -> None:
    params = {
        "b": {"type": "boolean"},
        "s": {"type": "string", "constraints": {"enum": ["a", "b"]}},
    }
    assert validate_command(_feature(params), "cmd", {"b": True, "s": "a"}) == {"b": True, "s": "a"}
    with pytest.raises(ServiceValidationError) as err:
        validate_command(_feature(params), "cmd", {"b": 1})
    assert _key(err) == "param_not_boolean"
    with pytest.raises(ServiceValidationError) as err:
        validate_command(_feature(params), "cmd", {"s": "c"})
    assert _key(err) == "param_not_allowed"


async def test_regex() -> None:
    params = {"d": {"type": "string", "constraints": {"regEx": r"^\d{4}-\d{2}-\d{2}$"}}}
    assert validate_command(_feature(params), "cmd", {"d": "2026-12-24"}) == {"d": "2026-12-24"}
    with pytest.raises(ServiceValidationError) as err:
        validate_command(_feature(params), "cmd", {"d": "24.12.2026"})
    assert _key(err) == "param_bad_format"


async def test_invalid_api_regex_is_rejected_not_raised() -> None:
    params = {"d": {"type": "string", "constraints": {"regEx": "([unclosed"}}}
    with pytest.raises(ServiceValidationError) as err:
        validate_command(_feature(params), "cmd", {"d": "x"})
    assert _key(err) == "param_bad_regex"


async def test_unknown_param_type_is_rejected() -> None:
    params = {"x": {"type": "Array"}}
    with pytest.raises(ServiceValidationError) as err:
        validate_command(_feature(params), "cmd", {"x": [1]})
    assert _key(err) == "param_unsupported_type"


@pytest.mark.parametrize(
    ("feat", "command", "params", "key"),
    [
        (_feature(NUMBER), "other", {}, "unknown_command"),
        (_feature(NUMBER, executable=False), "cmd", {"v": 60}, "not_executable"),
        (_feature(NUMBER), "cmd", {"v": 60, "w": 1}, "unknown_params"),
        (_feature(NUMBER), "cmd", {}, "missing_param"),
        (_feature(NUMBER, name="device.setDefaultValues"), "cmd", {"v": 60}, "feature_blocked"),
    ],
)
async def test_command_rejected(feat, command, params, key) -> None:
    with pytest.raises(ServiceValidationError) as err:
        validate_command(feat, command, params)
    assert _key(err) == key


SCHEDULE_C = {"maxEntries": 2, "resolution": 10, "modes": ["on"], "overlapAllowed": False}


def _slot(start: str, end: str, mode: str = "on") -> dict:
    return {"start": start, "end": end, "mode": mode}


async def test_schedule_ok_with_end_2400() -> None:
    week = {"mon": [_slot("05:30", "06:30"), _slot("22:00", "24:00")]}
    assert commands._check_schedule("newSchedule", SCHEDULE_C, week) == week


@pytest.mark.parametrize(
    ("week", "key"),
    [
        ({"mon": [_slot("24:00", "24:00")]}, "schedule_start_24"),
        ({"mon": [_slot("05:35", "06:30")]}, "schedule_raster"),
        ({"mon": [_slot("06:00", "05:00")]}, "schedule_end_before_start"),
        ({"mon": [_slot("05:00", "07:00"), _slot("06:00", "08:00")]}, "schedule_overlap"),
        ({"mon": [_slot("05:00", "06:00", "eco")]}, "schedule_bad_mode"),
        ({"mon": [_slot("01:00", "02:00"), _slot("03:00", "04:00"), _slot("05:00", "06:00")]}, "schedule_too_many"),
        ({"mon": [{"start": "5"}]}, "schedule_bad_entry"),
        ({"mon": [_slot("05:00", "24:30")]}, "schedule_bad_entry"),
        ({"funday": []}, "schedule_unknown_day"),
        ({"mon": "05:00"}, "schedule_entries_not_list"),
        (["mon"], "schedule_not_dict"),
    ],
)
async def test_schedule_rejected(week, key) -> None:
    with pytest.raises(ServiceValidationError) as err:
        commands._check_schedule("newSchedule", SCHEDULE_C, week)
    assert _key(err) == key


def _setup(core_coordinator=None):
    dev = device([_feature(NUMBER)])
    core = core_device(core_coordinator)
    coordinator = FakeCoordinator({dev.identifier: dev}, {dev.identifier: core})
    return coordinator, core, dev


async def _cancel(coordinator: FakeCoordinator) -> None:
    for task in coordinator.config_entry.tasks:
        task.cancel()
    await asyncio.gather(*coordinator.config_entry.tasks, return_exceptions=True)


async def test_execute_sends_patches_and_holds() -> None:
    coordinator, core, dev = _setup(FakeCoreCoordinator())
    fname = "heating.dhw.temperature.hygiene"
    await async_execute(FakeHass(), coordinator, dev.identifier, fname, "cmd", {"v": 65}, {"value": 65})
    assert core.api.calls == [(fname, "cmd", {"v": 65})]
    assert dev.features[fname]["properties"]["value"]["value"] == 65
    assert coordinator.updates == 1
    assert coordinator.held == commands.HOLD_AFTER_WRITE
    await _cancel(coordinator)


async def test_one_confirmation_per_core_coordinator(monkeypatch) -> None:
    monkeypatch.setattr(commands, "CONFIRM_DELAY", 0)
    core_coord = FakeCoreCoordinator()
    coordinator, _, dev = _setup(core_coord)
    fname = "heating.dhw.temperature.hygiene"
    await async_execute(FakeHass(), coordinator, dev.identifier, fname, "cmd", {"v": 60})
    first = coordinator.confirm_tasks[id(core_coord)]
    await async_execute(FakeHass(), coordinator, dev.identifier, fname, "cmd", {"v": 65})
    assert len(coordinator.confirm_tasks) == 1
    await asyncio.sleep(0)
    await asyncio.gather(*coordinator.config_entry.tasks, return_exceptions=True)
    assert first.cancelled()
    # Exactly one debounced request to the core, then our own refresh.
    assert core_coord.refresh_requests == 1
    assert coordinator.refresh_requests == 1


async def test_execute_without_current_data() -> None:
    coordinator, _, dev = _setup()
    dev.features = {}
    with pytest.raises(ServiceValidationError) as err:
        await async_execute(FakeHass(), coordinator, dev.identifier, "x", "cmd")
    assert _key(err) == "no_current_data"


async def test_execute_api_error_is_translated() -> None:
    coordinator, core, dev = _setup()

    def fail(*args):
        raise RuntimeError("boom")

    core.api.setProperty = fail
    with pytest.raises(HomeAssistantError) as err:
        await async_execute(
            FakeHass(), coordinator, dev.identifier, "heating.dhw.temperature.hygiene", "cmd", {"v": 60}
        )
    assert _key(err) == "command_failed"
    assert coordinator.config_entry.tasks == []
