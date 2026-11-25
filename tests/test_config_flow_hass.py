"""Config and options flow (needs the test harness)."""

from __future__ import annotations

from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry
import voluptuous as vol

from homeassistant.data_entry_flow import FlowResultType

from custom_components.vicare_extras.const import (
    CONF_ALLOW_RAW_COMMANDS,
    CONF_CALORIFIC_VALUE,
    DOMAIN,
)

SETUP = "custom_components.vicare_extras.async_setup_entry"
UNLOAD = "custom_components.vicare_extras.async_unload_entry"


def _suggested(schema, key):
    for marker in schema.schema:
        if marker == key:
            return (marker.description or {}).get("suggested_value"), marker.default
    raise AssertionError(key)


async def test_user_flow_needs_vicare(extras_hass) -> None:
    hass = extras_hass
    hass.config.components.add("vicare")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_vicare"


async def test_user_flow_creates_entry_with_raw_commands_off(extras_hass) -> None:
    hass = extras_hass
    hass.config.components.add("vicare")
    MockConfigEntry(domain="vicare").add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.FORM
    with patch(SETUP, return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_CALORIFIC_VALUE: 11.479}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == {CONF_CALORIFIC_VALUE: 11.479, CONF_ALLOW_RAW_COMMANDS: False}


async def test_options_flow_suggests_and_clears_calorific_value(extras_hass) -> None:
    hass = extras_hass
    hass.config.components.add("vicare")
    entry = MockConfigEntry(
        domain=DOMAIN,
        options={CONF_CALORIFIC_VALUE: 11.5, CONF_ALLOW_RAW_COMMANDS: True},
        minor_version=2,
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    suggested, default = _suggested(result["data_schema"], CONF_CALORIFIC_VALUE)
    assert suggested == 11.5
    # No default: an emptied field must stay empty.
    assert default is vol.UNDEFINED
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_ALLOW_RAW_COMMANDS: False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_ALLOW_RAW_COMMANDS: False}


async def test_options_flow_changes_calorific_value(extras_hass) -> None:
    hass = extras_hass
    hass.config.components.add("vicare")
    entry = MockConfigEntry(domain=DOMAIN, options={CONF_CALORIFIC_VALUE: 11.5}, minor_version=2)
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_CALORIFIC_VALUE: 10.9, CONF_ALLOW_RAW_COMMANDS: True}
    )
    assert entry.options == {CONF_CALORIFIC_VALUE: 10.9, CONF_ALLOW_RAW_COMMANDS: True}


async def test_migration_keeps_raw_commands_for_existing_entries(extras_hass) -> None:
    hass = extras_hass
    hass.config.components.add("vicare")
    entry = MockConfigEntry(domain=DOMAIN, options={CONF_CALORIFIC_VALUE: 11.5}, minor_version=1)
    entry.add_to_hass(hass)
    with patch(SETUP, return_value=True), patch(UNLOAD, return_value=True):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.minor_version == 2
        assert entry.options == {CONF_CALORIFIC_VALUE: 11.5, CONF_ALLOW_RAW_COMMANDS: True}
        assert await hass.config_entries.async_unload(entry.entry_id)
