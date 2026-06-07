# Changelog

All notable changes to LeapMotor Mate are documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

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
