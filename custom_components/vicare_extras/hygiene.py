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
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.select import SelectEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.components.time import TimeEntity
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .commands import async_execute
from .const import VICARE_DOMAIN
from .coordinator import ExtrasDevice, ViCareExtrasCoordinator
from .descriptions import prop

F_HYGIENE = "heating.dhw.hygiene"
F_TRIGGER = "heating.dhw.hygiene.trigger"
F_TEMP = "heating.dhw.temperature.hygiene"

DAILY = "täglich"
WEEKDAYS = {
    "Mon": "Montag",
    "Tue": "Dienstag",
    "Wed": "Mittwoch",
    "Thu": "Donnerstag",
    "Fri": "Freitag",
    "Sat": "Samstag",
    "Sun": "Sonntag",
}


def hygiene_devices(coordinator: ViCareExtrasCoordinator, feature: str) -> list[ExtrasDevice]:
    return [d for d in coordinator.data.values() if feature in d.features]


class HygieneEntity(CoordinatorEntity[ViCareExtrasCoordinator]):
    """Base for writable hygiene entities."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _feature_name: str
    _key: str

    def __init__(self, coordinator: ViCareExtrasCoordinator, device: ExtrasDevice) -> None:
        super().__init__(coordinator)
        self._identifier = device.identifier
        self._attr_unique_id = f"{device.identifier}-extras-{self._key}"
        self._attr_device_info = DeviceInfo(identifiers={(VICARE_DOMAIN, device.identifier)})

    def feature(self, name: str | None = None) -> dict[str, Any] | None:
        dev = (self.coordinator.data or {}).get(self._identifier)
        return dev.features.get(name or self._feature_name) if dev else None

    @property
    def available(self) -> bool:
        return super().available and self.feature() is not None

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
            raise ServiceValidationError(
                "Legionellenschutz ist deaktiviert - die Anlage meldet dann keinen "
                "Startzeitpunkt. Bitte zuerst einschalten."
            )

    async def set_trigger(self, hour: int, minute: int, weekdays: list[str]) -> None:
        optimistic = {"startHour": hour, "startMinute": minute, "weekdays": weekdays}
        if len(weekdays) == 1:
            await self.run(
                F_TRIGGER,
                "triggerOncePerWeek",
                {"startHour": hour, "startMinute": minute, "weekday": weekdays[0]},
                optimistic,
            )
        else:
            await self.run(
                F_TRIGGER, "triggerDaily", {"startHour": hour, "startMinute": minute}, optimistic
            )


# ---------------------------------------------------------------- switch


class HygieneSwitch(HygieneEntity, SwitchEntity):
    _feature_name = F_HYGIENE
    _key = "dhw_hygiene_enabled"
    _attr_name = "Legionellenschutz"
    _attr_icon = "mdi:bacteria-outline"

    @property
    def is_on(self) -> bool | None:
        f = self.feature()
        return None if f is None else bool(prop(f, "enabled"))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.run(F_HYGIENE, "setEnabled", {"enabled": True}, {"enabled": True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.run(F_HYGIENE, "setEnabled", {"enabled": False}, {"enabled": False})


# ---------------------------------------------------------------- button


class HygieneStartButton(HygieneEntity, ButtonEntity):
    _feature_name = F_HYGIENE
    _key = "dhw_hygiene_activate"
    _attr_name = "Legionellenschutz jetzt starten"
    _attr_icon = "mdi:play-circle-outline"
    _attr_entity_category = None

    async def async_press(self) -> None:
        await self.run(F_HYGIENE, "activate", None, {"active": True})


# ---------------------------------------------------------------- number


class HygieneTemperature(HygieneEntity, NumberEntity):
    _feature_name = F_TEMP
    _key = "dhw_hygiene_temperature"
    _attr_name = "Legionellenschutz Temperatur"
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:thermometer-water"

    def _constraints(self) -> dict[str, Any]:
        f = self.feature() or {}
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
        f = self.feature()
        return None if f is None else prop(f, "value")

    async def async_set_native_value(self, value: float) -> None:
        await self.run(F_TEMP, "setTemperature", {"targetTemperature": value}, {"value": value})


# ---------------------------------------------------------------- select


class HygieneDay(HygieneEntity, SelectEntity):
    _feature_name = F_TRIGGER
    _key = "dhw_hygiene_day"
    _attr_name = "Legionellenschutz Tag"
    _attr_icon = "mdi:calendar-week"
    _attr_options = [DAILY, *WEEKDAYS.values()]

    @property
    def current_option(self) -> str | None:
        hour, _, days = self.trigger_state()
        if hour is None:
            return None
        if len(days) == 1:
            return WEEKDAYS.get(days[0])
        return DAILY

    async def async_select_option(self, option: str) -> None:
        self.require_enabled()
        hour, minute, _ = self.trigger_state()
        if hour is None or minute is None:
            raise ServiceValidationError("Aktueller Startzeitpunkt unbekannt")
        days = [] if option == DAILY else [k for k, v in WEEKDAYS.items() if v == option]
        await self.set_trigger(hour, minute, days)


# ---------------------------------------------------------------- time


class HygieneStartTime(HygieneEntity, TimeEntity):
    _feature_name = F_TRIGGER
    _key = "dhw_hygiene_time"
    _attr_name = "Legionellenschutz Startzeit"
    _attr_icon = "mdi:clock-outline"

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
