# Changelog

All notable changes to LeapMotor Mate are documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

## [1.9.0] — 2026-06-05

### Added
- **OIDC login with a user allow-list (standalone).** Mate can now require sign-in
  through any OpenID Connect provider (Keycloak, Authentik, Google, Entra ID, …).
  Only the identities listed in **`OIDC_ALLOWED_USERS`** (matched against the token's
  email / username / sub) may log in — everyone else is rejected with an
  "access denied" page. Configured entirely via **environment variables**
  (`OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_ALLOWED_USERS`,
  `OIDC_SESSION_SECRET`, optional `OIDC_REDIRECT_URI` / `OIDC_COOKIE_SECURE`), so the
  auth boundary is never configured through the UI it protects. When enabled, the
  guard also covers the first-run setup wizard. See README → **Authentication**.
  - In **Home Assistant add-on** mode OIDC is skipped — HA ingress already
    authenticates the panel (detected via `SUPERVISOR_TOKEN`).

### Security
- **CSRF protection.** State-changing requests (commands, settings, navigation) are
  now rejected unless their `Origin`/`Referer` is same-origin, and the session cookie
  is `SameSite=Lax` — so a malicious web page can no longer forge car commands.
  (Standalone only; HA ingress already enforces this in add-on mode.)
- **No more third-party CDN scripts.** Tailwind, htmx, ApexCharts, Chart.js and
  Leaflet are now **self-hosted** under `web/static/vendor/` (pinned versions) instead
  of being fetched from `unpkg`/`jsdelivr`/`cdn.tailwindcss.com`. A compromised CDN can
  no longer inject JavaScript into the UI, and the browser no longer discloses your IP
  to those CDNs. (Map tiles still load from OpenStreetMap.)
- **Loopback bind by default.** A bare `python main.py` now binds `127.0.0.1`
  (override with `WEB_HOST`), and `docker-compose.yml` publishes the port on the host
  **loopback only** (`127.0.0.1:4000:4000`). The car-controlling UI is no longer
  LAN/internet-open out of the box; expose it deliberately behind a proxy + OIDC.

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
