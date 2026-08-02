# Model-aware capability profile

Mate shows only what **this** car actually supports. Features the car is known not to have, or
proven not to actuate, are hidden from the UI and from MQTT — **never** the CORE telemetry that
trips, charges, reports and charts depend on.

Code: `web/capability_profile.py`, mirrored in `poller/` (same duplication convention as
`session_share.py` / `crypto.py`), so the web UI and the poller's MQTT bridge decide identically.
**32 features** are registered there; that module's docstring is the authority on the registry, this
document on the reasoning behind it.

## The two-axis model

A **SENSOR** (the car reports the state) and a **COMMAND** (we can actuate it remotely) are
**independent** — they're classified separately (e.g. `seat_heat` the sensor vs `seat_heat_cmd` the
command). A sensor can work while its command doesn't, or the reverse, so each gets its own verdict.

Verdicts: `working` (proven), `broken` (confirmed accepted-but-not-executed, or a dead sensor),
`untested` (unknown → shown; we never hide on a guess).

## Three gates, and why there are three

They were added in this order, each because the previous one could not answer a real case.

**1 — Per-VIN empirical verdicts.** Stored in `settings` as `capabilities_<vin>`, accumulated across
sessions: a live non-default value proves a sensor, an at-the-car probe proves a command. This is the
original mechanism and still the one that decides most things.

*Limit:* it only learns from the car in front of it. A feature the car simply doesn't have looks
identical to one never exercised, so it stays `untested` and stays visible.

**2 — `MODEL_ABSENT`, a per-model table.** For features a model does not physically have. Today it
holds one entry: the **T03** has no ventilated seats, no heated seats, no heated steering wheel and no
PREPARE right, so those are hidden on it (#144).

*Why a hand-kept table at all,* when the car declares its own abilities: because the **declaration
lies**. The European T03 declares `SEAT_HEATING` and `STEERING` — a shared-platform over-declaration
— while the Stellantis spec for its single trim lists only heated mirrors. Gating on what the car
says would have shown controls for hardware that isn't fitted.

**3 — `COMMAND_ABILITY`, gating on the declared ability.** A **whitelist**, used only where the
declaration has been proven reliable. One entry: **`unlock_charger` → ability 53**. It qualifies on
three concordant signals — the T03 omits the code, the official app hides the option there, and the
button no-ops on that car (#142). Needing no per-model table, it covers models nobody here owns.

**Climate is deliberately gated by neither 2 nor 3.** The T03 omits `AC_ON` (6) and cools anyway, and
declares `CLIMATE_ADVANCED` while ignoring some writes (#67). Its declarations are wrong in both
directions, so climate is decided only on direct empirical proof.

## CORE — always work, never hidden

soc, range, odometer, vehicle state, gear, speed, GPS, charge state/power, plug, inside & battery
temperature, tyres, doors, trunk, lock, sunshade, window open/closed, **READY** (signal `1258`
`bcmKeyPositionOn3`, raised only by the physical key).

A 0 on one of these means parked / closed / idle, not broken. Hiding them would break Mate's own
trips, charges, reports and charts, so a `broken` verdict on a CORE feature is ignored by design.

## What the API does and doesn't do — empirical findings

Tested live on the car: commands through the public `leapmotor-api` client, effects read back from
the fresh signals. The hard cases (A/C full-off, comfort) were cracked on-car plus payloads captured
by **@kerniger** (leapmotor-ha #41/#42); we shared the B10 READY PID `1258` back, and the A/C-off
finding went upstream as **markoceri/leapmotor-api#3** (closed).

### Climate

- Quick **COOL / HEAT / VENTILATION** (cmd 170, `operate=manual`): work.
- Full A/C **OFF** on the B10: `ac_switch` + `{"operate":"off"}`, which drives `1938 acSwitch → 0`
  (confirmed on-car, ≤3 s). The library's old `ac_off()` sent `operate=close`, which on the B10 only
  flips HVAC to AUTO — that was the source of the old "can't turn it off" reports.
- Target **temperature** stepper 18–32 °C (cmd 170, auto cool/heat against cabin temp): works.
- **T03 only (#67):** the firmware silently ignores `operate=auto` writes — A/C button, temperature,
  fan and recirculation all no-op, while the cloud still answers `code:0`, so the acknowledgement
  means nothing. It **honours `operate=manual`**, so on the T03 an auto write is rewritten to manual.
  ⚠️ Derived from the works-vs-fails difference in the #67 logs (rossiadobe, Gr1m214), **not verified
  on-car by us** — nobody here has a T03. Switching a T03 off is still unsolved: neither
  `operate=off` nor `operate=close` does it (markoceri #9).

### Comfort — both axes work on the B10

Using @kerniger's payloads (leapmotor-ha 0.6.11):

- **Seat heating** (301) / **ventilation** (370): `{"position":"driver|copilot","level":"0..3"}` —
  actuates `2100`/`2118` (heat) and `2101`/`2119` (vent); levels 0–3 map exactly.
- **Steering-wheel heat** (320): ON `{"level":"2"}` / OFF `{"level":"1"}` — sensor `1816`.
- **Mirror heat** (440): ON `{"value":"2"}` / OFF `{"value":"1"}` — sensors `49`/`50`.

The payloads are shared across C10/B10; our earlier failure was sending `position` as a numeric index
instead of the string `"driver"` / `"copilot"`.

### Windows — the command works; the scale is per-model; the position sensor doesn't

`cmd 230` **actuates**, and it is **global**: all four windows move together, the API has no
per-window control. What differs is the native range (#62):

- **LEAP platform (B10, C10, B05): 0–10.** 10 is fully open and anything above is silently ignored.
  Confirmed on-car on the B10 (us) and the C10 (kerniger); B05 shares the platform and pack.
  On the B10, only `0 / 2 / 5 / 10` actually move the car — closed / vent / half / open — so it is
  four discrete stops, not a continuous range.
- **T03: 0–100**, continuous (per markoceri/leapconnect).

Mate presents one 0–100 % slider and maps it to the model's native scale (`_WINDOWS_SCALE`), snapping
the B10 to its valid stops. The quick button is 20 % — a vent gap.

The **opening-% sensor** (`3727/3728/1879/1880`) is **dead** on the B10: it reads 0 with the windows
physically open, verified on-car at 50 %. So `windows_pct` shows the last *commanded* position,
gated by the open/closed flag (`1693–1696`: 0 = closed, 2 = open), which does work.

### Charge-port cable unlock

Right 192. **Confirmed actuating on a real B10** (2026-06-08). Exposed on the Charges page and over
MQTT, gated on ability 53 per gate 3 above — so it is present where the car declares it and absent on
the T03, which doesn't.

### Still broken / not exposed

- **sentry** (`3636`, cmd 220): command accepted (`code=0`) but never actuates on the B10.
  ⚠️ Last checked at the time of the original characterisation; not re-tested since.
- **defrost**: engages heating, but signal `1945` never moves.
- **cmd_id recon:** `340` = native charge-limit, actuates (`{"chargesoc":80}`) · `410` ON3 is
  vehicle-gated (only the physical key raises `1258`) · `420` accepted but inert · `361` = read-only
  prepare-car schedule.
- **Not exposed at all on the B10:** outside/ambient temperature, tyre temperature, window opening-%.

## How it's wired

- `web/capability_profile.py` (+ the `poller/` copy): the registry plus `is_shown()` /
  `command_shown()`, with a parameterized settings accessor so the web app (`db_reader`) and the
  poller (`db.get_setting`) share one implementation.
- The **poller** writes the live comfort states each poll as `comfort_state_<vin>` in `settings`, so
  the web UI can display them and drive the comfort tiles.
- **MQTT discovery** publishes the working command buttons (climate incl. A/C Off, comfort, find car,
  unlock charge cable) and the comfort sensors, each gated per-VIN by `command_shown` — a button
  confirmed `broken` on a car has its retained config cleared, so Home Assistant drops it.
- The **Commands page** shows the comfort controls (sliders and toggles) and a battery / quick-actions
  card in the unified MDI-icon tile style.

## Open

- **sentry (220)** is still accepted-but-not-executed — worth asking @kerniger for that payload too,
  as he supplied the comfort ones.
- The B10's dead **window-position sensor**: nothing to do unless a firmware update revives it.
- Every verdict here except the T03 climate note came from a car standing in front of someone.
  Re-checking one means doing the same again — this document should not be updated from reasoning.

## Notes

Detailed reverse-engineering of the official app (static decompile and dynamic unpacking, both
blocked by the 360 Jiagu packer and its anti-emulator self-kill) is kept in local notes outside this
public repository, for legal reasons. This document covers only the empirical, behavioural findings,
which are already public via upstream issue #3.
