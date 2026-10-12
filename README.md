# ViCare Extras

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

## What you get (v0.1, read-only)

Enabled by default (tested on a Vitodens 200 / E3):

| Entity | Feature |
|---|---|
| Heat produced, heating and DHW (today / month / year) | `heating.heat.production.summary.*` |
| Hydraulic separator temperature | `heating.sensors.temperature.hydraulicSeparator` (the core entity stays empty on gas boilers) |
| Malfunction, external locks | `device.lock.*`, `heating.external.lock` |
| Status / service / info messages (count + entries) | `device.messages.*.raw` |
| Burner stop counters by reason | `device.timeseries.burner.stops` |
| Legionella protection: enabled, running, time, temperature | `heating.dhw.hygiene*` |
| Holiday / holiday at home (with start/end) | `heating.operating.programs.holiday*` |
| Schedules for heating circuit, DHW, circulation (entries as attributes) | `*.schedule` |
| Forced-from-schedule, energy saving (with reason), summer eco | `heating.circuits.N.operating.programs.*` |
| Boiler target temperature | `heating.boiler.temperature` |

Every other enabled feature property that the core integration does not
cover is created as a **disabled diagnostic entity**, so everything the API
reports is reachable. Excluded on purpose: `device.setDefaultValues`
(factory reset), house location, daylight saving, serial numbers.

Writing (hygiene, holiday, schedules) is planned for later versions.

## Installation

1. HACS -> Custom repositories -> `https://github.com/skornehl/ha-vicare-extras` (Integration)
2. Install **ViCare Extras**, restart Home Assistant
3. Settings -> Devices & services -> Add integration -> **ViCare Extras**

Requires the core ViCare integration to be set up.

## Caveat

The integration reads internals of the core integration
(`entry.runtime_data.devices`, PyViCare's `service._cache`). If a Home
Assistant update changes them, ViCare Extras raises a repair issue and goes
unavailable; the core integration keeps working.
