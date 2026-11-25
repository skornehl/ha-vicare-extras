"""Fakes shared by the tests (no Home Assistant instance needed)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

from custom_components.vicare_extras.coordinator import ExtrasDevice


def num(value: float = 1, unit: str | None = None) -> dict[str, Any]:
    prop: dict[str, Any] = {"type": "number", "value": value}
    if unit:
        prop["unit"] = unit
    return prop


def feature(name: str, properties: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    return {"feature": name, "isEnabled": True, "properties": properties or {}, **extra}


def device(features: list[dict[str, Any]], identifier: str = "gw_dev", **kw: Any) -> ExtrasDevice:
    return ExtrasDevice(
        identifier, "E3 Vitodens 200", {f["feature"]: f for f in features},
        kw.get("cache_time", datetime.now()),
    )


class FakeCoreCoordinator:
    """Stands in for the core ViCareCoordinator."""

    def __init__(self, success: bool = True, interval: timedelta = timedelta(seconds=60)) -> None:
        self.last_update_success = success
        self.update_interval = interval
        self.listeners: list = []
        self.refresh_requests = 0

    def async_add_listener(self, listener):
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)

    async def async_request_refresh(self) -> None:
        self.refresh_requests += 1


class FakeEntry:
    def __init__(self, options: dict[str, Any] | None = None) -> None:
        self.options = options or {}
        self.entry_id = "extras_entry"
        self.tasks: list[asyncio.Task] = []

    def async_create_background_task(self, hass, target, name, eager_start=True):
        task = asyncio.get_running_loop().create_task(target, name=name)
        self.tasks.append(task)
        return task


class FakeCoordinator:
    """Just what commands.py and services.py use."""

    def __init__(self, devices: dict[str, ExtrasDevice], core_devices: dict[str, Any] | None = None,
                 options: dict[str, Any] | None = None) -> None:
        self.data = devices
        self.core_devices = core_devices or {}
        self.config_entry = FakeEntry(options)
        self.confirm_tasks: dict[Any, asyncio.Task] = {}
        self.updates = 0
        self.held = 0.0
        self.refresh_requests = 0

    def async_set_updated_data(self, data) -> None:
        self.data = data
        self.updates += 1

    def hold(self, seconds: float) -> None:
        self.held = seconds

    async def async_request_refresh(self) -> None:
        self.refresh_requests += 1


def core_device(core_coordinator: Any = None) -> SimpleNamespace:
    calls: list = []
    api = SimpleNamespace(setProperty=lambda *args: calls.append(args), calls=calls)
    return SimpleNamespace(api=api, coordinator=core_coordinator)


class FakeHass:
    async def async_add_executor_job(self, func, *args):
        return func(*args)

    async def async_add_import_executor_job(self, func, *args):
        return func(*args)
