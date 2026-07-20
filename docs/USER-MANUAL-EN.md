# LeapMotor Mate — User Manual

> **Mate version:** v1.27.0 · **Language:** English (first edition)
> This manual is written for people who *use* Mate, not for those who develop it. It explains how to
> set it up from scratch and what every page does. For the internal technical details, see `ARCHITECTURE.md`.

---

## Table of Contents

1. [What Mate is (and what it isn't)](#1-what-mate-is-and-what-it-isnt)
2. [Before you start: the requirements](#2-before-you-start-the-requirements)
3. [Installation](#3-installation)
4. [First start: the setup wizard](#4-first-start-the-setup-wizard)
5. [Getting to know the interface](#5-getting-to-know-the-interface)
6. [The pages, one by one](#6-the-pages-one-by-one)
   - [Overview](#overview) · [Trips](#trips) · [Map](#map) · [Charges](#charges)
   - [Charge Prices](#charge-prices) · [Statistics](#statistics) · [Monthly Report](#monthly-report)
   - [Battery health](#battery-health) · [Maintenance](#maintenance) · [Commands](#commands)
   - [Scheduling](#scheduling) · [Prepare car](#prepare-car)
   - [Navigation](#navigation) · [Vehicle](#vehicle) · [Wallbox](#wallbox)
7. [Settings](#7-settings)
8. [The integrations in detail (Wallbox, ABRP, MQTT)](#8-the-integrations-in-detail)
9. [Demo mode](#9-demo-mode)
10. [Frequently asked questions and troubleshooting](#10-frequently-asked-questions-and-troubleshooting)
11. [Glossary](#11-glossary)

---

## 1. What Mate is (and what it isn't)

**LeapMotor Mate** is an application that you install yourself (self-hosted) and that acts as a
"companion" for your Leapmotor electric car. It connects to the **Leapmotor cloud** (the same one the
official app talks to), reads the car's status and, from that data, reconstructs on its own:

- your **trips** (distance, duration, consumption, regenerative braking recovery);
- your **charges** (energy, power, type, cost);
- the **costs** and the **efficiency** over time;
- the **battery health** and the **maintenance due dates**.

On top of that it lets you **send remote commands** (locking, climate, vehicle preparation,
scheduling…) and, if you like, integrate the data with **Home Assistant** (via MQTT), with
**A Better Routeplanner (ABRP)** and with your **wallbox**.

**What it does NOT do / important limits:**

- **It does not talk to the car directly.** Everything goes through the Leapmotor cloud. When Mate
  "queries" the cloud (polling) it reads the **last known status**: it does *not* wake the car up and
  does *not* drain the battery. It's a safe and inexpensive operation.
- **Only 100% electric cars (BEV).** The supported models are **T03, B05, B10, C10** in their
  electric versions. The **REEV** versions (with a petrol range extender) are **not** supported: the
  energy/consumption/cost calculations would use the wrong battery capacity and come out distorted.
- **European cloud only (Leapmotor International / Stellantis).** Accounts registered on servers of
  other regions (e.g. China) cannot log in. Outside Europe, Mate currently can't be used.
- **It is not an accounting tool.** It estimates cost *from the telemetry*; it does not keep track of
  payment methods, invoices or charging-station subscriptions.

---

## 2. Before you start: the requirements

To set Mate up you need three things:

1. **A Leapmotor account dedicated to Mate.** ⚠️ **Very important.** Create (or set aside) a
   Leapmotor account that you use **only** for Mate. Leapmotor allows only a few simultaneous
   sessions per account: if the same account is also logged in to the official app, to another
   integration or to a second instance of Mate, the clients keep "evicting" each other's session. The
   result is a barrage of *"Invalid token"* / repeated re-logins, the car going **offline** and
   **lost data** (trips and charges not recorded). It's the number-one cause of the problems people
   report. *Solution:* a secondary account with a **password used only in Mate**.

2. **The Leapmotor app certificate** (`app.crt` + `app.key`). It's a certificate that is **the same
   for everyone** (it belongs to the app, not to your account) and is needed to talk to the cloud.
   You download it from a public repository — the wizard gives you the direct link
   ([github.com/markoceri/leapmotor-certs](https://github.com/markoceri/leapmotor-certs)).

3. **Email, password and the account's operation PIN.** The **4-digit PIN** is the one you also use
   in the official app to authorize remote commands (locking, climate…).

> 💡 Just want to take a look without setting anything up? Skip it all and use **[demo mode](#9-demo-mode)**:
> Mate starts with a month of realistic fake data, with no car and no account.

---

## 3. Installation

Mate runs the same way in two environments (the interface is identical):

- **As a Home Assistant add-on** — the easiest way if you already have Home Assistant. You add the
  add-on repository, install "LeapMotor Mate" and open it from the HA sidebar (ingress). In this case
  Mate can also read your **wallbox** directly from Home Assistant.
- **As a standalone Docker container** (for example on a NAS) — via `docker-compose`. In this case
  the app is reachable from the browser on **port 4000** (`http://YOUR-SERVER-ADDRESS:4000`).

The step-by-step installation instructions (repository, compose, etc.) are in the project's
**README** and on the **Docker Hub** page. Once it's up and running, the *first sign-in* is the same
for both and is described below.

> 🔒 **Backup.** All of Mate's data lives in a persistent folder (`/data`): the database, the
> encryption key for the secrets (`secret.key`) and the certificate. If you make a backup, **save the
> database together with its `secret.key`** — without the key, saved passwords and tokens can no
> longer be read. From the Settings page you can download a database backup at any time.

---

## 4. First start: the setup wizard

On your first sign-in Mate shows a **wizard** (guided procedure). At the top you can choose the
language (🇮🇹 Italiano). Then:

### Step 0 — Choose how to start

Two buttons:

- **▶ Configure my car** — the actual setup (continues below).
- **🧪 Try the demo** — enters demo mode with fake data. You can leave whenever you want.

### Step 1 — App certificate

Mate asks you for the Leapmotor app's TLS certificate. You have two ways:

- **Upload the files** `app.crt` and `app.key` (the default mode), or
- **Paste the PEM text** of the two files (the *"Paste the PEM text instead"* button).

Download them from the link shown, upload them and press **Save certificate**. This step only appears
if the certificate isn't already present in the image.

### Step 2 — Account sign-in

Enter:

- **Leapmotor account email**
- **Password**
- **Operation PIN** (4 digits)

> ⚠️ Here Mate reminds you to use **an account dedicated only to Mate** (see
> [requirements](#2-before-you-start-the-requirements)).

Press **🔍 Detect my car**. Mate checks the credentials and reads the **model and chassis number
(VIN)** from the cloud. If all goes well you see a "Car detected" card showing `Leapmotor <model> ·
VIN ···xxxxxx`.

### Step 3 — Battery

Depending on the model:

- if the European version has **a single battery variant**, Mate detects it on its own (e.g. T03 →
  37.3 kWh);
- if there are **several variants** (e.g. B10 Pro 56.2 kWh / Pro Max 67.1 kWh; C10 RWD 69.9 / AWD
  81.9), you choose yours;
- if the detection fails, you can **enter the capacity by hand** (in kWh).

> The capacity shown is the **usable/net** one (the one that really matters for consumption and
> costs) and can always be corrected later, from Settings → Battery.

### Step 4 — Connect

Press **Connect & Start**. Mate saves the configuration, connects and takes you to the **Overview**.
From this moment the "poller" starts collecting data in the background: the first trips and charges
will appear as you drive and charge.

---

## 5. Getting to know the interface

The interface is made up of:

- **Side menu (sidebar)** — the list of pages (see below). On a small screen it opens with the ☰
  icon.
- **Header** — the page title, any **update available** notice (↑ vX.Y.Z) and the **🔄 Refresh now**
  button.
- **Refresh now button** — forces an immediate read of the car's status without waiting for the
  automatic cycle. Handy after sending a command.

At the bottom of the menu you'll find **⚙️ Settings** and **🚪 Log out**.

Many pages **refresh themselves** roughly every 30 seconds, so the "live" values (status, charge in
progress…) stay fresh without reloading the page.

**Language, currency and units** are changed from *Settings → 🌍 Language & Currency*:

- **Language:** Italiano, English, Français, Deutsch.
- **Currency:** for costs (€, £, …).
- **Units:** metric (km, °C) or imperial UK/US (miles, °F). The data is always stored in km/°C; only
  the way it's **displayed** changes.

---

## 6. The pages, one by one

The order below is the same as in the side menu.

### Overview
**(menu: Overview)** — The home. At the top there's a **main card** with the car's image and its live
status:

- **state of charge (SoC)** and estimated range;
- **status icons** that change colour: lock (green = locked, amber = unlocked), trunk (red if open),
  windows (purple if open), climate, etc.;
- **quick commands** (lock/unlock, find car…), already "aware" of the current state;
- when the car is **charging**, an **animation** shows the energy flow and a tag with the estimated
  time "to X%" (X = the charge limit you set in the car);
- a **"Cable connected / Charge complete"** tag when the cable is plugged in but it isn't actively
  charging.

When the car is **powering an external device through the V2L (vehicle-to-load) adapter**, the
Overview shows a **V2L block** with the **status** (Active / Inactive), the **instantaneous power** in
watts — reported **net of the car's own ~300 W overhead**, so it matches what your device actually
draws — with a 0–3500 W bar, and the **energy drawn this session**. It refreshes about every **10 s**
while a session is running. It is **read-only**: V2L is started **on the car** (gear in Park + a device
connected), not from Mate. It is accurate from about **42 W** upward (the car's own current-sensor
resolution — a tiny ~10 W load stays invisible).

Further down you'll find mini-statistics and a **"Car responsiveness" indicator** (a 🟢/🟡/🔴 dot, ⚪
if there's no data): it summarizes how well the car has responded to the latest commands sent.

### Trips
**(menu: Trips)** — The list of your drives, one per drive. For each trip you see **distance,
duration, consumption (kWh/100 km), energy recovered** in braking and the estimated **cost**.

- Clicking a trip opens the **detail**, with the **GPS track** on a map and the data of that single
  trip.
- You can **merge** two trips that were split by mistake (Merge 🔗) or **split** them again, and
  **delete** a trip.
- Short stops (traffic lights, queues) do **not** split a trip: one drive stays a single row.
- **Official consumption from the cloud 🆕** — when available, a trip's **consumption, efficiency and
  cost** come from Leapmotor's **official figure** (the real **driving / A·C / other** split) instead of
  the battery‑% estimate alone. Right after a drive you see the estimate marked **⏳ provisional**; once
  the cloud has processed the data (usually some tens of minutes) it is **replaced on its own** with the
  official one and the **breakdown** appears in the detail. Older trips have a **"Convert with official
  data"** button. If the cloud doesn't have a trip's data (it happens, on any connected car), the
  **estimate** stays — not an error. **Always on**, no setup.
  - **Counted from when the car is switched ON, not from the drive 🆕** — the official figure covers the
    whole **power-on session** (from switch-on to switch-off), so it can include time the car was on
    before you started driving. If you **never switch the car off between two trips** (you stop, stay in
    Park, drive again), the cloud counts them as **one** session — Mate tells you to **merge the two
    trips** to get the real combined consumption.
- **Your note + driving tags 🆕** (#107) — in a trip's detail you can jot a **free-text note** (traffic,
  weather, road type, any remark) and tag the **drive mode** (Comfort / Normal / Sport) and **One-Pedal**
  (on/off) you used. Mate can't read these from the car — Leapmotor doesn't send them to the cloud — so
  you set them by hand; they help explain why two otherwise similar drives consumed differently.

### Map
**(menu: Map)** — The car's position on a map. It shows the last known position; if the latest data
from the cloud doesn't have a valid GPS fix, Mate **keeps the last valid position** instead of making
the map disappear.

### Charges
**(menu: Charges)** — The list of charges. For each one: **energy added (kWh)**, **peak power**,
**type** and **cost**, with the **effective €/kWh** clearly visible. The type is classified with a
label:

- **Home** (your wallbox), **AC** (public alternating current), **Fast/DC**, **HPC** (ultra-fast
  charging) and **✎ Manual**.
- **✎ Manual**: for public charging points with complicated tariffs (subscriptions, session fees…)
  you can **write in the total you actually paid by hand**; this value overrides the automatic
  estimate.
- Charges that happened while the car was off/offline are **reconstructed** too, from the jump in the
  state of charge.
- **Your note 🆕** (#107) — each charge has a **free-text note** (just above *Delete charge*) for the
  things the numbers don't capture: where the station was, shade/shelter, how reliable it is, parking
  conditions, weather, any personal remark.

### Charge Prices
**(menu: Charge Prices)** — Here you set **how much you pay for energy**, so Mate can calculate the
costs. You can define a price **for each type** of charge (Home, AC, Fast, HPC) and choose between:

- **Fixed rate** (a single €/kWh), or
- **Time-of-use bands (TOU)** — different prices for the day of the week and the time band (e.g.
  F1/F2/F3, cheaper at night).

The **Home** price is the one that feeds the cost of home charges and, in turn, the cost of trips
(calculated on the "average" price of the energy in the battery at the time of the trip).

> Price changes apply **only to future charges**: costs already calculated do not change. With
> time-of-use bands you can also choose *how* to split a session across the bands — *Accurate split*
> (on the real power curve) or *By start time* (the whole session at the band it started in).

### Statistics
**(menu: Statistics)** — Your averages and totals over time: **total distance** and number of trips,
**average distance per trip**, **drive time**, **average consumption** (weighted by distance) and
**best**, **energy used and charged**, total and average **regen**, number of **charge sessions**,
with the related **trends** (efficiency and regen over time). The totals also include a **Total V2L**
card showing the cumulative energy drawn via V2L over all time.

### Monthly Report
**(menu: Monthly Report)** — A summary **month by month**: how much you drove, how much energy you
used and charged, how much you spent. Handy for keeping an eye on the trend.

### Battery health
**(menu: Battery health)** — An **estimate of the state of health (SoH)** of the battery, that is, how
much "real" capacity is left compared to new. Mate calculates it from the real charging data (energy
actually delivered versus the percentage gained), **excluding** cold charges that would distort the
measurement, and shows it over time and/or by mileage. It is an **estimate**, not an official
diagnosis, but it improves as data accumulates.

### Maintenance
**(menu: Maintenance)** — The **maintenance due dates** for your car, based on the **official schedule
for your model** (T03, B05, B10, C10). For each service item (e.g. service, brake fluid, cabin
filter, tyres…) you see two progress bars: one for the **kilometres** and one for the **time**,
because whatever comes first is what's due.

- You can **log a service** ("done today at X km") directly from the page: the next due date is
  recalculated.
- For a **new car** that has no history yet, you can set a **reference date/mileage** so the due dates
  start from delivery ("first service in…") instead of showing up as "never done".
- The **registration / delivery date is now editable**: click the **✏️** next to the saved date to
  correct a mistake (the new value overwrites the old one).
- The distances respect the chosen unit (km or miles).

### Commands
**(menu: Commands)** — The **remote commands**. From here you can:

- **lock/unlock**, open the **trunk**, **find the car** (horn/lights);
- manage the **climate**: cooling, heating, defrost, ventilation, **switch off**;
- activate **seat heating**, **steering wheel** and **mirror heating** (where supported);
- manage the **charge limit**.

The **climate card** now has a **temperature slider, a fan slider and a recirculation toggle** (fresh
air / recirculate). Each climate tile — **A/C AUTO · Cool · Heat · Vent · Defrost** — lights from the
car's **real mode**, with exactly one lit at a time (just like the official app). In the three
**manual** modes (**Cool / Heat / Vent**) you can set target temperature and fan speed: the car stays
in that mode and remembers the value. In **AUTO** the car manages fan and recirculation itself, so
those two controls show the current value but are **read-only** — the temperature stays adjustable.
**Rapid Ventilation** now reliably engages true ventilation (air only, no heat/cool) from any state.

When you send a command, Mate updates the interface immediately in an "optimistic" way and then
confirms on the next read. If the cloud accepts but the car doesn't confirm within a few seconds, you
see an **amber** notice ("sent, it may have worked") — it's not an error: the command often goes
through anyway (it depends on the car's coverage/standby).

### Scheduling
**(menu: Scheduling)** — The car's **schedules**:

- **Scheduled charging** (and the **charge limit**);
- **Scheduled climate** — 5 presets (cool / heat / ventilate / defrost / auto) with a future start
  time; you can create, edit and cancel them.

### Prepare car
**(menu: Prepare car)** — The "**pre-condition your car with one touch**" function: it brings the
cabin to the desired temperature (and the related functions) **right now** or at a **scheduled time**.
You can also turn everything off.

**🆕 Automatic on power-on** — Instead of tapping the button every time, you can let Mate run the
preparation **by itself the moment the car goes Ready** (powered on). Turn on **Automatic on power-on**,
choose once what it should do — climate preset and target temperature, how far to open the windows,
driver/passenger seat **heating or ventilation**, heated steering and mirrors — and save.

You can add an **optional condition on the interior temperature**: run the preparation **only when the
cabin is above** a value (e.g. pre-cool only when it's over 25 °C) **or only when it's below** one (e.g.
pre-heat only when it's under 5 °C). **Leave the condition off and it runs on every power-on**, whatever
the temperature. Two things to know about the condition: it looks at the **interior** temperature (the
car reports no outside temperature), and it's decided **once, at the instant you turn the car on** — so
if the cabin changes later during the drive, it won't fire a second time.

It runs **once per power-on** (it won't repeat while you stay on, or for a later trip in the same
driving session), it ignores brief signal glitches, and it never re-fires just because Mate restarted.

### Navigation
**(menu: Navigation)** — *Send a destination to the car's navigation* and **find nearby charging
stations**. The page has three parts:

- **Destination** — type an **address** (and, if needed, the **city**), press **Search**: the
  destination appears on the map and with **🧭 Send to car** you send it to the on-board navigation.
  *Searching by address requires a geocoding key* (see [Settings → Geocoder](#7-settings)).
- **⚡ Charging stations — "Find charging stations"** — searches for **public charging stations around
  the car** (using its current GPS position). You can set:
  - **Max distance** — 500 m, 1, 2, **5 km** (default) or 10 km;
  - **Results per page** — 25, 50 or 100;
  - **Network / operator** (optional) — to filter a specific provider (e.g. Electra, Ionity, Enel X
    Way, Be Charge, Plenitude, A2A, Atlante, Ewiva, Tesla…).

  The results appear both as **⚡ pins on the map** and in a **list** below, with **name, distance**
  and, where available, **real-time availability** (🟢/🔴 "available now", e.g. on the Italian public
  network). Tap a station in the list to **see it on the map**, and with a click you can **use it as a
  destination** and then send it to the car. If there's nothing within the chosen radius, Mate widens
  it and shows **the nearest ones**.

  > The station search **requires no keys** (it uses open maps + a public charging-station database);
  > the optional keys in *Settings → ⚡ Charging stations* (Open Charge Map, TomTom) enrich it. The car
  > does, however, need a known **GPS position**.
- **Car's current position** — the car's address and a map with its 🚗 pin.

### Vehicle
**(menu: Vehicle)** — The **full status** card for the car: all the sensors available on your model
(charge, range, inside temperature, gear, doors, windows, tyres, locks, charge status…), now also the
**climate detail**: **fan level** (1–7), **air recirculation** (fresh / recirculate) and the **active
climate mode** (AUTO / Cool / Heat / Vent). Mate shows **only what your car actually reports** (some
models don't expose certain data).

### Wallbox
**(menu: Wallbox)** — If you've connected a wallbox (see
[Integrations](#8-the-integrations-in-detail)), here you see its **live** data (power, energy), the
**summary** and the list of **sessions**, and possibly the **controls** (e.g. max current) if your
wallbox exposes them through Home Assistant.

---

## 7. Settings

**(menu: ⚙️ Settings)** — The page is organized into **accordion cards**: you open one at a time. It's
divided into three columns.

**Column 1 — Vehicle and driving**

- **🌍 Language & Currency** — the interface language, the currency for costs, the **units**
  (metric/imperial).
- **Vehicle** — your car's model and VIN. Here you also have the **🔓 Log out** button to link a
  different account: it deletes *only* the saved credentials, **not** your trips/charges nor the
  certificate.
- **Battery** — the **capacity** in kWh used for all calculations; correctable. If Mate has a
  "measured" estimate from your data, it offers it to you.
- **Polling Cadence** — how often Mate reads the status from the cloud, with two sliders: **parked**
  (10 s–5 min, default 30 s) and **driving** (10–60 s, default 10 s). Reading more often does not
  drain the car, but it generates more traffic to the cloud.
- **Charge detection** — the **current threshold** (in amperes) above which Mate considers it "charge
  in progress". Lower it only if you have very slow charges that go undetected.

**Column 2 — Integrations**

- **ABRP** — sending telemetry to A Better Routeplanner (see [§8](#8-the-integrations-in-detail)).
- **Geocoder** — the service that translates addresses ↔ coordinates on the Navigation page (Geoapify
  *recommended*, LocationIQ, TomTom). It requires a free **key** for the chosen service.
- **⚡ Charging stations** — enables the **station names** on charges (📍) and accepts optional keys
  (Open Charge Map, TomTom) to enrich the search. It's **off** by default.
- **Wallbox** — connect your wallbox for **real costs** and any controls (see
  [§8](#8-the-integrations-in-detail)).
- **MQTT → Home Assistant** — publishes the car's data as entities in Home Assistant (see
  [§8](#8-the-integrations-in-detail)).

**Column 3 — Data and maintenance**

- **Database** — the size of the DB and the **GPS retention**: you can keep the GPS points "forever"
  (default) or delete those older than 6/12/18/24 months to save space. *Only positions are pruned*:
  trips, charges and charge curves stay.
- **Export / Backup** — download **trips (CSV)**, **charges (CSV)** and a **database backup**.
- **🩺 Diagnostics** — a snapshot of the system (version, model, counts, last poll, active
  integrations), the ability to **view the logs** (poller/web) and, above all, to **download a
  diagnostics bundle** by ticking the parts you want (info, poller log, web log, **raw signals**). The
  bundle is **already cleaned** of sensitive data: **GPS removed** and VIN/secrets masked, so it's
  safe to attach when you ask for support. There's also a **scan for missed charges** that happened
  while the car was asleep.
- **⚙️ Advanced** — fine parameters for expert users: the minimum threshold to **reconstruct** a
  missed charge, the **vampire-drain** threshold, the kW threshold to distinguish **DC**, and the
  minimum temperature for the **battery-health** calculation. There's a button to **reset to
  defaults**.

> 🆕 When a new feature arrives, its card may show a **NEW** badge until you open it for the first
> time.

---

## 8. The integrations in detail

All the integrations are **optional** and **off** by default. They are configured from **Settings**.

### Wallbox (for the real charging costs)
By connecting your wallbox, Mate uses the **energy actually delivered** (on the alternating-current
side) to calculate the cost of home charges, instead of estimating it from the change in percentage.

Mate reads the wallbox **through Home Assistant**:

1. In *Settings → Wallbox*, turn on **Wallbox present**.
2. **If you use the Home Assistant add-on**, Mate can reach HA on its own: you don't need to enter an
   address or token.
3. **If you use Mate as standalone Docker**, enter the **Home Assistant URL** (e.g.
   `http://192.168.1.10:8123`) and an HA **long-lived access token**, then press **Test**.
4. With the **keywords** you can help Mate recognize the right entities of your wallbox (e.g.
   `wallbox, charger, evse, keba, pulsar`). Some known wallboxes (e.g. V2C Trydan) are recognized
   automatically; the "trap" entities (solar/home) are excluded.
5. Open the entity list to check that Mate has latched onto the right **energy/power** sensors.
6. **"auto home"** option: it automatically assigns the **Home** label to charges made on your
   wallbox.

### ABRP (A Better Routeplanner)
Sends the car's telemetry to ABRP for real-time trip planning.

1. In *Settings → ABRP*, turn on **Enabled**.
2. Paste your ABRP **token** (you'll find it in the "generic"/telemetry settings of your ABRP
   account).
3. Save. The integration's status appears in the card's header.

### MQTT → Home Assistant
Publishes the car's status (charge, range, position, doors, charge status…) as **entities in Home
Assistant**, with **auto-discovery**. You can also **command** the car from the HA entities — including a writable **Charge Limit** number to set the target SoC, a writable **Charge Schedule** text entity that takes a JSON plan for automations (`{"start":"23:00","soc":90}` — every key optional, and anything you omit keeps its current value), a writable **Fan Level** number (1–7) and a writable **Recirculation** switch, plus a **Climate Mode** sensor (AUTO / Cool / Heat / Vent). The published entities also include three read-only V2L ones: **`V2L Active`** (binary sensor), **`V2L Power`** (W) and **`V2L Session Energy`** (Wh).

1. Get an **MQTT broker** ready (usually the *Mosquitto* add-on in Home Assistant).
2. In *Settings → MQTT*, turn on **Enabled** and fill in:
   - **Broker** (e.g. `192.168.1.10` or `core-mosquitto`) and **Port** (default `1883`);
   - the broker's **Username** and **Password**;
   - the topic **Prefix** (default `leapmotor`);
   - options: **Discovery** (recommended), **TLS** and **TLS insecure** if you use self-signed
     certificates.
3. Press **Test connection** to check the connection, then **Save**. Within a few seconds the
   entities appear in Home Assistant.

> For commands via MQTT, the car still requires the PIN: Mate uses it automatically with the saved
> credentials.

---

## 9. Demo mode

**Demo** mode lets you try Mate without a car and without an account: it starts with **a month of
fake but realistic data**. You can activate it in two ways:

- from the first-start wizard, with the **🧪 Try the demo** button;
- or by starting the container with the variable `MATE_DEMO=1`.

In demo: the data is openly fictitious (a **DEMO** badge), the commands are **simulated** (no car is
contacted) and a banner at the top stays visible at all times with the button to **exit**. When you
exit, Mate returns to the normal setup.

---

## 10. Frequently asked questions and troubleshooting

**The car often goes "offline" / I keep seeing "Invalid token".**
Almost always it's because the **same Leapmotor account is being used somewhere else** (the official
app, another integration, a second instance of Mate). Use an **account dedicated only to Mate** and
**change its password**, using it only here (so the other client is kicked out and can't get back in).
See [requirements](#2-before-you-start-the-requirements).

**A command gives a "timeout" / amber notice.**
It's (usually) not a Mate problem. The commands are *real-time* and depend on the **car's
reachability** (coverage, standby). Mate retries and the command often still goes through. The
**"Car responsiveness"** indicator in the Overview gives you an idea of the situation.

**Some trips or km are missing after an offline period.**
When the car was unreachable, some data may not have been recorded. Charges that happened "while
asleep" are usually **reconstructed** from the charge jump; for the lost km it isn't always possible
to recover them. The **missed-charge scan** (Settings → Diagnostics) helps find charges that weren't
recorded.

**I see a strange charge / an absurd cost.**
Mate has protections against impossible values (e.g. wallbox meters that report the lifetime total).
If a public charge has a complicated tariff, use the **✎ Manual** type and enter the total paid.

**The vampire-drain chart is empty.**
You need at least one **long stop** with a measurable drop in charge in the last few days. If the car
is always charging or sleeps while parked, there may not be enough material. Mate also captures the
drop that only "reveals itself" on wake-up.
Another frequent cause is the **vampire-drain threshold** in *Settings → Advanced*: if you raised it
above your car's real drops, the chart draws nothing. Bring it back toward **0.2** (or press
**Reset**) and the windows reappear. From **v1.22.4** the page tells you so explicitly — it still
shows the typical value and a "below your threshold" notice instead of looking empty.

**I have a Leapmotor REEV (hybrid with a range extender).**
It's not supported: the energy calculations would use the BEV battery capacity and come out wrong.
Mate is **only for the 100% electric versions**.

**I'm not in Europe.**
At the moment Mate only works with the **European** Leapmotor cloud. Accounts on servers in other
regions cannot log in.

**How do I make a backup?**
From *Settings → Export/Backup* you download the database (and the CSVs). Keep the DB **together with
its `secret.key`**.

---

## 11. Glossary

- **SoC** (*State of Charge*) — the battery's percentage of charge.
- **SoH** (*State of Health*) — the battery's state of health: capacity remaining compared to new.
- **AC / DC** — alternating current (slow charging, from home/AC stations) / direct current (fast and
  ultra-fast charging).
- **Home / AC / Fast (DC) / HPC / Manual** — the charge types that Mate recognizes or that you can
  assign; "HPC" is very-high-power charging.
- **TOU** (*Time-of-Use*) — a **time-band** tariff (different prices by day/hour).
- **Regen** — energy **recovered** in braking/lift-off and put back into the battery.
- **Vampire drain** — what the car consumes while **completely switched off**, measured from power‑off
  to the next power‑on. It **includes remote heating/cooling done with the car off** (by design — car
  off → it counts as drain). Idle with the car *on* (parked, engine/climate running) is not counted here.
- **Polling** — the periodic reading of the car's status from the cloud (does not drain the car).
- **Wallbox** — your home charging station.
- **Poller / Web** — Mate's two internal components: the *poller* collects the data, the *web* shows
  the interface. For you as a user it's a detail: they work together.
- **VIN** — the car's chassis number; it uniquely identifies your vehicle.
- **Operation PIN** — the account's 4-digit PIN, needed to authorize remote commands.

---

> 📌 **Manual maintenance note.** This document describes version **v1.27.0**. When something visible
> to the user changes (a new page, an option, a flow), update the corresponding section and the
> version line at the top. It's meant as a base for the translations (EN/FR/DE): the structure is
> deliberately the same as the interface.
