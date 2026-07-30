"""One fill-up, one refuel — across a fuel gauge that arrives in steps (beta #17 @pdifeo).

MEASURED on his C10 REEV, 30 July 2026 08:39. The float gauge does not jump to the final level: it
climbs there in FOUR steps over twenty-eight seconds, and the car reports every one of them.

    08:35:51   70.2 %   33.390 L      (last reading before the pump)
    ── three minutes of nothing: the car is off, the tank is being filled ──
    08:39:44   78.0 %   35.691 L      +7.8 points
    08:39:53   87.0 %   40.939 L      +9.0
    08:40:03   98.1 %   46.603 L      +11.1
    08:40:12  100.0 %   47.500 L      +1.9

scan_fuel_refuels walked consecutive PAIRS and filed each rise as its own refuel. Three of the four
steps clear the 2 % floor, so one fill-up was recorded as THREE — which is exactly what he reported.

The floor is not the problem and tuning it cannot fix this: raise it to 5 points and you still get
three, lower it to 1 and you get four. The event has to be counted as a RUN, not as steps.

Absorbing the sub-floor tail matters for more than tidiness: stopping at 98.1 % would book
13.213 L against a real 14.110 L, losing nine-tenths of a litre off every full tank.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

import db as poller_db
import db_reader


# The real gauge trace, as (seconds from the first reading, level %, litres).
PDIFEO = [
    (0,   70.2, 33.390),
    (233, 78.0, 35.691),
    (242, 87.0, 40.939),
    (252, 98.1, 46.603),
    (261, 100.0, 47.500),
    (400, 99.9, 47.499),      # settling back down; the tank is not filling any more
    (700, 99.8, 47.444),
]


BASE = datetime(2026, 7, 30, 6, 35, 51, tzinfo=timezone.utc)   # his 08:35:51 local


def _at(secs):
    return (BASE + timedelta(seconds=secs)).isoformat()


def _db(tmp_path, monkeypatch, trace):
    path = str(tmp_path / "fuel.db")
    poller_db.Database(path)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'REEVTEST','C10')")
    for secs, pct, litres in trace:
        con.execute(
            "INSERT INTO positions (vehicle_id, recorded_at, soc, fuel_level_pct, fuel_liters) "
            "VALUES (1, ?, 50.0, ?, ?)", (_at(secs), pct, litres))
    con.commit(); con.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setattr(db_reader, "_current_vehicle_id", lambda: 1)
    return path


def _detected(path):
    con = sqlite3.connect(path); con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM fuel_detected ORDER BY ts").fetchall()
    con.close()
    return [dict(r) for r in rows]


# ── the defect ─────────────────────────────────────────────────────────────────

def test_one_fill_up_is_one_refuel(tmp_path, monkeypatch):
    path = _db(tmp_path, monkeypatch, PDIFEO)
    db_reader.scan_fuel_refuels(1)
    rows = _detected(path)
    assert len(rows) == 1, f"one fill-up was recorded as {len(rows)} refuels"


def test_the_whole_tank_is_counted_including_the_last_small_step(tmp_path, monkeypatch):
    """98.1 → 100.0 is +1.9 points, under the floor. It is still the same fill, and dropping it
    would book 13.213 L instead of 14.110."""
    path = _db(tmp_path, monkeypatch, PDIFEO)
    db_reader.scan_fuel_refuels(1)
    r = _detected(path)[0]
    assert abs(r["liters"] - 14.110) < 0.01, f"got {r['liters']} L"
    assert r["fuel_before_pct"] == 70.2
    assert r["fuel_after_pct"] == 100.0


def test_the_refuel_spans_from_before_the_pump_to_the_last_rise(tmp_path, monkeypatch):
    path = _db(tmp_path, monkeypatch, PDIFEO)
    db_reader.scan_fuel_refuels(1)
    r = _detected(path)[0]
    assert r["ts_from"] == _at(0),   r["ts_from"]    # the reading before the pump
    assert r["ts"]      == _at(261), r["ts"]        # the 100.0 % step, not the settling after it


# ── what it must still separate ────────────────────────────────────────────────

def test_two_fill_ups_with_driving_in_between_stay_two(tmp_path, monkeypatch):
    """Separated by the level going DOWN — the car was driven between them."""
    trace = [(0, 20.0, 9.5), (60, 55.0, 26.1), (120, 55.0, 26.1),
             (7200, 54.0, 25.6), (7260, 95.0, 45.1), (7320, 95.0, 45.1)]
    path = _db(tmp_path, monkeypatch, trace)
    db_reader.scan_fuel_refuels(1)
    assert len(_detected(path)) == 2


def test_two_fill_ups_with_no_reading_in_between_stay_two(tmp_path, monkeypatch):
    """The case the settle window is actually FOR. With the log this sparse — the car asleep, no
    polls — there is no falling or flat pair to close the first fill, so only the clock can: the
    second rise is four hours later, and four hours is nobody's petrol station."""
    trace = [(0, 20.0, 9.5), (60, 55.0, 26.1),
             (14400, 60.0, 28.5), (14460, 95.0, 45.1), (14520, 95.0, 45.1)]
    path = _db(tmp_path, monkeypatch, trace)
    db_reader.scan_fuel_refuels(1)
    assert len(_detected(path)) == 2, "the second fill was swallowed by the first"


def test_a_slow_climb_that_never_clears_the_floor_is_not_a_refuel(tmp_path, monkeypatch):
    """Each step is under 2 points AND every one of them holds, so the 'did the rise stick?' check
    cannot save us here — only the floor can. This is thermal drift on a warm tank, not a fill."""
    trace = [(0, 60.0, 28.5), (60, 61.0, 29.0), (120, 61.5, 29.2),
             (180, 62.0, 29.5), (240, 62.4, 29.6), (300, 62.8, 29.8)]
    path = _db(tmp_path, monkeypatch, trace)
    db_reader.scan_fuel_refuels(1)
    assert _detected(path) == []


def test_a_lone_spike_that_falls_straight_back_is_not_a_refuel(tmp_path, monkeypatch):
    trace = [(0, 40.0, 19.0), (60, 70.0, 33.3), (120, 40.1, 19.0), (180, 40.0, 19.0)]
    path = _db(tmp_path, monkeypatch, trace)
    db_reader.scan_fuel_refuels(1)
    assert _detected(path) == []
