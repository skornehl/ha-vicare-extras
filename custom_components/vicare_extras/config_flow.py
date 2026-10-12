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

from .const import CONF_CALORIFIC_VALUE, DOMAIN, VICARE_DOMAIN


def _schema(default: float | None) -> vol.Schema:
    key = (
        vol.Optional(CONF_CALORIFIC_VALUE, default=default)
        if default is not None
        else vol.Optional(CONF_CALORIFIC_VALUE)
    )
    return vol.Schema({key: vol.All(vol.Coerce(float), vol.Range(min=5, max=15))})


class ViCareExtrasConfigFlow(ConfigFlow, domain=DOMAIN):
    """Single-step flow: requires a configured core ViCare integration."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not self.hass.config_entries.async_entries(VICARE_DOMAIN):
            return self.async_abort(reason="no_vicare")
        if user_input is not None:
            return self.async_create_entry(title="ViCare Extras", data={}, options=user_input)
        return self.async_show_form(step_id="user", data_schema=_schema(None))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return ViCareExtrasOptionsFlow()


class ViCareExtrasOptionsFlow(OptionsFlow):
    """Change the calorific value."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=_schema(self.config_entry.options.get(CONF_CALORIFIC_VALUE)),
        )
