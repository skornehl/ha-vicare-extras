"""Writable entities for DHW hygiene (legionella protection).

Features:
- heating.dhw.hygiene            enabled/active; setEnabled, activate
- heating.dhw.hygiene.trigger    startHour/startMinute/weekdays;
                                 triggerDaily, triggerOncePerWeek
- heating.dhw.temperature.hygiene value; setTemperature
"""

from __future__ import annotations

from datetime import time
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.components.select import SelectEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.components.time import TimeEntity
from homeassistant.const import EntityCategory, UnitOfTemperature

from .commands import async_execute, validation_error
from .coordinator import ExtrasDevice, ViCareExtrasCoordinator
from .descriptions import prop
from .entity import ViCareExtrasBaseEntity

F_HYGIENE = "heating.dhw.hygiene"
F_TRIGGER = "heating.dhw.hygiene.trigger"
F_TEMP = "heating.dhw.temperature.hygiene"

# Language-neutral option keys; the shown names come from translations
# (entity.select.dhw_hygiene_day.state).
DAILY = "daily"
WEEKDAYS = {
    "Mon": "mon",
    "Tue": "tue",
    "Wed": "wed",
    "Thu": "thu",
    "Fri": "fri",
    "Sat": "sat",
    "Sun": "sun",
}

# unique-id key per feature, for the registry cleanup in __init__.py
HYGIENE_KEYS = {
    F_HYGIENE: ("dhw_hygiene_enabled", "dhw_hygiene_activate"),
    F_TEMP: ("dhw_hygiene_temperature",),
    F_TRIGGER: ("dhw_hygiene_day", "dhw_hygiene_time"),
}


def hygiene_keys(device: ExtrasDevice) -> set[str]:
    """Unique-id keys of the hygiene entities this device gets."""
    return {key for feature, keys in HYGIENE_KEYS.items() if feature in device.features for key in keys}


def is_daily(weekdays: list[str]) -> bool:
    """No weekday or all seven: the trigger runs every day."""
    return len(weekdays) in (0, len(WEEKDAYS))


class HygieneEntity(ViCareExtrasBaseEntity):
    """Base for writable hygiene entities."""

    _attr_entity_category = EntityCategory.CONFIG
    _feature_name: str
    _key: str

    def __init__(self, coordinator: ViCareExtrasCoordinator, device: ExtrasDevice) -> None:
        super().__init__(coordinator, device, self._key)

    @property
    def available(self) -> bool:
        return super().available and self.feature(self._feature_name) is not None

    async def run(
        self,
        feature: str,
        command: str,
        params: dict[str, Any] | None = None,
        optimistic: dict[str, Any] | None = None,
    ) -> None:
        await async_execute(
            self.hass, self.coordinator, self._identifier, feature, command, params, optimistic
        )

    # --- trigger helpers shared by select + time
    def trigger_state(self) -> tuple[int | None, int | None, list[str]]:
        f = self.feature(F_TRIGGER)
        if f is None:
            return None, None, []
        return prop(f, "startHour"), prop(f, "startMinute"), list(prop(f, "weekdays") or [])

    def require_enabled(self) -> None:
        f = self.feature(F_HYGIENE)
        if f is not None and prop(f, "enabled") is False:
            raise validation_error("hygiene_disabled")

    async def set_trigger(self, hour: int, minute: int, weekdays: list[str]) -> None:
        optimistic = {"startHour": hour, "startMinute": minute, "weekdays": weekdays}
        if len(weekdays) == 1:
            await self.run(
                F_TRIGGER,
                "triggerOncePerWeek",
                {"startHour": hour, "startMinute": minute, "weekday": weekdays[0]},
                optimistic,
            )
        elif is_daily(weekdays):
            await self.run(
                F_TRIGGER, "triggerDaily", {"startHour": hour, "startMinute": minute}, optimistic
            )
        else:
            # Several (but not all) weekdays: there is no command that keeps
            # them, triggerDaily would silently change the plan.
            raise validation_error("hygiene_multiple_weekdays", weekdays=", ".join(weekdays))


# ---------------------------------------------------------------- switch


class HygieneSwitch(HygieneEntity, SwitchEntity):
    _feature_name = F_HYGIENE
    _key = "dhw_hygiene_enabled"
    _attr_translation_key = "dhw_hygiene"

    @property
    def is_on(self) -> bool | None:
        f = self.feature(F_HYGIENE)
        enabled = None if f is None else prop(f, "enabled")
        return None if enabled is None else bool(enabled)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.run(F_HYGIENE, "setEnabled", {"enabled": True}, {"enabled": True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.run(F_HYGIENE, "setEnabled", {"enabled": False}, {"enabled": False})


# ---------------------------------------------------------------- button


class HygieneStartButton(HygieneEntity, ButtonEntity):
    _feature_name = F_HYGIENE
    _key = "dhw_hygiene_activate"
    _attr_translation_key = "dhw_hygiene_start"
    _attr_entity_category = None

    async def async_press(self) -> None:
        await self.run(F_HYGIENE, "activate", None, {"active": True})


# ---------------------------------------------------------------- number


class HygieneTemperature(HygieneEntity, NumberEntity):
    _feature_name = F_TEMP
    _key = "dhw_hygiene_temperature"
    _attr_translation_key = "dhw_hygiene_temperature"
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_mode = NumberMode.BOX

    def _constraints(self) -> dict[str, Any]:
        f = self.feature(F_TEMP) or {}
        cmd = (f.get("commands") or {}).get("setTemperature") or {}
        return ((cmd.get("params") or {}).get("targetTemperature") or {}).get("constraints") or {}

    @property
    def native_min_value(self) -> float:
        return self._constraints().get("min", 10)

    @property
    def native_max_value(self) -> float:
        return self._constraints().get("max", 85)

    @property
    def native_step(self) -> float:
        return self._constraints().get("stepping", 1)

    @property
    def native_value(self) -> float | None:
        f = self.feature(F_TEMP)
        return None if f is None else prop(f, "value")

    async def async_set_native_value(self, value: float) -> None:
        v = int(value) if float(value).is_integer() else value
        await self.run(F_TEMP, "setTemperature", {"targetTemperature": v}, {"value": v})


# ---------------------------------------------------------------- select


class HygieneDay(HygieneEntity, SelectEntity):
    _feature_name = F_TRIGGER
    _key = "dhw_hygiene_day"
    _attr_translation_key = "dhw_hygiene_day"
    _attr_options = [DAILY, *WEEKDAYS.values()]

    @property
    def current_option(self) -> str | None:
        hour, _, days = self.trigger_state()
        if hour is None:
            return None
        if len(days) == 1:
            return WEEKDAYS.get(days[0])
        if is_daily(days):
            return DAILY
        # 2-6 weekdays (set in the ViCare app) cannot be shown as one option.
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        _, _, days = self.trigger_state()
        return {"weekdays": [WEEKDAYS.get(d, d) for d in days]}

    async def async_select_option(self, option: str) -> None:
        self.require_enabled()
        hour, minute, _ = self.trigger_state()
        if hour is None or minute is None:
            raise validation_error("hygiene_time_unknown")
        days = [] if option == DAILY else [k for k, v in WEEKDAYS.items() if v == option]
        await self.set_trigger(hour, minute, days)


# ---------------------------------------------------------------- time


class HygieneStartTime(HygieneEntity, TimeEntity):
    _feature_name = F_TRIGGER
    _key = "dhw_hygiene_time"
    _attr_translation_key = "dhw_hygiene_start_time"

    @property
    def native_value(self) -> time | None:
        hour, minute, _ = self.trigger_state()
        if hour is None or minute is None:
            return None
        return time(int(hour), int(minute))

    async def async_set_value(self, value: time) -> None:
        self.require_enabled()
        _, _, days = self.trigger_state()
        # The API only accepts minutes in 10-minute steps; validation in
        # commands.py rejects anything else with a clear message.
        await self.set_trigger(value.hour, value.minute, days)
