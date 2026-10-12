"""Config flow for ViCare Extras - nothing to configure, it reuses ViCare."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import DOMAIN, VICARE_DOMAIN


class ViCareExtrasConfigFlow(ConfigFlow, domain=DOMAIN):
    """Single-step flow: requires a configured core ViCare integration."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not self.hass.config_entries.async_entries(VICARE_DOMAIN):
            return self.async_abort(reason="no_vicare")
        if user_input is not None:
            return self.async_create_entry(title="ViCare Extras", data={})
        return self.async_show_form(step_id="user")
