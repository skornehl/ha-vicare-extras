"""Config flow for ViCare Extras - reuses ViCare, only optional settings."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .const import CONF_ALLOW_RAW_COMMANDS, CONF_CALORIFIC_VALUE, DOMAIN, VICARE_DOMAIN

CALORIFIC_SELECTOR = NumberSelector(
    NumberSelectorConfig(
        min=5, max=15, step=0.001, mode=NumberSelectorMode.BOX, unit_of_measurement="kWh/m³"
    )
)


def _calorific_schema(current: float | None) -> dict[vol.Marker, Any]:
    # suggested_value, not default: a default would be filled in again when
    # the user empties the field, so the value could never be removed.
    return {
        vol.Optional(
            CONF_CALORIFIC_VALUE, description={"suggested_value": current}
        ): CALORIFIC_SELECTOR
    }


def _clean(user_input: dict[str, Any]) -> dict[str, Any]:
    """Drop an emptied calorific value: no value means no gas energy sensors."""
    options = dict(user_input)
    if options.get(CONF_CALORIFIC_VALUE) in (None, ""):
        options.pop(CONF_CALORIFIC_VALUE, None)
    return options


class ViCareExtrasConfigFlow(ConfigFlow, domain=DOMAIN):
    """Single-step flow: requires a configured core ViCare integration."""

    VERSION = 1
    # 1.2 (release 1.0.0): option allow_raw_commands, see async_migrate_entry
    MINOR_VERSION = 2

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not self.hass.config_entries.async_entries(VICARE_DOMAIN):
            return self.async_abort(reason="no_vicare")
        if user_input is not None:
            options = {CONF_ALLOW_RAW_COMMANDS: False, **_clean(user_input)}
            return self.async_create_entry(title="ViCare Extras", data={}, options=options)
        return self.async_show_form(
            step_id="user", data_schema=vol.Schema(_calorific_schema(None))
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return ViCareExtrasOptionsFlow()


class ViCareExtrasOptionsFlow(OptionsFlow):
    """Change the calorific value and allow or block raw commands."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=_clean(user_input))
        options = self.config_entry.options
        schema = {
            **_calorific_schema(options.get(CONF_CALORIFIC_VALUE)),
            vol.Optional(
                CONF_ALLOW_RAW_COMMANDS,
                default=bool(options.get(CONF_ALLOW_RAW_COMMANDS, False)),
            ): BooleanSelector(),
        }
        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema))
