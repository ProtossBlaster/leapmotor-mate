# LeapMotor Mate

[![CI](https://github.com/ProtossBlaster/leapmotor-mate/actions/workflows/ci.yml/badge.svg)](https://github.com/ProtossBlaster/leapmotor-mate/actions/workflows/ci.yml)
[![Docker](https://github.com/ProtossBlaster/leapmotor-mate/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/ProtossBlaster/leapmotor-mate/actions/workflows/docker-publish.yml)
[![Docker Hub](https://img.shields.io/docker/pulls/protossblaster/leapmotor-mate?label=docker%20pulls&logo=docker&logoColor=white)](https://hub.docker.com/r/protossblaster/leapmotor-mate)
[![Release](https://img.shields.io/github/v/release/ProtossBlaster/leapmotor-mate)](https://github.com/ProtossBlaster/leapmotor-mate/releases)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue)](LICENSE)
[![Stars](https://img.shields.io/github/stars/ProtossBlaster/leapmotor-mate?style=social)](https://github.com/ProtossBlaster/leapmotor-mate/stargazers)
![Python](https://img.shields.io/badge/python-3.12+-blue?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![Home Assistant](https://img.shields.io/badge/Home%20Assistant-add--on-41BDF5?logo=homeassistant&logoColor=white)

**Trip tracking, charge logging and remote control for Leapmotor vehicles** — a self‑hosted companion (think *TeslaMate* for Leapmotor). Runs as a **Home Assistant add‑on** or as a **standalone Docker** container.

Supported models: **B05 · B10 · C10 · T03** — full‑electric (BEV) only, European spec (the Leapmotor lineup distributed by Stellantis/Leapmotor). Not for REEV / range‑extender versions.

> 🇮🇹 [Versione italiana più sotto.](#leapmotor-mate--italiano)

## ☕ Support

LeapMotor Mate is free and open-source, developed in my spare time. If it's useful to you, you can support its development with a coffee — thank you! ☕

<a href="https://www.buymeacoffee.com/protossblaster" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" height="48"></a>
<a href="https://www.paypal.me/ProtossBlaster" target="_blank"><img src="https://img.shields.io/badge/PayPal-Donate-00457C?style=for-the-badge&logo=paypal&logoColor=white" alt="PayPal" height="48"></a>

## Screenshots

| Overview | Trips |
|---|---|
| ![Overview](docs/screenshots/overview.png) | ![Trips](docs/screenshots/trips.png) |
| **Charges** | **Wallbox** |
| ![Charges](docs/screenshots/charges.png) | ![Wallbox](docs/screenshots/wallbox.png) |
| **Statistics** | **Commands** |
| ![Statistics](docs/screenshots/statistics.png) | ![Commands](docs/screenshots/commands.png) |

---

## Features

- **Demo mode** — explore the whole app on a realistic, self‑contained month of **sample data** (commutes, home + DC fast charging, costs, battery health, a weekend trip) with **no car or account needed**. On first launch just click **"Try the demo"** on the welcome screen — **one click, no command line**, so it works straight from the Home Assistant add‑on too. (Standalone, you can also run `docker run --rm -p 4000:4000 -e MATE_DEMO=1 ghcr.io/protossblaster/leapmotor-mate` and open <http://localhost:4000>.) A **DEMO** badge is shown throughout and *"Exit demo & set up my car"* takes you to the real setup. _All data is purely demonstrative — nothing is real._
- **Overview** — live status, battery, range, **READY state**, location map, vehicle picture.
- **At-a-glance status** — the Overview's first card now also shows a **Security** indicator (green **Active** when the car is locked and its alarm is armed) and, while the cable's still plugged in after a completed charge, a **Fully charged** badge.
- **Trips** — automatic trip detection with route map, distance, energy, efficiency and regen. Each trip also shows its **total kWh consumed** and its **cost** (energy × the price per kWh of the last charge before the trip, in your currency). You can **delete a trip** (with confirmation) to drop bad data, or **merge trips** that a short, non-charging stop split apart — pick the joinable pairs with a gap slider, preview the combined route, and it's fully reversible (Unmerge any time).
- **Official trip consumption (cloud) 🆕** — each trip's **energy, efficiency and cost** now use Leapmotor's **official cloud figure** (the real **driving / A·C / other** split) when the cloud has it, instead of only the battery‑% estimate — shown with a **breakdown** on the trip, and flowing into the Report and Statistics. While the cloud is still aggregating a fresh trip it shows the estimate marked **⏳ provisional**, then swaps to the official number on its own (the estimate is kept as a reversible backup). A **"Convert with official data"** button upgrades older trips on demand, and a since‑delivery **"Vehicle Cumulative Total"** card shows lifetime energy, mileage and average. If the car didn't upload a trip to the cloud (it happens, on any brand), that trip simply stays on the estimate — never an error. Always on, no setup. The official figure is measured from when the car is switched **on** (Ready), not from when driving starts; if you **never switch off between two trips**, the cloud counts them as one session and Mate asks you to **merge** them for the real combined consumption.
- **Elevation profile & outside temperature per trip 🆕** — the Leapmotor cloud reports neither altitude nor outside temperature, so Mate looks each finished trip's GPS track up against [Open-Meteo](https://open-meteo.com) (free, no key, no account). The trip detail then gains an **altitude line on the SoC & speed chart** (a topographic cross-section under your telemetry), the trip's total **elevation gain and loss** (↑ climbed / ↓ descended), and the **outside temperature at departure and on arrival** — not an average, so a valley-to-pass climb shows the real drop. Together they explain a good part of a trip's consumption: a climb costs energy, cold costs range. It runs quietly in the background a few minutes after the trip (one lookup per trip); trips recorded before this existed get a **Calculate elevation** button. Only the trip's GPS points are sent, and you can switch it off in *Settings*.
- **Charges** — charge sessions with AC/DC detection, energy added, power and a distribution chart. Each session shows its **effective €/kWh** (cost ÷ energy), and for messy public charges you can set a **✎ Manual** cost — the real total you actually paid — which overrides the table estimate and feeds the trip cost / weighted‑average cost.
- **Battery card on the Charges page** — battery %, range and a battery bar with a **marker at your charge limit**, refreshing live while you watch a charge — no more flipping back to Overview. The **Unlock cable** button now lives here too (it's a charging action, after all).
- **User notes 🆕** — jot a **free-text note** on any **charge** (station location, shade, reliability, parking, weather…) or **trip** (traffic, road type, any remark), and tag each trip's **drive mode** (Comfort / Normal / Sport) and **One-Pedal** (on/off). The cloud doesn't report drive mode or One-Pedal, so you set those by hand — they explain why two similar drives consumed differently. _(#107, idea by @riri19.)_
- **V2L (vehicle-to-load) monitoring** — when the car powers an external device through the V2L adapter, the Overview shows a live **V2L block** (status, **net power** in watts with a 0–3500 W bar, and energy drawn this session — refreshing every 10 s), the Statistics page tracks the **total energy drawn** over all time, and three **MQTT entities** (`V2L Active`, `V2L Power`, `V2L Session Energy`) appear in Home Assistant. Power is reported **net of the car's own overhead**, so it matches what your device actually draws. Read-only (V2L is started on the car: Park + a connected device). *A first for any Leapmotor tool — found by on-car testing; accurate from ~42 W up (the car's own current resolution).*
- **Battery health (SoH)** — a dedicated page estimating your **usable capacity over time**: each charge's *measured* energy (∫ voltage × current) divided by the SoC it added is a capacity estimate that actually tracks ageing (no efficiency guess, no wallbox needed). It plots the trend per **time or distance** (calendar vs cycle ageing), **excludes cold‑weather charges** (an LFP pack reads low when cold) and **weighs full charges most** (where the BMS recalibrates the SoC), for a stable State‑of‑Health figure.
- **Charge prices** — flat 24h pricing or **time-of-use bands**: set prices per time window, per **day of the week** and per charge type, and each session is costed correctly (energy split across the bands it spans by the real power curve).
- **One-touch vehicle preparation** — a dedicated **Prepare car** page mirroring the official app's *"prepare the vehicle with one tap"*: bundle **A/C** (cool / heat / ventilation / defrost / auto + temperature), **front-seat heating / ventilation**, **steering-wheel & mirror heating**, and run it **now** (immediate) or on a **schedule** (time + weekdays). Scheduled preparations are read from the car, **editable** and removable, and a **Cancel preparation (all off)** button turns everything back off. **New 🆕: Automatic on power-on** — the same preparation can now run **by itself the moment the car goes Ready**, with an optional **interior-temperature condition** (run only *above*, or only *below*, a threshold — e.g. pre-cool above 25 °C; leave it off and it runs on every start). It fires once per power-on, decided at the instant you switch on.
- **Charge & climate scheduling** — a dedicated **Scheduling** page. Program a **charge window** (on/off, target SoC, start/end, days of the week) and the **climate pre-conditioning** schedule (quick **cool / heat / ventilation / defrost / auto** presets, time, days, target temperature). Both write to the car and stay in sync with the Leapmotor app; "Active" is a master switch (off = no schedule / charge anytime).
- **CSV export** — export your trips and charges to **CSV** (plus per-trip **GPX**), from the Trips/Charges pages or *Settings → Export/Backup*.
- **Wallbox (optional)** — pair a wallbox already in Home Assistant to see live charging power/status, set the max charging current, and compare **AC delivered by the wallbox** vs **DC into the battery** per session, with charging efficiency. A **"show all entities" advanced mode** lets you map *any* sensor — handy for foreign-language entity names or a generic energy-meter/relay.
- **Saved wallbox profiles 🆕** — charge in more than one place? Save each wallbox (its Home Assistant connection, entity mapping **and** tariff) under a name and switch between them in one click from **Settings**; the active profile shows on the Costs page, and switching is blocked mid‑charge so a session is never split across two wallboxes. _(Community feature by @domevite, #84.)_
- **Charging-station names on your charges (optional)** — every public charge is tagged automatically with the **name of the station** (📍 on the Charges list and on the Overview "last charge"), looked up from **OpenStreetMap** and the **Italian national registry (PUN)**. Home charges are never looked up, and charges already recorded get filled in too. Off by default *(Settings → Charging stations)*. *(Idea: @hubcasale.)*
- **Find charging stations (Navigation)** — a **⚡ Find chargers** button maps the public stations around the car: pick the **max distance** and **results per page**, filter **by operator** (e.g. Electra, Ionity), and see **AC/DC, kW and live availability**, both as map pins and a list underneath — tap one to set it as your destination and send it to the car's navigator. Fuses **OpenStreetMap + the Italian PUN registry** (both keyless), plus **Open Charge Map** and **TomTom** when you add their free API keys — the more sources, the better the coverage.
- **Auto-assign "Home" charges (optional)** — a *Settings → Wallbox* toggle: charges where **your wallbox measured the energy** are confirmed as **Home** automatically — no more tapping the badge after every overnight charge. The cost goes through the **same engine as a manual confirm** (flat prices *and* time-of-use bands, billed on the wallbox AC energy), the type stays editable, and DC/public/reconstructed charges are never touched. Off by default. *(Idea: @hubcasale.)*
- **ABRP (optional)** — forward live telemetry to **A Better Route Planner** for live route planning (enable it with your ABRP token).
- **MQTT → Home Assistant (optional)** — publish the car to Home Assistant via **MQTT Discovery** as native entities (sensors, binary sensors, GPS tracker) plus command buttons.
- **EVCC (optional)** — publish EVCC-friendly MQTT topics so an **EVCC** `type: custom` vehicle reads SoC, plug/charging status, range and odometer (ready-to-paste config in `docs/EVCC.md`).
- **Statistics** — driving/AC/other energy split and a 6‑week consumption trend (from the Leapmotor cloud).
- **Monthly Report** — a per-month dashboard of driving, charging and **cost** in one place: distance, efficiency and energy; charging cost, sessions and average €/kWh; a **Home vs public** split; **deltas vs the previous month**; daily distance/cost charts; and a **map of every trip that month**. Move between months with ◀ ▶. The numbers match the Statistics and Charges pages exactly.
- **Remote control** — lock, windows, trunk, panoramic roof, **climate** (cool / heat / ventilation / defrost, A/C on-off, target temperature), **heated & ventilated seats** (per-seat level), **heated steering wheel & mirrors**, find car, battery preheat, **unlock charge cable**.
- **Navigation** — search an address and **send the destination straight to the car's built‑in navigation**. Shows the car's current address too. Address lookup is keyless by default (OpenStreetMap) with an optional API key (Geoapify/LocationIQ/TomTom) for better house‑number coverage.
- **Independent** — polls the Leapmotor cloud directly (configurable 10–30 s). No dependency on the phone app or Home Assistant; polling the cloud does **not** wake or drain the car. It isn't real-time, so a **Refresh** button (in the sidebar, and the mobile header) pulls the car's latest state on demand.
- **Multilingual UI** — English · Italiano · Français · Deutsch · **Polski**.
- **Currency** — pick your display currency from 30 world currencies (€, $, £, CHF, kr, zł…); every cost reformats to it, with the right symbol placement and decimals.
- **Units** — choose **Metric**, **Imperial UK** (miles & mph, but °C) or **Imperial US** (miles, °F, psi). Distances, speeds, temperatures and tyre pressures display in your chosen system. It's display-only — your stored data always stays metric, so you can switch any time with nothing lost.
- **Diagnostics** — a *Settings → Diagnostics* card with a read-only system snapshot, the recent poller/web logs and the car's current raw signals (with Copy), plus a one-click **downloadable bundle** to attach to a GitHub issue. Personal info (VIN — including where it's embedded in the MQTT topic, credentials, e-mail and **exact GPS coordinates**) is **always masked** in the exported logs.
- **OTA-update indicator** — the Overview card tells you when the car has a **vehicle software update** waiting, without opening the official app.
- **Mate self-update badge** — a small badge next to the version number when a **newer Mate release** is on GitHub (checked in the background every 6 h) — handy for standalone-Docker installs.
- **Editable battery capacity** — pre-filled per model (usable/net kWh); edit it if yours differs, or click **“use measured”** to adopt the value Mate worked out from your own charges. Changing it never rewrites past charges.
- **Advanced settings** — a collapsible card to tune the edge cases: missed-charge detection threshold, vampire-drain noise floor, the AC/DC power threshold (for 22 kW AC wallboxes), and the **battery-health cold cutoff**. Sane defaults, one-tap reset.
- **Recover missed charges** — scan your history for charges that happened while the car was asleep before automatic detection existed; previews what it finds before adding anything.
- **Single Home Assistant lock toggle** — an MQTT *lock* entity for dashboards **plus a “Door Lock Toggle” switch** for launcher widgets that can't toggle locks (e.g. Samsung's): one tap locks, the next unlocks — perfect as a phone front-screen button. The classic buttons stay too.
- **Delete account / Factory reset** — a guarded action in *Settings → Vehicle* that wipes **everything** (account, trips, charges and all settings) and reopens the setup wizard as a brand‑new install — for handing the install on, or starting clean. Type‑to‑confirm; the app certificate is kept, so re‑onboarding only needs your login.

## How it works

```
Leapmotor Cloud  ──►  Poller (state machine)  ──►  SQLite  ──►  Web UI (FastAPI + HTMX)
                       trips / charges / regen                   + remote commands
```

The data lives in a local SQLite database. Nothing is sent anywhere except to the official Leapmotor cloud.

> ℹ️ **Mate isn't real-time — it polls.** It reads the car's state from the Leapmotor cloud on an interval: about every **30 s while parked** and **10 s while driving** (tunable in Settings). So a change you make in the official app (opening the trunk, changing the charge limit…) shows on Mate within that window, not instantly. Mate reads **passively** and never wakes the car, so it doesn't drain your battery — the official app feels instant because opening it *wakes* the car. Need it sooner? The **🔄 Refresh** button (top of the sidebar) pulls the latest state on demand. If the car is asleep, the cloud serves its last reported state until the car next wakes.

---

## Requirements

1. **A Leapmotor account — dedicated to Mate and used by *nothing else*.** ⚠️ Leapmotor allows only ~one active session per account, so **any other client on the same account — the official phone app, another add-on, a Docker container, or any other integration — fights Mate for the session**: they evict each other in a loop, the car goes **offline to Mate**, and you get **missing or inconsistent data**. Use a separate account for Mate only (not the one on your phone). Create a separate account, then **share the car with it from the official app**: logged in on the account that *owns* the car, share/authorise the vehicle to the new account with **all permissions** and a **permanent** duration (a temporary share expires and breaks Mate later). **Check it worked:** **set the *second* account up in the official Leapmotor app on a device** (not just logging into the account on the web) and confirm the car appears there — if it doesn't, the share isn't active yet and Mate will report *“No vehicle found on this account.”* **Then sign out of that account in the app and leave it to Mate.** That check is a one-off: an app left signed in on Mate's account *is* the “other client” described above, and you're back to the session fight.
2. **The Leapmotor app TLS certificate** (`app.crt` + `app.key`). This is the *same for everyone* (it identifies the Leapmotor app, not you) and is **not** included in this repository. Download the two files from:

   👉 **https://github.com/markoceri/leapmotor-certs**

   You upload them once during the setup wizard (see below).

---

## Installation

> **▶️ Just want to see what Mate can do?** Try the **demo** first — a realistic month of sample data, **no car or account needed**. Install it (add‑on or Docker), open Mate and click **"Try the demo"** on the welcome screen — **no command line**. Or run it standalone:
>
> ```bash
> docker run --rm -p 4000:4000 -e MATE_DEMO=1 ghcr.io/protossblaster/leapmotor-mate
> ```
>
> Open <http://localhost:4000>. Everything in demo mode is **sample data — nothing is real**.

### Option A — Home Assistant add‑on

1. In Home Assistant: **Settings → Apps → Install app → ⋮ → Repositories** (on Home Assistant before 2026.2: **Settings → Add‑ons → Add‑on Store → ⋮ → Repositories**), and add the repository URL (note the `-addon` suffix — this is a separate repo from the code):

   ```
   https://github.com/ProtossBlaster/leapmotor-mate-addon
   ```

2. Install **LeapMotor Mate**, start it, and open the panel (car icon in the sidebar).
3. Follow the setup wizard.

The database is stored in the add‑on's persistent `/data`, so it survives restarts and updates.

### Option B — Standalone Docker

**Easiest — run the prebuilt image** (no clone, no build):

```bash
docker run -d --name leapmotor-mate \
  --restart unless-stopped \
  -p 4000:4000 \
  -v "$(pwd)/data:/data" \
  ghcr.io/protossblaster/leapmotor-mate:latest
```

The same image is also on [Docker Hub](https://hub.docker.com/r/protossblaster/leapmotor-mate) — use `protossblaster/leapmotor-mate:latest` interchangeably.

To update later: `docker pull ghcr.io/protossblaster/leapmotor-mate:latest` then recreate the container (or use [Watchtower](https://containrrr.dev/watchtower/) for automatic updates).

**Or build from source:**

```bash
git clone https://github.com/ProtossBlaster/leapmotor-mate.git
cd leapmotor-mate
docker compose up -d
```

Then open **http://localhost:4000** and follow the setup wizard.

The database is stored in `./data/` (mounted at `/data` in the container).

---

## Setup wizard

The first launch opens on a choice — **Set up my car** or **Try the demo**. Choosing *Set up my car* walks you through two steps:

1. **Certificate** — upload `app.crt` and `app.key` (or paste their PEM text). Get them from [markoceri/leapmotor-certs](https://github.com/markoceri/leapmotor-certs). Stored persistently in `/data/certs`.
2. **Login** — your Leapmotor account email, password and operation **PIN**. The wizard reads your **model** and VIN from the cloud. The **battery** it can only fill in by itself where the European version has a single variant (T03) — where there are several (B10 Pro / Pro Max, C10 RWD / AWD) you pick yours. Correctable at any time in Settings → Battery.

That's it — the poller starts and data begins to appear.

To switch to a **different Leapmotor account** later, use **Settings → Vehicle → Log out**: it clears only the stored login and re‑opens this wizard (your app certificate stays). All your trips and charges are kept — they're tied to the car's VIN, so the same car carries straight over.

## Configuration

Everything is configured from the web UI (**Settings**), no YAML needed:

- **Polling interval** — parked (default 30 s) and driving (default 10 s). Faster catches trips/charges sooner; slower means fewer API calls. Polling the cloud does not wake or drain the car.
- **Charge prices** — flat or time-of-use, on the dedicated *Charge Prices* page (see below).
- **Language & currency** — English / Italiano / Français / Deutsch / **Polski**, and your display currency (€, $, £, CHF, zł… 30 currencies). The number format (decimal/thousands separator) follows the selected language.

### Charge prices

Set what each kWh costs on the dedicated **Charge Prices** page (💰 in the sidebar), so Mate prices your sessions. Two modes:

- **Fixed (24h)** — one price per charge type (Home / AC / DC / HPC).
- **Time-of-use bands** — add one or more time windows, choose the **days of the week** each applies to (All / Weekdays / Weekend shortcuts), and set a price per charge type for every band. Leave a price blank to fall back to the base price, or enter `0` if it's free in that band. A session spanning two bands is split by its real power curve, and one crossing midnight on a Sat→Sun boundary is priced per day correctly.

Cost changes apply to **new charges only**: a charge's cost is frozen when you confirm its type, so editing prices or bands later never changes past sessions.

**How the kWh are counted (home charges):** if your wallbox is paired and exposes a **kWh energy counter**, Mate samples it **throughout the charge** and bills the **energy it added** — the sum of the counter's increases over the session, i.e. the exact energy the wallbox delivered (conversion losses included), measured, not estimated. It's **reset/race-safe**: it works whether the counter is a lifetime total (like an odometer) or a per-session meter that zeroes mid-charge, no matter when it resets. The charge card leads with the **🔌 wallbox (billed)** kWh and shows the **🔋 in-battery (DC, from SoC)** energy with the AC→DC efficiency beneath it; the cost is simply *wallbox kWh × price*. Without a wallbox counter (or for public charges), Mate bills the **battery (SoC) energy × price**. The instantaneous power is used only for the chart, never for the cost.

> ⚠️ This applies to charges recorded from **v1.12.0 onward** (the counter readings are captured live during the session). Older charges keep the value they were calculated with and **can't be recomputed** with the new method — if you want, you can delete an old session with the 🗑 button on its card.

### Optional: boost from Home Assistant

If you run Home Assistant on the same network, you can trigger a temporary fast‑poll when a trip is about to start (e.g. from a Bluetooth/phone shortcut) by calling `POST http://<mate-host>:4000/api/boost`. With the default 30 s cadence this is optional.

### Wallbox (Home Assistant)

If you charge at home and have a **wallbox already integrated in Home Assistant** (Wallbox Pulsar, Easee, go‑e, Keba, OCPP, …), Mate can pair with it to show live charging data and compare what the **wallbox delivers (AC)** with what the **car receives into the battery (DC)**.

Enable it in **Settings → Wallbox present**, then connect to Home Assistant. How you connect depends on how you run Mate:

- **As a Home Assistant add‑on** — *nothing to configure.* Mate reaches HA through the internal Supervisor API automatically, regardless of how HA is exposed externally (HTTP, HTTPS, Nabu Casa). You'll just see a green **connection status** dot.
- **As standalone Docker** — enter your HA URL (e.g. `http://192.168.1.10:8123`) and a **Long‑Lived Access Token** (HA → your profile → *Security* → *Long‑Lived Access Tokens* → *Create Token*). Local HTTPS, even with a self‑signed certificate, works.

Then expand **Entity mapping** and assign the wallbox sensors. Mate pre‑selects them automatically and only lists your wallbox device's own entities. Each field's label shows the expected **unit**, and the dropdown offers **only sensors of that unit** for the two that feed the maths — **Charging power** lists only kW, **Session energy** only kWh — so you can't accidentally map a kWh meter as power (which would corrupt the stored power and cost figures). The **Show all entities** toggle lifts this for non‑standard setups, and a sensor you already mapped is never hidden.

**What each setting means** — all optional (Mate auto‑detects them; override one only if auto‑mapping picks the wrong entity, e.g. foreign‑language names):

| Setting | What it is |
| --- | --- |
| **Charging power (kW)** | The power the wallbox is delivering **right now** (AC). Drives the live "charging" indicator and the **AC** side of the AC‑vs‑DC comparison. W is auto‑converted to kW. |
| **Status** | The wallbox's own state text from Home Assistant (e.g. *Charging / Connected / Idle / Error*). |
| **Session energy (kWh)** | Energy delivered in the session (kWh; Wh auto‑converted). This is the **AC kWh** Mate bills home charges on (you pay the wallbox AC, conversion losses included) and uses for the efficiency figure. |
| **Max charging current (A)** | The **only writable wallbox** setting (a `number` entity): sets the wallbox **max charging current** in **amps** from the Wallbox page. Your own HA load‑balancing automations may override what you set. (The car's **charge limit** is a separate writable `number` — see the MQTT section.) |
| **Charging speed (km/h)** | Your wallbox's own "charging speed" reading, if it exposes one (shown live). |
| **Max available (kW or A)** | The maximum currently available to the wallbox (e.g. after dynamic load balancing or a tariff cap), if exposed — in **kW or A** depending on the wallbox (V2C/Pulsar report it in amps). Shown as‑is with its own unit. |

Only **Max charging current** writes to the wallbox; everything else is read‑only.

What you get on the new **Wallbox** page:
- a **live panel** (power, status, session energy, charging speed, max available power) plus the session cost (reused from your home charges);
- a **max‑current control** to set the wallbox charging current — note your own HA load‑balancing automations may override it;
- an **AC‑vs‑DC comparison** per charge session (kWh delivered vs into the battery + efficiency), laid out as a year/month/day history; expand a session for its power chart. The wallbox curve uses Home Assistant's history (kept ~10 days), so the comparison appears for recent sessions;
- optional **auto‑assign "Home"** (Settings → Wallbox): charges the wallbox measured are confirmed as **Home** automatically, with the cost computed from your prices and time‑of‑use bands exactly like a manual confirm. Off by default. *(Idea: @hubcasale.)*

### ABRP (A Better Route Planner)

Forward the car's live data to **A Better Route Planner** for live route planning. In **Settings → ABRP**, enable it and paste your personal ABRP token (in the ABRP app: *Settings → Car → Live Data*, "Generic"). It's off until you enable it, and nothing is sent without a token.

### MQTT → Home Assistant

Publish the car to Home Assistant as **native entities** (in parallel to the Mate UI), via MQTT Discovery. In **Settings → MQTT**, enable it and enter your broker (host, port, username/password; TLS optional). Home Assistant then auto‑creates a *Leapmotor Mate* device with sensors (SOC, range, individual tyres, temperatures, charge…), binary sensors (doors/windows/lock/charging), a GPS tracker, a writable **Charge Limit** (target SoC) `number`, a writable **Charge Schedule** `text` that takes a JSON plan for automations (`{"start":"23:00","soc":90}` — every key optional, and whatever you omit keeps its current value), a read-only **V2L** group (`V2L Active` / `V2L Power` / `V2L Session Energy`), and command buttons (lock/unlock, trunk, find car, unlock charge cable, climate — Quick Cool / Quick Heat / Quick Ventilation / Defrost / A/C Off — and comfort: heated/ventilated seats, steering-wheel & mirror heating). Turning the A/C fully **off** now works on the B10 (using the `operate=off` command found by on‑car testing); the comfort commands use the payloads captured by [@kerniger](https://github.com/kerniger/leapmotor-ha). Works with any MQTT broker (e.g. the Mosquitto add‑on). Use **Test connection** to verify the broker before saving. After a command the state now updates in Home Assistant immediately (no waiting for the next poll), and the **topic prefix** scopes the device — so you can run a second instance on a different prefix without it clashing with the first.

---

## Notes & disclaimer

- **"Vehicle not reporting live data" in the logs is normal.** When the car is parked long enough it goes into **deep sleep** and the cloud returns no live signals. Mate backs off to 15‑minute polling (logged once, not every cycle) and recovers automatically the moment the car reports again — when it's driven, or woken by the official Leapmotor app. To be sure a short trip is captured even straight out of deep sleep, use the boost trigger above.
- **Your credentials are encrypted at rest.** The Leapmotor password/PIN (and any HA / ABRP / MQTT / geocoder tokens) are stored encrypted in the local database, with a per‑install key in `/data/secret.key` (auto‑generated, or set your own via the `MATE_SECRET_KEY` env var). ⚠️ Keep `secret.key` together with your backups — restoring only the database without it will ask you to re‑enter the credentials.
- **Standalone: optional login.** When running standalone (not as an add‑on), you can require a password to open the app — set one from **Settings → Access**, or via the `MATE_AUTH_PASSWORD` environment variable (the env var wins if both are set). Standalone Mate also refuses state‑changing requests that arrive from another website and refuses to be embedded in one, so a page in your browser can't drive the car behind your back. As a Home Assistant add‑on all of this is unnecessary (ingress already authenticates) and is skipped.
- **Remote access: put an authenticating proxy in front, don't expose Mate directly.** Mate holds your Leapmotor credentials and can command the car, so for access from outside your network the safest route is to keep authentication *out* of Mate and delegate it. A **VPN** (Tailscale, WireGuard) means no public exposure at all. If you'd rather reach it from any browser without a VPN, an **identity‑aware proxy** — [Pomerium](https://www.pomerium.com/), Cloudflare Access, or Authelia — sits in front and logs you in with an account you already have (GitHub, Google, …), so your user accounts stay separate from Mate and you get sessions, lockout and password reset done properly. *(Thanks to @DerMAp for the Pomerium tip.)*
- Use a **dedicated Leapmotor account** (see Requirements).
- This is an **unofficial** project, not affiliated with Leapmotor. It relies on reverse‑engineered cloud APIs and may break if Leapmotor changes them. Use at your own risk.
- Built on the [`leapmotor-api`](https://github.com/markoceri/leapmotor-api) Python client.

## Credits

- [`kerniger/leapmotor-ha`](https://github.com/kerniger/leapmotor-ha) — original Leapmotor cloud API reverse-engineering / Home Assistant integration.
- [`markoceri/leapmotor-api`](https://github.com/markoceri/leapmotor-api) — Python cloud client.
- [`markoceri/leapmotor-certs`](https://github.com/markoceri/leapmotor-certs) — app certificate.
- Inspired by [TeslaMate](https://github.com/teslamate-org/teslamate) and the Leapmotor Home Assistant integrations.

## License

[GNU AGPL‑3.0](./LICENSE) © Silvio Bressani.

---
---

# LeapMotor Mate · Italiano

**Tracciamento viaggi, registro ricariche e controllo remoto per veicoli Leapmotor** — un companion self‑hosted (un *TeslaMate* per Leapmotor). Funziona come **add‑on di Home Assistant** o come **container Docker standalone**.

Modelli supportati: **B05 · B10 · C10 · T03** — solo full‑electric (BEV), spec. europea (gamma Leapmotor distribuita da Stellantis/Leapmotor). NON per le versioni REEV / range‑extender.

## ☕ Sostieni il progetto

LeapMotor Mate è gratuito e open-source, sviluppato nel tempo libero. Se ti è utile, puoi sostenerne lo sviluppo con un caffè — grazie! ☕

<a href="https://www.buymeacoffee.com/protossblaster" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" height="48"></a>
<a href="https://www.paypal.me/ProtossBlaster" target="_blank"><img src="https://img.shields.io/badge/PayPal-Donate-00457C?style=for-the-badge&logo=paypal&logoColor=white" alt="PayPal" height="48"></a>

## Schermate

| Panoramica | Viaggi |
|---|---|
| ![Panoramica](docs/screenshots/overview.png) | ![Viaggi](docs/screenshots/trips.png) |
| **Ricariche** | **Wallbox** |
| ![Ricariche](docs/screenshots/charges.png) | ![Wallbox](docs/screenshots/wallbox.png) |
| **Statistiche** | **Comandi** |
| ![Statistiche](docs/screenshots/statistics.png) | ![Comandi](docs/screenshots/commands.png) |

## Funzionalità

- **Modalità demo** — esplora tutta l'app su un mese realistico e autonomo di **dati di esempio** (pendolarismo, ricarica casa + DC fast, costi, salute batteria, un weekend al mare) **senza auto né account**. Al primo avvio basta cliccare **"Prova la demo"** nella schermata di benvenuto — **un clic, niente riga di comando**, quindi funziona direttamente anche dall'add‑on Home Assistant. (Standalone puoi anche usare `docker run --rm -p 4000:4000 -e MATE_DEMO=1 ghcr.io/protossblaster/leapmotor-mate` e aprire <http://localhost:4000>.) Un badge **DEMO** è sempre visibile e *"Esci dalla demo e configura la mia auto"* ti porta al setup reale. _I dati sono puramente dimostrativi — niente è reale._
- **Panoramica** — stato live, batteria, autonomia, **stato READY**, mappa posizione, immagine del veicolo.
- **Stato a colpo d'occhio** — la prima scheda della Panoramica mostra ora anche un indicatore **Sicurezza** (verde **Attiva** quando l'auto è chiusa e l'allarme è inserito) e, finché il cavo è ancora collegato dopo una ricarica completata, un badge **Carica completa**.
- **Viaggi** — rilevamento automatico con mappa del percorso, distanza, energia, efficienza e regen. Ogni viaggio mostra anche i **kWh totali consumati** e il **costo** (energia × prezzo per kWh dell'ultima ricarica prima del viaggio, nella tua valuta). Puoi **eliminare un viaggio** (con conferma) per togliere dati sbagliati, o **unire viaggi** separati da una sosta breve senza ricarica — scegli le coppie unibili con uno slider, vedi l'anteprima del percorso combinato, ed è totalmente reversibile (Separa quando vuoi).
- **Consumi ufficiali del viaggio (cloud) 🆕** — energia, efficienza e costo di ogni viaggio ora usano il **dato ufficiale del cloud** Leapmotor (la vera ripartizione **guida / A·C / altro**) quando il cloud ce l'ha, invece della sola stima dal % batteria — con la **ripartizione** sul viaggio e i valori che confluiscono in Report e Statistiche. Mentre il cloud sta ancora elaborando un viaggio appena fatto mostra la stima marcata **⏳ provvisorio**, poi passa da solo al numero ufficiale (la stima resta come backup reversibile). Un bottone **"Converti con dati ufficiali"** aggiorna i viaggi più vecchi su richiesta, e una card **"Cumulativo Totale del Veicolo"** mostra energia, km e media da consegna. Se l'auto non ha caricato un viaggio sul cloud (capita, su qualsiasi marca), quel viaggio resta semplicemente sulla stima — mai un errore. Sempre attivo, nessuna configurazione.
- **Profilo altimetrico e temperatura esterna per viaggio 🆕** — il cloud Leapmotor non espone né l'altitudine né la temperatura esterna, così Mate confronta la traccia GPS di ogni viaggio concluso con [Open-Meteo](https://open-meteo.com) (gratuito, senza chiave né account). Nel dettaglio del viaggio compaiono la **linea della quota sul grafico SoC e velocità** (una sezione topografica sotto la tua telemetria), il **dislivello totale** (↑ salita / ↓ discesa) e la **temperatura esterna alla partenza e all'arrivo** — non una media, così una salita da fondovalle al passo mostra il calo reale. Insieme spiegano buona parte dei consumi di un viaggio: una salita costa energia, il freddo costa autonomia. Gira in background pochi minuti dopo il viaggio (una richiesta per viaggio); i viaggi registrati prima hanno un pulsante **Calcola dislivello**. Vengono inviati solo i punti GPS del viaggio, e puoi disattivarlo in *Impostazioni*.
- **Ricariche** — sessioni con rilevamento AC/DC, energia aggiunta, potenza e grafico di distribuzione. Ogni sessione mostra il **€/kWh effettivo** (costo ÷ energia) e per le ricariche pubbliche "ingestibili" puoi impostare un costo **✎ Manuale** — il totale reale pagato — che scavalca la stima da tabella ed entra nel costo viaggio / media‑ponderata.
- **Scheda Batteria nella pagina Ricariche** — % batteria, autonomia e barra con un **marker sul tuo limite di carica**, aggiornate live mentre segui una ricarica — senza più tornare in Panoramica. Anche il pulsante **Sblocca cavo** ora vive qui (in fondo è un'azione di ricarica).
- **Prezzi di ricarica** — prezzo fisso 24h o **fasce orarie**: prezzi per fascia, per **giorno della settimana** e per tipo di ricarica, e ogni sessione viene calcolata correttamente (energia ripartita tra le fasce attraversate dalla curva di potenza reale).
- **Preparazione veicolo con un tocco** — una pagina **Preparazione veicolo** dedicata che rispecchia la *"preparazione del veicolo con un solo tocco"* dell'app ufficiale: combina **A/C** (raffreddamento / riscaldamento / ventilazione / sbrinamento / auto + temperatura), **riscaldamento / ventilazione dei sedili anteriori**, **riscaldamento volante e specchietti**, ed eseguila **subito** (immediata) o su **programmazione** (orario + giorni). Le programmazioni si leggono dall'auto, sono **modificabili** e rimovibili, e un pulsante **Annulla preparazione (spegni tutto)** riporta tutto in off.
- **Schedulazione ricarica e clima** — una pagina **Schedulazione** dedicata. Programma una **fascia di ricarica** (on/off, SoC obiettivo, inizio/fine, giorni della settimana) e la **pre-climatizzazione** (preset rapidi **raffreddamento / riscaldamento / ventilazione / sbrinamento / auto**, orario, giorni, temperatura). Entrambe scrivono sull'auto e restano allineate con l'app Leapmotor; "Attivo" è l'interruttore principale (off = nessuna schedulazione / ricarica sempre).
- **Esportazione CSV** — esporta viaggi e ricariche in **CSV** (più **GPX** per viaggio), dalle pagine Viaggi/Ricariche o da *Impostazioni → Esporta/Backup*.
- **Wallbox (opzionale)** — abbina una wallbox già presente in Home Assistant per vedere potenza/stato di carica live, impostare la corrente max e confrontare l'**AC erogato dalla wallbox** con il **DC entrato in batteria** per sessione, col rendimento di carica. Una **modalità avanzata "mostra tutte le entità"** permette di mappare *qualsiasi* sensore — utile per nomi in altre lingue o un contatore/relè generico.
- **Profili wallbox salvati 🆕** — carichi in più posti? Salva ogni wallbox (connessione Home Assistant, mappatura entità **e** tariffa) con un nome e passa dall'una all'altra con un clic dalle **Impostazioni**; quella attiva è mostrata nella pagina Costi e il cambio è bloccato durante una ricarica, così una sessione non viene mai divisa tra due wallbox. _(Feature della community di @domevite, #84.)_
- **Nome della colonnina sulle ricariche (opzionale)** — ogni ricarica pubblica viene etichettata automaticamente col **nome della colonnina** (📍 nella lista Ricariche e sull'"ultima ricarica" della Panoramica), cercato su **OpenStreetMap** e sul **registro nazionale italiano (PUN)**. Le ricariche di casa non vengono mai interrogate, e vengono completate anche quelle già registrate. Spento di default *(Impostazioni → Colonnine di ricarica)*. *(Idea: @hubcasale.)*
- **Trova colonnine (Navigazione)** — un pulsante **⚡ Trova colonnine** mappa le colonnine pubbliche attorno all'auto: scegli **distanza massima** e **risultati per pagina**, filtra **per operatore** (es. Electra, Ionity) e vedi **AC/DC, kW e disponibilità live**, sia come pin sulla mappa sia in una lista sotto — tocca una colonnina per impostarla come destinazione e inviarla al navigatore dell'auto. Fonde **OpenStreetMap + il registro nazionale PUN** (entrambi senza chiave), più **Open Charge Map** e **TomTom** se aggiungi le loro chiavi API gratuite — più fonti, più copertura.
- **Assegnazione automatica "Casa" (opzionale)** — un toggle in *Impostazioni → Wallbox*: le ricariche in cui **il tuo wallbox ha misurato l'energia** vengono confermate come **Casa** automaticamente — basta toccare il badge dopo ogni ricarica notturna. Il costo passa dallo **stesso motore della conferma manuale** (prezzi flat *e* fasce orarie, fatturato sull'energia AC del wallbox), il tipo resta modificabile, e le ricariche DC/pubbliche/ricostruite non vengono mai toccate. Spento di default. *(Idea: @hubcasale.)*
- **ABRP (opzionale)** — invia la telemetria live ad **A Better Route Planner** per la pianificazione dei percorsi (attivala col tuo token ABRP).
- **MQTT → Home Assistant (opzionale)** — pubblica l'auto a Home Assistant via **MQTT Discovery** come entità native (sensori, binary sensor, tracker GPS) più pulsanti comando.
- **EVCC (opzionale)** — pubblica topic MQTT compatibili con **EVCC** così un veicolo `type: custom` legge SoC, stato spina/ricarica, autonomia e odometro (configurazione pronta in `docs/EVCC.md`).
- **Statistiche** — ripartizione energia guida/clima/altro e trend consumo a 6 settimane (dal cloud Leapmotor).
- **Report mensile** — un cruscotto per-mese di guida, ricariche e **costi** in un colpo solo: distanza, efficienza ed energia; costo ricariche, sessioni e €/kWh medio; split **Casa vs pubblico**; **variazioni rispetto al mese precedente**; grafici giornalieri distanza/costo; e una **mappa di tutti i viaggi del mese**. Naviga i mesi con ◀ ▶. I numeri combaciano esattamente con le pagine Statistiche e Ricariche.
- **Controllo remoto** — blocco, finestrini, bagagliaio, tetto panoramico, **clima** (raffredda / riscalda / ventilazione / sbrinamento, A/C on-off, temperatura), **sedili riscaldati e ventilati** (livello per sedile), **volante e specchietti riscaldati**, trova auto, preriscaldo batteria, **sblocco cavo di ricarica**.
- **Navigazione** — cerca un indirizzo e **invia la destinazione direttamente al navigatore di bordo dell'auto**. Mostra anche l'indirizzo attuale dell'auto. La ricerca indirizzi funziona senza chiave (OpenStreetMap) con una chiave API opzionale (Geoapify/LocationIQ/TomTom) per una copertura migliore dei civici.
- **Indipendente** — interroga direttamente il cloud Leapmotor (configurabile 10–30 s). Nessuna dipendenza dall'app o da Home Assistant; interrogare il cloud **non** sveglia né scarica l'auto. Non è in tempo reale, quindi un pulsante **Aggiorna** (barra laterale, e nell'header su mobile) recupera lo stato attuale dell'auto su richiesta.
- **UI multilingua** — Italiano · English · Français · Deutsch · **Polski**.
- **Valuta** — scegli la valuta di visualizzazione tra 30 valute mondiali (€, $, £, CHF, kr, zł…); ogni costo si riformatta con simbolo e decimali corretti.
- **Unità di misura** — scegli **Metrico**, **Imperiale UK** (miglia e mph, ma °C) o **Imperiale US** (miglia, °F, psi). Distanze, velocità, temperature e pressioni gomme si mostrano nel sistema scelto. È **solo visualizzazione**: i dati restano sempre in metrico, quindi puoi cambiare quando vuoi senza perdere nulla.
- **Diagnostica** — una scheda in *Impostazioni → Diagnostica* con uno snapshot di sistema in sola lettura, i log recenti poller/web e i segnali grezzi attuali dell'auto (con Copia), più un **bundle scaricabile** con un clic da allegare a una issue su GitHub. Le info personali (VIN — anche dove è incorporato nel topic MQTT, credenziali, e-mail e **coordinate GPS esatte**) sono **sempre mascherate** nei log esportati.
- **Indicatore aggiornamenti OTA** — la scheda Panoramica ti dice quando l'auto ha un **aggiornamento software del veicolo** in attesa, senza aprire l'app ufficiale.
- **Badge auto-aggiornamento di Mate** — un piccolo badge accanto al numero di versione quando su GitHub c'è una **release di Mate più recente** (controllo in background ogni 6 h) — comodo per le installazioni Docker standalone.
- **Capacità batteria modificabile** — precompilata per modello (kWh netti/utilizzabili); modificala se la tua è diversa, o clicca **“usa misurata”** per adottare il valore che Mate ha calcolato dalle tue ricariche. Cambiarla non riscrive mai le ricariche passate.
- **Impostazioni avanzate** — una scheda richiudibile per regolare i casi particolari: soglia rilevamento ricariche perse, soglia rumore consumo-da-fermo, soglia potenza AC/DC (per wallbox AC da 22 kW) e la **soglia freddo per la salute batteria**. Valori predefiniti sensati, reset con un tocco.
- **Recupero ricariche perse** — cerca nella cronologia le ricariche avvenute mentre l'auto dormiva prima che esistesse il rilevamento automatico; mostra cosa trova prima di aggiungere qualcosa.
- **Toggle blocco singolo per Home Assistant** — un'entità MQTT *lock* per i dashboard **più uno switch “Door Lock Toggle”** per i widget launcher che non sanno toggleare i lock (es. Samsung): un tocco blocca, il successivo sblocca — perfetto come bottone singolo sulla home del telefono. I pulsanti classici restano.
- **Cancella account / Factory reset** — un'azione protetta in *Impostazioni → Veicolo* che azzera **tutto** (account, viaggi, ricariche e ogni impostazione) e riapre il wizard di setup come una nuova installazione — per passare l'installazione a qualcun altro o ripartire da zero. Conferma da digitare; il certificato dell'app viene mantenuto, quindi il re-onboarding chiede solo il login.

## Come funziona

```
Cloud Leapmotor  ──►  Poller (state machine)  ──►  SQLite  ──►  Web UI (FastAPI + HTMX)
                       viaggi / ricariche / regen              + comandi remoti
```

I dati restano in un database SQLite locale. Nulla viene inviato altrove se non al cloud ufficiale Leapmotor.

> ℹ️ **Mate non è in tempo reale — fa polling.** Legge lo stato dell'auto dal cloud Leapmotor a intervalli: circa ogni **30 s da fermo** e **10 s in marcia** (regolabile nelle Impostazioni). Quindi un cambiamento fatto dall'app ufficiale (apertura baule, modifica del limite di carica…) compare su Mate entro quel lasso, non all'istante. Mate legge **passivamente** e non sveglia mai l'auto, così non scarica la batteria — l'app ufficiale sembra istantanea perché aprirla *sveglia* l'auto. Ti serve prima? Il pulsante **🔄 Aggiorna** (in cima alla barra laterale) recupera lo stato su richiesta. Se l'auto dorme, il cloud restituisce l'ultimo stato noto finché l'auto non si risveglia.

## Requisiti

1. **Un account Leapmotor — dedicato a Mate e usato da *nient'altro*.** ⚠️ Leapmotor consente circa una sola sessione attiva per account: **qualsiasi altro client sullo stesso account — l'app ufficiale del telefono, un altro add-on, un container Docker o qualsiasi altra integrazione — litiga con Mate per la sessione**: si sfrattano a vicenda in loop, l'auto va **offline per Mate** e ottieni **dati mancanti o incoerenti**. Usa un account separato solo per Mate (non quello del telefono). Crea un account separato, poi **condividi l'auto con esso dall'app ufficiale**: dall'account che *possiede* l'auto, condividi/autorizza il veicolo al nuovo account con **tutti i permessi** e durata **permanente** (una condivisione temporanea scade e poi rompe Mate). **Verifica che funzioni:** **configura il *secondo* account nell'app ufficiale Leapmotor su un dispositivo** (non solo accedere all'account via web) e controlla che l'auto compaia — se non c'è, la condivisione non è ancora attiva e Mate dirà *«No vehicle found on this account».*
2. **Il certificato TLS dell'app Leapmotor** (`app.crt` + `app.key`). È *uguale per tutti* (identifica l'app, non te) e **non** è incluso in questo repository. Scarica i due file da:

   👉 **https://github.com/markoceri/leapmotor-certs**

   Li carichi una volta sola durante il wizard di setup.

## Installazione

> **▶️ Vuoi solo vedere cosa sa fare Mate?** Prova prima la **demo** — un mese realistico di dati di esempio, **senza auto né account**. Installala (add‑on o Docker), apri Mate e clicca **"Prova la demo"** nella schermata di benvenuto — **niente riga di comando**. Oppure eseguila standalone:
>
> ```bash
> docker run --rm -p 4000:4000 -e MATE_DEMO=1 ghcr.io/protossblaster/leapmotor-mate
> ```
>
> Apri <http://localhost:4000>. In modalità demo è tutto **dati di esempio — niente è reale**.

### Opzione A — Add‑on Home Assistant

1. In Home Assistant: **Impostazioni → Applicazioni → Installa app → ⋮ → Archivi digitali** (su Home Assistant prima della 2026.2: **Impostazioni → Add‑on → Store → ⋮ → Repository**), e aggiungi l'URL del repository (nota il suffisso `-addon` — è un repo separato dal codice):

   ```
   https://github.com/ProtossBlaster/leapmotor-mate-addon
   ```

2. Installa **LeapMotor Mate**, avvialo e apri il pannello (icona auto nella barra laterale).
3. Segui il wizard di setup.

Il database è salvato nella `/data` persistente dell'add‑on, quindi sopravvive a riavvii e aggiornamenti.

### Opzione B — Docker standalone

**Più semplice — immagine già pronta** (niente clone, niente build):

```bash
docker run -d --name leapmotor-mate \
  --restart unless-stopped \
  -p 4000:4000 \
  -v "$(pwd)/data:/data" \
  ghcr.io/protossblaster/leapmotor-mate:latest
```

La stessa immagine è anche su [Docker Hub](https://hub.docker.com/r/protossblaster/leapmotor-mate) — puoi usare `protossblaster/leapmotor-mate:latest` in modo equivalente.

Per aggiornare in seguito: `docker pull ghcr.io/protossblaster/leapmotor-mate:latest` e ricrea il container (oppure usa [Watchtower](https://containrrr.dev/watchtower/) per gli aggiornamenti automatici).

**Oppure build da sorgente:**

```bash
git clone https://github.com/ProtossBlaster/leapmotor-mate.git
cd leapmotor-mate
docker compose up -d
```

Poi apri **http://localhost:4000** e segui il wizard.

Il database è salvato in `./data/` (montato su `/data` nel container).

## Wizard di setup

Al primo avvio compare una scelta — **Configura la mia auto** o **Prova la demo**. Scegliendo *Configura la mia auto*, due passi:

1. **Certificato** — carica `app.crt` e `app.key` (oppure incolla il testo PEM). Li trovi su [markoceri/leapmotor-certs](https://github.com/markoceri/leapmotor-certs). Salvati in modo persistente in `/data/certs`.
2. **Login** — email account Leapmotor, password e **PIN** operativo. Il wizard legge dal cloud **modello** e VIN. La **batteria** riesce a metterla da solo soltanto dove la versione europea ha una variante unica (T03) — dove ce ne sono più d'una (B10 Pro / Pro Max, C10 RWD / AWD) la scegli tu. Si corregge quando vuoi da Impostazioni → Batteria.

Fatto — il poller parte e i dati iniziano a comparire.

## Configurazione

Tutto si configura dalla UI web (**Impostazioni**), senza YAML:

- **Intervallo di polling** — parcheggiata (default 30 s) e in marcia (default 10 s). Più veloce rileva prima viaggi/ricariche; più lento riduce le chiamate. Interrogare il cloud non sveglia né scarica l'auto.
- **Prezzi di ricarica** — fisso o a fasce orarie, dalla pagina dedicata *Prezzi di ricarica* (vedi sotto).
- **Lingua e valuta** — Italiano / English / Français / Deutsch / **Polski**, e la valuta di visualizzazione (€, $, £, CHF, zł… 30 valute). Il formato numero (separatore decimale/migliaia) segue la lingua selezionata.

### Prezzi di ricarica

Imposta quanto costa ogni kWh dalla pagina dedicata **Prezzi di ricarica** (💰 nella barra laterale), così Mate calcola il costo delle ricariche. Due modalità:

- **Fisso (24h)** — un prezzo per tipo di ricarica (Home / AC / DC / HPC).
- **Fasce orarie** — aggiungi una o più fasce, scegli i **giorni della settimana** in cui valgono (scorciatoie Tutti / Feriali / Weekend) e imposta un prezzo per tipo di ricarica per ogni fascia. Lascia un prezzo vuoto per usare il prezzo base, oppure metti `0` se in quella fascia è gratis. Una sessione a cavallo di due fasce viene ripartita dalla sua curva di potenza reale, e una che attraversa la mezzanotte sab→dom è tariffata per giorno correttamente.

Le modifiche ai costi valgono solo per le **ricariche future**: il costo si congela alla conferma del tipo, quindi cambiare prezzi o fasce non altera le sessioni già fatte.

**Come vengono contati i kWh (ricariche di casa):** se la tua wallbox è abbinata ed espone un **contatore di kWh**, Mate lo campiona **per tutta la ricarica** e fattura l'**energia aggiunta** — la somma degli incrementi del contatore durante la sessione, cioè l'energia esatta erogata dalla wallbox (perdite di conversione incluse), misurata, non stimata. È **a prova di reset/race**: funziona sia che il contatore sia un totale a vita (come un contachilometri) sia che sia un contatore per-sessione che si azzera a metà ricarica, indipendentemente da quando si resetta. La card della ricarica mostra in primo piano i kWh **🔌 wallbox (da pagare)** e, sotto, l'energia **🔋 in batteria (DC, da SoC)** con il rendimento AC→DC; il costo è semplicemente *kWh wallbox × prezzo*. Senza contatore wallbox (o per le ricariche pubbliche) Mate fattura l'**energia in batteria (SoC) × prezzo**. La potenza istantanea serve solo al grafico, mai al costo.

> ⚠️ Vale per le ricariche registrate **da v1.12.0 in poi** (le letture del contatore vengono catturate dal vivo durante la sessione). Le ricariche più vecchie mantengono il valore con cui erano state calcolate e **non sono ricalcolabili** col nuovo metodo — se vuoi puoi eliminare una vecchia sessione col pulsante 🗑 sulla sua card.

### Opzionale: boost da Home Assistant

Se hai Home Assistant sulla stessa rete, puoi attivare un polling veloce temporaneo all'inizio di un viaggio (es. da uno shortcut Bluetooth/telefono) chiamando `POST http://<host-mate>:4000/api/boost`. Con la cadenza di default a 30 s è opzionale.

### Wallbox (Home Assistant)

Se ricarichi a casa e hai una **wallbox già integrata in Home Assistant** (Wallbox Pulsar, Easee, go‑e, Keba, OCPP, …), Mate può abbinarla per mostrare i dati di ricarica live e confrontare ciò che la **wallbox eroga (AC)** con ciò che l'**auto riceve in batteria (DC)**.

Attivala in **Impostazioni → Wallbox presente**, poi connettiti a Home Assistant. Come ti connetti dipende da come esegui Mate:

- **Come add‑on di Home Assistant** — *niente da configurare.* Mate raggiunge HA tramite l'API interna del Supervisor in automatico, a prescindere da come HA è esposto all'esterno (HTTP, HTTPS, Nabu Casa). Vedrai solo lo **stato connessione** con la pallina verde.
- **Come Docker standalone** — inserisci l'URL di HA (es. `http://192.168.1.10:8123`) e un **Long‑Lived Access Token** (HA → tuo profilo → *Sicurezza* → *Token di accesso Long‑Lived* → *Crea token*). L'HTTPS locale, anche con certificato self‑signed, funziona.

Poi espandi **Mappatura entità** e assegna i sensori della wallbox (potenza, energia, stato, corrente max, velocità di carica, potenza max disponibile). Mate li pre‑seleziona da solo e mostra solo le entità del tuo dispositivo wallbox, così non devi scorrere tutti i sensori di Home Assistant.

**Cosa significa ogni impostazione** — tutte opzionali (Mate le rileva da solo; sovrascrivi una voce solo se la mappatura automatica sceglie l'entità sbagliata, es. nomi in altra lingua):

| Impostazione | Cos'è |
| --- | --- |
| **Potenza** | La potenza che la wallbox eroga **in questo momento** (AC). Pilota l'indicatore "in carica" live e il lato **AC** del confronto AC‑vs‑DC. I W vengono convertiti automaticamente in kW. |
| **Stato** | Il testo di stato della wallbox da Home Assistant (es. *In carica / Connessa / Inattiva / Errore*). |
| **Energia sessione** | Energia erogata nella sessione (kWh; i Wh sono convertiti). È l'**energia AC in kWh** con cui Mate addebita le ricariche di casa (paghi l'AC della wallbox, perdite di conversione incluse) e calcola il rendimento. |
| **Controllo potenza** | L'**unica** impostazione **wallbox** scrivibile (entità `number`): imposta la **corrente di carica massima** (A) della wallbox dalla pagina Wallbox. Le tue automazioni HA di bilanciamento del carico potrebbero sovrascrivere il valore impostato. (Il **limite di carica** dell'auto è un `number` scrivibile a parte — vedi la sezione MQTT.) |
| **Velocità di carica** | La lettura "velocità di carica" della tua wallbox, se la espone (mostrata live). |
| **Potenza max disponibile** | La potenza massima attualmente disponibile per la wallbox (es. dopo bilanciamento dinamico o limite tariffario), se esposta. |

Solo **Controllo potenza** scrive sulla wallbox; tutto il resto è in sola lettura.

Cosa ottieni nella nuova pagina **Wallbox**:
- un **pannello live** (potenza, stato, energia sessione, velocità di carica, potenza max disponibile) più il costo sessione (riusato dalle tue ricariche home);
- un **controllo della corrente max** per impostare la corrente di carica della wallbox — nota che le tue automazioni HA di bilanciamento del carico potrebbero sovrascriverlo;
- un **confronto AC‑vs‑DC** per sessione (kWh erogati vs entrati in batteria + rendimento), come storico anno/mese/giorno; espandi una sessione per il grafico di potenza. La curva wallbox usa lo storico di Home Assistant (conservato ~10 giorni), quindi il confronto compare per le sessioni recenti;
- l'**assegnazione automatica "Casa"** opzionale (Impostazioni → Wallbox): le ricariche misurate dal wallbox vengono confermate come **Casa** da sole, col costo calcolato dai tuoi prezzi e fasce orarie esattamente come una conferma manuale. Spenta di default. *(Idea: @hubcasale.)*

### ABRP (A Better Route Planner)

Invia i dati live dell'auto ad **A Better Route Planner** per la pianificazione dei percorsi. In **Impostazioni → ABRP**, attivala e incolla il tuo token ABRP personale (nell'app ABRP: *Impostazioni → Auto → Dati live*, "Generic"). È disattivata finché non la abiliti, e non invia nulla senza token.

### MQTT → Home Assistant

Pubblica l'auto a Home Assistant come **entità native** (in parallelo all'interfaccia di Mate), via MQTT Discovery. In **Impostazioni → MQTT**, attivala e inserisci il tuo broker (host, porta, utente/password; TLS opzionale). Home Assistant crea automaticamente un dispositivo *Leapmotor Mate* con sensori (SOC, autonomia, gomme singole, temperature, carica…), binary sensor (porte/finestrini/serratura/ricarica), un tracker GPS, un **limite di carica** (target SoC) `number` scrivibile, una **Programmazione ricarica** (`text` scrivibile) che accetta un piano in JSON pensato per le automazioni (`{"start":"23:00","soc":90}` — ogni campo è opzionale, e quello che ometti resta com'è), e pulsanti comando (lock/unlock, baule, trova auto, sblocco cavo di ricarica, clima — Quick Cool / Quick Heat / Ventilazione / Sbrinamento / A/C Off — e comfort: sedili riscaldati/ventilati, riscaldamento volante e specchietti). Lo spegnimento **completo** dell'A/C ora funziona sulla B10 (usa il comando `operate=off`, individuato con i test sull'auto); i comandi comfort usano i payload catturati da [@kerniger](https://github.com/kerniger/leapmotor-ha). Funziona con qualsiasi broker MQTT (es. l'add‑on Mosquitto). Usa **Prova connessione** per verificare il broker prima di salvare. Dopo un comando lo stato ora si aggiorna in Home Assistant all'istante (senza aspettare il polling successivo), e il **prefisso topic** delimita il dispositivo — così puoi far girare una seconda istanza con un prefisso diverso senza che entri in conflitto con la prima.

## Note e disclaimer

- **Il messaggio "Vehicle not reporting live data" nei log è normale.** Quando l'auto resta parcheggiata abbastanza a lungo va in **deep sleep** e il cloud non restituisce segnali live. Mate passa al polling ogni 15 minuti (loggato una volta sola, non ad ogni ciclo) e si riprende da solo appena l'auto torna a riportare — quando viene guidata, o svegliata dall'app ufficiale Leapmotor. Per essere sicuro di registrare anche un viaggio breve subito dopo il deep sleep, usa il trigger boost qui sopra.
- **Le tue credenziali sono cifrate a riposo.** La password/PIN Leapmotor (e gli eventuali token HA / ABRP / MQTT / geocoder) sono salvati cifrati nel database locale, con una chiave per‑installazione in `/data/secret.key` (auto‑generata, oppure la tua tramite la variabile `MATE_SECRET_KEY`). ⚠️ Conserva `secret.key` insieme ai backup — ripristinando solo il database senza la chiave dovrai re‑inserire le credenziali.
- **Standalone: login opzionale.** In modalità standalone (non add‑on), puoi richiedere una password all'apertura dell'app — impostala da **Impostazioni → Accesso**, oppure tramite la variabile d'ambiente `MATE_AUTH_PASSWORD` (se ci sono entrambe, vince la variabile). In standalone Mate rifiuta anche le richieste che modificano qualcosa provenienti da un altro sito e si rifiuta di essere incorniciato in una pagina esterna, così una pagina aperta nel tuo browser non può comandare l'auto a tua insaputa. Come add‑on Home Assistant tutto questo non serve (l'ingress autentica già) e viene saltato.
- **Accesso remoto: metti davanti un proxy con autenticazione, non esporre Mate direttamente.** Mate custodisce le tue credenziali Leapmotor e può comandare l'auto, quindi per l'accesso da fuori rete la strada più sicura è tenere l'autenticazione *fuori* da Mate e delegarla. Una **VPN** (Tailscale, WireGuard) elimina del tutto l'esposizione pubblica. Se preferisci raggiungerlo da qualsiasi browser senza VPN, un **proxy identity‑aware** — [Pomerium](https://www.pomerium.com/), Cloudflare Access o Authelia — si mette davanti e ti fa accedere con un account che hai già (GitHub, Google, …): così i tuoi account utente restano separati da Mate e ottieni sessioni, blocco tentativi e reset password fatti come si deve. *(Grazie a @DerMAp per il suggerimento su Pomerium.)*
- Usa un **account Leapmotor dedicato** (vedi Requisiti).
- Progetto **non ufficiale**, non affiliato a Leapmotor. Usa API cloud ricavate per reverse‑engineering e può smettere di funzionare se Leapmotor le cambia. Usalo a tuo rischio.
- Basato sul client Python [`leapmotor-api`](https://github.com/markoceri/leapmotor-api).

## Licenza

[GNU AGPL‑3.0](./LICENSE) © Silvio Bressani.
