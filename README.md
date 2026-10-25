<div align="center">

<img src="custom_components/felicity/brand/icon@2x.png" width="128" alt="Felicity Inverter Integration Cloud">

# Felicity Inverter Integration Cloud

**Your Felicity Solar inverter, battery and electricity bill — live in Home Assistant.**

Energy flow, charts, every inverter setting, time-of-use tariffs with real costs, an Energy dashboard that sets itself up,
and Matter plugs for Apple Home — through the official Felicity cloud API, no extra hardware.

![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.6%2B-41BDF5?logo=homeassistant&logoColor=white)
![IoT class](https://img.shields.io/badge/IoT-cloud%20polling-6c7a89)
![Inverters](https://img.shields.io/badge/inverters-IVAM%20%C2%B7%20IVEM%20%C2%B7%20IVGM%20%C2%B7%20T--REX-ffb547)
![License](https://img.shields.io/badge/license-AGPL--3.0-blue)
![Made in Ukraine](https://img.shields.io/badge/made%20in-Ukraine-0057B7?labelColor=FFD700)

<a href="https://send.monobank.ua/jar/6YqKSm3yaD"><img src="docs/images/donate.svg" alt="Donate to the monobank jar" height="48"></a>

<br>

<img src="docs/images/overview.png" alt="Overview: live energy flow with today's numbers, battery and electricity price" width="100%">

</div>

---

## Why

The Felicity app shows your system, but it can't switch a lamp when the battery is full. It also doesn't know what your
electricity costs, and it isn't next to the rest of your home. This integration brings all of it into Home Assistant:
- live data as soon as the cloud has it, without hammering the cloud;
- the inverter's full settings, with guard rails;
- the money side: what each kWh cost, in which tariff zone, and what the solar and the battery saved you.

It is built and used on a real home system: an **IVAM6048** inverter with a **314 Ah** LiFePO₄ pack, on the Ukrainian three-zone tariff.

> [!WARNING]
> **Use at your own risk.** This integration can change your inverter's and battery's settings. A wrong setting can
> cut power to your home, damage the inverter, the battery or connected equipment, or void the manufacturer's
> warranty. Change only what you understand, follow the Felicity manual, and leave installation and grid settings to a
> qualified electrician. Costs, savings and runtime are estimates. The software comes with **no warranty**, and the
> author accepts **no liability** for any damage — see [LICENSE](LICENSE).

## Highlights

| Feature | What you get |
|---|---|
| ⚡ **Live energy flow** | Solar (per string, with V and A), grid, battery, home and the GEN / smart-load port, with today's kWh next to each, like the Sunsynk card |
| 🔋 **Battery you can read** | Level, voltage, current and temperature; how long it lasts at your usage down to the inverter's own reserve (from your usual use hour by hour), or when it will be full at the current charging power, with the clock time |
| 💸 **Tariffs and real costs** | Ukrainian 1/2/3-zone presets (to the kopeck: 6.48 / 4.32 / 1.728 ₴), Spain 2.0TD, UK Economy 7, your own zones or a dynamic price (Nord Pool, ENTSO-E, Tibber…). Grid import and cost per zone for today, yesterday or any month, to check against the bill, and what the battery saved |
| 📊 **Energy dashboard, set up for you** | One grid connection per tariff zone with its price, solar, battery with SOC and live power flows, kept in step with your tariff |
| 🛠 **All inverter settings** | Laid out like the Felicity app and editable, checked against the inverter's own limits; risky ones need a second confirmation |
| 🕑 **Work mode** | The time-of-use schedule on a 24-hour timeline, over your tariff zones, with a hint when grid charging runs in an expensive zone; a recommended schedule that is cheaper for your usual use and tariff and still carries announced outages |
| 📈 **Charts** | Six day charts (power, solar, grid with tariff zones, battery, panel voltage, grid voltage with blackouts), history by day and month, and a chart explorer with diagnostic presets like the Felicity data filter |
| 🔌 **Power outage schedule (Ukraine, DTEK)** | Your queue (черга), today's and tomorrow's outages and the week's plan for your address, with the battery runtime next to the next outage (optional) |
| 🍏 **Matter plugs** | Optional Home / Battery / Grid plugs with power and energy, for Apple Home and other Matter controllers (via a Matter bridge) |
| 📱 **Made for phones** | A "Solar" sidebar panel and ten dashboard widgets that work at 390 px; dark theme, no horizontal scrolling |

## A closer look

### Overview and day details

Today's numbers sit next to every node of the flow:
- solar produced;
- bought from the grid and what it cost;
- the battery charged and used;
- what the home used.

The price panel shows the price now, when the next zone starts, a 24-hour zone strip, and today's grid import split by
zone with its cost. The battery panel says how long the battery lasts at your usage (down to the reserve) or, while
charging, when it will be full; both with the clock time.

Six day charts below let you step through any past day: power, solar, grid import over the tariff zones, battery,
panel voltage, and grid voltage with the time without grid.

<img src="docs/images/day-charts.png" alt="Day charts: power through the day, solar, grid import over tariff zones, battery" width="100%">

### Tariff

Pick a ready tariff, or build your own zones: any names, prices and hours, several time ranges per zone, ranges across
midnight, weekdays-only or weekend-only hours.
- **Week preview and warnings.** A week preview shows the result, and overlapping hours are flagged.
- **Price changes apply at once,** without a restart. A change made mid-day applies from that minute; earlier minutes and past days keep the prices they had.
- **The split by zone is exact.** It uses the inverter's own grid-import counter, not estimates from power samples. The Ukrainian presets match a real bill to the kopeck (the night rate is 1.728 ₴, not the 1.73 printed on it).
- **Days and months.** Switch between today, yesterday, this month and last month; *Cost by month* lists every day with its kWh per zone, to compare with the bill. Past days are fetched once in the background and kept.
- **Where the savings come from.** Your home's use at zone prices, what you paid for the grid, and the battery's part: what charging it from the grid cost (losses included) and what its energy would have cost in the zones it was used.

<img src="docs/images/tariff.png" alt="Tariff tab: zone editor with week preview, cost by day, Energy dashboard status" width="100%">

### Work mode

The time-of-use rules from the inverter, drawn over the tariff zones and today's grid, battery and SOC. If a rule
charges from the grid during a dearer zone, the card says so and names the cheapest one.

<img src="docs/images/work-mode.png" alt="Work mode: charging schedule over tariff zones and today's result" width="100%">

### Saved schedules

Keep several time-of-use schedules and switch between them, e.g. one for a stable grid and one that keeps the
battery full for outages. **Work mode → Saved schedules**: a new schedule starts as a copy of the one the inverter runs now (for outages,
e.g. turn Grid on in every period with the battery at 95 %); edit it as in the Felicity app (the same
periods back to back from 00:00 to 23:59, with Grid, Gen, power and battery %), save it, and send it to the
inverter when you need it: the whole schedule goes in one command, after a confirmation, checked against the
inverter's own limits (checked on a real IVAM: the inverter reports the new schedule back within about 15 s). Each
schedule shows what it would cost up to the end of tomorrow and the lowest battery level, and the one the inverter
runs now is marked.

### Recommended schedule

Under the schedule, the **Work mode** tab suggests the cheapest daily time-of-use rules up to the end of tomorrow:
charge from the grid in the cheapest zone up to a level, let the battery carry the home down to a floor in the
other zones, and, when DTEK announced an outage, keep enough charge before it (planned with 15 % extra use).
It simulates the battery from your usual use hour by hour, your usual solar and your tariff; replayed over 11 real
days the simulation followed the battery level within 1.4 % and the grid import within 2.4 %. It only advises:
nothing is written to the inverter. Time-of-use rules repeat every day, so set them back after an outage.

### Settings

Every setting the inverter reports, grouped like the Felicity app. Change a value and press **Send**.
- **Limits.** Values are checked against the inverter's own limits, including each field of a time-of-use rule.
- **Risky settings** (battery type, voltages, grid code, parallel set-up…) need a second **Confirm**.
- **Blocked:** factory reset, remote power-off and log clearing can't be sent at all.

<img src="docs/images/settings.png" alt="Settings tab laid out like the Felicity app" width="100%">

### Charts and history

<img src="docs/images/charts.png" alt="Chart explorer: power balance preset with min / max / average" width="100%">

<img src="docs/images/history.png" alt="History: energy by day since the first day" width="100%">

### On a phone

<img src="docs/images/mobile.png" alt="The Solar panel on a phone" width="300">

## Installation

1. Install it through [HACS](https://hacs.xyz): search for **Felicity Inverter Integration Cloud** and download it (or [open it in HACS](https://my.home-assistant.io/redirect/hacs_repository/?owner=zegioni&repository=felicity-ha&category=integration) directly).
   Without HACS: copy `custom_components/felicity` (or the contents of `felicity.zip` from the [latest release](https://github.com/zegioni/felicity-ha/releases/latest)) into your Home Assistant `config/custom_components/felicity/` folder.
2. Restart Home Assistant.
3. **Settings → Devices & services → Add integration → Felicity Inverter Integration Cloud.**
4. Read and accept the risk notice (the integration can change inverter settings), then sign in with your Felicity account (the one you use in the Felicity app). The account needs Open API access; ask Felicity support if sign-in is refused.

The **Solar** panel appears in the sidebar, and the cards appear in the dashboard card picker.

## Set-up

- **Tariff:** open **Solar → Tariff**, pick a template or set your zones, and press **Save**. The first save also sets up the Energy dashboard, if it is empty; later saves keep only the sources the integration wrote in step with the tariff, and sources you set up yourself (also on its zone sensors) are never changed. You can also use **Configure → Electricity tariff** on the integration.
- **Battery reserve:** empty means the inverter's own *Low Batt* level (IVAM) or off-grid discharge depth (IVGM).
- **Matter plugs:** see [Matter plugs (Apple Home)](#matter-plugs-apple-home) below.
- **Power outage schedule (DTEK):** see [Power outage schedule](#power-outage-schedule-dtek-ukraine) below.

### Power outage schedule (DTEK, Ukraine)

For homes on the grids of DTEK Odesa, DTEK Dnipro, DTEK Kyiv Electric Networks and DTEK Kyiv Regional Grids.
**Configure → Power outage schedule (DTEK)** on the integration: pick the grid company, then your settlement,
street and house from DTEK's own lists. The **Outages** tab (and the `felicity-outages-card` widget) then shows:
- your queue (черга) and whether there is power by the schedule right now;
- today and tomorrow as DTEK announced them; with nothing announced it says so (the week's plan, where half of every day is "possible", appears only once outages are announced);
- the next outage and the next possible one, and whether the battery lasts until the outage ends;
- the week's plan for your queue.

Good to know:
- DTEK has no public API: the data comes from the same requests its website makes. When DTEK changes its site,
  this can stop working until the integration is updated.
- The site has a bot filter that now and then answers with a check instead of the data. The last schedule is then
  kept (also over restarts) and retried; the tab says so. It asks gently: whether the schedule changed every 15
  minutes (a small request), the whole page every 6 hours, the house's queue once a day.
- Times are Ukrainian time. Choose **Off** in the same place to turn it off.

### Matter plugs (Apple Home)

Your inverter can show up in Apple Home (or Google Home, Alexa, SmartThings) as three smart plugs with live power
and energy: **Felicity Home** (what the home uses), **Felicity Battery** (battery discharge) and **Felicity Grid**
(bought from the grid). *Felicity Battery* also carries the battery level (%), which Apple Home shows in the plug's
details. The plugs never control the inverter: switching one off only sets its power and energy to 0.

Home Assistant does not speak Matter to Apple Home by itself, so a **Matter bridge** is needed. These steps use
[Matterbridge](https://github.com/Luligu/matterbridge) with its Home Assistant plugin.

1. **Create the plugs.** In Home Assistant open **Settings → Devices & services → Felicity Inverter Integration Cloud →
   Configure → Matter outlets** and turn on *Create Matter outlets*. Three devices appear: *Felicity Home*,
   *Felicity Battery* and *Felicity Grid*, each with a switch, a power sensor and an energy sensor.
2. **Labels are added for you.** The integration puts the label from *Label for your Matter bridge* (by default
   `matterbridge`) on the three devices and creates the label if it doesn't exist. Leave the field empty to add no label.
3. **Install Matterbridge** (Docker, or the Home Assistant add-on) and add the plugin **matterbridge-hass** with your
   Home Assistant URL and a long-lived access token.
4. **Let the plugin find the plugs.** In the plugin settings:
   - set **filterByLabel** to the same label (`matterbridge`), so only labelled devices are bridged;
   - make sure **entityWhiteList** (if you use it) contains `switch` and `sensor`.

   Restart the plugin. The bridge combines each device's switch, power and energy into one plug.
5. **Add the bridge to Apple Home.** Open the Matterbridge web page and scan its pairing QR code in the Home app
   (**+ → Add Accessory**). The three Felicity plugs appear with their power and energy.

Using another bridge? Give it the three devices, or the entities `switch.felicity_home`, `sensor.felicity_home_power`,
`sensor.felicity_home_energy` and the same for `battery` and `grid`. Use a different label in step 2 if your bridge
filters by another one.

### Widgets

All of them find their data by themselves; no entity configuration is needed.

| Card | Shows |
|---|---|
| `felicity-flow-card` | Everything, with tabs and an inverter picker |
| `felicity-energy-flow-card` | The live flow with today's numbers and the price panel |
| `felicity-day-charts-card` | Day charts with day navigation |
| `felicity-all-time-card` | Energy by day / month |
| `felicity-charts-card` | The chart explorer |
| `felicity-settings-card` | Inverter settings |
| `felicity-tou-card` | The time-of-use schedule |
| `felicity-price-card` | Price now, next zone, today's cost by zone |
| `felicity-tariff-card` | The tariff editor, cost by day and by month, Energy dashboard set-up |
| `felicity-outages-card` | DTEK outage schedule for your address (when set up) |
| `felicity-events-card` | The inverter's alarms and faults: the last 7 days and any earlier month (`compact: true` shows only the last 7 days) |

### Entities

| Entity | Use it for |
|---|---|
| **Inverter and battery sensors** | Live values: solar, grid, battery, home, voltages, temperatures; plus the cloud's daily energy |
| **Alarms and faults** | `Last event` sensor per inverter with the last 7 days of alarms and faults in its attributes (checked every 2 minutes); each new one triggers the `Alarm or fault` event entity (event type `alarm` / `fault`, with `code`, `name`, `time`, and on IVAM `label` and `advice`) and is fired as a `felicity_event` bus event, so you can build your own notifications; the panel's **Events** tab lists them, month by month. On IVAM inverters each code also gets its meaning and advice from the user manual (W alarms, F faults). `Ambient temperature` and `Heat sink temperature` (IVAM inverter) are there for your own over-temperature alerts |
| `sensor.electricity_tariff_current_price` | *Energy → Use an entity with current price*, automations |
| `sensor.electricity_tariff_zone` | Automations ("run the boiler at night") |
| `…_grid_import_today_<zone>` | Grid import per tariff zone (Energy dashboard) |
| `…_energy_solar_today`, `…_energy_battery_charged_today`, `…_energy_battery_discharged_today`, `…_energy_sold_to_grid_today` | Energy dashboard meters; they never go down within a day |
| `…_grid_cost_today`, `…_saved_today` | Money: today's grid cost, and what solar and the battery saved |
| `…_grid_import_this_month_<zone>`, `…_grid_cost_this_month` | This calendar month so far, today included |
| `…_saved_this_month`, `…_battery_saved_this_month` | Savings over this month's finished days |
| `…_battery_runtime` | Hours the battery would carry the home down to the reserve, by your usual use hour by hour |
| `sensor.power_outages_*` | With the outage schedule set up: `outage_queue`, `power_by_schedule` (on / off / maybe), `next_outage` and its `_end`, `next_possible_outage` and its `_end` (only from schedules DTEK announced) |
| `…_last_data` | When the cloud received the latest record (diagnostic); the card's *Updated … ago* counts from it |
| **Number / select / switch controls** | Inverter settings on the device page (risky ones only while *Unlock risky settings* is on) |

## Supported inverters

Every model in the Felicity Open API documentation is supported. Each inverter tells the integration which settings it
has and their limits, so settings, controls and the time-of-use schedule follow the model by themselves. Sensors are
created only for the values the inverter reports.

| Family | Models in the documentation | Status |
|---|---|---|
| **IVAM** | IVAM | Tested on IVAM6048P1G1: data, settings, schedule, tariffs |
| **IVEM / IVBM / IVPM / IVCM / IVPS / IVPA** | IVEM3024 … IVEM12048, IVEM-AI, IVBM8048/10048, IVPM (II), IVCM (II), IVPS, IVPA | Off-grid inverters: data, settings (charging / output priority, battery voltages and currents), tariffs |
| **IVGM** | IVGM5048 … IVGM100600 | Hybrid: home load behind the meter, explicit time-of-use rule modes, grid-code settings |
| **T-REX** | T-REX-6K … T-REX-15K | Three-phase: per-phase grid, output and generator values, up to four PV inputs, a second battery, grid-code and ride-through settings |
| **Felicity batteries** | LPBA and other BMS packs | Level, power, voltage, current, temperatures, BMS state |

Only the IVAM is tested on hardware; the others are built from the API documentation. Reports are welcome.

- **Watts or kilowatts.** Models and endpoints report powers differently (the documentation lists kW for T-REX and W
  for IVGM). The integration checks each record against its own voltage × current and converts.
- **Safety by family.** Grid-code curves, ride-through, protection (ISO, GFCI, arc fault), battery voltages and currents,
  parallel and metering settings need a second **Confirm**. Calibration factors, the device clock, factory reset,
  power-off and log clearing can't be sent at all.

## Good to know

- **One set of daily figures.** Every kWh figure (the flow, the day charts, the price panel, months, history and the
  Energy-dashboard sensors) comes from the same day figures. Grid import follows the inverter's own counter, the same
  as the Felicity app; home and battery energy are scaled to it, so they add up. Your utility meter may differ by a few
  per cent.
- **How fresh the data is.** Data comes from the Felicity cloud (Open API):
  - the inverter uploads about one record per minute, and a record reaches the API about 1–2 minutes after it was measured;
  - the integration times its polls to just after the next record is due, then checks every 15 seconds while it is
    late (once a minute for a device that went quiet), so each record shows up seconds after it reaches the cloud;
  - today's totals and costs are refreshed every 5 minutes from the newest page of the day's history (the whole day
    is downloaded only when something was missed); settings are read every 5 minutes, and every minute while the
    panel shows them; alarms and faults every 2 minutes;
  - the card's *Updated … ago* is the age of that record (the *Last data* sensor);
  - the Felicity app can show a newer value for a moment: it gets live data through its own channel, which the Open API does not offer.
- **History timestamps** from the API are Beijing time and powers are in kW; both are converted.
- **Grid current with zero grid power** happens while the home runs on the battery. It is the inverter's reactive standby current: no energy is bought.
- **The cloud's own "… today" sensors** can drop to 0 for a moment. Use the *Energy: …* sensors for the Energy dashboard.

## Support the project

If the integration saves you money (or just time), you can drop something into the jar:

<table>
<tr>
<td align="center" width="50%">
<a href="https://send.monobank.ua/jar/6YqKSm3yaD"><img src="docs/images/donate-qr.png" width="220" alt="QR code for the monobank jar «отложить гривны»"></a>

**🫙 monobank jar «отложить гривны»**

<a href="https://send.monobank.ua/jar/6YqKSm3yaD"><img src="docs/images/donate.svg" alt="Donate" height="44"></a>

🔗 https://send.monobank.ua/jar/6YqKSm3yaD

💳 `4874 1000 3321 2872`
</td>
<td align="center" width="50%">
<img src="docs/images/donate-usdt.png" width="220" alt="QR code for the USDT (TRC20) address">

**💵 USDT · Tron (TRC20)**

`TV8M4HcRcc3jiX5EJmu9ggFXe4rWA4Cvx3`

<sub>Send only USDT on the Tron network (TRC20); other coins or networks will be lost.</sub>
</td>
</tr>
</table>

Thank you! 💛💙

## For developers

The integration lives in `custom_components/felicity/`:

| File | What it does |
|---|---|
| `api.py` | Felicity Open API client |
| `coordinator.py` | Polling, setting writes, the daily cost pipeline |
| `daystats.py` | Numbers from a day of minute history |
| `tariff.py` | The tariff model |
| `settings.py` | Settings layout and validation |
| `energy.py` | Energy dashboard set-up |
| `views.py` | The card's HTTP API |
| `frontend/` | The card, widgets and panel (vanilla web components, no build step) |
| `brand/` | The icon |

## License

[GNU AGPL v3](LICENSE) with additional terms in [NOTICE.md](NOTICE.md):

- **Free and open.** You may use, study, change and share the integration.
- **Copies stay open.** Every copy and every changed version — also one run as a service — must be published under
  the same license, with its source code.
- **Credit stays.** The copyright "zegioni" and a link to this repository must be kept, and changed versions must say they were changed.
- **The name and the icon are not licensed.** Forks must use a different name and icon.
- **No warranty, no liability.** You use it at your own risk.

---

<sub>Not affiliated with Felicity Solar. "Felicity" is used only to name the inverters this integration works with.</sub>
