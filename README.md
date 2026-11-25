# ViCare Extras

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5?style=for-the-badge&logo=homeassistantcommunitystore&logoColor=white)](https://hacs.xyz/docs/faq/custom_repositories/)
[![Release](https://img.shields.io/github/v/release/skornehl/ha-vicare-extras?style=for-the-badge&color=6366F1)](https://github.com/skornehl/ha-vicare-extras/releases)
[![Home Assistant 2026.1+](https://img.shields.io/badge/Home%20Assistant-2026.1%2B-18BCF2?style=for-the-badge&logo=homeassistant&logoColor=white)](https://www.home-assistant.io/)
[![Checks](https://img.shields.io/github/actions/workflow/status/skornehl/ha-vicare-extras/validate.yml?branch=main&style=for-the-badge&label=checks)](https://github.com/skornehl/ha-vicare-extras/actions/workflows/validate.yml)
[![License](https://img.shields.io/github/license/skornehl/ha-vicare-extras?style=for-the-badge&color=9333EA)](LICENSE)

Home Assistant custom integration that exposes the Viessmann ViCare API
features the core [`vicare`](https://www.home-assistant.io/integrations/vicare/)
integration does not turn into entities - for example heat production,
malfunction state and device messages, burner stop reasons, DHW hygiene
(legionella protection), holiday programs and schedules.

It is an **add-on to the core integration, not a replacement**:

- no login of its own - it reuses the core ViCare config entry
- **no API calls of its own** - it reads the feature payload the core
  integration already fetched into PyViCare's cache. The free Viessmann API
  tier allows roughly 1450 calls per day, which the core integration
  already uses up almost completely with three devices, so any extra
  polling would hit the rate limit
- entities are attached to the existing ViCare devices

## What you get

### Read (since v0.1)

Enabled by default (tested on a Vitodens 200 / E3):

| Entity | Feature |
|---|---|
| Heat produced, heating and DHW (today / month / year) - **an estimate**: Viessmann derives it as gas m³ × 10 kWh/m³, it is not measured | `heating.heat.production.summary.*` |
| Hydraulic separator temperature | `heating.sensors.temperature.hydraulicSeparator` (the core entity stays empty on gas boilers) |
| Target supply temperature per heating circuit | `heating.circuits.{x}.temperature` (core only reads it device-wide, so it never shows up) |
| Internal boiler pump (running) and its set output in % | `heating.boiler.pumps.internal`, `heating.boiler.pumps.internal.target` |
| Malfunction, external locks | `device.lock.*`, `heating.external.lock` |
| Status / service / info messages (count + entries) | `device.messages.*.raw` |
| Burner stop counters by reason | `device.timeseries.burner.stops` |
| Legionella protection running | `heating.dhw.hygiene` |
| Holiday / holiday at home (with start/end) | `heating.operating.programs.holiday*` |
| Schedules for heating circuit, DHW, circulation (entries as attributes) | `*.schedule` |
| Forced-from-schedule, energy saving (with reason), summer eco | `heating.circuits.N.operating.programs.*` |
| Boiler target temperature | `heating.boiler.temperature` |
| API data from (diagnostic timestamp, one per device) | time of the core integration's last API fetch |

Since v0.7 the fetch time is this one sensor instead of an `api_data_from`
attribute on every entity: an attribute that changes on every poll makes the
recorder write a new row for every entity each time.

Burner stops come in two windows: "total" counts since the counter reset,
"last stops" covers the most recent stops (46 on a Vitodens 200), not a time
span, so it can go down. The same features also report the burner runtime in
seconds over both windows (`timeAllStops`, `timeLastStops`). These are
duration sensors ("Burner runtime"), disabled by default; earlier versions
mislabeled them as a stop reason "time limit".

Every other enabled feature property that the core integration does not
cover is created as a **disabled diagnostic entity**, so everything the API
reports is reachable. Excluded on purpose: `device.setDefaultValues`
(factory reset), house location, daylight saving, serial numbers.

### Write (since v0.2)

Every command is validated against the definition the API ships with the
feature (`isExecutable`, min/max/stepping/enum/regEx, schedule rules) before
it is sent. One command costs one API call plus one refresh.

| Entity / service | Command |
|---|---|
| Switch *Legionella protection* | `heating.dhw.hygiene` `setEnabled` |
| Button *Start legionella protection now* | `heating.dhw.hygiene` `activate` |
| Number *Legionella protection temperature* | `heating.dhw.temperature.hygiene` `setTemperature` |
| Select *Legionella protection day* (daily or a weekday) + Time *Legionella protection start time* (10-minute steps) | `heating.dhw.hygiene.trigger` `triggerDaily` / `triggerOncePerWeek` |
| `vicare_extras.set_holiday` / `change_holiday_end` / `cancel_holiday` | `heating.operating.programs.holiday` / `holidayAtHome` |
| `vicare_extras.execute_command` (opt-in, see below) | any other command of any feature, same validation (blocked: factory reset) |

A legionella trigger that runs on several (but not all) weekdays, set in the
ViCare app, shows no day option; changing only its time is refused instead of
silently turning it into a daily trigger.

**`execute_command` is admin-only.** A non-admin user who runs a script that
calls it gets "Unauthorized"; automations are not affected. The holiday and
schedule actions are normal user actions, like the entities of this
integration.

**`execute_command` is opt-in.** It can send any command of any feature, so
it is off for new installations. Turn it on in the integration options
("Allow raw API commands"). Installations that existed before 1.0.0 are
migrated with it switched on, so their automations keep working. Parameters
are always validated against the API's own constraints; a parameter type the
integration cannot validate, or a format rule (regEx) it cannot evaluate, is
refused instead of sent.

When several devices support an action, `device_id` is required (before 1.0.0
the first device was used silently).

### Schedules (since v0.3)

`schedule` is `heating` (the lowest heating circuit with an enabled
schedule; before 1.0.0 always circuit 1), `heating_circuit_N`, `dhw` or
`circulation`. The API only accepts a whole week, so day-level services read
the current week, change the requested days and send it back.

| Service | What it does |
|---|---|
| `vicare_extras.set_schedule_day` | replace the slots of one or more days, e.g. `entries: [{start: "05:30", end: "06:30"}]` (mode defaults to `on` / `normal`) |
| `vicare_extras.copy_schedule_day` | copy one day to other days |
| `vicare_extras.reset_schedule_day` | reset one day to the boiler default |
| `vicare_extras.reset_schedule` | reset the whole week (save it with `get_schedule` first) |
| `vicare_extras.get_schedule` | return the week, allowed modes and limits as service response |

Plus a sensor *Schedule … today* per schedule showing today's slots. A slot
may end at `24:00` but not start there.

### Gas energy (since v0.4)

Set the calorific value (*Brennwert*, kWh/m³, from your gas bill) in the
integration options to get **gas energy in kWh** (today / month / year,
heating and DHW) = gas m³ × calorific value - the real energy input.

- Only for devices that report gas in **m³**. If the API already reports
  kWh, no gas energy sensor is created (the core integration shows those
  values; multiplying them would give ~11× too much).
- Empty the field in the options to remove the gas energy sensors.
- **Changing the calorific value changes the statistics.** The sensors are
  `total_increasing`; a new value rescales the whole running total, and the
  long-term statistics (Energy dashboard) record that as a jump. For the
  Energy dashboard prefer the core integration's gas sensor in **m³** with a
  price per m³ - it does not depend on this value.

Why not an efficiency sensor: Viessmann's "heat production" is exactly
gas m³ × 10 kWh/m³ (checked against two years of data), so heat/gas would
always be the same constant.

## Values that moved into the core integration

Some values started here and were later added to the core ViCare integration.
At startup ViCare Extras checks, per device, whether the loaded `vicare`
integration (the built-in one or a custom copy) defines the value **and** has
actually created the entity for that device (entity registry). Only then the
Extras entity is not created, its feature is not offered as a generic
diagnostic either, and an old Extras entity of it is removed from the entity
registry. With an older Home Assistant, or a device the core does not create
the value for, everything stays as before.

| Extras entity | Core entity (since) |
|---|---|
| Internal boiler pump (`boiler_pump_internal`) | Internal pump, binary sensor (HA 2026.12, core #193054) |
| Boiler pump target output (`boiler_pump_internal_target`) | Internal pump target modulation (HA 2026.12) |
| generic `heating.boiler.pumps.internal.current` | Internal pump modulation (HA 2026.12) |

Automations that use the Extras entity IDs have to be switched to the core
entities after the update.

## Devices, data and availability

- **Pushed updates:** ViCare Extras listens to the core coordinators and
  re-reads the cache right after each core fetch; its own 30-second cache
  read is only a fallback. Still no API call of its own.
- **Stale data is unavailable:** when the core's last fetch is older than
  3× its update interval (at least 5 minutes) or the core fetch failed, the
  entities go unavailable instead of showing old values. *API data from*
  stays available and shows the time of the last fetch.
- **Several ViCare accounts:** devices of all loaded ViCare config entries
  are used.
- **New devices and features** get their entities without a restart. At
  startup, entities that are no longer provided (option changed, value moved
  to the core, feature gone) are removed from the entity registry - only for
  devices whose data is present at that moment. A device the core no longer
  reports can be deleted from the device page.

## Languages

Entity names, options, actions and error messages are translated (English,
German, Dutch) and follow the language set in Home Assistant. Attribute names
are English (`note`, `entries`, `entries_total`, ...); long lists in
attributes are cut to 20 entries so the recorder keeps them. Entity IDs of new installations are generated from the
English names, so YAML examples work for everyone. Existing entities keep their
entity IDs. Generic diagnostic entities keep their technical feature names.
Translations for more languages are welcome: `custom_components/vicare_extras/translations/`.

## Installation

1. HACS -> search for **ViCare Extras** (once it is in the HACS default
   list; until then: HACS -> Custom repositories ->
   `https://github.com/skornehl/ha-vicare-extras`, type Integration)
2. Install **ViCare Extras**, restart Home Assistant
3. Settings -> Devices & services -> Add integration -> **ViCare Extras**

Requires the core ViCare integration to be set up.

## Removal

1. Settings -> Devices & services -> ViCare Extras -> three dots -> Delete.
   This removes its entities from the ViCare devices; the core integration
   and its devices stay.
2. Remove the integration in HACS and restart Home Assistant.

## Troubleshooting

- **Entities unavailable:** check *API data from* on the device. If it is
  old, the core ViCare integration is not fetching (rate limit, login,
  polling disabled); ViCare Extras only shows what the core fetched.
- **"ViCare Extras cannot read the ViCare integration" repair:** a Home
  Assistant update changed the core internals; update ViCare Extras.
- **An action says several devices support it:** pass `device_id`.
- **`execute_command` is switched off:** allow raw API commands in the options.
- **Diagnostics:** Settings -> Devices & services -> ViCare Extras -> three
  dots -> Download diagnostics. Contains the feature payload with serials,
  installation/gateway IDs, URLs, network data and coordinates removed -
  please attach it to issues.
- **Debug logging:**

  ```yaml
  logger:
    logs:
      custom_components.vicare_extras: debug
  ```

## Caveat

The integration reads internals of the core integration
(`entry.runtime_data.devices`, PyViCare's `service._cache`). If a Home
Assistant update changes them, ViCare Extras raises a repair issue and goes
unavailable; the core integration keeps working.
