# Changelog

All notable changes to LeapMotor Mate are documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **ABRP: automatic battery preconditioning before DC fast charging.** When travelling with an
  active ABRP plan, Mate now monitors the next waypoint every 60 s and sends the `battery_preheat`
  command when a DC fast charger is within the configured distance *and* the battery temperature is
  below the configured threshold. A 10-minute debounce prevents repeated commands on the same stop.
  Configurable in *Settings → ABRP*: enable the feature, set the trigger distance (km) and the
  maximum battery temperature (°C).
- **ABRP preconditioning confirmation via BMS signal 1186** (`batteryThermalRequest`). After
  sending the command, Mate watches the BMS thermal state for up to 3 minutes and publishes one of
  three MQTT events on `<prefix>/<vin>/event`:
  - `battery_preheat` — command sent (payload: `batt_temp_c`, `dist_km`, `charger`).
  - `battery_preheat_confirmed` — BMS confirmed active heating (signal 1186 = 2).
  - `battery_preheat_not_needed` — BMS did not start heating (signal 1 = already cooling;
    or no response after 3 min), with a `reason` field (`bms_cooling` / `no_bms_response`).
- **New MQTT sensor `battery_thermal_request`** (signal 1186): exposes the BMS thermal management
  state as a Home Assistant entity (0 = none, 1 = cooling, 2 = heating/preconditioning).
- **Docs**: all four language manuals now document the preconditioning feature with a full
  Home Assistant automation example for mobile push notifications.

## [1.25.2] — 2026-06-19

### Fixed
- **Running the standalone Docker image no longer dead-ends — the UI is reachable, data persists, and "Try the demo" works without a restart policy.** Three rough edges that hit anyone running the published image directly (especially via Docker Desktop's **Run** button):
  - The image now declares **`EXPOSE 4000`**, so Docker Desktop's Run dialog pre-fills the port mapping instead of showing *"No ports exposed in this image"* (which left the UI unreachable).
  - It now declares **`VOLUME /data`**, so trips/charges/login persist in an anonymous volume even if you forget `-v ...:/data`, instead of vanishing with the container.
  - The data directory is now **created explicitly** at startup (it used to be created only as a side effect), with a clear error if `/data` isn't writable — no more cryptic *"unable to open database file"* crash on first boot.
- **In-app relaunches ("Try the demo" / "Exit demo" and the account switch) no longer need a container restart policy.** They used to rely on the orchestrator recreating the container; a plain `docker run` / Docker Desktop "Run" sets no restart policy, so pressing **"Try the demo"** stopped the container and looked like a crash. The entrypoint now relaunches Mate **in-process**, so the toggles work everywhere. (Home Assistant add-on and policy-managed setups are unaffected.)

### Changed
- Removed an unused leftover translation key (`cmd_wait_next`) in all four languages.

## [1.25.1] — 2026-06-18

### Changed
- **Command feedback now shows only the car's REAL state — clearer and consistent across the Overview and the Commands page.** Pressing a control no longer optimistically guesses the outcome: the tile you pressed shows a **"⏳ command in progress"** message until the car actually confirms the new state, every control is briefly disabled while a command completes so you can't fire a second one before the first is accepted (no more "not sent — retry in Ns" surprises), and the Commands grid now refreshes **live** so what you see always tracks the car. The same mechanism covers the Overview hero quick-commands and the whole Commands grid — vehicle (lock / trunk / windows / sunshade), climate, comfort toggles (steering & mirror heat) and the seat-level sliders — in EN/IT/FR/DE.

### Fixed
- **B10: a non-binary window status flag (e.g. `2`) is read as OPEN.** Confirmed live against the official app on a B10 — `2` is a genuinely open window. This reverts the 1.24.1 reading (which treated `2` as closed, from what turned out to be a stale cloud frame); transient/stale frames are now handled by the real-state polling above rather than by re-interpreting the flag.
- **Web log lines — and the Diagnostics bundle — are no longer doubled.** `uvicorn.run("main:app")` re-imports the app module a second time in the same process, so the rotating-file-log setup ran twice and attached two handlers to the same file: every web line was written twice, doubling both the log and the diagnostics export. The setup is now idempotent (the handler is added once); the poller was never affected. (Spotted in @riri19's diagnostics export.)

## [1.25.0] — 2026-06-18

### Added
- **Home Assistant: a single "Trunk" toggle ([#71](https://github.com/ProtossBlaster/leapmotor-mate/issues/71), thanks @wlighter).** A new `switch` entity that shows the trunk's open/closed state **and** opens or closes it from one control — the trunk analog of the existing "Door Lock Toggle" (ON = open, OFF = closed). It reuses the `trunk_open` state and routes to the existing open/close commands; the separate Open/Close Trunk buttons stay for anyone already using them in automations.

### Fixed
- **Remote commands are no longer fired twice (and the session is no longer needlessly re-logged in) when the car doesn't confirm in time.** When a command timed out waiting for the car to acknowledge — the cloud accepted it (HTTP 200) but the car, parked and asleep, never confirmed — Mate misread the "timed out" message as a *network* error, reset the session (forcing a re-login) and **sent the command at the car a second time**. A car-confirm timeout is now recognised as exactly that: logged best-effort, with no reset, no re-login and no resend. Genuine connection errors still reset and retry as before. (Surfaced from @riri19's logs, [#73](https://github.com/ProtossBlaster/leapmotor-mate/issues/73).)
- **Battery health: charges distorted by a BMS SoC recalibration or by active cabin use are now excluded from the capacity/SoH figure ([#72](https://github.com/ProtossBlaster/leapmotor-mate/pull/72), thanks @hubcasale).** Two cases produced artificially low capacity estimates: (1) the BMS occasionally snaps SoC upward without matching energy, inflating ΔSoC (detected on AC charges as a SoC rise > 0.8 %/min, ≈ 2× the physical AC ceiling); (2) the cabin A/C or heater running while plugged in feeds part of the charger energy to car loads instead of the battery (detected via climate cooling/heating during the session). Both are now excluded from the headline figure but still shown on the chart in amber for context, with per-reason labels and an explanatory note. The vampire-drain headline likewise excludes windows where the car was in active use (drain rate > 15 %/day). Four new strings in EN/IT/FR/DE.

## [1.24.2] — 2026-06-18

### Fixed
- **Home Assistant: "A/C Off" over MQTT now actually turns the climate off after a Quick Cool/Heat ([#67](https://github.com/ProtossBlaster/leapmotor-mate/issues/67), thanks @Gr1m214).** The MQTT "A/C Off" button is guarded so that pressing it when the climate is already off is a harmless no-op. That guard read a reference (`last_climate_on`) that was only ever updated by a *poll* — the optimistic state published right after a command didn't update it. So immediately after a Quick Cool/Heat (before the next poll caught up) the reference still said "off", and the following "A/C Off" was **silently skipped**. The guard now stays in sync with the optimistic state, so "A/C Off" fires as expected. The web "A/C Off" button was never affected (it sends the command directly, without that guard). Verified end-to-end on a B10: Quick Cool → A/C on → A/C Off → A/C off. _Note: `ac_switch operate=off` is confirmed to fully switch the A/C off on the B10; on other models the cloud may accept but ignore it — a separate, model-level limit._

## [1.24.1] — 2026-06-17

### Fixed
- **B10: windows no longer shown open when they are shut ([#68](https://github.com/ProtossBlaster/leapmotor-mate/issues/68), thanks @riri19).** Some B10 firmware reports the window status flag with a non-binary value (e.g. `2`) on a *fully closed* window, while the actual opening position reads 0%. Mate treated any non-zero flag as "open", so the Overview tile, the Commands grid and the new live car image all showed a window — and the "windows open" count — open when it wasn't, and a *close windows* command couldn't self-confirm (it timed out and reverted). Mate now trusts the opening position when the car reports it (0% = shut) and treats only the canonical flag value as open, so a closed window reads closed everywhere. The T03 (position-driven) and the genuinely-open cases are unchanged.

## [1.24.0] — 2026-06-17

### Added
- **The Overview car picture is now live.** Instead of a single static render, Mate composes the car image from the per-vehicle layer package it already downloads — so it reflects the real state: the **charge cable** (animated while charging), the **four doors**, the **two near-side windows**, and the **tailgate**. It updates the moment the state changes (and right after a command), and falls back to the static render if anything is unavailable. The interactive **demo** shows it too (charging animation out of the box). The model and colour come from the car's own package, so it works for any model.

### Changed
- **Unified colour system across Overview, Commands and Vehicle ([#66](https://github.com/ProtossBlaster/leapmotor-mate/issues/66), thanks @riri19).** One consistent meaning for every colour so the eye can read state at a glance: **green = safe** (locked, closed), **amber = attention** (windows open), **red = alert** (trunk open, unlocked); **blue = cold** and **orange = hot** for climate and comfort; **teal** for non-critical/brand (sunroof shade, charts); and a **neutral grey** for any control at rest (no more "rainbow" of per-button colours). The Overview gained a **"trunk open" chip**, and the battery standby chart now uses the brand colour. State is shown by which control is highlighted plus the status chips — the action buttons stay neutral.

## [1.23.1] — 2026-06-17

### Fixed
- **T03: the Overview tile and the Commands page now show the windows open (with the count) — not just the Vehicle page.** The window open/closed state was computed in several places, but only the Vehicle page had the 1.22.6 T03 fix. The Overview "windows" chip (and its open-count badge), the Commands grid and the post-command verification still read only the open/closed flags — which the T03 leaves at 0 even when the windows are open — so they kept showing "closed", and "open windows" never self-confirmed (it timed out after ~30s and reverted the optimistic state). All four surfaces now share a single position-aware reader, so they agree and the **"windows open" count** is correct on the T03 too. The B10 is unaffected: it reports no window position at all, so it stays flag-driven exactly as before. (#62)
- **Overview hero card: quick-command feedback is now immediate.** Pressing a quick command (lock/unlock, trunk, windows) on the car card only updated the visible state on the next 30-second auto-refresh, so it looked like nothing had happened and you couldn't tell whether the command had worked. The card now refreshes right after a command — an instant flip from the optimistic state, then it reconciles with the car as the cloud catches up (the same behaviour the Commands page already had). The window-open count flips with it.

## [1.23.0] — 2026-06-17

### Added
- **Window position control + per-window opening %** ([#62](https://github.com/ProtossBlaster/leapmotor-mate/issues/62)). The Commands page now has a position slider for the windows, next to the quick "vent" button. It snaps to the stops each model actually actuates — the **B10** opens to 4 discrete positions (closed / ~20% vent / ~50% / fully open; it uses a 0–10 scale and ignores everything else, confirmed on-car), while the **T03** is continuous 0–100%. Opening asks for confirmation like the buttons, the slider reflects the last commanded position, and it triggers a fast status refresh so the change shows immediately.
- **The Vehicle page now shows each window's opening %.** The real value on cars that report it (T03), and the last commanded position on cars whose window-position sensor is dead (B10) — shown only for windows the open/closed flag confirms open, so a closed window never shows a stale number.

## [1.22.6] — 2026-06-16

### Fixed
- **T03: the window open/closed status now reflects reality, and "open windows" works.** On the T03 the Vehicle page always showed the windows as "closed" — it read only the open/closed flags, which the T03 doesn't drive (it reports the live window *position* instead) — and the "open windows" button did nothing, because the command value the T03 needs differs from the B10's. Mate now also reads the window position where it's a reliable signal for the car (gated per-car via the capability profile, so the B10's dead position-sensor can't produce false "open" readings), and sends the model-appropriate open value. The B10 is unaffected (same command, same behaviour as before). (#62)

## [1.22.5] — 2026-06-16

### Fixed
- **No more spurious "Could not find the TLS certificate file" errors and unnecessary re-logins.** The per-login account TLS certificate can be cleaned up mid-session — most visibly on a car with weak mobile coverage that fails many polls — which used to surface as an alarming error and force a full re-login. Mate now re-creates the certificate in place from the copy it already keeps (before each status poll and each remote command), so the session keeps working without a re-login (#64). Note: commands to a parked car on a weak signal or in deep sleep can still time out — that's the car not answering, not a Mate error, and the **Car responsiveness** indicator on the Overview reflects it.

## [1.22.4] — 2026-06-16

### Fixed
- **The battery-standby (vampire-drain) chart no longer looks empty when your display threshold hides every window.** The Advanced "minimum drop" setting is a *display* threshold — raising it above your car's actual standby losses used to blank the whole section with a "no measurable drain" message, making it look as if the history had been lost (#63). Now the typical-rate headline always shows while measurable parked windows exist; the chart adds a "+N below your threshold" note when it hides smaller windows; and when every window is below the threshold the page says so explicitly and points you to lower the slider in **Settings → Advanced**, instead of looking empty.

### Changed
- **Diagnostics bundle now reports the vampire-drain display threshold and reproduces the page.** The computed section uses the same `vampire_min_drop_pct` the battery page does (so a high threshold shows `count=0` here too, with the measurable-window count revealing the real cause), and the header prints the threshold value.

## [1.22.3] — 2026-06-16

### Changed
- **Diagnostics bundle now shows the computed battery-standby (vampire-drain) result and a 14-day SoC-by-day summary.** When someone reports an empty or missing battery-drain chart, the downloaded bundle now includes exactly what Mate computes — the window count, the time-weighted rate and each detected window — plus, for the last 14 days, the daily battery-% high→low and the km driven. That makes the actual cause (sparse data, no qualifying parked period, gaps, etc.) visible at a glance instead of guessing from a screenshot.

## [1.22.2] — 2026-06-16

### Fixed
- **Vampire-drain chart now captures standby loss that only becomes visible when the car wakes.** While a car is in deep sleep it stops reporting and the cloud serves a *frozen* battery %, so a slow standby loss stays invisible — and if you drove off right after waking, that drop fell in the gap between the last parked reading and the trip's start, and was never counted as standby drain (so the chart could look empty). Mate now closes the parked window at the fresh battery % from the wake-into-drive reading, attributing the loss correctly. Charging wake-ups are left out (the pre-charge gap is ambiguous), and the existing reliability flag still marks short/high-rate windows as estimates.

## [1.22.1] — 2026-06-16

### Fixed
- **The Overview map no longer disappears when the car reports no GPS fix.** Parked or in standby, the car can answer a poll with no position — coordinates come through as `0, 0` — and the Overview's "Last position" map was then hidden until the next valid fix. Mate now falls back to the **last known valid position**, so the map keeps showing where the car was last seen (this also gives the Navigation page a sensible starting point instead of a default).

## [1.22.0] — 2026-06-16

### Added
- **Maintenance page.** A new section that tracks your car's factory service schedule, taken from the official owner's manual for your exact model (T03, B05, B10, C10) — never another model's, so each car shows only its own programme. Mate computes what's **overdue / due soon / up to date** from the live odometer and the time since delivery, with a distance bar and a time bar (whichever comes first wins). For a brand‑new car with no history it shows the **first‑service due** from your delivery date; you log each service (date + km) as it's done and the schedule rolls forward. Distances follow your unit setting (km or miles) and the whole page is translated (EN/IT/FR/DE). *(B05 inherits the validated B10 schedule provisionally until its own manual is published; range‑extender/REEV models are out of scope — Mate is BEV‑only.)*

### Changed
- **Diagnostics bundle is richer and easier to share.** "Download diagnostics" now also bundles the car's **raw signals** — with your GPS coordinates stripped — so reporting a model‑specific issue no longer needs any copy‑paste: one click attaches everything, and it stays safe to share publicly. The bundle header also shows the **position‑data span and retention setting**, which makes "my history looks empty" reports (e.g. an empty battery‑health or vampire‑drain chart) diagnosable at a glance. The separate on‑screen "raw signals" view — which showed your live GPS — was removed in favour of this redacted download.

## [1.21.7] — 2026-06-16

### Fixed
- **Front-seat heat/vent tiles are labelled "Driver / Passenger" again, not "Left / Right".** The per-seat comfort *command* is role-based — Mate sends `driver`/`co-driver` and the car maps the role to the physical side for its market — so the fixed "Left/Right" labels introduced in v1.14.0 were inverted on right-hand-drive (UK) cars: turning on "Right seat" actuated the seat the official app shows on the **left** ([#61](https://github.com/ProtossBlaster/leapmotor-mate/issues/61)). Restored the role labels across the Commands tiles, the one-touch Prepare-car screen and the MQTT entities (object_ids unchanged → no Home Assistant churn), so Mate now matches the official app on both LHD and RHD. **Doors, windows, tyres and mirrors are unaffected** — those are physical-position signals (e.g. doors come from the left/right body-control modules) and correctly stay Left/Right.

## [1.21.6] — 2026-06-15

### Added
- **"Car responsiveness" indicator on the Overview.** A small coloured dot next to the status card shows how reliably your car has been answering remote commands lately — a proxy for the mobile coverage where it's parked (🟢 good · 🟡 patchy · 🔴 poor · ⚪ no data yet). It's built from the outcome of your **last 24 commands** — only "car confirmed in time" vs "car didn't confirm" count, since a cloud/network or PIN error isn't the car's fault — and recovers to green within a handful of good commands. A poll only reads the cloud's *cached* state, so a command is the one moment Mate reaches the car in real time, which is exactly what this measures. Hover for details.

### Changed
- **Clearer message when a command "times out".** When you send a command and the Leapmotor cloud accepts it ("request successful") but the car doesn't confirm in time — typically weak cellular coverage or the car in deep standby — Mate now shows an **amber "sent — the car didn't confirm in time (it may still have worked); try again shortly"** notice instead of a red error. The command often *did* apply: it's the car's reachability, not a Mate fault. Genuine cloud-unreachable or auth/PIN errors still show clearly in red.

## [1.21.5] — 2026-06-15

### Fixed
- **No more negative "best efficiency" in Statistics.** A trip recorded with the SoC *rising* (e.g. a trip window mis‑bounded across a charge during an offline gap) produced a negative efficiency, which surfaced as a nonsensical "best efficiency" like −39 kWh/100km. Mate now never stores or displays a negative efficiency (the trip‑finalize/repair paths withhold it, and a one‑time cleanup nulls any already recorded), so the best/average figures stay real.

### Changed
- **Clearer "dedicated account" guidance.** The Leapmotor account Mate uses must be **exclusive to Mate** — never signed into another app, add‑on, Docker container or integration at the same time. Leapmotor allows only ~one active session per account, so concurrent clients evict each other's session, the car goes offline, and you get **missing or inconsistent data**. This is now spelled out in the setup wizard, the README and the Docker Hub page.

## [1.21.4] — 2026-06-15

### Fixed
- **No more phantom "charged from 0%" charges (and false "Recover missed charges" results).** Once in a
  while the car answers a poll without its battery‑% (SoC) field — often a read perturbed by a command
  you just sent (e.g. changing the charge limit). Mate read that missing value as **0%**, so both the
  live detection and the **Recover missed charges** scan could invent a charge "from 0% to your current
  level" (tens of kWh that never happened). Three‑layer fix: a poll with no usable SoC (or SoC 0% while
  the car still reports range) is now treated as *no live data* and skipped at the source; a
  reconstructed or scanned charge whose implied power is physically impossible (a full pack "charged" in
  seconds) is rejected; and a one‑time cleanup removes any phantom charges already recorded plus the
  bogus 0% data points behind them. Real charges that happened while the car was asleep are still
  reconstructed as before.

## [1.21.3] — 2026-06-14

### Fixed
- **Overview charge ETA now shows the real charge limit, not a hardcoded 100%.** While charging, the
  hero card read e.g. *"3h 00m al 100%"* even when the car's charge limit was set to 90% — the target
  percentage was a fixed string. It now shows the **actual configured limit** the car reports
  (*"… al 90%"*, the same value as the Charges page "Charge Limit"). The car already reports both the
  remaining time and the SoC it will stop at — only the label was wrong. The limit is captured by the
  poller from each status read (free — it's in the same response; updates even if you change it from the
  official app) and persisted, and is mirrored immediately when you set it from Mate. Localised IT/EN/FR/DE.
- **Charging animation no longer overlaps the car image.** The plug → flow → battery animation was
  absolutely positioned over the car picture, so on every screen the icons sat on top of the vehicle
  (wheels/body), and in the narrow 3‑column layout (~1024px, e.g. a slim Home Assistant panel) the
  status pill wrapped and split *"al 90%"* across two lines. The animation now sits **below** the car in
  normal flow — it can't overlap the vehicle at any resolution — and the charging pill stays on **one
  line** at every width (verified 320 → 1280px).

## [1.21.2] — 2026-06-14

### Added
- **Manual charge cost — enter what you actually paid.** Public charging is a jungle (flat
  subscriptions, per‑plan Ionity rates, Tesla's monthly fee + time‑of‑use pricing, session/idle fees…)
  and the bill often isn't a clean €/kWh — so a per‑kWh tariff can't model it. A charge's type selector
  now has a 5th option, **✎ Manual**: pick it and type the **real total paid**; it **overrides** Mate's
  table estimate. The effective **€/kWh** (cost ÷ the charge's energy) is shown on the card and feeds
  the trip cost / weighted‑average‑cost (WAC) **exactly like any priced charge** — so the next trip is
  priced from what you really paid. A manual cost is **protected**: the auto‑Home confirm and the
  one‑time energy/cost repairs never overwrite it (the repairs may still refine the *energy*, which only
  sharpens the manual €/kWh). Accepts a comma decimal (`18,45`).
- **Effective €/kWh on every charge card** — each session now shows its real rate (cost ÷ energy) under
  the cost, so you can see at a glance what each charge actually cost per kWh.

> Implements the cost‑precision half of the request in #56. Payment‑method tagging / per‑method spend
> breakdowns stay **out of scope** for Mate (telemetry‑derived cost is in; payment/billing tracking is
> not — as decided in #17).

## [1.21.1] — 2026-06-14

### Added
- **Try the demo from inside Mate — no command line needed.** The setup screen now opens on a simple
  choice — **"Set up my car"** or **"Try the demo"**. Picking the demo turns it on and restarts straight
  into it; an amber banner at the top of every page lets you leave again (*"Exit demo & set up my car"*).
  So a Home Assistant **add-on** user can explore the sample‑data demo with **one click**, instead of
  needing `-e MATE_DEMO=1` on the command line (which still works for standalone Docker). The in‑demo
  restart waits for the container to come back in the right mode before reloading, so it never hangs.
- **Overview — status‑aware quick‑command icons.** The lock / trunk / windows buttons on the car image
  now mirror the live state: **unlocked** highlights amber, **locked** highlights green, an **open trunk
  turns red**, **open windows** highlight violet — the car's state is readable at a glance.
- **Overview — "cable connected / charge complete" state.** When a charge finishes (or pauses) with the
  cable still plugged in, the car no longer just reads *"Parked"*: it shows **"Cable connected · NN%"** on
  the car image (with the plug icon) plus a **"Charge complete"** status, in the teal charge colour. The
  percentage is the real battery SoC; unplugging the cable returns to the normal parked view.

### Changed
- The setup screen leads with the car/demo choice; the Leapmotor account login and the app‑certificate
  step now appear only **after** choosing *"Set up my car"* (with a **Back** link). The in‑app logo now
  uses Mate's car icon — matching the Docker/add‑on icon — instead of the old "LM" placeholder.

### Security
- **Never bundle the app TLS certificate in the image.** The shared app cert (`certs/app.crt` / `app.key`,
  provided by the user at setup) was already git‑ignored and **absent from the published image**, but a
  *local* `docker build` from a working dir that had the files would have copied them in. They're now in
  `.dockerignore` as well, so a local build can never accidentally bundle them.

## [1.21.0] — 2026-06-14

### Added
- **Demo mode — try Mate before configuring anything.** Run Mate with `MATE_DEMO=1`
  (e.g. `docker run --rm -p 4000:4000 -e MATE_DEMO=1 ghcr.io/protossblaster/leapmotor-mate`) and it serves
  a realistic, self‑contained **month of sample data** — weekday commutes, cheap overnight home AC charging,
  a *weekend al mare* with an expensive **DC fast charge**, the blended (WAC) trip costs, battery health,
  vampire drain and the monthly report — with **no Leapmotor account, car or cloud**. Remote commands are
  **simulated** (lock, climate, windows… flip the demo's own state), so the whole UI is explorable and
  interactive, and a **DEMO** badge is shown. **All data is purely demonstrative — nothing is real.**
  Fully gated behind `MATE_DEMO`: a normal install is unaffected (the demo code is inert when the flag is
  off — verified by the full test suite running in normal mode).

## [1.20.2] — 2026-06-14

### Fixed
- **Remote commands no longer get stuck on "Token is invalid" after heavy use.** The account
  TLS certificate Leapmotor issues at login is a short‑lived temp file; once it got cleaned up,
  the poller and the web process could no longer reuse the shared session and re‑logged in on
  **every cycle** — a login storm the Leapmotor cloud then throttled ("Information verification
  failed, please try again later"), so remote commands failed with *Token is invalid*. Mate now
  copies that certificate to a stable file and can re‑create it from the saved session, so the
  shared session survives a vanished temp file and the re‑login storm is gone. (Reported by
  @riri19, #54. Note: the Leapmotor cloud also needs at least ~10 s between remote commands.)

## [1.20.1] — 2026-06-14

### Added
- **Leapmotor B05 battery capacities.** The new B05 hatchback (2026) is now recognised at setup with its
  two European variants — **55.0 kWh usable** (Pro · 401 km WLTP) and **65.0 kWh usable** (Pro Max · 482 km
  WLTP) — instead of falling back to a generic default. The B05 shares the B10's battery pack, so the figures
  match. Affects new setups and the capacity used for energy/efficiency and battery‑health estimates;
  existing installs keep whatever they already configured.

## [1.20.0] — 2026-06-13

### Changed
- **Trip cost is now based on the _blended_ price of the energy actually in your battery
  (weighted‑average cost), not the price of your single last charge.** This fixes a real
  over‑billing on mixed charging: previously, right after a small expensive public/HPC charge,
  **every** following trip was billed at that premium rate — even though most of the energy still in
  the battery came from a cheaper home charge. Now each charge blends into a running average by **how
  much energy it added**, and a trip is priced at that blend. *Example:* a full home charge at
  €0.25/kWh, then a 20 kWh HPC top‑up at €0.75/kWh, leaves the pack at a blended **€0.42/kWh** (not
  €0.75) — so a 20 kWh trip reads **€8.33**, not €15.00. _(Suggested by @riri19, #53; builds on the
  #51 billed‑energy fix.)_

  **How trip costs are calculated from now on — please read, so the numbers make sense:**
  - **A per‑trip cost is an estimate, not an invoice.** You pay at the charger, not per trip, so Mate
    _allocates_ your charging spend across trips. The trip costs will normally sum to a bit **less**
    than what you actually paid: some energy goes to climate, standby (vampire) drain, charging losses
    and regen. **That gap is expected — not a bug.**
  - **The price only moves when you charge** (never while driving): at each charge, new blended €/kWh =
    `(kWh still in the pack × old price + kWh added × this charge's price) ÷ total kWh`.
  - **A public charge counts only once you confirm its cost** on the Charges page (home charges are
    priced automatically). Until you confirm it, your trips keep the previous price and then update by
    themselves the moment you confirm it.
  - **The trip's energy (kWh) is still estimated from the battery %**, so very short or sparsely‑polled
    trips can still read rough. A more precise energy method (from pack voltage × current) is planned
    (#52).
  - The new figure can come out **higher _or_ lower** than before depending on your charge mix — that's
    it being more accurate, not a regression.
- **Overview layout tidied** — the vehicle card and the "last known position" map swapped places for a
  cleaner flow.

### Added
- **Redesigned vehicle card on the Overview.** The car photo now doubles as a live panel: **doors
  locked/unlocked** and **open‑windows count** overlaid on the image, **quick command buttons**
  (unlock · lock · trunk · windows, colour‑coded) below it, and — while charging — an **animated energy
  flow** with live **kW · % · time‑to‑full**. It now falls back to a placeholder when the cloud photo
  isn't available, instead of the whole card disappearing.
- **"New section" badges in Settings.** When a future release adds a Settings section, it shows a
  **NEW** badge on its header until you open it once — so a new option isn't missed in the changelog.
  (No section is flagged in this release; the mechanism is ready for the next one.)

## [1.19.3] — 2026-06-13

### Fixed
- **The account TLS certificate now survives restarts (root-cause fix for the vanishing cert).** At
  every login the API writes the account certificate and key as temporary files. They used to land in
  the container's **ephemeral `/tmp`**, which a standalone Docker install (e.g. on a NAS) wipes on
  every restart — so the two files vanished, remote commands failed with *"Could not find the TLS
  certificate file"*, and the poller had to re-login on each restart. They now live on the
  **persistent `/data`** volume (via `TMPDIR`), so they persist across restarts and the session is
  reused without a re-login. The v1.19.2 self-heal stays as a safety net. *(Reported by @riri19.)*

## [1.19.2] — 2026-06-13

### Fixed
- **Remote commands now recover from a missing TLS certificate.** On some setups the account's TLS
  certificate (a temporary file) gets cleaned out of `/tmp`, and a command would then fail outright
  with *"Could not find the TLS certificate file"*. Commands now treat this like any other auth
  hiccup — re-login (which re-creates the certificate) and retry — the same self-heal the background
  poller already had. *(Reported by @riri19.)*
- **Quieter command logs.** A command that fails once and succeeds on retry (a transient stale
  connection or an expired token) no longer logs an alarming `ERROR`; the error level is now reserved
  for a command that actually gives up.

## [1.19.1] — 2026-06-13

### Fixed
- **Trip starts no longer missed after the car sleeps.** When the car was parked and not reporting,
  the poller could back off to a fixed 15-minute cycle and only notice a drive well after it had begun
  (the first sample already at speed, the start of the route cut off). It now keeps polling at **your
  configured cadence** whenever the car or the cloud is briefly unreachable, so the start of a trip is
  caught as soon as data returns. Polling the cloud never wakes or drains the car, and re-login stays
  rate-limited. *(Diagnosed with @riri19, #52.)*
- **"Driving" shown while parked (Home Assistant / ABRP).** The published vehicle state was derived
  from a climate-fan signal, so a fan speed of 3–5 while parked could read as *driving*. It now comes
  from the **gear and speed** — the same inputs as trip detection — so the MQTT `state` sensor and the
  ABRP `is_parked` flag match reality. *(Reported by @riri19.)*
- **Crash-recovery trip distance.** Closing a trip left open by a restart now filters out null-island
  (0, 0) GPS fixes before measuring, so a single stray point can no longer inflate a trip's distance.

## [1.19.0] — 2026-06-13

### Added
- **📆 Monthly Report.** A new **Report** page brings one month of driving, charging and **cost**
  together at a glance: distance, trips, average efficiency and energy used; charging cost, sessions,
  energy charged and average €/kWh; a **Home vs public** split; cost per 100 km, regen and drive time;
  **deltas vs the previous month**; and **daily distance/cost charts**. Move between months with ◀ ▶
  or the dropdown, and see a **map of every trip that month**. The figures match the Statistics
  (driving) and Charges (cost) pages exactly.
- **🔒 Security indicator on the Overview.** The first card now shows a **Security** row (just above
  READY) — green **Active** when the car is locked and its alarm is armed — mirroring Leapmotor's own
  *vehicle security active* signal. *(Suggested by @riri19.)*
- **✅ "Fully charged" badge.** While the cable is still plugged in and the charge has completed, the
  Overview charging card shows a **Fully charged** badge.
- **Battery-health cold cutoff.** A new slider in **Settings → Advanced** sets the temperature below
  which cold charges are excluded from the State-of-Health estimate (a cold LFP pack reads low). Default
  15 °C; set it to 0 to include every charge.

### Fixed
- **Privacy of the shareable diagnostics bundle.** The exported diagnostics now also redact three things
  that could leak when a bundle is posted publicly: the remote-control **`operatePassword`** (it was
  printed in clear in the web log), the **VIN where it appears lowercase inside the Home Assistant MQTT
  discovery topic**, and **exact GPS coordinates** in the trip logs (truncated to ~10 km). The actual
  MQTT topic, the database and live logging are unchanged. *(Reported by @riri19.)*

## [1.18.1] — 2026-06-12

### Fixed
- **Trip cost on a wallbox install.** A trip's cost is derived from the €/kWh of the last
  charge before it. That rate divided the charge's cost by the **battery (DC/SoC) energy**, but
  HOME charges are billed on the (larger) **wallbox AC energy** — so the rate, and every trip's
  cost, was overstated by the charging losses (and by more when a charge ended near 100%). It now
  divides by the **same energy the cost was billed on** (AC for HOME, battery otherwise), so a
  trip's €/kWh matches your real tariff. *(Reported by @riri19, #51.)*

## [1.18.0] — 2026-06-12

### Added
- **📍 Charging-station names on charges** *(opt-in)*. Every public charge is tagged automatically
  with the name of the station it happened at — shown as **📍 Station name** on the Charges list and
  on the Overview "last charge" card. The lookup runs in the background, fills in already-recorded
  charges too, and **never looks up home charges**. Enable it in **Settings → Charging stations**
  *(off by default)*. *(Idea: @hubcasale, PR #48 — reimplemented Mate-side over multiple sources.)*
- **⚡ Find charging stations** on the **Navigation** page. A new button maps the public chargers
  around the car: choose the **max distance** and **results per page**, filter **by operator**
  (e.g. Electra, Ionity), and see **AC/DC, power (kW) and live availability** — as map pins and a
  list underneath. Tap a station to set it as your destination and send it to the car's navigator.
- **Multi-source charger search**, with cross-source de-duplication: **OpenStreetMap** and the
  **Italian national registry (PUN)** — both keyless and always on — plus **Open Charge Map** and
  **TomTom** when you add their free API keys *(Settings → Charging stations)*. More sources, better
  coverage; live availability comes from the PUN where available.

## [1.17.1] — 2026-06-12

### Changed
- **🎨 Mate has its own face.** New app icon (a car on a telemetry pulse, in the UI's teal) shown in
  the sidebar and mobile header as "LeapMotor **Mate**", plus a **browser-tab favicon** (there was
  none). The same icon now identifies the Home Assistant add-on in the store. The official Leapmotor
  wordmark is no longer used inside the app — Mate is an unofficial companion and now looks the part.

## [1.17.0] — 2026-06-12

### Added
- **🏠 Auto-assign "Home" to wallbox charges** *(opt-in)*. New toggle in **Settings → Wallbox**:
  charges where your wallbox measured energy are confirmed as **Home** automatically — if *your*
  wallbox saw the energy flow, the charge happened at home. The cost goes through the **same engine
  as a manual confirm**, so flat prices **and time-of-use bands** (including the accurate split
  across band changes) come out identical to tapping the badge yourself — verified on real charges.
  Turning the toggle on also confirms the eligible charges already in your history and tells you how
  many. DC/public charges, reconstructed ones and anything you already typed are never touched, and
  you can still change the type by hand afterwards. Off by default.
  *(Idea: @hubcasale, PR #47 — thank you!)*

## [1.16.14] — 2026-06-11

### Changed
- **⚡ Faster add-on installs & updates.** The Home Assistant add-on now installs a **prebuilt
  image** instead of compiling on your device, so installs and updates are much quicker and lighter
  on the hardware. No action needed — your data and settings are kept across the switch. (This also
  clears two deprecation notices the Supervisor was logging about the old build files.)

## [1.16.13] — 2026-06-11

### Fixed
- **🔌 Impossible charge energy & cost fixed.** A wallbox energy counter that reads ~0 when you plug
  in and then snaps back to its **lifetime total** could log a single charge as tens of thousands of
  kWh — with a matching three‑figure cost — throwing off your charge totals. Mate now rejects a
  physically impossible counter jump, keeps counting the **real** energy after it, and a **one‑time
  cleanup** repairs any such charge already in your history: it drops the bogus wallbox figure and
  re‑prices the charge on the battery (SoC) energy at the same €/kWh. Genuine charges are never
  touched (verified on real data). *(GitHub #46.)*

## [1.16.12] — 2026-06-11

### Fixed
- **🔌 No more phantom charges.** A brief plug / charge‑state blip — e.g. the car re‑evaluating after
  you change a charge **schedule** — could leave a fake "charge" in the log that gained no SoC and
  delivered no energy. These empty sessions are now dropped on the spot, and a **one‑time cleanup**
  removes any already in your history. Strictly empty‑only: a charge with **any** SoC gain **or any**
  wallbox‑measured energy is never touched (verified on real data). *(Reported on Telegram.)*

### Changed
- **⏱️ Charge & trip durations read as hours.** A long session now shows **10h 19m** instead of a bare
  *619 min* — in the Overview "last charge" card, the Charges and Trips lists, and the trip detail.

## [1.16.11] — 2026-06-11

### Fixed
- **🎛️ Wallbox picker no longer floods with home power sensors.** On a charger surrounded by lots
  of home‑energy sensors (e.g. a V2C/Trydan with whole‑home monitoring), the role dropdowns could
  list *every* `power` entity in the house. The device filter now anchors on the **power + energy**
  roles (the ones auto‑detection nails reliably), so a secondary role that mapped off‑device — e.g. a
  max‑current control falling back to a household `number` — can no longer collapse the narrowing;
  each dropdown again shows only the wallbox's own sensors. *(Reported on Telegram.)*

## [1.16.10] — 2026-06-11

### Changed
- **🔋 Sharper Battery health (SoH) trend.** The state‑of‑health estimate no longer follows the
  seasons: **cold charges are shown but excluded** from the figure (an LFP pack reads low when cold,
  so a winter session isn't real ageing — threshold configurable via `soh_temp_min_c`, default 15 °C),
  and **charges that end near 100% weigh the most** (the BMS recalibrates the SoC there, so their SoC
  delta — and the estimate — is the most trustworthy). The trend chart gains a **Time / Distance
  toggle** to separate calendar ageing from cycle (per‑km) ageing; each point now carries its battery
  temperature and odometer, and excluded points appear faded with the reason in the tooltip. The
  *Settings → "use measured" capacity* value benefits from the same cleaner estimate. EN/IT/FR/DE.

## [1.16.9] — 2026-06-11

### Added
- **🔓 Log out / change Leapmotor account.** A new **Log out** button in *Settings → Vehicle* clears
  only the stored login and re‑opens the setup wizard so you can link a **different Leapmotor
  account** — without losing anything. Your trips, charges and positions are kept (they're tied to
  the car's VIN, so the same car carries straight over) and the shared app certificate is untouched.
  The poller re‑authenticates as the new account automatically. (Asks for confirmation first.)

### Changed
- **🗺️ Map tiles now load in privacy‑strict Firefox.** OpenStreetMap blocks tiles that arrive without
  a `Referer`, which some Firefox setups (strict tracking protection, private windows, hardened forks
  like LibreWolf/Mullvad) strip — so the map showed "Access blocked" tiles. The tile layers now send
  the page origin explicitly (`referrerPolicy`), fixing it on every map (Map, Overview, Trip detail,
  Navigation). Chrome was unaffected and stays the same.
- **🎛️ Wallbox sensor picker can't be mis‑mapped by unit.** Each role's dropdown now offers **only
  sensors of the right unit** for the two that feed the calculations — **Charging power** lists only
  kW, **Session energy** only kWh — so a kWh meter can no longer be mapped as power (or vice‑versa),
  which used to silently corrupt the stored power and cost data. A choice you already saved is never
  hidden, the other roles (whose unit varies by wallbox) stay unfiltered, and **Show all entities**
  bypasses it for non‑standard setups.
- **🏷️ Precise wallbox field names with units.** Every mapping field now states its unit — *Charging
  power (kW)*, *Session energy (kWh)*, *Charging speed (km/h)* — and the mislabeled current control is
  fixed from "Wallbox power control" to **Max charging current (A)** (it sets amps, not power). *Max
  available* is clarified as **kW or A** since V2C/Pulsar report it in amps. EN/IT/FR/DE.
- The *Vehicle* settings card now opens by default so the new Log out button is easy to find.

## [1.16.8] — 2026-06-11

### Changed
- **🪗 The Settings page is now a tidy accordion.** Every section is collapsible (the same pattern as
  the old *Advanced* card) and **starts collapsed**, so instead of scrolling past long, always-open
  cards you get a clean list of titles and open only the one you need. Each card **remembers its
  open/collapsed state** (saved server-side, shared across devices), and the integration cards (ABRP,
  Wallbox, MQTT) show their **connection status right in the header** even while collapsed. Cards are
  balanced **5/4/4** across the three columns so the page no longer leaves big empty gaps.
  *(Suggested by a user on Telegram.)*

### Fixed
- The *Advanced* card now actually remembers whether you left it open — its key was missing from the
  save allowlist, so toggling it never persisted before.

## [1.16.7] — 2026-06-11

### Changed
- **🔌 Readable wallbox entity dropdowns.** Long Home Assistant entity names were truncated in the
  two-column mapping grid — and the truncated part was exactly the word that tells roles apart
  (*Voltage* / *Power* / *Energy*). The pickers are now **one per line** (full width), and **hovering
  shows the whole name** as a tooltip — both on each option in the open list and on the closed box,
  where it reflects the entity you've mapped (including its full `entity_id`, handy when two sensors
  look almost identical). *(Suggested by a user on Telegram.)*

## [1.16.6] — 2026-06-10

### Added
- **🔌 Wallbox setup now explains itself (#44).** Every entity-mapping field has a short hint under it
  saying exactly what to pick — the *type* of Home Assistant entity (a `sensor`, or a `number` for the
  current control) and its *unit* — plus a line at the top explaining how auto-detection works and what to
  do if your charger isn't listed (tick "Show all", or add its name to the detection keywords). EN/IT/FR/DE.

### Fixed
- **🔍 V2C Trydan chargers are detected automatically (#44).** Added `v2c`/`trydan` to the detection
  keywords (matched on the entity id, which doesn't change with your HA language), so the picker fills in
  on its own. Solar and house-power sensors (e.g. "Energia fotovoltaica", "Alimentazione domestica",
  "Consumo appartamento") — which are also `power`/`energy` entities — are now kept out of the charging
  roles so they can't be mapped by mistake.

## [1.16.5] — 2026-06-10

### Fixed
- **🦇 No more scary “9 %/day” vampire-drain bars (#41).** The chart normalises every parked window to
  %/day — so a single 0.1%-resolution SoC step over a short park got multiplied into a huge bar (a real
  case: −0.4% over 1.1 h → “9.1 %/day”, against a true error band of ±4.4). Short or still-running parks
  are still recorded, but now render as **pale bars** with the ± uncertainty in the tooltip and a “low
  confidence (short park)” note; the park still in progress is marked “still parked” with a “…” on its
  date. Long, trustworthy windows stay solid purple. *(The small drop itself is usually genuine: in the
  first hour after a drive the car hasn’t reached deep sleep yet — that’s a few hundred watts, briefly.
  It’s the ×22 extrapolation to a daily rate that was misleading.)*
- **📍 The map no longer drifts back into the sea for UK / west-of-Greenwich cars (#43).** The signed-sign
  fix from v1.15.0 (#30) is still in place, but some cars omit the signed coordinate in certain poll states
  (and a restart — e.g. an add-on update — forgot what it had learned), so Mate fell back to the *unsigned*
  value and a Lichfield car jumped out to the North Sea again. Mate now **remembers which hemisphere your
  car is in** and re-applies it whenever a poll sends only the unsigned coordinate — and it persists that
  across restarts. East-of-Greenwich cars are unaffected.

### Changed
- **The “typical idle drain” headline is now time-weighted** — total SoC lost ÷ total parked time, across
  *all* parks of at least an hour **including the ones that lost nothing** (which the old median quietly
  skipped, so it overstated drain: a real install read 1.9 %/day where the honest figure is 0.8). If the
  number drops after updating, that’s the correction — not a change in your car.
- **“Average efficiency” now means the same thing on every page (#42).** The Statistics overview showed a
  plain average of each trip’s efficiency, while the Trips page showed a distance-weighted one — so the
  same trips read e.g. 16.6 vs 19.1 kWh/100 km, and the Statistics figure didn’t even match its own
  “energy used ÷ distance”. Both pages now use the distance-weighted value (total energy ÷ total distance),
  which is the physically correct fleet average and what other EV apps report.

## [1.16.4] — 2026-06-10

### Fixed
- **The “Delete trip” button actually deletes now.** It had never worked since it shipped (v1.13.0): its
  relative URL resolved against the app root, so the request went to a route that doesn’t exist and the
  button silently did nothing. If you've ever pressed it and the trip stubbornly stayed — that was this.
  Verified end-to-end, standalone and behind Home Assistant ingress.

## [1.16.3] — 2026-06-10

### Added
- **🔘 “Door Lock Toggle” — a switch you can put on one button (#38).** Launcher widgets (like Samsung’s
  Home Assistant widget) force a *fixed* action on lock-type entities, so the Door Lock entity couldn’t
  work as a single lock/unlock button. There’s now also a **switch** entity (ON = locked): widgets *can*
  toggle a switch, so one tap locks, the next unlocks. The lock entity stays for dashboards. *(Validated
  end-to-end on a real MQTT broker.)*

### Removed
- **The separate Lock / Unlock buttons in Home Assistant** — fully redundant now that the Door Lock entity
  (state + both actions) and the Door Lock Toggle switch exist; they disappear automatically on update. If
  you had them on a dashboard, swap in **Door Lock**. Automations that publish the raw `lock` / `unlock`
  MQTT commands keep working unchanged.

### Fixed
- **🅿️ A few-metres manoeuvre is no longer logged as a “1 km” trip.** The odometer reads in whole km, so a
  short driveway shuffle that happened to cross a km boundary was recorded as a 1.0 km trip (a real case:
  24 m logged as 1 km). On that ambiguous reading Mate now cross-checks the GPS track and drops the phantom
  hop; a **one-time repair** corrects such historical trips to their real distance (nothing is deleted).
- **✋ Closing asks for confirmation too.** Boot, windows and roof shade asked “are you sure?” only when
  *opening*; closing fired immediately — but a remote close can pinch a hand (or just be an accidental
  tap). Both directions now confirm when parked; while driving you still go straight to the “vehicle in
  motion” notice.
- **📅 Dates in your language.** The recent-trips list and the trip page title now show “10 giu 2026” (or
  “10 Jun 2026”, “10 juin 2026”…) instead of the raw “2026-06-10”.
- **📱 No more giant wrapped text on phones.** The efficiency figure (“20 kWh/100km”) rendered whole at
  headline size, wrapping mid-value on the trip page and overflowing the Trips summary tile; it now shows
  a big number with a small unit, like every other stat. The trip-page date also stays on one line.

## [1.16.2] — 2026-06-10

### Fixed
- **No more pointless “are you sure?” prompt when a control is blocked by motion.** Pressing lock / boot /
  windows / sunshade **while driving** used to ask for confirmation first and only *then* show the “vehicle
  in motion — disabled” notice. Now, while moving, the press goes straight to the notice. **Parked behaviour
  is unchanged** — those controls still ask for confirmation, so an accidental tap can’t open a parked car
  you’re not standing next to.

## [1.16.1] — 2026-06-10

### Fixed
- **Controls the car locks while moving no longer misbehave — sunshade, boot, windows and door-lock.**
  These can't be operated in motion (the official app shows the same notice), and the sunshade's state
  signal is unreliable at speed — which could make the **Panoramic Roof** tile briefly read *“closed”*
  while it was actually open. The Commands page now shows a **“car in motion” banner** over those controls
  while driving, and pressing one returns a clear **“Vehicle in motion — … disabled”** notice instead of
  firing a command the car would only reject. Climate and comfort controls are unaffected — they work
  while driving.

## [1.16.0] — 2026-06-10

### Added
- **⚙️ Advanced settings** (Settings → Advanced, collapsed by default). Three tunables for edge cases, with
  sane defaults and a Reset: the **missed-charge SoC threshold** (the battery-% jump that counts as a charge
  Mate missed while the car was asleep), the **vampire-drain noise floor** (parked SoC drops smaller than this
  are treated as sensor noise), and the **AC/DC power threshold** (raise it if you have a 22 kW AC wallbox so
  its sessions aren't labelled DC). “The defaults suit most users — change these only if you know why.”
- **🔎 Recover missed charges** (Settings → Diagnostics). A one-time scan of your history for charges that
  happened while the car was asleep, *before* automatic detection existed, and were never logged — it **shows
  you what it found before adding anything**, and is safe to re-run (no duplicates).
- **🔋 Battery capacity is now editable** (Settings). Pre-filled per model; edit it if yours differs, or click
  **“use measured”** to adopt the value Mate worked out from your own charges. Changing it never rewrites past
  charges, and your battery-health % keeps measuring against the original spec.
- **🔒 Single “Door Lock” toggle for Home Assistant.** A proper MQTT *lock* entity that shows the locked state
  **and** locks/unlocks in one tap — so it fits as a single dashboard or phone front-screen button. The
  separate Lock/Unlock buttons stay for anyone already using them.

### Changed
- **🔋 Battery capacity defaults are now the usable (net) figures**, not the gross pack — T03 36.0, B10 Pro
  55.0 / Pro Max 65.0, C10 69.9 / 81.9 kWh. Existing installs keep whatever they had configured (nothing is
  silently changed); the new editable field + “use measured” let you fine-tune to your own car.

### Fixed
- **🔄 Command tiles no longer get stuck after a lock / trunk / climate command (#34).** The command worked,
  but the few-seconds-later check read the cloud before the car had reported the new state, decided the command
  “hadn’t taken”, and saved that stale reading — so the tile (and even a page reload) kept showing the old
  state until you tapped again or hit Refresh. Mate now **waits for the car to actually confirm** the change
  (and never saves an unconfirmed reading in the meantime), the tile flips instantly on the first tap, and
  back-to-back commands can’t clobber each other.

## [1.15.0] — 2026-06-10

### Added
- **🔋 Battery card on the Charges page.** Charge percentage and range now live right where you watch a
  charge happen — no more flipping back to Overview. The card sits between the live-charging panel and the
  charge limit, refreshes every 30 s while you watch, and its battery bar carries a small **marker at your
  charge-limit position**, so you can see at a glance how far the charge will go. (Overview keeps its own
  card, nothing moved away.)
- **🔄 OTA-update indicator (Overview).** Mate now checks the car's message inbox for a pending vehicle
  software update and shows it on the Overview card — so you know an OTA is waiting without opening the
  official app.
- **⬆️ Mate self-update badge.** Mate checks GitHub (every 6 h, in the background) for a newer release and
  shows a small badge next to the version number when one is available — handy for standalone-Docker users
  who don't get Home Assistant's update prompts.

### Changed
- **🔌 The "Unlock cable" button moved to the Charges page** (inside the Charge-limit card, with a short
  description). It's a charging action, so it now lives with the other charging controls instead of the
  Commands page. Same command, same confirmation prompt — and the Home Assistant / MQTT button is unchanged.

### Fixed
- **🌍 West-of-Greenwich cars were plotted in the sea (#30).** The cloud reports the GPS longitude in two
  signals — one **without its sign**, which is the one Mate read: fine for most of mainland Europe (east of
  Greenwich), but a UK car at 1.9°W was mapped at 1.9°E, in the North Sea. Mate now reads the **signed**
  coordinate pair (with the old ones as fallback). Thanks @BatterBits for capturing the raw signals that
  cracked it — positions recorded before this fix keep the old sign; the map is correct from the first poll
  after updating.
- **⚡ Charge energy was over-stated ~15% on charges ending at 100% — the "107% efficiency".** The car's BMS
  *snaps* the displayed battery % to 100 with zero energy actually delivered in the very moment charging
  stops (a top-of-charge recalibration), inflating the ΔSoC-based energy estimate — which became visible as
  an impossible ">100% efficiency" next to the measured wallbox figure. Verified against the integrated
  charging-power telemetry (the true AC→DC efficiency is ~90%): Mate now anchors the energy of 100%-ending
  charges to the last battery % seen *while still charging*, a **one-time automatic repair** recomputes the
  affected historical charges on first start after updating (costs billed on the wallbox counter are
  untouched; DC-estimated costs are rescaled at your original price), and an impossible efficiency is never
  displayed again. Mid-range charges were verified byte-identical before/after — they were always correct.

## [1.14.0] — 2026-06-09

### Added
- **🩺 Diagnostics card (Settings → Diagnostics).** A one-stop place to grab everything we need when
  something goes wrong, so you don't have to dig through container / Home-Assistant add-on logs. It shows a
  read-only system snapshot (Mate version, model, masked VIN, DB size + row counts, last poll, which
  integrations are on), lets you view the recent **poller / web logs** and the car's current **raw signals**
  inline (with a Copy button), and a **Download diagnostics** button that bundles the parts you tick into one
  `.txt` you can attach to a GitHub issue. **Privacy by design:** the downloadable logs always mask your
  personal info — VIN, credentials and e-mail addresses — and never include GPS; the raw-signals view (which
  does include your location) is a separate, explicit action.
- **📏 Units: Metric / Imperial UK / Imperial US** (Settings → Units). Distances, speeds, temperatures and
  tyre pressures now display in your chosen system — **km/°C/bar**, **miles + mph but °C** (the UK), or
  **miles + °F + psi** (the US). It's **display-only**: your stored data always stays metric, so you can
  switch back and forth any time with nothing lost.

### Fixed
- **🔌 Charges missed while the car was asleep are now recorded (#29).** A home charge that started and
  finished while the car was offline/asleep to the cloud was never seen "live" and so was lost entirely.
  Mate now notices the battery-level jump and **reconstructs the charge** from it (marked *auto-detected*),
  instead of dropping it.
- **🛞 Tyre pressures were shown on the wrong wheels (#32).** The wheel order inherited from the upstream
  library was wrong for the B10; corrected (cross-checked on two real cars against the official app), so each
  pressure now matches the right corner.
- **🔄 The Refresh button is now reachable on phones.** On small / portrait screens it was hidden; it now sits
  in the mobile header, always one tap away.

### Changed
- **Doors and seats are now labelled by physical position** (front-left / front-right …) instead of
  driver / passenger. This reads correctly on both left- and right-hand-drive cars (the old labels were
  inverted for RHD/UK vehicles).

## [1.13.0] — 2026-06-09

### Added
- **🔗 Manual trip merge.** A journey that a short, non-charging stop split into two (or more) separate
  trips can now be joined back into one. On the Trips page, the **🔗 Merge** toggle draws a connector
  between every pair of adjacent trips that can be joined; a **gap slider** (5–90 min, default 30) widens
  or narrows which pairs qualify, live. A pair is offered only when the second trip starts within the gap
  **and** at no higher SoC than the first one ended — a SoC rise means you charged in between, so those
  (e.g. the legs of a long road trip) are never merged. Clicking a connector shows a preview (combined
  route + distance, energy and *driving-only* duration) before you confirm. It's **fully reversible and
  non-destructive**: a merged trip carries a 🔗 badge and an **Unmerge** button, and splitting it restores
  the originals exactly. Distance/efficiency are recomputed over the whole trip; the stop time is excluded
  from the duration (and shown separately).
- **🔄 Refresh button.** A manual "Refresh" at the top of the sidebar pulls the car's current state from
  the Leapmotor cloud right away instead of waiting for the next poll. Mate still reads **passively** (it
  never wakes the car or drains the battery), so this just skips the wait when the car is already awake; a
  sleeping car keeps its last reported state until it next wakes. The README now spells out that Mate
  isn't real-time — it polls about every 30 s while parked and 10 s while driving.

### Changed
- **Stationary discharge (vampire drain) now catches slow-draining cars.** The detection threshold dropped
  from 0.5 % to **0.2 %** of SoC per parked period, so cars that lose little while parked — and shorter
  stops — now show up too, instead of being filtered out as sensor noise.

## [1.12.0] — 2026-06-09

### Changed
- **Home charge cost is now measured from the wallbox's own energy counter — not estimated.**
  When a wallbox with a kWh counter is paired, Mate samples the counter all through the charge and bills
  the **energy it actually added** — the sum of the counter's increases over the session, i.e. the exact
  AC energy the wallbox delivered (conversion losses included). It's **reset/race-safe**: a per-session
  counter that zeroes mid-charge is handled just like a lifetime total, no matter when it resets relative
  to a poll. The charge card now leads with the **🔌 wallbox (billed)** kWh and shows the **🔋 in-battery
  (DC, from SoC)** energy beneath it with the AC→DC efficiency; the cost is plainly *wallbox kWh × price*.
  With no wallbox counter (or for public charges) the **battery (SoC) energy × price** is billed instead.
  The previous method integrated the fluctuating power curve, which under-counted on short/sparse sessions
  and produced costs that didn't add up — that estimation is gone; instantaneous power now feeds only the
  chart. Day / month / year and lifetime energy totals sum the billed energy too, so cards and totals agree.
  *(Charges recorded before 1.12.0 keep their earlier value and **can't be recomputed** with the new
  method — the wallbox counter readings weren't captured during those past sessions. You can delete an old
  session with its 🗑 button if you prefer.)*

### Added
- **Delete a charge session** — a 🗑 button on each charge card (with confirmation), mirroring the
  existing delete-trip action. Day / month / lifetime totals recompute automatically.
- **Pages keep themselves fresh.** The Charges page reloads the instant a charge finishes, so the new
  completed session shows up without a manual refresh; the live/data pages (Vehicle, Wallbox, Battery,
  Statistics, Trips, Commands) auto-refresh while idle — never while you're filling in a form, and your
  scroll position is kept.

### Fixed
- **A finished charge no longer stays "charging" for several minutes.** On the B10 the car's plug flag
  (signal 47) latches on after an AC charge and clears only minutes later — so a charge session could
  linger open long after charging had finished (and even after the cable was unplugged), inflating its
  window. Mate now derives "plugged in" from the charge-connection signal (1149), which drops as soon as
  the session ends, so the charge closes promptly and its window is accurate.
- **GPX export of a single trip downloaded an empty file.** The download link resolved to the wrong URL
  (a 404) under the app's base path; it's now linked correctly (Home Assistant ingress included).

## [1.11.18] — 2026-06-08

### Added
- **Vampire drain** on the Battery page — how much charge the car loses while **parked and not charging**.
  Computed automatically from the telemetry Mate already logs: it groups the parked-idle periods (no driving — by
  speed *or* an odometer change — and no charging), measures the SoC each one lost, and shows it as a per-period
  bar plus a "typical %/day" figure. Periods under an hour or with a sub-0.5 % drop are treated as sensor noise.
  No setup, no input.

### Changed
- **Sending a command no longer risks logging you out of the official Leapmotor app** (on a shared account). When a
  command hits an expired token, Mate now **refreshes the token** (keeping the same session) instead of doing a full
  re-login — a full login is what the cloud treats as a new device session and uses to evict your phone. A full
  re-login is still the fallback if the refresh can't recover (and still self-heals a missing certificate). The
  background poller already worked this way; this brings the command path in line.

## [1.11.17] — 2026-06-08

### Fixed
- **A home charge could show an impossible cost** (e.g. 10.3 kWh billed at 11.07 € — GitHub #24). Home charges
  are billed on the real AC energy the wallbox delivers, which is read from the charge's power curve. The 1.11.16
  fix capped the *displayed* charging window and the split-cost query so an offline-interrupted/overlapping charge
  couldn't absorb a later charge's power — but the **power-curve read used for the AC energy was left un-capped**,
  so for such a charge the AC energy (and therefore the cost) still leaked in a later charge's wallbox power. That
  read is now capped at the next charge's start too, so the AC energy and cost stay bounded to the charge itself.
  The same cap was applied to the battery-health energy integral.
- **An implausible wallbox AC reading is no longer shown or billed.** If the AC energy comes out more than twice the
  DC energy into the battery (physically impossible — real efficiency is ~75–95 %), or can't be validated because the
  DC figure is zero/missing, it's discarded and the cost falls back to the battery (DC/SoC) energy. This guards
  against a mis-mapped Home Assistant entity (e.g. a cumulative-kWh meter mapped as the wallbox *power* sensor, which
  showed thousands of kWh at ~0 % efficiency).
- **The AC/DC comparison energy now skips gaps over 15 min**, consistent with the time-of-use cost split, so a long
  pause inside a single charge no longer inflates the AC energy (and the cost billed on it).

> **Note:** a charge's cost is frozen when you confirm its type. To recompute an already-recorded charge with the
> fix, just re-select its location (e.g. "🏠 Home") on the Charges page.

## [1.11.16] — 2026-06-08

### Fixed
- **Two charge rows for one plug-in could share the same "Charged HH:MM → HH:MM" end time** (GitHub #23).
  If the cloud API went unreachable mid-charge (3 errors → OFFLINE) and recovered, the poller opened a
  *second* charge row instead of resuming the open one, leaving the first as an orphan. When that orphan was
  later closed, its `ended_at` was set to the latest position — bleeding past the next charge so both rows'
  active-power windows (and split costs) inherited the later charge's last power sample. Fixed at the source
  (the `recorder` no longer opens a charge while one is already open; `close_orphan_charges` caps the orphan's
  end at the next charge's start) **and** defensively in the web layer (the power-window/cost queries are
  capped at the next charge's start, so already-recorded overlaps display and cost correctly too).
- **Time-of-use "split" cost was distorted for a charge with a long pause between two power bursts.** The gap
  between bursts was integrated as a phantom constant-power interval priced at the gap-start's band, skewing
  the weighted-average price. Gaps over 15 min are now skipped (matching the energy-integration guard).
- **A day-restricted off-peak band crossing midnight no longer drops its after-midnight hours to the base
  price.** A "23:30 → 07:30" band on selected days now correctly prices the early-morning hours as belonging
  to the previous (selected) day.

### Changed
- **Re-confirming a charge's type now refreshes its cost immediately** (instead of blanking it until reload)
  and adds a tooltip showing whether the cost was billed on the wallbox **AC** energy or fell back to battery
  **DC** (e.g. when the wallbox AC history is no longer available). The DC fallback is also logged.

## [1.11.15] — 2026-06-08

### Fixed
- **Trips were no longer saved on cars that don't report GPS.** The 1.11.10 odometer fix falls back to the
  GPS track for distance; if the car reports neither a valid odometer nor any GPS fixes, the distance came
  out as 0 and the trip was dropped as a "< 0.5 km short hop". Such trips are now **kept** with an unknown
  distance (time, SoC and energy preserved); only genuinely-measured sub-0.5 km hops are dropped. The
  one-time odometer-repair migration is likewise fixed to keep (not delete) GPS-less trips. (Regression in 1.11.10.)
- **Wallbox max-current slider snapping back to 0 after you moved it.** Setting the value re-read the entity
  immediately, but HA's `number.set_value` is async and a device-backed wallbox often still reports the
  old/idle value for a moment, so the slider jumped back. It now keeps the value you set (optimistic), and
  the real reading reappears on the next page load.

### Changed
- **Clearer label for the wallbox power control in Settings.** The wallbox entity-mapping role previously
  labelled "Max current" is now **"Wallbox power control"** (with a hint that it's the adjustable charging
  current/power `number` entity), to make it easier to map the right entity and avoid mistakes.

## [1.11.14] — 2026-06-08

### Changed
- **Home charges are billed on the wallbox's AC energy.** When a wallbox is configured, a **HOME** charge's
  cost now uses the **AC energy the wallbox actually delivered** (what you pay the utility, including AC→DC
  conversion losses) instead of only the DC energy that reached the battery. Public/away charges and setups
  without a wallbox keep DC billing (the only figure available there). Per the "new charges only" rule,
  already-costed charges are unchanged — re-confirm a charge's type to recompute it. Thanks @riri19 (#23).

## [1.11.13] — 2026-06-08

### Fixed
- **"Charged" (actual charging window) showed the wrong start time.** The real charging window added in
  1.11.12 compared the charge timestamps (which arrive in local time) against the UTC position log, so the
  start could be shifted by the timezone offset (e.g. 20:48 instead of 18:48) and the line could even appear
  on normal charges. The window bounds are now normalized to UTC before the lookup. (Regression in 1.11.12.)

## [1.11.12] — 2026-06-08

### Changed
- **Charges show the actual charging window for delayed/scheduled charges.** A session is recorded from
  cable plug-in to unplug, so a scheduled/delayed charge folds in the idle time before/after power
  actually flows. The charges list now adds an **"Charged HH:MM → HH:MM"** line with the real charging
  window (first→last power sample) whenever it differs from the plug-in→unplug window, so the displayed
  times match reality. Normal charges are unchanged, and off-peak (time-of-use) cost was already correct
  in the default "Accurate split" pricing (energy is attributed by the real power curve). Reported in #23.

## [1.11.11] — 2026-06-08

### Added
- **One-touch vehicle preparation.** A new **Prepare car** page mirrors the official app's
  "Preparazione del veicolo con un solo tocco": bundle air-conditioning (cool/heat/vent/defrost +
  temperature), front-seat heating/ventilation, steering-wheel heating, mirror heating and an optional
  destination, then run it **now** (cmd 360) or on a **schedule** (cmd 361) with a time + weekdays.
  Scheduled preparations are listed (read from the car), **editable** and individually removable. A
  **"Cancel preparation (all off)"** button turns A/C, seats, steering and mirror heating back off. This
  completes coverage of the B10 app's remote functions in Mate.
- **Delete trip.** The trip detail page now has a **🗑 Delete trip** button (with an explicit
  confirmation prompt) that permanently removes a trip and its GPS track; daily/monthly/lifetime
  totals recompute automatically. Useful for one-off bad data.

## [1.11.10] — 2026-06-08

### Fixed
- **Trip distance could log the car's entire mileage.** When the odometer signal was missing on the
  first poll of a trip, the trip's start odometer was recorded as 0 and its distance became the full
  odometer reading — a few-metre move showing up as thousands of km (e.g. a 3-minute hop logged as
  6441 km), inflating daily/monthly totals and efficiency. Trip distance now trusts the odometer delta
  only when both readings are valid, otherwise falling back to the GPS track, which also ignores
  spurious `(0,0)`/out-of-range GPS fixes. Affected trips already in the database are recomputed from
  their GPS track (or removed if under 0.5 km) automatically on the next start — no action needed.
  (Thanks to the user who reported a 6441 km "trip".)

## [1.11.9] — 2026-06-08

### Added
- **Configurable wallbox detection keywords.** The **Settings → Wallbox** panel now has a field for
  comma-separated keywords used to auto-detect your wallbox entities in Home Assistant. Useful when
  your charger's entities don't match the built-in names (Easee, go-e, KEBA, Pulsar, Feyree…). Leave
  it empty to keep the defaults; custom keywords replace the automatic device-class detection.
  (Thanks to **@hubcasale** — Corrado Gamberoni — PR #22.)

### Fixed
- **Wallbox AC energy / efficiency.** The AC-from-wallbox energy is now integrated with the same
  step-hold resampling used by the comparison chart, so the numeric kWh totals match the chart and no
  longer show impossible efficiency above 100% when Home Assistant logs the wallbox power sparsely.
  (PR #22.)
- Keyword parsing hardened so a delimiter/whitespace-only entry cleanly falls back to the defaults
  instead of disabling detection, and the entity scan reads the setting once instead of per-entity.

## [1.11.8] — 2026-06-08

### Added
- **Climate pre-conditioning scheduling.** The **Scheduling** page now also writes the **climate
  schedule** (cmd 171) on the B10 — five quick presets (**Quick Cool / Quick Heat / Ventilation /
  Defrost / Auto**), time, days of the week, and (for ventilation / auto) a target temperature on a
  slider; cool/heat lock to their preset temperature. The earlier "the B10 rejects the climate write
  (code -2)" turned out to be a stale/expired `start_time`, not a blocked endpoint — Mate now anchors
  the start to the next occurrence, so the write works. Read / write / edit / cancel all stay in sync
  with the official Leapmotor app. (Reverse-engineered on-car; details shared upstream at
  markoceri/leapmotor-api#5 and kerniger/leapmotor-ha#43.)

### Changed
- **Scheduling UX (charge + climate).** "Active" is now a clear master switch: turning it off resets
  the day selection (the car may charge anytime); the day chips start clean so clicking a day
  *selects* it instead of de-selecting one of seven. Plus an active/inactive badge on both cards, a
  prominent "Mate manages this schedule" note, MDI icons across the page, and an immediate refresh of
  the card after a successful save.

## [1.11.7] — 2026-06-07

### Changed
- **Self-hosted front-end assets (no CDN).** Tailwind, htmx, ApexCharts, Chart.js and Leaflet are now
  served from the add-on itself (`web/static/vendor/`) instead of public CDNs (cdn.tailwindcss.com /
  unpkg / jsdelivr). Benefits: privacy (the UI no longer leaks your IP to third-party CDNs), reliability
  (it keeps working offline or when a CDN is down/blocked), and security (no third-party script in a
  car-control app). No visible change to the interface; verified in both standalone and HA ingress.
  Idea from PR #20 by @LeeTeng2001.

## [1.11.6] — 2026-06-07

### Added
- **Charge scheduling** — a new **Scheduling** page (sidebar). The **charge schedule** (cmd 190) is
  read **and write**: enable, target SoC, start/end window, and a **7-day picker**. Days match the
  Leapmotor app (shown Dom→Sab; stored Monday-first in the `cycles` mask). Writes use read-modify-write
  so the car's existing day mask / repeat / recharge are preserved. (Confirmed on-car.)
  Climate (pre-conditioning) scheduling is **not** included: the B10 cloud rejects the climate-schedule
  write (cmd 171, code -2) even with valid data, so set those in the Leapmotor app for now; we're
  investigating the write path separately.
- **EVCC integration.** The MQTT bridge now also publishes EVCC-friendly `evcc/plugged`, `evcc/charging`
  and `evcc/climate` booleans (`true`/`false`, which EVCC's parser accepts) next to the Home Assistant
  topics — so an EVCC `type: custom` vehicle can read SoC / status / range / odometer. Ready-to-paste
  config in `docs/EVCC.md`.
- **CSV export buttons** on the Trips and Charges pages (links the export that already lived in
  Settings → Export/Backup, plus per-trip GPX).
- **Wallbox — advanced entity mapping** (#21). A **"Show all entities"** toggle in Settings → Wallbox
  lists every sensor/number entity, not just charger-named/typed ones, so foreign-language names or a
  generic energy-meter/relay can be mapped manually. Added FR charger keywords (`borne`, `recharge`,
  `feyree`) to auto-detection (#19).

### Changed
- **Commands page polish.** Uniform tile sizing across all cards; **Quick actions** are now vertical
  tiles with action buttons (Find / Preheat / Unlock cable); stacked columns are equal width on mobile.

## [1.11.5] — 2026-06-07

### Added
- **Unlock charge cable** — unlock the B10 charge port (`unlock_charger`, right 192), promised on #19.
  Exposed both in the **web UI** (new **Quick actions** card, with a confirm prompt) and over **MQTT**
  (Home Assistant discovery button + command handler). i18n in en/it/fr/de.

### Changed
- **Commands page restyle.** A full pass over the page's look & layout:
  - **Icons → Material Design Icons** everywhere, from a single source (`partials/_icons.html` `mdi()`
    macro): automotive glyphs (vehicle lock = `shield-car` green/red, boot = `car-back`, windows =
    `car-side`, roof = `car-convertible`, climate = `air-conditioner`/`snowflake`/`heat-wave`/`fan`/
    `car-defrost-front`, defrost, EV plug, etc.). Card headers and status pills use them too.
  - **Uniform tiles** — fixed icon/label slots + bottom-anchored controls so every tile in a row
    aligns, regardless of label length or control type (slider vs toggle vs button).
  - **Rebalanced two-column layout** — LEFT = Vehicle + Climate, RIGHT = Comfort + Quick actions;
    Comfort widened to 3-up so the columns are height-matched (bottom-left void measured 618px → 72px).
    Collapses to a single column on mobile (vehicle controls first).
  - **Consistent card headers** (icon + title) on every card, including Vehicle.
  - Merged the old Find Car + Battery cards into **Quick actions**; "Preheat" → "Preheat battery".

## [1.11.4] — 2026-06-06

### Added
- **Full comfort controls on the B10** (thanks @kerniger, leapmotor-ha#41, payloads captured from the app):
  - **Heated & ventilated seats** — a per-seat **level slider (off / 1 / 2 / 3)** for driver & passenger,
    colour-accented (heat = amber, ventilation = sky-blue). Payload `{"position":"driver|copilot","level":"0..3"}`.
  - **Heated steering wheel** and **heated mirrors** — on/off toggles on the Comfort card.
- **More climate controls.** The Climate card adds a **Rapid Ventilation** tile and a **temperature stepper
  (18–32 °C)** that sets the target and starts the climate (auto cool/heat vs the cabin temp). Everything runs
  through the cars' single climate command (cmd 170).
- **READY indicator.** The Overview battery card now shows the car's **READY / Not Ready** state, from the
  faithful B10 signal `bcmKeyPositionOn3` (1258) — driven only by the physical key/READY.
- **Home Assistant (MQTT).** The new comfort & climate commands (seat heat/vent on/off per seat, steering &
  mirror heating, rapid ventilation) are exposed as model-aware buttons over MQTT discovery.

### Changed
- **Tyre pressure — status label per wheel.** Each wheel tile on the Vehicle page now shows a
  colour-coded **status**: *normal* (green), *low* / *high* (amber), *too low* / *too high* (red),
  next to the bar value. Adds **high-pressure** warnings (the view previously only flagged low). Low
  still uses the vehicle's own TPMS warning (plus a < 2.0 bar floor); high is threshold-based
  (> 3.0 bar high, > 3.3 too high). Translated in EN/IT/FR/DE.
- **Colour-coded icons for doors, windows & roof.** On the Vehicle page the tile **icon** (not just the
  text) now carries the state: **closed = green**, **open = sky-blue** (doors, trunk, windows and the
  panoramic roof) — blue reads as "open", not as an alarm.

## [1.11.3] — 2026-06-06

### Added
- **Working A/C On/Off on the B10.** Turning the climate **fully off** now works on the B10: a new
  **A/C** tile on the Commands → Climate card powers the air-conditioning off (and on). This uses the
  newly-found command (`ac_switch` with `operate=off`, which drives the `acSwitch` signal to 0) —
  discovered by on-car testing. Previously the B10 had no working remote A/C-off and the button was
  hidden; the capability is now re-enabled for the B10 over both the web UI and Home Assistant (MQTT).

### Changed
- **Removed the 1.11.2 "A/C won't fully turn off" notice and the on-press confirmation** — they are
  obsolete now that Mate can fully turn the climate off on the B10.

### Internal
- Reported the B10 A/C-off payload upstream (the library's `ac_off()` sends `operate=close`, which only
  flips the B10 to AUTO; the B10 needs `operate=off`).

## [1.11.2] — 2026-06-06

### Added
- **Climate "turn off" note.** The Commands page **Climate** card now shows a highlighted notice that
  turning a function off returns the climate to its base mode rather than fully powering the A/C off —
  to switch it off completely you use the Leapmotor app or do it manually in the car. (Reflects the
  cloud API's lack of a reliable remote "A/C off"; avoids confusion that the climate "won't turn off".)
- **Confirmation when turning the climate on.** Pressing a climate **On** button now asks for
  confirmation, reminding you that Mate can't fully power the climate off afterwards (use the Leapmotor
  app or the car). The confirm fires only on the *On* action, not when turning a function off.

## [1.11.1] — 2026-06-06

### Added
- **Total energy consumed per trip.** The trip detail now shows the trip's total **kWh consumed**
  (next to the efficiency), so you can compare it directly against the regenerated energy. (#18)
- **Per-trip cost.** Each trip now shows its **cost**, computed from the energy consumed × the
  price per kWh of the last charge before the trip. Currency-aware (formatted with the configured
  currency). (#18)

## [1.11.0] — 2026-06-06

### Added
- **Comfort sensors on the Commands page.** A new **Comfort** card (beside the controls block)
  shows the read-only state of the **heated/ventilated seats** (driver & passenger), the
  **heated steering wheel** and the **heated mirrors** (left & right) — as tiles matching the
  rest of the page, with proper car icons. These reflect what the car reports. They are also
  published to Home Assistant as native **MQTT sensors**.

### Changed
- **Model-aware controls (per vehicle).** Mate now shows only what *your* car actually supports.
  On the **B10**, for example, the over-MQTT **A/C Off** button is hidden, because the Leapmotor
  cloud does not honour a remote full power-off on that model (an open limitation tracked with the
  API maintainers). CORE telemetry — trips, charges, reports, charts — is never affected.

### Fixed
- **Battery card.** The minimum battery temperature now shows correctly as `NN°` (it previously
  rendered a raw label such as `22min_temp`), and the header texts no longer overlap on narrow
  layouts.

### Docs
- Updated the Home Assistant install instructions for the **2026.2 "Apps" rename** (formerly
  "Add-ons"; *Applicazioni* in Italian).

### Internal
- New per-VIN **capability profile** that drives the model-aware UI/MQTT (each feature classified
  working / broken / untested from on-car probing; confirmed-broken non-core features are hidden).
- CI workflow that auto-syncs the add-on repository's version on each published release.

## [1.10.0] — 2026-06-05

### Added
- **Collapsible integration cards (Settings).** The ABRP, MQTT and Wallbox cards now tuck
  their configuration fields behind a chevron, so integrations you don't use stay compact
  and the page is easier to scan. The enable toggle and Save button stay visible, and
  ticking the enable box opens the card automatically. Each card's open/collapsed state is
  saved **server-side**, so it's remembered across reloads, reboots and devices — not just
  in the current browser.
- **At-a-glance status badges (Settings).** Each integration card shows a small status dot
  in its header, visible even when the card is collapsed. MQTT does a live broker connect
  (*Connected* / *Not connected*), while ABRP reflects its configuration state (*Active* /
  *Not configured* / *Off*) — the same visual language as the existing Wallbox connection
  badge.

### Changed
- **Upgraded the Leapmotor API library to 0.3.1** (from 0.1.4). This brings native
  handling of the T03 status format and the B10→C10 status-path mapping, so Mate no
  longer needs its own patches for those. The vehicle-data parsing is unchanged
  (raw-signal based), so trips, charges and the dashboard are unaffected.

### Internal
- Dropped the bundled `_get_vehicle_raw_status` monkey-patch (the library now maps the
  B10/B11 status path natively) and replaced the hand-rolled last-week energy and
  consumption-rank endpoints with the library's native methods.

## [1.9.0] — 2026-06-05

### Added
- **Battery health page.** A new *Battery health* page estimates your pack's usable
  capacity (and a state-of-health %) over time. For each charge it integrates the
  **measured** energy delivered (∫ voltage × current, the same source as the charge
  power curve) and divides it by the SoC gained — so the estimate tracks real battery
  ageing rather than just echoing the configured nominal capacity. Only charges with a
  meaningful SoC rise and stored telemetry are used; the headline figure is smoothed
  over the most recent charges to cut single-session noise. It's an estimate, not a lab
  measurement.
- **Global map.** A new *Map* page draws every trip as a connected route line (white
  casing + blue line so it stays readable over any road colour) — showing everywhere the
  car has driven — plus your **most-visited places** as bubbles sized by visit count
  (start/end points clustered to ~110 m, no reverse geocoding).
- **SoC & speed profile on each trip.** The trip detail page now charts state-of-charge
  and speed over the course of the drive (replacing the plain speed bar).

### Fixed
- **T03 (EU) vehicles now report live data.** The European API returns the live status
  as named fields at the top level instead of the numeric `signal` block used by
  C10/B10, so the poller saw *"no live data"* forever. Mate now parses both shapes. This
  was the real root cause on the T03 in #9 (the shared-car `carId` retry added in 1.8.2
  was unrelated).

### Notes
- Both new pages read **existing data** (the charge telemetry and trip GPS already
  logged) — nothing new is collected. Very old sessions whose GPS samples were pruned
  simply won't appear.

## [1.8.2] — 2026-06-05

### Added
- **Encrypted credentials at rest.** Your Leapmotor password/PIN and the other stored
  secrets (Home Assistant, ABRP, MQTT and geocoder tokens) are now encrypted in the
  local database with a per‑install key (`/data/secret.key`, auto‑generated; or set
  your own via the `MATE_SECRET_KEY` env var). Existing installs migrate transparently
  on the next start — no re‑login needed. Keep `secret.key` with your backups: a
  database restored without it will ask you to re‑enter the credentials.
- **Optional database pruning** (Settings → Database). Cap raw GPS‑sample storage to
  6/12/18/24 months; the poller prunes old non‑charging samples daily and reclaims
  space — trips and charge curves are always kept. Off by default. The page also shows
  the current database size.
- **Health endpoint** `GET /healthz` (+ Docker HEALTHCHECK): reports whether the poll
  loop is alive, so a wedged poller is visible instead of data silently stopping.
- **Data export & backup.** Settings → Export: download Trips and Charges as CSV and a
  full database backup; each trip page now has a GPX download of its GPS track.

### Changed
- **Faster charge/Wallbox history at scale.** Added a partial index on the telemetry
  table so the charge‑power, time‑of‑use cost and Wallbox queries stay fast as the
  database grows over the years.

### Fixed
- **Shared cars never reported live data** (the poller was stuck on *"Vehicle returned
  no live data"* forever, even while driving). When a car is *shared* to the account —
  exactly what happens if you follow the "use a different account than your phone"
  advice and share the car to that second account — the Leapmotor cloud returns an
  empty status unless the request also carries `carId`. The poller (and the web command
  client) now retry the status request with `carId` when a shared car comes back empty,
  recovering live data automatically. The login line also logs `shared: true/false` to
  make this diagnosable. Reported on the T03 in #9.
- **Regen energy** now scales with the configured driving poll interval instead of a
  hardcoded 10 s.
- **Trip efficiency** is no longer stored as a negative kWh/100km when the battery SOC
  rose over a trip (regen / a cloud SOC blip) — it's withheld instead.

### Security & hardening
- **Secrets are no longer rendered back into the Settings page.** The ABRP / MQTT /
  geocoder fields now show a masked placeholder when set (like the HA token already
  did) and are only overwritten on a non‑empty submit.
- **MQTT commands are thread‑safe.** Remote MQTT commands run on a background thread;
  API access is now serialized with the poll loop and the post‑command "boost" write
  uses its own DB connection, avoiding a rare race.
- **Clear warning on a wrong/missing encryption key** at startup (e.g. a database
  restored without its `secret.key`), instead of an obscure later login failure.
- Added a `.dockerignore` so the local database, `secret.key`, backups and caches can
  never be baked into a built image.
- **Optional login for standalone** (set `MATE_AUTH_PASSWORD`): a password gate with a
  signed, HttpOnly, SameSite=strict session cookie. Off by default and ignored when
  running as a Home Assistant add-on (ingress already authenticates). When enabled it
  also closes the previously open re-`POST /setup` path.

## [1.8.1] — 2026-06-04

### Added
- **Climate over MQTT is now four buttons** — *Quick Cool*, *Quick Heat*, *Defrost*
  and *A/C Off* — mirroring the in-app Commands page, instead of a single on/off
  switch. The old switch only ran the ventilation fan and its OFF did nothing; the
  buttons send the real climate commands. Turning the A/C fully **off** is
  best-effort — the vehicle cloud doesn't reliably honour it (an open issue with the
  Leapmotor API). The deprecated switch is removed from Home Assistant automatically;
  the read-only *Climate* state sensor stays. (Reported in #14.)
- **Malaysian Ringgit (MYR)** added to the display currencies. (Requested in #13.)

### Fixed
- **Recent Trips on the Overview showed UTC** while the Trips page showed local time.
  The Overview now converts trip times to your local timezone too. (#12)

### Changed
- **Quieter logs when the car is asleep.** A parked car in deep sleep is normal; the
  back-off is now logged once instead of repeating every cycle with a climbing
  "after N tries" count that read like an escalating failure.

## [1.8.0] — 2026-06-04

### Added
- **Customizable display currency.** The euro is no longer hardcoded — pick your
  currency from **30 world currencies** (€, $, £, CHF, kr, zł, Ft, ¥, …) in
  **Settings → Language & Currency**. Every cost across the app (Overview, Charges,
  Wallbox, totals) reformats to it, with the correct **symbol placement**
  (e.g. `$12.50` vs `12,50 €`) and **decimal digits** per currency (2 for most,
  0 for yen/forint/won). The number format (decimal/thousands separator) follows
  the selected UI language. The Settings *Language* card is now *Language &
  Currency*, listing currencies by name (e.g. "Euro (€) — EUR"). (Requested in #10.)

## [1.7.1] — 2026-06-04

### Fixed
- **Scary `Poll error: 'signal'` when the car is asleep.** When the Leapmotor cloud
  returned a status without the live signal block — the car in deep sleep / briefly
  not reporting, or a transient cloud hiccup — the poller raised a bare `KeyError`
  and logged it as an `ERROR`, which looked like a crash. It's now handled cleanly:
  the poller logs a clear "vehicle not reporting (asleep/unavailable)" message,
  retries a couple of times, then backs off — and recovers on its own once the car
  reports again. (#9)

## [1.7.0] — 2026-06-04

### Added
- **Charge Prices page with time-of-use tariffs.** A dedicated *Charge Prices*
  page replaces the single price field in Settings. Choose **flat (24h)** pricing
  or **time-of-use bands**: add one or more time windows, pick which **days of the
  week** each applies to (with All / Weekdays / Weekend shortcuts), and set a price
  per **charge type** (Home/AC/DC/HPC) for each. Leave a cell blank to use the base
  price, or enter `0` for free. Each session's cost is computed by splitting its
  energy across the bands it spans using the real power curve. Cost changes apply to
  **new charges only** — a charge's cost is frozen when you confirm its type, so
  later price/band edits never alter past sessions. (Requested in #7.)
- **MQTT "Test connection" button** (Settings → MQTT) — check the
  broker/port/credentials/TLS before saving.

### Fixed
- **MQTT state out of sync after a command.** Lock/unlock/trunk/climate commands
  sent over MQTT executed, but the published state only refreshed on the next poll
  (up to 30 s when parked), so Home Assistant showed stale values. Mate now
  publishes the expected state immediately and triggers a fast re-poll to confirm —
  the same approach the web UI already uses.
- **Inverted lock state in Home Assistant.** A locked car showed up as "Unlocked"
  (Home Assistant's `lock` binary-sensor class is inverted). The lock entity now
  displays correctly; the published topic value is unchanged.

### Changed
- **MQTT topic prefix now scopes the Home Assistant device.** You can run a second
  instance (e.g. a test poller alongside the production add-on, same car) on a
  different prefix without it overwriting the same entities. The default prefix is
  unchanged, so existing installs are unaffected.

## [1.6.3] — 2026-06-04

### Changed
- **Vehicle page redesigned with Material Design icons.** Doors, trunk, windows,
  panoramic roof, tyres and temperatures now use clear, car-specific icons instead
  of emoji (windows even switch between open/closed icons). Self-contained inline
  SVGs — no external icon font.

### Fixed
- **Panoramic roof shows its real state.** The Vehicle page now reads the roof
  position live from the car (signal 1724), consistent with the Commands page,
  instead of relying on the last command / showing "no data".
- **Version number is now visible on mobile** (in the top bar), not only on desktop.

## [1.6.2] — 2026-06-04

### Fixed
- **Wrong clock times for users outside Italy.** The add-on fell back to a
  hardcoded `Europe/Rome` timezone when `TZ` wasn't in the environment (the
  Supervisor only sets the container's local time). Now the UI uses the system /
  Home Assistant timezone, so trips, charges and "last seen" show your real local
  time everywhere.
- **Overview "State" now follows the gear, not just speed.** A stop in traffic
  with the car in Drive used to read as "Parked"; now any gear other than P shows
  "Driving".

### Changed
- **Panoramic roof** shows "Operate first" instead of "No data" when its position
  is unknown — the B10 doesn't report the sunblind's position, so Mate only knows
  it after you open/close it from the app.

## [1.6.1] — 2026-06-04

### Fixed
- **Poller regression (since 1.5.1).** The configurable charge-detection setting
  was applied with a wrong call that raised an error on every poll cycle, so the
  poller stopped collecting data. Fixed — polling, trip and charge detection work
  again. **Update strongly recommended if you're on 1.5.1 or 1.6.0.**
- **Setup PIN field said "6-digit".** The Leapmotor operation PIN is **4 digits** —
  the placeholder/hint and the input length now say 4.

## [1.6.0] — 2026-06-04

### Added
- **Responsive layout for phones and tablets.** On small screens the sidebar
  becomes a slide-out drawer with a top bar + hamburger menu, the content reflows
  to full width, and the maps no longer overlap the navigation. The desktop layout
  is unchanged. Contributed by **@hubcasale** (#6) — thank you!

## [1.5.1] — 2026-06-04

### Added
- **Configurable charge-detection threshold.** The minimum charging current that
  counts as "charging" (below it a plugged-in car is treated as idle) is now
  adjustable in **Settings → Charge detection** (0.5–16 A, default 2 A). Useful for
  low-power / experimental supplies. The poller applies it live, no restart needed.
  Thanks @hubcasale for the suggestion.

## [1.5.0] — 2026-06-04

### Added
- **Navigation page 🧭 — send a destination to the car.** Type a street + city,
  preview it on the map, and push it straight to the vehicle's built-in navigation
  (no PIN). The page also shows the car's **current address** (reverse-geocoded from
  its GPS). Fully translated (EN/IT/FR/DE).
- **Configurable geocoder.** Address lookup works out of the box with a free
  OpenStreetMap-based provider (no key). For better street/house-number coverage you
  can optionally pick a provider and paste an API key in **Settings → Address lookup**
  — **Geoapify** (recommended, free, no credit card, includes house numbers),
  **LocationIQ** or **TomTom**. Any provider error falls back to the keyless lookup.
- **"Free" charge type.** Mark a session as free charging (🆓) — its cost is recorded
  as €0.00.

### Changed
- **Charge-type labels are now language-neutral** — 🏠 Home · 🔌 AC · ⚡ DC · 🚀 HPC ·
  🆓 FREE — so they read the same in every UI language.

### Fixed
- **The "charges to confirm" banner no longer sticks while a charge is in progress.**
  An ongoing session can't be confirmed yet, so it's excluded from the count; only
  finished, unconfirmed charges are flagged.
- **Wallbox power/energy units are auto-detected.** Wallboxes that report power in
  **watts** (or energy in **Wh**) are now normalised to kW/kWh everywhere — the
  AC-vs-DC comparison and the per-session power chart, not just the live panel.

## [1.4.0] — 2026-06-04

### Added
- **German (Deutsch) UI language.** Full translation of the web interface — nav,
  Overview, Trips, Charges, Statistics, Commands, Vehicle, Wallbox, Settings and the
  first-run Setup wizard. Selectable from Settings and the setup screen, and
  auto-detected from the browser language. Requested by the community on GitHub.

### Fixed
- **Month names in the history trees are now localized.** The year → month → day
  breakdowns on the Trips, Charges, Statistics and Wallbox-comparison pages built
  their labels with `strftime("%B"/"%b")`, which is always English regardless of the
  selected language. Month names (full and abbreviated) are now translated for all
  languages (it/fr/de/en) without relying on system locales.

## [1.3.2] — 2026-06-04

### Fixed
- **Tyre pressures were shown on the wrong wheels.** The B10 signal→wheel mapping is
  corrected per markoceri/leapmotor-api's documented signal table — the pressure and
  its low-pressure alarm now refer to the same (correct) wheel.
- **Removed the bogus "outside temperature".** That signal (2101) is actually the
  driver-seat ventilation level; no ambient-temperature signal exists, so the value
  was meaningless. Dropped from the Vehicle page, the MQTT sensors and ABRP
  telemetry (battery/cabin/AC-target temperatures were already correct).

## [1.3.1] — 2026-06-04

### Changed
- Lower the charge-detection current threshold from 3.0 A to 2.0 A so low-power
  home charges (and the tail end of a charge) are still detected as charging. The
  regen detection threshold is separate and unaffected.

## [1.3.0] — 2026-06-04

### Added
- **ABRP (A Better Route Planner) live telemetry** — optional. Enable it and paste
  your personal ABRP token in Settings, and the car's live data (SOC, position,
  speed, power, temperatures…) is forwarded to ABRP for live route planning. Off
  by default; nothing is sent without a token.
- **MQTT → Home Assistant bridge** — optional. Configure a broker in Settings and
  the car is published to Home Assistant via MQTT Discovery as native entities:
  sensors (SOC, range, individual tyres, temperatures, charge…), binary sensors
  (doors/windows/lock/charging), a GPS tracker, command buttons (lock/unlock,
  trunk, find car) and a climate switch. TLS supported. Off by default.

## [1.2.0] — 2026-06-04

### Added
- **Charge type confirmation.** A new charge is no longer silently assumed to be
  "Home": until you set its type it shows a "To confirm" badge (with a "What type
  of charge?" prompt), and the Charges page shows how many are still pending. A
  charge enters the wallbox comparison only once you confirm it as Home.

### Changed
- **The wallbox comparison is now scoped to Home charges**, so it stays correct
  with multiple EVs sharing one wallbox and with public/away charging. History,
  totals and the per‑charge overlay only consider Home charges (a wallbox charges
  one car at a time, so a Home session means this car was on the wallbox);
  public/away and unconfirmed charges are excluded.
- The wallbox **live panel** now shows session metrics only while the car is
  plugged in — otherwise the live reading could be another vehicle on the same
  wallbox. Session cost and max available power are always shown.

## [1.1.1] — 2026-06-04

### Fixed
- **Wallbox in add‑on mode** — the add‑on now correctly detects the Home Assistant
  Supervisor token. On the s6‑overlay base images the Supervisor‑provided
  environment (including `SUPERVISOR_TOKEN`) isn't passed to the service process,
  so the add‑on fell back to the standalone URL+token form and showed "not
  connected". `run.sh` now loads it from the s6 container environment, and logs
  whether the HA API is available at startup.

## [1.1.0] — 2026-06-04

### Added
- **Wallbox integration (Home Assistant)** — optional. Pair a wallbox already in
  Home Assistant to get a dedicated **Wallbox** page with: a live panel (power,
  status, session energy, charging speed, max available power) plus the session
  cost; a control to set the wallbox max charging current; and an **AC‑vs‑DC
  comparison** per charge session — kWh delivered by the wallbox vs kWh into the
  battery, with charging efficiency — as a year/month/day history with an
  expandable power chart. Connects automatically via the Supervisor API when run
  as an add‑on (any external access mode — HTTP, HTTPS, Nabu Casa), or via an HA
  URL + a Long‑Lived Access Token when standalone (self‑signed HTTPS is fine).
  Enable and configure it in **Settings → Wallbox present** (live connection
  status + an entity picker limited to your wallbox's own sensors).

### Changed
- **Trips page redesign** — trip rows show a remaining/used SOC bar, a coloured
  efficiency pill and a route thumbnail; the dashboard gained four summary tiles
  (total distance, trips, average efficiency, regen). Trip distance now comes from
  the odometer delta (more accurate than GPS).
- **Vehicle page** restyled to match the rest of the app (slate cards/tiles).
- **Settings** reorganised into three columns; the Wallbox card stays minimal when
  disabled and reveals the HA connection + an expandable sensor list when enabled.
- Quantities across the UI are shown to at most two decimals, at full precision
  (no over‑rounding).

## [1.0.8] — 2026-06-02

### Added
- **Charging-power chart** in the Charges page. Each session has an expandable
  "Charging power" section that lazy-loads an inline chart of power over time
  (with SOC on a second axis). Power is the same value as the official
  charging-power reading (battery voltage × current) and is kept at full
  precision so the real curve is visible — most useful on DC fast charging.

### Changed
- **Settings layout**: cards now use a masonry column layout, removing the empty
  gap that appeared under shorter cards (e.g. Language) next to taller ones.

## [1.0.7] — 2026-06-02

### Added
- **Language selector in Settings**. The language could previously only be chosen
  in the initial setup wizard, so already-installed users had no way to switch.
  Settings now has a language dropdown (🇬🇧 English / 🇮🇹 Italiano / 🇫🇷 Français);
  changing it saves immediately and reloads the page in the new language.

## [1.0.6] — 2026-06-02

### Added
- **French language** (🇫🇷). The setup wizard now offers three languages — English,
  Italian and French — with three flag buttons and auto-detection of French
  browsers. The whole app is translated: overview, trips, charges, commands,
  statistics, the vehicle page, and both wizard steps (certificate + account login).

### Fixed
- Two certificate-step labels (`app.crt`, `app.key`) were hard-coded in English
  regardless of the chosen language; they are now translated (this also fixes
  Italian, where they were previously shown in English too).

## [1.0.5] — 2026-06-02

### Fixed
- **Poller self-recovery**: if the account TLS certificate temp file vanished from
  `/tmp` (every poll then failed with "Could not find the TLS certificate file"),
  the poller stayed stuck in an error loop indefinitely. It now forces a fresh
  login to re-create the certificate on cert/auth/token/connection errors (rate-
  limited to ~once per minute). Also recovers from auth/token drops.

## [1.0.4] — 2026-06-02

### Fixed
- **Local time in the UI**: trip/charge times were shown in UTC; they are now
  converted to the local timezone (`TZ`, which Home Assistant passes to add-ons
  automatically; standalone Docker sets `TZ` in compose). Added `tzdata`.
- **Trip fragmentation**: a drive was split into many records because a trip ended
  after just ~20s of zero speed. Trip detection is now gear-based and matches the
  HA reference: a trip ends only when gear **P** is held ~1 min (red lights / brief
  stops in gear D no longer split it), and movements **< 0.5 km** are discarded.

## [1.0.3] — 2026-06-02

### Fixed
- **Statistics**: the "Consumption trend (6 weeks)" chart legend showed week
  dates as `MM-DD` (US-style); they are now formatted as `DD/MM`.

## [1.0.2] — 2026-06-02

### Added
- **Vehicle page**: new sidebar page with live tyre pressure (per corner, with
  low-pressure alarms), door and window open/closed states, panoramic roof and
  battery/cabin temperatures — styled as gradient status cards.

### Fixed
- `find_car` was calling a non-existent client method; now driven through the
  registered remote action so it reaches the car.
- Install docs spell out the exact add-on repository URL.

## [1.0.1] — 2026-06-01

### Fixed
- **Home Assistant ingress support**: the web UI now works inside the add-on
  panel. URLs are resolved against the ingress path via `<base href>` (from the
  `X-Ingress-Path` header) and all template/JS URLs are relative; server
  redirects carry the ingress prefix. Standalone is unaffected.

## [1.0.0] — 2026-06-01

First public release.

### Added
- Trip tracking with route map, distance, energy, efficiency and regen.
- Charge logging with AC/DC detection, energy added, power and distribution chart.
- Statistics: driving/AC/other energy split and a 6-week consumption trend (Leapmotor cloud).
- Remote control: lock, windows, trunk, panoramic roof, climate, find car, battery preheat.
- Two-step setup wizard: app certificate (upload/paste) + account login with EU model/battery auto-detect.
- Configurable polling (parked/driving), bilingual UI (EN/IT).
- Home Assistant add-on and standalone Docker deployment.

[1.0.2]: https://github.com/ProtossBlaster/leapmotor-mate/releases/tag/v1.0.2
[1.0.1]: https://github.com/ProtossBlaster/leapmotor-mate/releases/tag/v1.0.1
[1.0.0]: https://github.com/ProtossBlaster/leapmotor-mate/releases/tag/v1.0.0
