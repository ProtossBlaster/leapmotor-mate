# Tyre pressure history on the Vehicle page — design

Date: 2026-11-15 · Origin: D#483 (michapr) · Approved approach: 1 (columns on `positions`), shown on the Vehicle page.

## Intent

In cold weather tyre pressure drops, often without the driver noticing. D#483 asks for statistics,
at least min/max per day, and for a way to read them against the outside temperature.
Today Mate shows the pressures only live (Vehicle page, MQTT, ABRP) and keeps none of them.

## Goals

- Store every tyre pressure the car sends, with the poll it came with.
- On the Vehicle page, under today's tyres: a chart of each tyre's daily min and max, plus the
  outside temperature on the same days when the user has it switched on.

## Non-goals

- The home-page warning asked in D#483: not decided (pending Silvio).
- Any history before the release: there is none to recover (the pressures were never stored;
  the only trace is 4 research readings from September, 2.61–2.75 bar).
- Changes to MQTT, ABRP or the live tyre card.

## Anti-goals

- A pressure the car did not send must never become 0 bar (NULL stays NULL, and a day with no
  reading is a gap, not a 0).
- No rounding of stored values; the chart prints what is stored.
- No outside temperature invented: when `outside_temp_enabled` is off, the temperature line is
  simply absent (on our own DB it is off: 0 rows with a temperature in the last 5 days).

## Facts measured (2026-11-15, mate-test, real DB)

- `positions` gets ~2,600–4,000 rows a day (one per poll, ~30 s).
- `positions.outside_temp` is filled only when `outside_temp_enabled` = 1 (`poller/main.py:1011`).
- `positions_retention_days` defaults to 0 (keep forever). A user who sets a retention loses the
  pressure history with the positions — same rule as every other signal on that row.
- Pressures arrive as kPa signals 2646 FL, 2653 FR, 2660 RL, 2667 RR; `client.py` already turns
  them into `tire_*_bar` (None when not sent).

## Approaches considered

1. **Four columns on `positions`** — `tire_fl_bar`, `tire_fr_bar`, `tire_rl_bar`, `tire_rr_bar`,
   REAL, NULL when not sent. Same pattern as every other signal; the temperature sits on the same
   row; full detail kept (a night's drop is visible); daily min/max is a query. **Chosen.**
2. A daily summary table (min/max per tyre, temperature at the minimum). Small, but needs its own
   day-boundary logic in local time and loses the detail inside a day.
3. `raw_signals_log`. Rejected: filled only in research/beta mode, pruned after 30 days.

## Design

- **Schema** (`poller/schema.py`): `_add_column` ×4 on `positions`, like `fuel_liters`.
- **Write** (`poller/db.py` `save_position`): the four `data.tire_*_bar` values, as they are.
- **Read** (`web/db_reader.py`): per day in the user's local time (the existing `_local_dt`/zone
  rules), for one vehicle: min and max of each tyre, and min/max of `outside_temp`; a day with no
  pressure at all is left out of the series (a gap), not given 0.
- **Page** (`vehicle.html`, under the tyre card): one chart, built with the existing
  `mateBandChart` (`web/static/band-chart.js`): a band for the pressures (4 tyres, min and max),
  a band for the outside temperature when present. Period selector (e.g. 30 / 90 days).
- **Texts**: every new label in all the app's languages, through `t()`.
- **Tests**: a pressure saved → read back; NULL stays NULL (not 0); day boundary in local time;
  no temperature → no temperature band. Each new test seen red once; suite in Docker.

## Left to the builder

- The exact SQL / Python split for the per-day grouping, and whether an index is needed (to be
  measured on the real DB's row count, not assumed).
- Colours and the period options of the chart (to be shown to Silvio before release).
