"""Full setup against a fake core ViCare entry (needs the test harness)."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er

from custom_components.vicare_extras.const import CONF_ALLOW_RAW_COMMANDS, DOMAIN

from .common import FakeCoreCoordinator, feature, num

IDENT = "7633_dev_1"


def _core_device(features: list[dict], core: FakeCoreCoordinator) -> SimpleNamespace:
    service = SimpleNamespace(_cache={"data": features}, _cacheTime=datetime.now())
    api = SimpleNamespace(service=service, setProperty=lambda *args: None)
    config = SimpleNamespace(
        getConfig=lambda: SimpleNamespace(serial="7633"),
        getModel=lambda: "E3_Vitodens_200",
        getId=lambda: "0",
    )
    return SimpleNamespace(api=api, config=config, serial="dev-1", coordinator=core)


def _features() -> list[dict]:
    return [
        feature("heating.boiler.temperature", {"value": num(50, "celsius")}),
        feature("heating.boiler.pumps.internal", {"status": {"type": "string", "value": "on"}}),
        feature("heating.boiler.pumps.internal.current", {"value": num(40, "percent")}),
        feature("heating.gas.consumption.summary.heating", {"currentDay": num(2, "cubicMeter")}),
    ]


async def _setup(hass, options: dict, core_entities: list[tuple[str, str]] = ()):
    hass.config.components.add("vicare")
    core = FakeCoreCoordinator()
    dev = _core_device(_features(), core)
    vicare = MockConfigEntry(domain="vicare")
    vicare.add_to_hass(hass)
    vicare.mock_state(hass, ConfigEntryState.LOADED)
    vicare.runtime_data = SimpleNamespace(devices=[dev])
    registry = er.async_get(hass)
    for domain, key in core_entities:
        registry.async_get_or_create(domain, "vicare", f"{IDENT}-{key}", config_entry=vicare)
    entry = MockConfigEntry(domain=DOMAIN, options=options, minor_version=2)
    entry.add_to_hass(hass)
    return entry, dev, core, registry


def _uid(registry, domain: str, key: str) -> str | None:
    return registry.async_get_entity_id(domain, DOMAIN, f"{IDENT}-extras-{key}")


async def test_setup_creates_entities_and_listens_to_core(extras_hass) -> None:
    hass = extras_hass
    entry, dev, core, registry = await _setup(hass, {"calorific_value": 11.5, CONF_ALLOW_RAW_COMMANDS: False})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert _uid(registry, "sensor", "boiler_target_temperature")
    assert _uid(registry, "binary_sensor", "boiler_pump_internal")
    assert _uid(registry, "sensor", "gas_energy_heating_currentDay")
    assert _uid(registry, "sensor", "api_data_from")
    assert len(core.listeners) == 1

    # A feature appearing later gets its entity without a reload.
    dev.api.service._cache["data"].append(
        feature("heating.sensors.temperature.hydraulicSeparator", {"value": num(40, "celsius")})
    )
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert _uid(registry, "sensor", "hydraulic_separator_temperature")

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert core.listeners == []


async def test_core_takeover_removes_extras_entities(extras_hass) -> None:
    hass = extras_hass
    entry, _, _, registry = await _setup(
        hass,
        {CONF_ALLOW_RAW_COMMANDS: False},
        [("binary_sensor", "boiler_internal_pump_active"), ("sensor", "boiler_internal_pump_modulation")],
    )
    # Left over from an older version: both must go.
    registry.async_get_or_create(
        "binary_sensor", DOMAIN, f"{IDENT}-extras-boiler_pump_internal", config_entry=entry
    )
    registry.async_get_or_create(
        "sensor", DOMAIN, f"{IDENT}-extras-x_heating_boiler_pumps_internal_current_value", config_entry=entry
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    keys = await _core_keys(hass)
    if ("binary_sensor", "boiler_internal_pump_active") in keys:
        assert _uid(registry, "binary_sensor", "boiler_pump_internal") is None
    if ("sensor", "boiler_internal_pump_modulation") in keys:
        assert _uid(registry, "sensor", "x_heating_boiler_pumps_internal_current_value") is None
    assert _uid(registry, "sensor", "boiler_target_temperature")
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_cleared_calorific_value_removes_gas_energy(extras_hass) -> None:
    hass = extras_hass
    entry, _, _, registry = await _setup(hass, {CONF_ALLOW_RAW_COMMANDS: False})
    registry.async_get_or_create(
        "sensor", DOMAIN, f"{IDENT}-extras-gas_energy_heating_currentDay", config_entry=entry
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert _uid(registry, "sensor", "gas_energy_heating_currentDay") is None
    assert await hass.config_entries.async_unload(entry.entry_id)


async def _core_keys(hass):
    from custom_components.vicare_extras.core_overlap import async_core_keys

    return await async_core_keys(hass)
