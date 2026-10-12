"""Constants for ViCare Extras."""

from __future__ import annotations

import re
from datetime import timedelta

DOMAIN = "vicare_extras"
VICARE_DOMAIN = "vicare"

# Calorific value of the gas (kWh per m³, "Brennwert" on the gas bill).
# Optional; when set, gas energy sensors in kWh are created.
CONF_CALORIFIC_VALUE = "calorific_value"

# How often the already-fetched PyViCare cache is re-read. This never
# touches the network - the core integration owns all API traffic.
SCAN_INTERVAL = timedelta(seconds=30)

# Features the core `vicare` integration already turns into entities.
# Derived from the PyViCare getters the core integration calls (HA 2026.9,
# PyViCare 2.62). `{x}` stands for an index (circuit, burner, ...).
# Anything matching here is skipped by the generic entity builder so we
# do not duplicate core entities. Curated descriptions below may still
# use a covered feature on purpose (see hydraulicSeparator).
_COVERED_TEMPLATES = (
    "device.identification",
    "device.messages.errors.raw",
    "device.name",
    "device.power.battery",
    "device.sensors.humidity",
    "device.sensors.temperature",
    "device.serial",
    "device.zigbee.lqi",
    "device.zigbee.parent.id",
    "gateway.devices",
    "gateway.wifi",
    "tcu.wifi",
    "heating.boiler.sensors.temperature.commonSupply",
    "heating.boiler.sensors.temperature.main",
    "heating.burners.{x}",
    "heating.burners.{x}.modulation",
    "heating.burners.{x}.statistics",
    "heating.circuits.{x}",
    "heating.circuits.{x}.circulation.pump",
    "heating.circuits.{x}.frostprotection",
    "heating.circuits.{x}.heating.curve",
    "heating.circuits.{x}.operating.modes.active",
    "heating.circuits.{x}.operating.programs.active",
    "heating.circuits.{x}.operating.programs.{x}",
    "heating.circuits.{x}.sensors.temperature.room",
    "heating.circuits.{x}.sensors.temperature.supply",
    "heating.circuits.{x}.temperature",
    "heating.dhw",
    "heating.dhw.charging",
    "heating.dhw.oneTimeCharge",
    "heating.dhw.operating.modes.active",
    "heating.dhw.pumps.circulation",
    "heating.dhw.pumps.primary",
    "heating.dhw.sensors.temperature.dhwCylinder",
    "heating.dhw.sensors.temperature.outlet",
    "heating.dhw.temperature.main",
    "heating.gas.consumption.dhw",
    "heating.gas.consumption.heating",
    "heating.gas.consumption.summary.dhw",
    "heating.gas.consumption.summary.heating",
    "heating.gas.consumption.total",
    "heating.power.consumption.dhw",
    "heating.power.consumption.heating",
    "heating.power.consumption.summary.dhw",
    "heating.power.consumption.summary.heating",
    "heating.power.consumption.total",
    "heating.sensors.pressure.supply",
    "heating.sensors.temperature.outside",
    "heating.sensors.temperature.return",
    "rooms.{x}.sensors.temperature",
)
COVERED = tuple(
    re.compile("^" + re.escape(t).replace(re.escape("{x}"), r"[^.]+") + "$")
    for t in _COVERED_TEMPLATES
)

# Never exposed: write-dangerous, irrelevant, or private.
EXCLUDED = (
    re.compile(r"^device\.setDefaultValues$"),  # factory reset
    re.compile(r"^device\.time\.daylightSaving$"),
    re.compile(r"^.*\.houseLocation$"),  # latitude/longitude
    re.compile(r"^heating\.circuits\.[^.]+\.name$"),
    re.compile(r"^heating\.boiler\.serial$"),
    re.compile(r"^device\.etn$"),
    re.compile(r"^device\.parameterIdentification\.version$"),
    re.compile(r"^rooms\.features"),
)

UNITS = {
    "celsius": "°C",
    "kelvin": "K",
    "kilowattHour": "kWh",
    "wattHour": "Wh",
    "kilowatt": "kW",
    "watt": "W",
    "cubicMeter": "m³",
    "liter": "L",
    "bar": "bar",
    "percent": "%",
    "hour": "h",
    "minute": "min",
    "second": "s",
}

BURNER_STOP_REASONS = {
    "normalStop": "Normale Abschaltung",
    "flameLossDuringOperation": "Flammenverlust im Betrieb",
    "noGasAtStart": "Kein Gas beim Start",
    "noIgnitionAtStart": "Keine Zündung beim Start",
    "noIonizationAtStart": "Keine Ionisation beim Start",
    "safetyShutdown": "Sicherheitsabschaltung",
    "safetyShutdownErrorShutdown": "Störabschaltung",
    "safetyTime": "Sicherheitszeit",
    "undervoltage": "Unterspannung",
    "flueGasTemperatureSensor": "Abgastemperaturfühler",
    "ionizationAlignment": "Ionisationsabgleich",
    "stabilisationProcess": "Stabilisierung",
    "temperatureMonitor": "Temperaturwächter",
    "temperatureMonitorIonizationComparison": "Temperaturwächter Ionisation",
    "time": "Zeitbegrenzung",
}
# Enabled by default (heating only); the rest is created disabled.
BURNER_STOP_DEFAULT_ON = {
    "normalStop",
    "flameLossDuringOperation",
    "noGasAtStart",
    "noIgnitionAtStart",
    "safetyShutdown",
    "safetyShutdownErrorShutdown",
    "undervoltage",
}

WEEKDAYS_DE = {
    "Mon": "Mo",
    "Tue": "Di",
    "Wed": "Mi",
    "Thu": "Do",
    "Fri": "Fr",
    "Sat": "Sa",
    "Sun": "So",
}


def is_covered(feature: str) -> bool:
    """Return True if the core integration already exposes this feature."""
    return any(p.match(feature) for p in COVERED)


def is_excluded(feature: str) -> bool:
    """Return True if this feature must never become an entity."""
    return any(p.match(feature) for p in EXCLUDED)
