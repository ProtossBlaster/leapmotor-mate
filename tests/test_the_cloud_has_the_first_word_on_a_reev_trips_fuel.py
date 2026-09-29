"""On a range-extender the litres come from the car's own cloud first, and from the tank second.

Both figures are the car's. They disagree, and on 29/09/2026 we finally had a drive where a third
party settles it: @ebagnoli's 19/09 trip, for which the official Leapmotor app states **77 km,
0.3 kWh and 4.9 L**. Measured on his bundle:

* the cloud's per-trip record says `totalMileage` 77.0, `totalEnergy` 0.3, **`driveReevOil` 4.9** —
  the same three figures the app shows him;
* Mate's own tank arithmetic says **3.886 L**, 20.7 % lower.

Mate's figure is not sloppy: the tank fell from 32.825 L to 28.939 L, the car reported that level
**145 times inside the drive** at millilitre resolution, and the last reading before the trip and
the first after it give the identical 3.886 L — so nothing is lost at the edges and nothing is lost
to a coarse gauge. Two of the car's own measurements simply do not agree, and we cannot say from
here which is physically right.

What we CAN say is which one the owner sees. The app shows 4.9, so the cloud is what Mate has to
agree with, or every REEV owner reads a fifth off every figure and reports it as our defect.

🔑 **The tank figure is kept, not replaced.** `mate_fuel_l` carries it on every trip, the cloud's
value only wins where a record MATCHES under the safeguards the energy figure already uses, and
`fuel_source` says which one is on screen. A cloud that has never synced, a drive outside its
28-day window, or a record that fails the match leaves the tank's answer exactly where it was.

⚠️ `driveReevOil` ABSENT is not `driveReevOil` 0.0. A BEV reports 0.0 and means it; a record
without the field is no answer, and takes the tank's.
"""
import json
from datetime import datetime, timedelta, timezone

import db as D
import db_reader

VIN = "LVIN0000000000001"
START = datetime(2026, 9, 19, 14, 54, tzinfo=timezone.utc)
MINUTES = 80
KM = 77.0
# His tank, as the car reported it: 69.1 % → 60.9 %, 32.825 L → 28.939 L = 3.886 L.
TANK = dict(fuel_start_pct=69.1, fuel_end_pct=60.9, fuel_start_l=32.825, fuel_end_l=28.939)
MATE_L = 3.886
CLOUD_L = 4.9


def _install(tmp_path, monkeypatch, *, records=(), is_reev="1", tank=TANK, km=KM):
    """One 77 km generator drive, plus whatever cloud records the case needs.

    `records` = [(minutes_from_START, duration_min, km, oil)] — `oil` None leaves the field OUT of
    the payload, which is not the same as 0.0 and is tested apart."""
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,?,'C10')", (VIN,))
    for key, value in (("is_reev", is_reev), ("timezone", "UTC"), ("setup_complete", "1")):
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
    end = START + timedelta(minutes=MINUTES)
    cols = dict(vehicle_id=1, started_at=START.isoformat(), ended_at=end.isoformat(),
                distance_km=km, duration_min=MINUTES, start_soc=80, end_soc=62,
                ec_kwh=0.3, ec_stable=1, outside_temp_start_c=18.0,
                outside_temp_end_c=18.0, **(tank or {}))
    c.execute("INSERT INTO trips (id, %s) VALUES (1, %s)"
              % (", ".join(cols), ", ".join("?" * len(cols))), tuple(cols.values()))
    # A sampled trail, so the generator's own footprint can be walked like on a real trip.
    for k in range(MINUTES):
        c.execute("INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude,"
                  " speed_kmh, soc) VALUES (1,?,?,9.0,60,?)",
                  ((START + timedelta(minutes=k)).isoformat(), 45.0 + k * 0.01, 80 - k * 0.2))
    c.execute("CREATE TABLE IF NOT EXISTS api_lab_cloud_history_records"
              " (id INTEGER PRIMARY KEY, kind TEXT, payload_json TEXT)")
    for offset, duration, rkm, oil in records:
        a = START + timedelta(minutes=offset)
        b = a + timedelta(minutes=duration)
        payload = {"vin": VIN, "routeStartTs": int(a.timestamp() * 1000),
                   "routeEndTs": int(b.timestamp() * 1000),
                   "totalEnergy": 0.3, "totalMileage": rkm, "maxSpeed": 120}
        if oil is not None:
            payload["driveReevOil"] = oil
        c.execute("INSERT INTO api_lab_cloud_history_records (kind, payload_json) VALUES ('mileage',?)",
                  (json.dumps(payload),))
    c.commit()
    pdb._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return db_reader


def _trip(tmp_path, monkeypatch, **kw):
    return _install(tmp_path, monkeypatch, **kw).get_trips(limit=10)[0]


# ── the calibrated case ───────────────────────────────────────────────────────

def test_the_cloud_figure_is_the_one_shown(tmp_path, monkeypatch):
    t = _trip(tmp_path, monkeypatch, records=[(0, MINUTES, KM, CLOUD_L)])
    assert t["fuel_source"] == "cloud"
    assert t["fuel_used_l"] == CLOUD_L
    assert t["cloud_fuel_l"] == CLOUD_L


def test_the_tanks_answer_is_kept_beside_it(tmp_path, monkeypatch):
    """Not replaced: the disagreement is the interesting part, and a figure we dropped is a figure
    we cannot show the owner when he asks why the two differ."""
    t = _trip(tmp_path, monkeypatch, records=[(0, MINUTES, KM, CLOUD_L)])
    assert t["mate_fuel_l"] == MATE_L


def test_the_consumption_follows_the_litres_that_won(tmp_path, monkeypatch):
    """4.9 L over 77 km is 6.4 L/100km, not the 5.0 the tank's 3.886 gave — a figure left on the old
    numerator is worse than no figure, because it looks right."""
    t = _trip(tmp_path, monkeypatch, records=[(0, MINUTES, KM, CLOUD_L)])
    assert t["fuel_l_100km"] == round(CLOUD_L / KM * 100, 1)


# ── and every way it must fall back ───────────────────────────────────────────

def test_without_a_cloud_record_the_tank_still_answers(tmp_path, monkeypatch):
    t = _trip(tmp_path, monkeypatch)
    assert t["fuel_source"] == "mate"
    assert t["fuel_used_l"] == MATE_L
    assert t["cloud_fuel_l"] is None


def test_a_record_without_the_field_is_no_answer(tmp_path, monkeypatch):
    """Absent is not zero. The match succeeds — the energy figure would take it — and the fuel does
    not, because there is nothing there to take."""
    t = _trip(tmp_path, monkeypatch, records=[(0, MINUTES, KM, None)])
    assert t["fuel_source"] == "mate"
    assert t["fuel_used_l"] == MATE_L


def test_a_record_saying_zero_is_an_answer(tmp_path, monkeypatch):
    """97 of the 98 records in his bundle read 0.0, and they are right: he drives on the battery.
    A drive the tank also calls electric must read 0.0 from the cloud, not fall back."""
    t = _trip(tmp_path, monkeypatch, records=[(0, MINUTES, KM, 0.0)],
              tank=dict(fuel_start_pct=69.1, fuel_end_pct=69.1,
                        fuel_start_l=32.825, fuel_end_l=32.825))
    assert t["fuel_source"] == "cloud"
    assert t["fuel_used_l"] == 0.0


def test_two_records_disagreeing_on_the_same_drive_are_refused(tmp_path, monkeypatch):
    t = _trip(tmp_path, monkeypatch,
              records=[(0, MINUTES, KM, CLOUD_L), (0, MINUTES, KM, 1.1)])
    assert t["fuel_source"] == "mate"


def test_a_record_that_covers_too_little_of_the_drive_is_refused(tmp_path, monkeypatch):
    """The same 80 % coverage the energy match demands: half a drive's litres are not the drive's."""
    t = _trip(tmp_path, monkeypatch, records=[(0, MINUTES // 2, KM, CLOUD_L)])
    assert t["fuel_source"] == "mate"


def test_a_record_whose_distance_disagrees_is_refused(tmp_path, monkeypatch):
    t = _trip(tmp_path, monkeypatch, records=[(0, MINUTES, KM * 2, CLOUD_L)])
    assert t["fuel_source"] == "mate"


# ── and the cars it must not touch ────────────────────────────────────────────

def test_a_plain_electric_car_gets_no_fuel_figures_at_all(tmp_path, monkeypatch):
    t = _trip(tmp_path, monkeypatch, is_reev="0", tank=None,
              records=[(0, MINUTES, KM, CLOUD_L)])
    assert t.get("fuel_source") is None
    assert not t.get("fuel_used_l")


def test_the_electric_figure_of_the_same_drive_is_left_alone(tmp_path, monkeypatch):
    """The fuel selection runs beside the energy one, not through it: a REEV's energy is still
    excluded from cloud selection, which is what `select_energy` has always done."""
    t = _trip(tmp_path, monkeypatch, records=[(0, MINUTES, KM, CLOUD_L)])
    assert t.get("energy_source") != "cloud"


# ── and the one number that must not disagree with itself ─────────────────────

def test_the_overview_total_says_what_the_trip_list_says(tmp_path, monkeypatch):
    """The card and the list are the same litres or they are a defect. `reev_fuel_summary` used to
    walk the trips itself, so a cloud figure on the list would have left the card on the tank's —
    3.9 L over a list of drives adding up to 4.9, under one label. Both now go through the same
    `_reev_trip_fuel` reader, which now takes `cloud_l` from one shared index."""
    d = _install(tmp_path, monkeypatch, records=[(0, MINUTES, KM, CLOUD_L)])
    listed = d.get_trips(limit=10)[0]
    summary = d.reev_fuel_summary()
    assert listed["fuel_used_l"] == CLOUD_L
    assert summary["total_l"] == CLOUD_L
    assert summary["avg_l_100km"] == round(CLOUD_L / KM * 100, 1)


def test_the_overview_total_falls_back_with_the_list(tmp_path, monkeypatch):
    """The mirror: no record, and both read the tank. A test that only checked the cloud case would
    pass on a summary hard-wired to the cloud and blind to its absence."""
    d = _install(tmp_path, monkeypatch)
    assert d.get_trips(limit=10)[0]["fuel_used_l"] == MATE_L
    assert d.reev_fuel_summary()["total_l"] == round(MATE_L, 1)


def test_the_detail_page_says_what_the_list_says(tmp_path, monkeypatch):
    """Two pages, one drive, one figure. The detail page works the litres out again from the group's
    own tank readings — the block it does that in carries a warning about exactly this — so it needs
    the same cloud answer or it prints the tank's while the row above prints the cloud's."""
    d = _install(tmp_path, monkeypatch, records=[(0, MINUTES, KM, CLOUD_L)])
    assert d.get_trip_detail(1)["fuel_used_l"] == d.get_trips(limit=10)[0]["fuel_used_l"] == CLOUD_L


def test_the_period_card_says_it_too(tmp_path, monkeypatch):
    """The card answers a window the reader chose, from the trips — its own fourth copy of the rule."""
    d = _install(tmp_path, monkeypatch, records=[(0, MINUTES, KM, CLOUD_L)])
    a = int((START - timedelta(days=1)).timestamp())
    b = int((START + timedelta(days=1)).timestamp())
    assert d.get_fuel_totals_between(a, b)["fuel_l"] == CLOUD_L


def test_the_temperature_chart_says_it_too(tmp_path, monkeypatch):
    """The fifth copy: the fuel band of consumption-against-temperature. It plots L/100km, so a
    numerator left on the tank's litres would put the point in the wrong place."""
    d = _install(tmp_path, monkeypatch, records=[(0, MINUTES, KM, CLOUD_L)])
    pts = d.get_efficiency_vs_temp(include_fuel=True, min_km=1.0).get("fuel_points") or []
    assert pts, "the fuel band drew nothing at all"
    # The reader rounds the rate to one decimal and the chart keeps it: 6.4, not 6.36.
    assert pts[0]["l"] == round(CLOUD_L / KM * 100, 1)


def test_a_refuel_mid_drive_is_no_longer_unknown_when_the_cloud_knows(tmp_path, monkeypatch):
    """The tank ended FULLER, so it cannot say what was burned and Mate has always answered
    "unknown" — correctly. The cloud can say, and does: the figure appears, and `fuel_refuelled`
    stays true so a page can still explain why the tank went up."""
    t = _trip(tmp_path, monkeypatch, records=[(0, MINUTES, KM, CLOUD_L)],
              tank=dict(fuel_start_pct=30.0, fuel_end_pct=95.0,
                        fuel_start_l=14.25, fuel_end_l=45.125))
    assert t["fuel_refuelled"] is True
    assert t["fuel_source"] == "cloud"
    assert t["fuel_used_l"] == CLOUD_L


# ── the guard that outlives all of the above ──────────────────────────────────

def test_every_reader_of_the_litres_asks_the_cloud():
    """`_reev_trip_fuel` is the one place the litres are decided, and five aggregates go through it:
    the trips list, the trip detail, the Overview card, the period card and the temperature chart.
    Each has to hand it the cloud's answer, or that one surface quietly keeps the tank's — and the
    file's own history is a list of exactly that happening (v3.6.6's fix left behind in two
    aggregates; the detail page reading the group's percentages and the parent's litres one line
    apart). A sixth reader added later fails here rather than on someone's screen.

    🔑 Read on the CALLS, not on a count: a count passes as soon as the numbers match, whichever
    calls they belong to."""
    import ast
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent / "web" / "db_reader.py").read_text()
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "_reev_trip_fuel"]
    assert len(calls) == 5, f"the readers of the litres changed: {len(calls)} calls, not 5"
    for call in calls:
        assert any(kw.arg == "cloud_l" for kw in call.keywords), \
            f"db_reader.py:{call.lineno} works the litres out without asking the cloud"


def test_the_index_is_built_once_per_read_and_not_per_trip():
    """The match walks every staged record, which is fine once and quadratic per trip — the same
    reason `_trip_fuel_rate_fn` exists. Measured on the lab's real database (598 trips, 189 records):
    0.1 ms on a plain electric car, where it correctly finds nothing, and 7.8 ms as a range-extender,
    where it matches the same 114 trips the energy selection matches."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent / "web" / "db_reader.py").read_text()
    for func in ("def get_trips(", "def reev_fuel_summary(", "def get_fuel_totals_between("):
        body = src.split(func, 1)[1].split("\ndef ", 1)[0]
        assert body.count("_cloud_fuel_index()") == 1, \
            f"{func} builds the index {body.count('_cloud_fuel_index()')} times"
        loop = body.index("for r in rows")
        assert body.index("_cloud_fuel_index()") < loop, \
            f"{func} builds the index inside its own loop"


def test_a_merged_drive_adds_up_its_segments(tmp_path, monkeypatch):
    """Two pieces joined into one drive: the parent's figure is the sum of both records, or nothing.
    The parent row alone is the FIRST segment only — the same rule the energy figure follows, and the
    walk is transitive because a merged trip can itself be merged into another."""
    path = str(tmp_path / "t.db")
    import json as _json
    import db as D
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,?,'C10')", (VIN,))
    for k, v in (("is_reev", "1"), ("timezone", "UTC"), ("setup_complete", "1")):
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (k, v))
    # 1 is the parent, 2 merged into 1, 3 merged into 2 — the nesting a one-level walk would miss.
    for tid, offset, parent in ((1, 0, None), (2, 30, 1), (3, 60, 2)):
        a = START + timedelta(minutes=offset)
        b = a + timedelta(minutes=25)
        c.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, duration_min,"
                  " start_soc, end_soc, fuel_start_pct, fuel_end_pct, merged_into_id)"
                  " VALUES (?,1,?,?,25.0,25,80,70,69.1,60.9,?)",
                  (tid, a.isoformat(), b.isoformat(), parent))
    c.execute("CREATE TABLE IF NOT EXISTS api_lab_cloud_history_records"
              " (id INTEGER PRIMARY KEY, kind TEXT, payload_json TEXT)")
    for offset in (0, 30, 60):
        a = START + timedelta(minutes=offset)
        b = a + timedelta(minutes=25)
        c.execute("INSERT INTO api_lab_cloud_history_records (kind, payload_json) VALUES ('mileage',?)",
                  (_json.dumps({"vin": VIN, "routeStartTs": int(a.timestamp() * 1000),
                                "routeEndTs": int(b.timestamp() * 1000), "totalEnergy": 0.3,
                                "totalMileage": 25.0, "driveReevOil": 1.5}),))
    c.commit()
    pdb._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    from trip_energy import cloud_fuel_by_trip
    import sqlite3
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    index = cloud_fuel_by_trip(con)
    assert index[1] == 4.5, f"the parent should carry all three segments' litres, got {index.get(1)}"


def test_an_empty_cloud_record_does_not_block_the_real_one(tmp_path, monkeypatch):
    """The cloud files a second record over the same drive that says nothing — 0 km and no litres —
    and the ambiguity rule then refuses BOTH.

    🔴 Measured on @ebagnoli's real history, which is the only reason this is here: **41 of his 98
    records carry 0 km**, none of them carries any fuel, and one sits across the tail of the 19/09
    drive. So the calibrated trip — the single drive in the bundle with a figure the owner can check —
    matched nothing at all, and the cloud-first change did precisely nothing on his install. A demo
    database invents no such records and showed a clean match; his did not.

    ⚠️ The rule is *no distance AND no litres*, not *no distance*. A record with 0 km and fuel on it
    is the generator charging a parked car — that fuel is real, Mate simply refuses to blame the
    driving distance for it (see `_reev_engine_on`). Such a record still counts, still creates the
    ambiguity, and the trip still falls back to the tank: an honest "cannot tell" instead of a
    silent loss. Only a record that describes NOTHING is dropped, because it cannot be anyone's
    answer.
    """
    empty = (MINUTES - 2, 2, 0.0, 0.0)                       # 0 km, 0 L, over the drive's tail
    t = _trip(tmp_path, monkeypatch, records=[(0, MINUTES, KM, CLOUD_L), empty])
    assert t["fuel_source"] == "cloud", "an empty record still blocks the real one"
    assert t["fuel_used_l"] == CLOUD_L


def test_a_parked_record_that_burned_fuel_still_counts(tmp_path, monkeypatch):
    """The mirror, and the reason the rule is not simply "drop 0 km": litres on a stationary record
    are real, so it is NOT dropped — it makes the drive ambiguous and the tank answers. Losing them
    quietly would be worse than saying we cannot tell."""
    parked = (MINUTES - 2, 2, 0.0, 0.8)                      # 0 km but 0.8 L really burned
    t = _trip(tmp_path, monkeypatch, records=[(0, MINUTES, KM, CLOUD_L), parked])
    assert t["fuel_source"] == "mate", "a stationary burn was dropped instead of blocking"
