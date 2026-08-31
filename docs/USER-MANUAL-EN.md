# LeapMotor Mate — User Manual

> **Mate version:** v3.14.24 · **Language:** English
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

Mate runs the same way in three environments (the interface is identical):

- **As a Home Assistant add-on** — the easiest way if you already have Home Assistant. You add the
  add-on repository, install "LeapMotor Mate" and open it from the HA sidebar (ingress). In this case
  Mate can also read your **wallbox** directly from Home Assistant.
- **As a standalone Docker container** (for example on a NAS) — via `docker-compose`. In this case
  the app is reachable from the browser on **port 4000** (`http://YOUR-SERVER-ADDRESS:4000`).
- **As a desktop application** — [**MateDesktop**](https://github.com/ProtossBlaster/MateDesktop)
  is the same Mate packaged for **macOS and Windows**, for people who run neither Home Assistant nor
  Docker: download it, open it, and you get the same setup wizard. On Windows it is distributed
  **inside a `.zip`** — unpack it first, then run the installer, because a bare `.exe` downloaded
  from the internet has no reputation with SmartScreen yet and gets stopped on the way in.

The step-by-step installation instructions (repository, compose, etc.) are in the project's
**README** and on the **Docker Hub** page. Once it's up and running, the *first sign-in* is the same
for both and is described below.

> 📱 **On your phone.** Mate is not a phone app and cannot be one — it has to poll for years, and a
> phone suspends what runs in the background. But you can put it **on your home screen**: open Mate
> in the phone's browser, then *Share → Add to Home Screen* on iPhone, or *⋮ → Add to Home screen*
> on Android. It gets Mate's own icon and opens full screen, without the address bar and toolbar —
> about 110 px of screen back. It stays a shortcut to the server you run: with that off, it opens
> nothing.

> 🔒 **Backup.** All of Mate's data lives in a persistent folder (`/data`): the database, the
> encryption key for the secrets (`secret.key`) and the certificate. If you make a backup, **save the
> database together with its `secret.key`** — without the key, saved passwords and tokens can no
> longer be read. From the Settings page you can download a database backup at any time.
> If you ever restore a database **without** its key, Mate now says so in the log by name — which
> secrets it cannot read and what to do — instead of failing later as a login error. Trips,
> charges and costs are not encrypted and always come back.


**How Mate updates.** A badge **↑ vX.Y.Z** beside the version, top left, means a newer release is on
GitHub (checked every 6 hours). It is a notice, not a button: what you press depends on how you run
Mate.

- **Home Assistant add-on** — nothing to do by hand. Home Assistant offers the update on the add-on
  itself and pressing it is the whole procedure. If the badge has not appeared yet, *Add-on Store →
  ⋮ → Check for updates*. Your data (`/data`) stays where it is.
- **Docker** — pull the new image and recreate the container:

  ```
  docker pull ghcr.io/protossblaster/leapmotor-mate:latest
  docker compose up -d          # or: docker rm -f <container> && docker run … as before
  ```

  The database lives in the volume, not in the image, so nothing is lost.
  [Watchtower](https://containrrr.dev/watchtower/) can do it for you automatically.
- **MateDesktop** — nothing to download: the app fetches Mate from the repository **every time it
  starts**, so closing and reopening it *is* the update.

**What changed in v3.14.2 🆕**

- **Trips you could join are drawn once.** The "mergeable" view proposed pairs, so a trip sitting
  between two others appeared twice. A run of trips is now one block with a connector between each
  neighbouring pair — the merge itself is unchanged.
- **A stop inside a joined trip is marked on its chart**, shaded and labelled with its length, so it
  no longer looks like the car losing signal.
- **The note of a joined charge describes the whole session**, not just its first piece.
- **The charge ETA and the "scheduled charge target"** now name what they really are: the target of
  the car's charging PLAN, used only while that plan is switched on. The upper limit you drag in the
  car's own app is not something the cloud reports.
- **Settings warns when the charge-detection floor is above what your car actually draws.** A
  threshold set too high does not record "no charges" — it records half of one.
- **The diagnostics bundle downloads from a phone.** It was a page navigation, which a Home
  Assistant webview drops silently; it is a normal download link now.

**In v3.14.3–3.14.4 🆕** — with two cars, a **command now reaches the car you picked, built the way that car's model expects**. Until these releases the session that talks to the cloud stayed on whichever car the account listed first, so lock, trunk, windows, climate and the charge commands went to that one whatever the picker said — and so did the car's picture and the cloud consumption figures. The model was read from that same car, so on an account holding two **different** models the window position and the climate and A/C-off commands were shaped by the wrong car's rules. One car, or two of the same model: nothing changes.

**In v3.14.5 🆕** — two more places were still answering for the whole install rather than for the car you picked: the **consumption figures** kept in cache (look at one car's Statistics, switch inside half an hour, and you were shown the first one's kilowatt-hours) and the **maintenance start of service**, whose delivery date and odometer were shared between cars — and every service interval is counted from those. With one car, nothing changes.

**In v3.14.6 🆕** — the **Security** row is no longer shown on cars that never report it. The C10 does not send that signal at all (measured on two of them, one over seventeen continuous days), and reading its absence as a zero printed *"Inactive"* — which on a security row reads as *your car is not protected*. A car that does report it, such as the B10, is unchanged.

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

- if the European version has **a single battery variant**, Mate sets it on its own — today only the
  T03 (36.0 kWh);
- if there are **several variants** — B10 and B05 (Pro 55.0 kWh / Pro Max 65.0), C10 (RWD 67.0 / AWD
  81.9) — **you choose yours**: the cloud does not say which one is in your car, so Mate cannot know;
- if the detection fails, you can **enter the capacity by hand** (in kWh).

> The capacity shown is the **usable/net** one (the one that really matters for consumption and
> costs) and can always be corrected later, from Settings → Battery.
> Beside it sits the **SoH reference** — the as-new capacity battery health is measured against.
> Mate captures it the first time you save the capacity and then leaves it alone, so that adopting
> a measured (already-aged) figure can never reset your health to ~100 % and hide the ageing. If it
> was captured from the wrong number, health can read above 100 %: correct it in the same place.

> **If Mate's own default has since been disproved 🆕**, Settings → Battery says so on the spot and
> offers the corrected figure with one button — it never rewrites the number behind you. Today this
> is the **C10 RWD**: 69.9 kWh is the nameplate figure, and real charges put the usable pack at 67.0.


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
- **"Never set up" strip 🆕** — an orange strip across the top of every page when a car reached
  Mate on its own, without ever going through the wizard: that happens to a **second car** added to
  an install where the sign-in was already done. Until someone answers for it, that car uses the
  **default battery pack of its model**, which bends its kWh, its price per kWh and its consumption.
  The button opens the wizard, where the pack and the PIN are chosen.


At the bottom of the menu you'll find **⚙️ Settings**, and **🚪 Log out** *only if you have set an
access password* — that one ends the password session, nothing else. It is not there otherwise, and
there is nothing to end.

**To change the car's PIN 🆕** — if you change it on the car, nothing needs unlinking: go to
**Settings → Vehicle**, and under the account address you will find **Operation PIN**. It is typed
twice, with an eye to read it back, and takes effect at once — both for commands from the page and
for the ones arriving from Home Assistant. Asked for by **@alextchao** (#225).

**If two Leapmotors share your account 🆕** — a **car picker** appears in the header, next to the
model badge. It is there only from the second car onwards: with one Leapmotor nothing changes at
all. Pick a car and everything follows it — the Overview, Statistics, trips, charges, the monthly
report, the commands that car allows and its Home Assistant entities. Your choice is remembered. On a phone the picker is inside the ☰ menu, under the heading.

Settings stay shared, because they rarely differ under one roof: prices, currency, time zone, home
location. What belongs to the car stays with the car — its battery capacity, its **operation PIN**, its **A Better Route Planner token**,
whether it is a range-extender, what it can be commanded to do and which sensors it actually has.
Both cars are handled by **one Mate**: one poller, one database, one session against the Leapmotor
cloud, instead of two installs signing each other out.

**To unlink the Leapmotor account** — a different thing entirely — go to **Settings → Vehicle →
🔓 Log out**. That clears the saved credentials and reopens the setup wizard; your certificate,
trips and charges stay (@JoseRMorales, #223, who went looking for the first one and wanted the
second).

Many pages **refresh themselves** roughly every 30 seconds, so the "live" values (status, charge in
progress…) stay fresh without reloading the page.

**Language, currency and units** are changed from *Settings → 🌍 Language & Currency*:

- **Language:** English, Italiano, Français, Deutsch, Polski, Nederlands, Português, Español.
  *(A written manual like this one exists in English, Italian, French, German and Spanish.)*
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
  charging. Beside it, if you have set a **scheduled charge**, the car's own window appears (for
  example **"Charge 01:50 – 12:00"**) — the answer to "the cable is in, so why isn't it charging?".

When the car is **powering an external device through the V2L (vehicle-to-load) adapter**, the
Overview shows a **V2L block** with the **status** (Active / Inactive), the **instantaneous power** in
watts — reported **net of the car's own ~300 W overhead**, so it matches what your device actually
draws — with a 0–3500 W bar, and the **energy drawn this session**. It refreshes about every **10 s**
while a session is running. It is **read-only**: V2L is started **on the car** (gear in Park + a device
connected), not from Mate. It is accurate from about **42 W** upward (the car's own current-sensor
resolution — a tiny ~10 W load stays invisible).

Further down you'll find mini-statistics and a **"Car responsiveness" indicator** (a 🟢/🟡/🔴 dot, ⚪
if there's no data): it summarizes how well the car has responded to the latest commands sent.

**The range at your charge limit, and at 100% 🆕** — under the estimated range Mate shows how far the
car would go **at the limit you actually charge to** (80%, say), with the figure at 100% beside it.
When the car reports no limit below 100 there is a single line, so the same number is never printed
twice.

**The outside temperature, from the weather 🆕** — the Leapmotor cloud sends the cabin temperature but
never the air outside, and neither does the official app. With the switch on, while the car is awake
Mate looks its position up against [Open-Meteo](https://open-meteo.com) — at most once every 20
minutes or 10 km, whichever comes first — and shows the reading next to the cabin one. It is **off by
default**, because the lookup sends the car's position to Open-Meteo: the single opt-in is in
*Settings → trip defaults*. The same reading becomes an **Outside Temp** entity in Home Assistant and
gives each trip its own departure and arrival figure.

#### The three temperatures: cabin, A/C target, battery
Not every Leapmotor sends all three. Mate tells **three different situations** apart, because
confusing them produces absurd numbers:

- **the sensor exists but this update didn't carry it** → the row stays and shows **"—"**;
- **zero is a real reading** (a battery pack genuinely at 0 °C, in winter) → Mate prints **0 °C**,
  because that is the reading that matters most;
- **the car never sends that sensor at all** → the row is **not shown**, and the matching Home
  Assistant entity is **removed**.

The last case is **measured, not inferred from the model**: Mate only says it after roughly half an
hour of updates in which that value never once arrived — so a fresh install shows every row, and if a
sensor starts answering the row (and the entity) **comes back on its own** within a few hours.

If you use the temperature condition in **Prepare vehicle** ("only pre-cool above 25 °C"), an
**unknown** temperature does not fire the preparation, and says so in the log. It used to count as
0 °C, so on a car without a cabin sensor the "below 5 °C" condition was satisfied on **every update,
all year round**.

### Trips
**(menu: Trips)** — The list of your drives, one per drive. For each trip you see **distance,
duration, consumption (kWh/100 km), energy recovered** in braking and the estimated **cost**.

- Clicking a trip opens the **detail**, with the **GPS track** on a map and the data of that single
  trip.
- **A calendar, and a search.** Trips are browsed by **month**; click a day to see just that day's
  drives, or use the **search** with a date range, a distance or an efficiency window to pull out a
  set across the whole history.
- **Merging, from the day you are looking at.** A stop long enough to end a drive can split one
  journey into two rows. Open a day and the **🔗** button beside its date offers that day's joinable
  pairs: a slider widens what counts as one stop, you preview the combined route before committing,
  and it is **reversible** at any time (Unmerge). You can also **delete** a trip.
- Short stops (traffic lights, queues) do **not** split a trip: one drive stays a single row.
- **A trip abandoned by the cloud ends when the car last spoke.** If the link drops while you are
  driving, Mate closes the trip by itself after half an hour — but dates it at the **last real
  news**, not at the moment it noticed. So the duration holds no half hour of silence and the
  average speed stays honest.
- **Kilometres covered while the car was out of contact go into no trip at all.** When the link to
  the cloud drops, the car keeps moving but Mate cannot see it; when the link returns, all it finds
  is an odometer further along. That jump can hold the end of one drive, a stop, and the beginning
  of another, and **nothing says how it divides** — so Mate attributes it to nobody. A line above
  the calendar states that month's kilometres, charge and cost, and the **Statistics** page states
  the running total: *measured, but not attributable to a specific trip — therefore left out of
  distances, consumption and costs.*
  ⚠️ This is why Mate's own total can sit below the car's odometer: the difference is that line.
- **Elevation and outside temperature.** The Leapmotor cloud reports neither, so a few minutes after
  a drive ends Mate looks the trip's GPS track up against [Open-Meteo](https://open-meteo.com) (free,
  no key, no account). The detail then gains an **altitude line under the SoC & speed chart**, the
  metres **climbed and descended**, and the temperature **at departure and on arrival** — not an
  average, so a valley-to-pass climb shows the real drop. Between them they explain a good part of a
  drive's consumption: a climb costs energy, cold costs range. Trips recorded before this existed
  have a **Calculate elevation** button, and the whole thing can be switched off in Settings.
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

- **A searched period adds itself up 🆕** — the date filters could always select any window, but the
  results listed their cards and totalled nothing, so a billing period that is not a calendar month
  had to be added up by hand. Above the results you now get that period's own **trips, km and cost**
  — the same figures, from the same source, as the calendar's month strip.

### Map
**(menu: Map)** — Everywhere you have driven, on one map. The car's current position is there (if the
latest data from the cloud has no valid GPS fix, Mate **keeps the last valid position** rather than
making the map disappear), and with it:

- **Every trip's route**, drawn as a connected line rather than loose dots, never joined across two
  different trips.
- **A dashed magenta bridge where the signal was lost.** A tunnel, a dead zone, a hiccup at the
  cloud — when the gap between two recorded points is much larger than that trip's own sampling
  rhythm, Mate draws the join **dashed** instead of solid. A solid line means *the car really drove
  this*; a dashed one means *we lost it here* and the straight line between the ends is not a road.
- **Frequent places**, as bubbles sized by how often you stop there, and **charging stations** you
  have used.
- **"Trips shown"**, a box on the legend row. A long history leaves the map a solid mass of
  overlapping lines, so you can cap it to the N most recently driven trips; **0 means all of them**,
  which is how it starts. Capping also makes each drawn route hug the real road more closely,
  because the drawing budget is spread over fewer trips.

### Charges
**(menu: Charges)** — The list of charges. For each one: **energy added (kWh)**, **peak power**,
**type** and **cost**, with the **effective €/kWh** clearly visible. The type is classified with a
label:


- **The "to confirm" banner takes you there 🆕** (#240) — when a charge has ended without a type,
  a strip appears at the top of the page. **Click it**: it opens the charge on its own day of the
  calendar and marks it, instead of leaving you to work out which day it is on.
- **When a part of the page can't load 🆕** — several blocks in Mate fill themselves in a moment
  after the page opens. If one of them fails, it now **says so under itself**, with the error and a
  **Try again**, rather than leaving an empty space with no explanation.
- **Home** (your wallbox **or a domestic socket**), **AC** (public alternating current), **Fast/DC**,
  **HPC** (ultra-fast charging) and **✎ Manual**.
- **Home does not mean wallbox.** *Home* is where you charged, not what you charged from — a
  three-pin socket in the garage is a Home charge too. It matters because of what gets billed: with a
  wallbox meter mapped (see *Wallbox* below), the charge is billed on the **energy the meter
  delivered**; without one, it is billed on the **energy that reached the battery**, exactly like a
  public charge. Between the two there is the charger's own loss as heat, typically 10–15 %.
- **✎ Manual**: for public charging points with complicated tariffs (subscriptions, session fees…)
  you can **write in the total you actually paid by hand**; this value overrides the automatic
  estimate.
- **The charger's own kWh 🆕** (#222) — on a public charger Mate **has no meter**: it reads only what
  went into the battery, while the charger bills you for what came out of its own. You can type that
  figure in: on the charge card, under the three tiles, there is a **✎**; the box **opens only if you
  open it** and is **always empty** — so a stray click changes nothing, and pressing OK on an empty
  box leaves everything as it was. *Remove* takes a wrong number back. From then on it **prices the
  charge**, exactly as a wallbox counter does at home, and shows the **efficiency** (how much the
  on-board charger turned into heat). The energy Mate reports stays the one **measured at the
  battery**.
- **What is counted, and what is not 🆕** — a charge appears in these comparisons only when it has
  **both** figures, the meter's and the battery's. A session with only one of them would push the
  ratio above 100 %, which no charger can do. **Charges still in progress are left out**: a session
  that is still arriving has no total to compare yet, and joins the totals when it ends.
- **The month says both 🆕** — above the calendar: *"154.93 kWh delivered · 142.57 in battery"*. The
  first is what came out of the meters (the wallbox, or the kWh you typed); the second is what
  reached the pack. Between them sits the conversion loss you pay for.
- Charges that happened while the car was off/offline are **reconstructed** too, from the jump in the
  state of charge.
- **Your note 🆕** (#107) — each charge has a **free-text note** (just above *Delete charge*) for the
  things the numbers don't capture: where the station was, shade/shelter, how reliable it is, parking
  conditions, weather, any personal remark.
- **The odometer of the charge 🆕** (#237) — every session now carries **what the odometer read when
  it started**. Mate writes it on everything it sees, and recovered it once from the charges already
  in the archive. On a charge **you type in** there is an *Odometer* box: it is the only way a
  session from before Mate existed can carry kilometres at all — nothing from those days can supply
  them. Typed in **your** unit (km or miles).
- **How far the car went between two charges 🆕** (#237) — under the charge: *"🛣 122 km since the
  previous charge"*, from the car's own odometer. It appears only where **both** charges carry a
  reading and only where the car actually moved: two sessions the same afternoon say nothing rather
  than print a zero.
- **Import charges from a spreadsheet (CSV)** — *Import charges from CSV* hands you a
  **self-documenting template**; fill it in with Excel or Numbers and upload it back. Only two
  columns are required, the date and the energy; the rest — cost, AC/DC, start/end percentages, end
  time and the **odometer 🆕** — are optional. The charges **export** can be re-imported as it
  stands. **Re-importing the same file no longer creates duplicates 🆕** (#237): a line matching a
  session already recorded **completes** it (writing the odometer) instead of adding a second copy,
  and Mate tells you how many it added and how many it completed. It used to double everything in
  silence. ⚠️ On a session already recorded **only** the odometer is written: a cost Mate worked out
  from a real charging curve is never overwritten.

- **A searched period adds itself up 🆕** — above the results, that window's own **sessions, kWh
  delivered (with the battery figure beside it) and cost**. Electricity billed from the 22nd to the
  21st, or any other period that is not a calendar month, no longer has to be added up by hand.

### Charge Prices
**(menu: Charge Prices)** — Here you set **how much you pay for energy**, so Mate can calculate the
costs. You can define a price **for each type** of charge (Home, AC, Fast, HPC) and choose between:

- **Fixed rate** (a single €/kWh);
- **Time-of-use bands (TOU)** — different prices for the day of the week and the time band (e.g.
  F1/F2/F3, cheaper at night).
- **Dynamic (Home Assistant sensor) 🆕** — Mate reads the price from an entity that **changes over
  time** (Nordpool, Tibber, your utility's own integration) and weighs it across the session's own
  power curve, so a charge that ran across a price change is billed at what each part of it really
  cost.
- **Custom kWh (Home Assistant) 🆕** — for when the price is fixed but **how much of the charge you
  paid for** is not. With solar on the roof only part of a session comes off the grid, and nothing
  in the car or in the cloud knows the split — a Home Assistant helper does. Pick the entity that
  holds the kWh this charge should be billed for; when the charge ends Mate reads it and multiplies
  it by your fixed price. **The energy Mate reports for the charge does not change** — that stays
  what reached the battery; only the money is worked out from your figure. If the entity is missing
  or says nothing, the charge falls back to the fixed price over the measured kWh.

- **Solar kWh (manual) 🆕** — the same case as above, without Home Assistant. Choose it if you have
  solar and would rather type, charge by charge, how many kWh came off your own roof: Mate subtracts
  them from what the wallbox measured and bills you only the rest. A **☀️ Solar** field appears on
  the charge, under the three tiles, and the line beside it spells the sum out — "20.0 delivered −
  8.0 solar = 12.0 paid" — so a number typed the wrong way round shows itself at once. A figure
  larger than the wallbox measured is refused. It is offered only on home charges the wallbox
  actually measured: without that reading there is nothing to subtract from, and a line says so.
  **The energy Mate reports does not change** — it stays the measured one; your figure only makes
  the cost.

> The last three are for **Home** charges only: a public session is billed by its operator, and a
> helper of yours has no business pricing it.

The **Home** price is the one that feeds the cost of home charges and, in turn, the cost of trips
(calculated on the "average" price of the energy in the battery at the time of the trip).

> Price changes apply **only to future charges**: costs already calculated do not change. With
> time-of-use bands you can also choose *how* to split a session across the bands — *Accurate split*
> (on the real power curve) or *By start time* (the whole session at the band it started in).

> **No ceiling on the price 🆕** — the fields used to refuse anything above `9.99`, which only ever
> suited euro- or dollar-scale tariffs. Iceland, Japan, Korea and Hungary price electricity in tens
> or hundreds of currency units per kWh: type the figure exactly as it is. The **Icelandic króna**
> is in the currency list, and every amount now shows **at least two decimals**, so nothing is
> rounded on screen.

### Statistics
**(menu: Statistics)** — Your averages and totals over time: **distance of recorded trips** 🆕 (it
used to read *total distance*, but it has always been the sum of the finished trips — not the car's
odometer) and number of trips,
**average distance per trip**, **drive time**, **average consumption** (weighted by distance) and
**best**, **energy used and charged**, total and average **regen**, number of **charge sessions**,
with the related **trends** (efficiency and regen over time). The totals also include a **Total V2L**
card showing the cumulative energy drawn via V2L over all time.

**Consumption against outside temperature 🆕** — one dot per finished trip: its consumption against
the air temperature it was driven in, with a dashed trend line through them. It answers the question
every owner asks when the cold arrives — *how much does my car really drink at 5 °C?* — out of your
own driving rather than a table. It needs the trips to carry an outside temperature (see *Overview*),
and on realistic data the pattern is legible after about a month of driving.

**Cost per 100 km 🆕** — what covering 100 km actually costs: **the euros spent**, divided by **the
kilometres driven**. No price per kWh and no estimate — the sum of what you paid over the sum of
what you drove, so it includes the kWh that moved the car nowhere (climate, preconditioning, the
charger's own losses).

**The euros and the kilometres are from the same period 🆕** (#237) — a charge that ended **before**
the first recorded trip has no kilometres of its own to be divided by, and does not enter the
figure. Anyone who had typed in a year of old charges was seeing months of spending divided by one
afternoon's kilometres: the number came out tens of times too high. A charge made **after** the last
trip does keep its money — those kilometres arrive tomorrow.

**And it can divide by the car's own odometer 🆕** (#237) — if your charges carry an odometer (see
*Charges*), Mate measures the distance between the first and the last with the car's own counter
instead of the reconstructed trips: brim to brim, the way fuel has always been measured. **It works
even with no recorded trips at all**, which is the case for anyone who kept a notebook and installs
Mate months later. Mate picks whichever basis prices **more of what you actually spent** and says
which one under the figure — *"over the 18422 km on the car's odometer"* rather than *"over the km
recorded"*. On an ordinary history the trips win and nothing changes. On a range-extender the petrol is added beside the electricity — the petrol **burned**, priced at what the tank cost you, not the whole refuel: a tank you have paid for is mostly still in the tank, and charging it to the kilometres it has not driven yet made the figure several times too high 🆕. If any charge
has no price the card says so, because the real figure is then higher. It follows your units: with
miles it becomes "per 100 mi".

Beside the money the card now also shows **how many kWh those 100 km took**, labelled *"including
standing time" 🆕*. It is worked out as a balance, not as a sum of journeys: the energy charged
inside the window, minus whatever was still in the battery at the end that was not there at the
start. So it covers everything that left the pack — driving, climate, preconditioning, the
charger's own losses — which is why it is **higher than the consumption in the Trips header**, and
why the label says so. If a charge in that window has no energy figure the card says that too: the
number is then a floor, not the whole story.

**How far back these numbers go 🆕** — a line at the top of the page is a reminder that **every**
total on Statistics is what Mate has recorded since it was installed, with the date it starts from,
and **not** the car's own odometer total.

**What each figure covers 🆕** — *Avg consumption* is the mean over the kilometres that **have** a
consumption figure, and *"over 452 km of 509 km"* appears underneath when those are fewer than the total — it names both
numbers, so you can see at a glance whether the figure covers most of the window or a corner of it.
*Energy used* adds up only the trips whose energy Mate knows: a trip without one is **left out**
rather than counted as zero, and the tile says how many trips it speaks for. On a car where every
trip carries its own consumption — which is nearly always — none of this shows at all.

### Monthly Report
**(menu: Monthly Report)** — A summary **month by month**: how much you drove, how much energy you
used and charged, how much you spent. Handy for keeping an eye on the trend. It also carries the
**official consumption** cards (Today / This week / This month) from the cloud.

It always opens on the **month you are in**, even on the 1st with nothing driven yet — an empty
month says so rather than quietly showing you the previous one, and a month with nothing in it
shows no comparison against the one before (every figure would read −100 %, which describes the
calendar and not your driving).

**Where the consumption figure comes from, and when Mate overrules it.** *Average consumption* and
*Energy used* normally come from the car's own official total for the month. That total is only as
complete as your car's connection was: if the car couldn't reach the cloud during a drive, that
whole drive is missing from it. When the total comes back far below what Mate's own trips add up to
for the same month, Mate shows **its own figure instead** — the same one the Trips page shows — and
says so under the tile. The Guida / A·C / Altro split stays the car's own, with a line saying it
covers only the part that reached the cloud.

### Battery health
**(menu: Battery health)** — An **estimate of the state of health (SoH)** of the battery: how much
usable capacity is left compared to new. For each charge Mate divides the energy it **measured**
going into the pack (voltage × current, integrated over the session) by the percentage that charge
added. That ratio is an estimate of the whole pack's capacity, and its trend over time — or over
mileage, your choice — is what ageing looks like.

Three things about how it is worked out, because they change what the number means.


- **A quiet stretch no longer ages the battery 🆕** (#241) — capacity is measured as energy against
  the SoC that rose. Where the car stops reporting for more than a quarter of an hour, that energy
  is deliberately not counted (nobody knows what the charger did meanwhile), and **the SoC of the
  same stretch is now left out too**. Before, a charge with an hour of silence in it could read
  81 % where the pack was at 100 %.
- **Nothing changes on a normal connection.** Where your car reports as usual the figures are
  identical to a tenth; only charges that had real gaps in them move — upwards, to where they
  belonged.
- **It stops at 95 %.** On an LFP pack the voltage barely changes across the middle of the range, so
  the BMS **counts** charge instead of reading it, and drifts; near the top the curve finally rises
  and the BMS **re-anchors** — adding percentage points that no energy paid for. Counting those
  points would make the pack look smaller, and worst of all on a short top-up to 100 %, where they
  are most of the rise. So the arithmetic stops at 95 %: the charge itself still counts, only its
  last stretch is left out.
- **Bigger charges count more, in proportion.** The headline pools the energy and the percentage
  across recent charges rather than averaging them one for one, so a charge that spanned 50 points
  carries about four times the weight of one that spanned 13. Nothing is discarded to achieve it.
- **Cold charges are shown but excluded** — an LFP reads low when it is cold — as are charges that
  started nearly empty or that show the BMS jumping.

**The figure comes with a ± , and that is the honest part.** It is the **scatter** of the charges
behind it, not an accuracy: the energy is measured, but the percentage it is divided by is a number
the BMS counted, and that number drifts. A narrow band means your charges agree with each other, not
that the pack is certainly that size. With a single charge no ± is shown at all, because one
measurement has no spread to report.

It is an **estimate**, then — not a laboratory diagnosis — and it settles as charges accumulate.

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

When your car is **not plugged in**, the card says so by name — *"C10 not connected"* — because a
wallbox can be charging somebody else's car and those live figures would not be yours. The cost tile
reads **Last home charge**: a charge is priced only once it ends, so that figure is never the session
in progress.

> In Mate "home" means **wallbox or domestic socket**, so a charge can carry that badge without your
> wallbox being involved at all.


---

## 7. Settings

**(menu: ⚙️ Settings)** — The page is organized into **accordion cards**: you open one at a time. It's
divided into three columns.

**Column 1 — Vehicle and driving**

- **🌍 Language & Currency** — the interface language, the currency for costs, the **units**
  (metric/imperial).
- **Vehicle** — your car's model, its VIN, and **which Leapmotor account this instance signs in
  with**. The account matters if you run Mate more than once — a second instance, a test one, one
  per car: model and VIN describe the *car*, so two instances watching the same car used to look
  identical from the inside. Here you also have the **🔓 Log out** button to link a different
  account: it deletes *only* the saved credentials, **not** your trips/charges nor the
  certificate.
- **Battery** — the **capacity** in kWh used for all calculations; correctable. If Mate has a
  "measured" estimate from your data, it offers it to you.
- **Polling Cadence** — how often Mate reads the status from the cloud, with two sliders: **parked**
  (10 s–5 min, default 30 s) and **driving** (10–60 s, default 10 s). Reading more often does not
  drain the car, but it generates more traffic to the cloud.
- **Charge detection** — the **current threshold** (in amperes) above which Mate considers it "charge
  in progress". Lower it only if you have very slow charges that go undetected.

- **Always charging at home 🆕** — with no wallbox and no Home Assistant there is nothing to tell Mate
  where a charge happened, so every session is born unclassified and has to be tagged by hand: a lot
  of identical clicks for someone who only ever charges at home, several short top-ups a day. With
  this on, a new charge is born **Home** and can still be changed afterwards for the rare public one.
  The **type** works forward only — charges from before you turned it on stay unclassified, exactly
  as they are — and turning it on asks for an explicit confirmation, so it can never happen by
  accident.
- **And priced, not only labelled 🆕** — a charge born **Home** used to arrive with the green badge
  and no cost, because the pricing engine only ever ran on a *confirmation*, by hand or from the
  wallbox. Being born already confirmed, it went through neither. It is now priced exactly as if you
  pressed its badge yourself — time-of-use bands read the hour of the charge, not the hour of now —
  and the ones already sitting there without a price are filled in too. A cost you typed is never
  overwritten, and a charge you marked free stays free.

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

- **🔐 Access** *(standalone Docker only — under the Home Assistant add-on, ingress already
  authenticates every request and the card isn't shown)* — a password to open Mate. Worth setting:
  without one, anything on your network can open Mate, and Mate can unlock your car.

  You type it **twice**, because there is nowhere to read it back afterwards — it's stored as a
  salted hash, never in clear text. **If you lose it**, you are not locked out for good: the *New
  password* box doesn't ask for the old one, so from any device still signed in you can simply set
  a new one. If no device is signed in any more, the `MATE_AUTH_PASSWORD` environment variable
  overrides whatever is stored.

- **Database** — the size of the DB and the **GPS retention**: you can keep the GPS points "forever"
  (default) or delete those older than 6/12/18/24 months to save space. *Only positions are pruned*:
  trips, charges and charge curves stay.
- **Export / Backup** — download **trips (CSV)**, **charges (CSV)** and a **database backup**. The
  backup arrives **gzip-compressed** (`leapmotor_mate.db.gz`) 🆕, streamed in pieces so even a large
  database never has to fit in memory whole. Restore takes **both** the compressed file and a plain
  `.db` saved before this change, so nothing you already have stops working — and a smaller file is
  easier to keep or to sync wherever you back things up.
- **🩺 Diagnostics** — a snapshot of the system (version, model, counts, last poll, active
  integrations), the ability to **view the logs** (poller/web) and, above all, to **download a
  diagnostics bundle** by ticking the parts you want (info, poller log, web log, **raw signals**). The
  bundle is **already cleaned** of sensitive data: **GPS removed** and VIN/secrets masked, so it's
  safe to attach when you ask for support. The integrations line reports the **wallbox switch** and
  **Home Assistant** separately: the first says whether you have the feature ticked, the second only
  whether Mate can reach HA. There's also a **scan for missed charges** that happened while the car
  was asleep.

  🆕 **The sliders that change how Mate behaves now need a Save press.** Poll cadence, charge
  detection, the advanced thresholds: they used to save the instant you let go of the slider, so a
  finger dragging across one while scrolling a phone changed it without asking. The slider still
  moves freely; nothing is written until you press Save. **And every such change is recorded** —
  when, from what, to what — and shown in the bundle, so "it changed by itself" can be checked.

  🆕 The bundle now also carries **the rows themselves** — the charges and the trips of the last
  fortnight, straight from the database — and a section that lists **every time the battery filled
  up while parked** together with what Mate could see at that moment: whether the cable declared
  itself, whether Mate concluded it was charging, the current, and whether the data was arriving
  fresh or the cloud was repeating an old reading. None of it is new information about you: it is
  what Mate already recorded, finally written where support can read it. Still no positions.
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
Assistant**, with **auto-discovery**. You can also **command** the car from the HA entities — including a writable **Charge Limit** number to set the target SoC, a writable **Charge Schedule** text entity that takes a JSON plan for automations (`{"start":"23:00","soc":90}` — every key optional, and anything you omit keeps its current value), a writable **Fan Level** number (1–7) and a writable **Recirculation** switch, plus a **Climate Mode** sensor (AUTO / Cool / Heat / Vent). The published entities also include three read-only V2L ones: **`V2L Active`** (binary sensor), **`V2L Power`** (W) and **`V2L Session Energy`** (Wh), and a **`Ready`** binary sensor that turns on the moment the car is powered up — before it moves, which is when an automation still has time to act.

Entities **your** car doesn't support aren't left on your hands: the ones the model lacks (heated seats,
steering wheel…) are never created, and a **temperature entity** whose sensor the car has never reported
is **removed** — not left on `unknown` for ever. The removal arrives when the evidence does (about half
an hour of updates), with no restart needed, and if the sensor starts answering the entity **comes back**.

Two more entities arrived recently 🆕: **Climate Power**, the watts the climate system is drawing
(so an automation can see the cabin being heated or cooled), and **Outside Temp**, the air
temperature from the weather — the latter only while that switch is on (see *Overview*).

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

**If you run more than one Mate against the same broker 🆕** — the normal add-on and the BetaTester
one, say — give each a **different Topic prefix** (*Settings → MQTT*). On the same prefix, watching
the same car, they are **one device** to Home Assistant: the second appears not to work, and worse,
**every command is executed twice**. Mate now notices and says so; the BetaTester build moves itself
to a prefix of its own, the normal one never moves.

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
The opposite case is covered too: if the wallbox meter **stops** mid-charge while the car goes on
drawing power, Mate stops trusting its total for that session and bills on the energy that reached
the battery instead — the meter's figure would be short by whatever it missed while frozen.
If a public charge has a complicated tariff, use the **✎ Manual** type and enter the total paid.

**The vampire-drain chart is empty.**
You need at least one **long stop** with a measurable drop in charge in the last few days. If the car
is always charging or sleeps while parked, there may not be enough material. Mate also captures the
drop that only "reveals itself" on wake-up.
Another frequent cause is the **vampire-drain threshold** in *Settings → Advanced*: if you raised it
above your car's real drops, the chart draws nothing. Bring it back toward **0.2** (or press
**Reset**) and the windows reappear. From **v1.22.4** the page tells you so explicitly — it still
shows the typical value and a "below your threshold" notice instead of looking empty.
From **v3.10.5** the chart is also followed by **the most recent discarded stop**, with its length,
its drop and the reason — so a chart that has not grown for days no longer reads as broken. Most
often the reason is that the car lost **0.1%**, one single step of its charge sensor: below that a
drop cannot be told apart from noise, and Mate would rather draw nothing than a number it invented.

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

> 📌 **Manual maintenance note.** This document describes version **v3.11.0**. When something visible
> to the user changes (a new page, an option, a flow), update the corresponding section and the
> version line at the top. It's meant as a base for the translations (EN/FR/DE): the structure is
> deliberately the same as the interface.
