"""Reading the core cache: staleness, write hold, devices, core listeners."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import time
from types import SimpleNamespace

from custom_components.vicare_extras.coordinator import (
    ExtrasDevice,
    ViCareExtrasCoordinator,
    _read_device,
    is_stale,
    max_age,
    shape_of,
)

from .common import FakeCoreCoordinator, feature


def _coordinator() -> ViCareExtrasCoordinator:
    """Only the state _settle and the listener sync use - no hass needed."""
    coordinator = ViCareExtrasCoordinator.__new__(ViCareExtrasCoordinator)
    coordinator._stale_logged = set()
    coordinator._core_listeners = {}
    return coordinator


def _snap(age: timedelta | None, features: bool = True) -> ExtrasDevice:
    feats = {"f": feature("f")} if features else {}
    return ExtrasDevice("gw_dev", "model", feats, None if age is None else datetime.now() - age)


async def test_max_age() -> None:
    assert max_age(None) == timedelta(minutes=5)
    assert max_age(FakeCoreCoordinator(interval=timedelta(seconds=180))) == timedelta(minutes=9)


async def test_is_stale_naive_and_aware() -> None:
    limit = timedelta(minutes=5)
    assert not is_stale(None, limit)
    assert not is_stale(datetime.now() - timedelta(minutes=1), limit)
    assert is_stale(datetime.now() - timedelta(minutes=6), limit)
    assert is_stale(datetime.now(timezone.utc) - timedelta(minutes=6), limit)


async def test_fresh_snapshot_is_used() -> None:
    snap = _snap(timedelta(seconds=10))
    assert _coordinator()._settle(snap, FakeCoreCoordinator(), None, False) is snap


async def test_stale_cache_makes_device_unavailable() -> None:
    snap = _snap(timedelta(hours=1))
    out = _coordinator()._settle(snap, FakeCoreCoordinator(), None, False)
    assert out.features == {}
    assert out.cache_time == snap.cache_time  # "API data from" keeps the fetch time


async def test_empty_cache_keeps_recent_previous() -> None:
    previous = _snap(timedelta(seconds=30))
    out = _coordinator()._settle(_snap(None, False), FakeCoreCoordinator(), previous, False)
    assert out is previous


async def test_empty_cache_does_not_keep_old_previous_forever() -> None:
    previous = _snap(timedelta(hours=2))
    out = _coordinator()._settle(_snap(None, False), FakeCoreCoordinator(), previous, False)
    assert out.features == {}
    assert out.cache_time == previous.cache_time


async def test_empty_cache_after_failed_core_fetch() -> None:
    previous = _snap(timedelta(seconds=30))
    out = _coordinator()._settle(_snap(None, False), FakeCoreCoordinator(success=False), previous, False)
    assert out.features == {}


async def test_write_hold_keeps_previous() -> None:
    previous = _snap(timedelta(hours=2))
    out = _coordinator()._settle(_snap(None, False), FakeCoreCoordinator(success=False), previous, True)
    assert out is previous


async def test_hold_window() -> None:
    coordinator = _coordinator()
    coordinator._hold_until = 0.0
    coordinator.hold(80)
    assert coordinator._hold_until > time.monotonic() + 70


async def test_core_listeners_follow_core_coordinators() -> None:
    coordinator = _coordinator()
    first, second = FakeCoreCoordinator(), FakeCoreCoordinator()
    coordinator._sync_core_listeners([first, first, None])
    assert len(first.listeners) == 1
    coordinator._sync_core_listeners([second])  # core entry reloaded
    assert first.listeners == [] and len(second.listeners) == 1
    coordinator.async_unsubscribe_core()
    assert second.listeners == []


async def test_shape_changes_only_with_structure() -> None:
    a = {"d": ExtrasDevice("d", "m", {"f": feature("f", {"value": {"value": 1}})})}
    b = {"d": ExtrasDevice("d", "m", {"f": feature("f", {"value": {"value": 2}})})}
    c = {"d": ExtrasDevice("d", "m", {"f": feature("f", {"other": {"value": 2}})})}
    assert shape_of(a) == shape_of(b)
    assert shape_of(a) != shape_of(c)
    assert shape_of(a) != shape_of({**a, "e": ExtrasDevice("e", "m")})


def _core_dev(features: list[dict], cache_time: datetime | None) -> SimpleNamespace:
    service = SimpleNamespace(
        _cache={"data": features},
        _cacheTime=cache_time,
        _extract_entities=lambda data, accessor: [
            f for f in data["data"] if f.get("deviceId") == accessor.device_id
        ],
    )
    api = SimpleNamespace(service=service, accessor=SimpleNamespace(device_id="0"))
    config = SimpleNamespace(
        getConfig=lambda: SimpleNamespace(serial="7633"),
        getModel=lambda: "E3_Vitodens_200",
        getId=lambda: "0",
    )
    return SimpleNamespace(api=api, config=config, serial="dev-1")


async def test_read_device_filters_gateway_payload() -> None:
    now = datetime.now()
    dev = _core_dev(
        [{**feature("a"), "deviceId": "0"}, {**feature("b"), "deviceId": "1"}], now
    )
    snap = _read_device(dev)
    assert snap.identifier == "7633_dev_1"
    assert snap.model == "E3 Vitodens 200"
    assert list(snap.features) == ["a"]
    assert snap.cache_time == now
