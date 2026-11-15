# Tyre pressure history Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store every tyre pressure the car sends and show each tyre's daily min/max (plus outside temperature when enabled) in a chart on the Vehicle page.

**Architecture:** Four nullable REAL columns on `positions`, written by `save_position` from the values `client.py` already parses. `db_reader` groups them per local day for the selected vehicle; a new HTML partial route renders them with the existing `mateBandChart` under the live tyre card.

**Tech Stack:** Python / SQLite / FastAPI + Jinja2 + htmx, ApexCharts via `web/static/band-chart.js`, pytest (Docker only).

**Spec:** `docs/superpowers/specs/2026-11-15-tyre-pressure-history-design.md`

## Global Constraints

- A pressure the car did not send is NULL, never 0; a day with no pressure is a gap, never 0.
- No rounding of stored values; the chart prints what is stored (display decimals only, 2 for bar, 1 for °C).
- Outside temperature appears only when the rows carry one; never invented.
- Days are the user's local days: `_local_tz()` resolved ONCE per call, passed to `_local_dt(tz=...)` (→ `tests/test_the_clock_is_not_read_once_per_row.py`).
- Reads scoped to `_current_vehicle_id()`.
- Every new UI string through `t()`, in all 9 locales (`web/locales/{cs,de,en,es,fr,it,nl,pl,pt-PT}.json`).
- Every new test seen RED once before the code. Suite runs only in Docker (`mate-pw425` image).
- Commits: no `Co-Authored-By` trailer (Mate repo rule). No push, no tag: release is Silvio's call.
- Never `git checkout --`.

## Review Focus

- A car that sends some tyres and not others (one sensor dead): that tyre's line has gaps, the others don't; no 0 bar.
- A poll at 23:59 local and one at 00:01 local land on two different days even when they share the UTC date (and vice versa, around the DST change).
- Two cars on the account: the chart of the selected car never mixes the other car's rows.
- An install upgraded today: no history yet → the card says so instead of an empty/broken chart.
- Outside temperature switched on for only some days: temperature band shows those days, gaps on the others.

---

### Task 1: Store the four pressures on every position row

**Files:**
- Modify: `poller/schema.py` (migration block near line 488, beside `fuel_liters`)
- Modify: `poller/db.py:1541` `save_position`
- Test: `tests/test_tyre_pressures_are_stored_with_each_reading.py`

**Interfaces:**
- Produces: columns `positions.tire_fl_bar`, `tire_fr_bar`, `tire_rl_bar`, `tire_rr_bar` (REAL DEFAULT NULL).
- Consumes: `client.VehicleData.tire_*_bar` (`poller/client.py:80-83`, None when not sent; signals 2646 FL, 2653 FR, 2660 RL, 2667 RR in kPa).

- [ ] **Step 1: Write the failing tests** (seed with `_seed` from `tests/test_missed_charge_scan.py`, frame via `client._parse_signal("VIN1", sig)`):

```python
def test_the_four_pressures_are_stored_in_bar(tmp_path, monkeypatch):
    db = _seed(tmp_path, monkeypatch)
    sig = {"100003": 60.0, "1318": 1000.0, "2646": 250, "2653": 252, "2660": 248, "2667": 251}
    db.save_position(1, client._parse_signal("VIN1", sig))
    assert db._conn.execute("SELECT tire_fl_bar, tire_fr_bar, tire_rl_bar, tire_rr_bar "
                            "FROM positions").fetchone() == (2.50, 2.52, 2.48, 2.51)

def test_a_tyre_the_car_did_not_send_is_null_not_zero(tmp_path, monkeypatch):
    # only 2646 sent → FL stored, FR/RL/RR NULL
    ...  assert row == (2.50, None, None, None)

def test_an_old_database_gets_the_columns(tmp_path):
    # create positions WITHOUT the columns, run the migration (schema.init / Database(path)),
    # assert the four names are in PRAGMA table_info(positions)
```

Exact kPa→bar conversion must match `client._bar` (`poller/client.py:485`); if it is not `/100`, take the expected values from it, not from this plan.

- [ ] **Step 2: Run, expect RED** (`no such column: tire_fl_bar`)

Run: `docker run --rm -v "$PWD":/app -w /app mate-pw425 pytest tests/test_tyre_pressures_are_stored_with_each_reading.py -v`

- [ ] **Step 3: Implement** — 4× `_add_column(conn, "ALTER TABLE positions ADD COLUMN tire_xx_bar REAL DEFAULT NULL")`; add the 4 columns and `data.tire_xx_bar` values (as they are, no `or None`: 0.0 is not expected but must not be hidden either) to the INSERT in `save_position`. Also add the 4 columns to the `CREATE TABLE positions` in `schema.py` so a fresh DB has them.

- [ ] **Step 4: Run, expect GREEN**, then the full suite in Docker (the INSERT is used by many tests).

- [ ] **Step 5: Commit** — `feat(vehicle): store the tyre pressures with every reading (D#483)`

---

### Task 2: Daily min/max per tyre, in local days

**Files:**
- Modify: `web/db_reader.py` (new function, near `get_charge_power_curve` ~line 8665 or the vehicle readers)
- Test: `tests/test_tyre_pressure_days.py`

**Interfaces:**
- Consumes: the Task 1 columns, `positions.outside_temp`.
- Produces: `get_tyre_pressure_days(days: int) -> list[dict]` — one dict per local day that has at least one pressure OR temperature, oldest first:
  `{"day": "YYYY-MM-DD", "fl": (min, max) | None, "fr": ..., "rl": ..., "rr": ..., "temp": (min, max) | None}`.
  Window: rows with `recorded_at >= now - days` (UTC), selected vehicle only.

- [ ] **Step 1: Write the failing tests** (DB built like `tests/test_fuel_liters_measured.py::_db`, timezone via the `timezone` setting = `Europe/Rome`):

```python
def test_min_and_max_per_tyre_per_day(...):
    # 3 rows same local day, FL 2.40/2.55/2.50 → day["fl"] == (2.40, 2.55)
def test_a_null_reading_does_not_pull_the_minimum_to_zero(...):
    # FL 2.50, NULL, 2.45 → (2.45, 2.50)
def test_a_tyre_never_sent_that_day_is_none(...):
    # FR all NULL → day["fr"] is None
def test_the_day_is_the_local_day(...):
    # 2026-11-14T23:30Z and 2026-11-15T00:30Z in Europe/Rome are both 15/11 → one day
def test_only_the_selected_vehicle(...):
    # vehicle 2's rows ignored
def test_no_temperature_means_temp_none(...):
def test_a_day_with_nothing_is_absent(...):
    # a day of rows with all four tyres and outside_temp NULL → not in the list
```

- [ ] **Step 2: Run, expect RED** (`AttributeError: get_tyre_pressure_days`).

- [ ] **Step 3: Implement** — one SQL read (`SELECT recorded_at, tire_*, outside_temp FROM positions WHERE vehicle_id = ? AND recorded_at >= ? AND (tire_fl_bar IS NOT NULL OR ... OR outside_temp IS NOT NULL)`), group in Python by `_local_dt(r, tz=tz).date()` with `tz = _local_tz()` resolved once. Measure on the real DB (via `docker exec mate-test`, read-only) the time for 90 days; add an index only if over ~200 ms — `idx_positions_vehicle(vehicle_id, recorded_at)` already exists.

- [ ] **Step 4: Run, expect GREEN** + full suite.

- [ ] **Step 5: Commit** — `feat(vehicle): read each tyre's daily lowest and highest pressure`

---

### Task 3: The chart on the Vehicle page

**Files:**
- Create: `web/templates/partials/tyre_history_chart.html`
- Modify: `web/main.py` (new route after `/api/vehicle-status`, ~line 3035; demo branch)
- Modify: `web/templates/vehicle.html` (container after `#vehicle-status`, loaded once, NOT every 30 s)
- Modify: `web/locales/*.json` (9 files)
- Test: `tests/test_vehicle_page_tyre_history.py`

**Interfaces:**
- Consumes: `db_reader.get_tyre_pressure_days(days)`.
- Produces: `GET /api/tyre-history?days=30|90` → HTML partial. Any other `days` value → 30.

- [ ] **Step 1: Write the failing tests** (Starlette TestClient as in `tests/events_fixture.py:66`):

```python
def test_the_partial_carries_the_days_and_the_values(...):
    # seeded day → response 200, contains "2026-11-15" and 2.4 / 2.55 in the chart data JSON
def test_no_history_says_so(...):
    # empty DB → 200, contains t("tyre_history_empty") text, no chart element
def test_no_temperature_no_temperature_band(...):
    # temp None on every day → the chart data has no "temp" line
def test_an_unknown_period_falls_back_to_30(...):
def test_every_new_key_is_in_every_locale():
    # the new keys below present in all 9 locale files
```

- [ ] **Step 2: Run, expect RED** (404 on the route).

- [ ] **Step 3: Implement**
  - Route: reads `days` (30 or 90), calls the reader, renders the partial. Demo mode: render the partial with an empty list (no invented data).
  - Partial: card under the tyre card; title `tyre_history_title`; 30/90 buttons that re-`hx-get` the partial; `mateBandChart` with two bands — pressures (8 lines: min and max of each tyre, max solid / min dashed, one colour per tyre, unit "bar", 2 decimals) and temperature (min/max, unit "°C", 1 decimal) only if any day has `temp`. Gaps for `None`. Follow `partials/charge_power_chart.html` for how `mateBandChart` is called.
  - New keys: `tyre_history_title`, `tyre_history_empty`, `tyre_history_min`, `tyre_history_max`, `tyre_history_outside_temp`, `days_30`, `days_90` (reuse existing keys if `git grep` finds equivalents).
  - `vehicle.html`: `<div hx-get="api/tyre-history" hx-trigger="load" hx-swap="innerHTML">`.

- [ ] **Step 4: Run, expect GREEN** + full suite.

- [ ] **Step 5: Show it to Silvio** — test container on a host port with the real DB copy (`docker cp` of the `.py`/templates only, never `.db`), screenshot of the Vehicle page in light and dark, 30 and 90 days. Colours and wording are his OK before any release.

- [ ] **Step 6: Commit** — `feat(vehicle): a chart of each tyre's daily pressure on the Vehicle page (D#483)`
