"""Services against a real Home Assistant (needs the test harness)."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ServiceValidationError, Unauthorized
from homeassistant.setup import async_setup_component

from custom_components.vicare_extras.const import CONF_ALLOW_RAW_COMMANDS, DOMAIN

from .common import FakeCoordinator, core_device, device, feature, num

FEATURE = "heating.dhw.temperature.hygiene"
WRITE_SERVICES = [
    "set_holiday",
    "change_holiday_end",
    "cancel_holiday",
    "execute_command",
    "set_schedule_day",
    "copy_schedule_day",
    "reset_schedule_day",
    "reset_schedule",
]


async def _setup(hass, options: dict) -> FakeCoordinator:
    # The manifest depends on vicare; pretend it is set up (no cloud login).
    hass.config.components.add("vicare")
    assert await async_setup_component(hass, DOMAIN, {})
    dev = device(
        [
            feature(
                FEATURE,
                {"value": num(60, "celsius")},
                commands={
                    "setTemperature": {
                        "isExecutable": True,
                        "params": {"targetTemperature": {"type": "number", "required": True,
                                                         "constraints": {"min": 50, "max": 70, "stepping": 1}}},
                    }
                },
            )
        ]
    )
    core = core_device()
    coordinator = FakeCoordinator({dev.identifier: dev}, {dev.identifier: core}, options)
    entry = MockConfigEntry(domain=DOMAIN, options=options, minor_version=2)
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    entry.runtime_data = coordinator
    coordinator.config_entry.async_create_background_task = (
        lambda hass_, target, name, eager_start=True: entry.async_create_background_task(
            hass_, target, name, eager_start
        )
    )
    return coordinator


async def test_services_registered_in_async_setup(extras_hass) -> None:
    hass = extras_hass
    hass.config.components.add("vicare")
    assert await async_setup_component(hass, DOMAIN, {})
    for service in [*WRITE_SERVICES, "get_schedule"]:
        assert hass.services.has_service(DOMAIN, service)


async def test_execute_command_needs_option(extras_hass) -> None:
    await _setup(extras_hass, {CONF_ALLOW_RAW_COMMANDS: False})
    with pytest.raises(ServiceValidationError) as err:
        await extras_hass.services.async_call(
            DOMAIN,
            "execute_command",
            {"feature": FEATURE, "command": "setTemperature", "params": {"targetTemperature": 65}},
            blocking=True,
        )
    assert err.value.translation_key == "raw_commands_disabled"


async def test_execute_command_allowed(extras_hass) -> None:
    coordinator = await _setup(extras_hass, {CONF_ALLOW_RAW_COMMANDS: True})
    await extras_hass.services.async_call(
        DOMAIN,
        "execute_command",
        {"feature": FEATURE, "command": "setTemperature", "params": {"targetTemperature": 65}},
        blocking=True,
    )
    core = next(iter(coordinator.core_devices.values()))
    assert core.api.calls == [(FEATURE, "setTemperature", {"targetTemperature": 65})]
    for task in coordinator.confirm_tasks.values():
        task.cancel()


async def test_write_services_are_admin_only(extras_hass, hass_read_only_user) -> None:
    from homeassistant.core import Context

    await _setup(extras_hass, {CONF_ALLOW_RAW_COMMANDS: True})
    with pytest.raises(Unauthorized):
        await extras_hass.services.async_call(
            DOMAIN,
            "execute_command",
            {"feature": FEATURE, "command": "setTemperature", "params": {"targetTemperature": 65}},
            blocking=True,
            context=Context(user_id=hass_read_only_user.id),
        )
    with pytest.raises(Unauthorized):
        await extras_hass.services.async_call(
            DOMAIN,
            "cancel_holiday",
            {"program": "holiday"},
            blocking=True,
            context=Context(user_id=hass_read_only_user.id),
        )
