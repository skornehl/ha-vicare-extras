"""Schedule helpers."""

from __future__ import annotations

import pytest
import voluptuous as vol

from homeassistant.exceptions import ServiceValidationError

from custom_components.vicare_extras import schedules
from custom_components.vicare_extras.schedules import format_day, schedule_feature

from .common import feature


def _features(*circuits: tuple[int, bool]) -> dict:
    out = {}
    for circuit, enabled in circuits:
        name = f"heating.circuits.{circuit}.heating.schedule"
        out[name] = {**feature(name), "isEnabled": enabled}
    return out


async def test_fixed_schedule_keys() -> None:
    assert schedule_feature("dhw") == "heating.dhw.schedule"
    assert schedule_feature("circulation") == "heating.dhw.pumps.circulation.schedule"
    assert schedule_feature("heating_circuit_2") == "heating.circuits.2.heating.schedule"


async def test_heating_is_lowest_enabled_circuit() -> None:
    # Circuit 0 exists but is disabled (common on Vitodens: circuit 1 is the active one).
    feats = _features((0, False), (2, True), (1, True))
    assert schedule_feature("heating", feats) == "heating.circuits.1.heating.schedule"
    assert schedule_feature("heating", _features((0, True), (1, True))) == (
        "heating.circuits.0.heating.schedule"
    )


async def test_heating_without_schedule() -> None:
    assert schedule_feature("heating", _features((1, False))) is None
    assert schedule_feature("heating") is None


async def test_unknown_key() -> None:
    with pytest.raises(ServiceValidationError) as err:
        schedule_feature("pool")
    assert err.value.translation_key == "unknown_schedule"


async def test_entry_schema_start_2400_rejected() -> None:
    assert schedules.ENTRY({"start": "22:00", "end": "24:00"})
    with pytest.raises(vol.Invalid):
        schedules.ENTRY({"start": "24:00", "end": "24:00"})


async def test_normalize_default_mode_and_positions() -> None:
    out = schedules._normalize(
        [{"start": "17:00", "end": "22:00"}, {"start": "05:30", "end": "06:30"}], {"modes": ["on"]}
    )
    assert out == [
        {"start": "05:30", "end": "06:30", "mode": "on", "position": 0},
        {"start": "17:00", "end": "22:00", "mode": "on", "position": 1},
    ]
    with pytest.raises(ServiceValidationError) as err:
        schedules._normalize([{"start": "05:30", "end": "06:30"}], {"modes": ["comfort", "eco"]})
    assert err.value.translation_key == "mode_required"


async def test_format_day() -> None:
    assert format_day([]) == "–"
    assert format_day(
        [{"start": "17:00", "end": "22:00", "mode": "comfort"}, {"start": "05:30", "end": "06:30", "mode": "on"}]
    ) == "05:30–06:30, 17:00–22:00 (comfort)"
