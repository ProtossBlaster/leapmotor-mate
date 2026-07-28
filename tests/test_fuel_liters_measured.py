"""Litres come from the car, not from an assumed tank (beta #10, @gm27271).

Everything Mate ever showed in litres was a tank percentage multiplied by a constant — 50 L, "C10/B10
REEV both 50 L, confirmed", confirmed off a spec sheet and never measured. Signal **3263** is the
litres the car counts for itself, in millilitres, and it measures the tank as a side effect: dividing
3263 by 3235 across seven bundles from three owners gives **47.5 L on a C10** and **50.0 L on a B10**,
each constant to ±0.05 L, and the fullest C10 tank ever logged reads exactly 47 500 mL.

So every litre a C10 owner ever saw was 5.3 % too big — the fuel burned per trip, the L/100 km, the
"≈ X L" in the tank, the weights behind the blended €/L, and the litres of a detected refuel.

Two things are pinned here. That the car's own count WINS wherever it exists — a measurement beats an
estimate, and @gm27271's fill read 34.416 L against a pump ticket of 33.84. And that the fallback,
which every trip recorded before v2.14.1 will use for ever, is now per model instead of one wrong
number for everybody.
"""
import sqlite3

import pytest

import db_reader


def _db(tmp_path, monkeypatch, car_type, readings):
    """`readings` = [(recorded_at, pct, litres|None), …]."""
    path = str(tmp_path / "t.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE vehicles (id INTEGER PRIMARY KEY, vin TEXT, car_type TEXT)")
    con.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1, 'V', ?)", (car_type,))
    con.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("CREATE TABLE positions (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "vehicle_id INTEGER, recorded_at TEXT, fuel_level_pct REAL, fuel_liters REAL)")
    con.executemany("INSERT INTO positions (vehicle_id, recorded_at, fuel_level_pct, fuel_liters) "
                    "VALUES (1,?,?,?)", readings)
    con.commit(); con.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return path


def _t(m):
    return f"2026-07-20T{m // 60:02d}:{m % 60:02d}:00+00:00"


# ── The tank size, per model ─────────────────────────────────────────────────────

@pytest.mark.parametrize("car_type, expected", [
    ("C10", 47.5), ("C10 REEV", 47.5),          # measured on gm27271's and ebagnoli's cars
    ("B10", 50.0), ("B10 REEV", 50.0),          # measured on michapr's
])
def test_the_fallback_capacity_is_the_measured_one_per_model(tmp_path, monkeypatch, car_type, expected):
    _db(tmp_path, monkeypatch, car_type, [])
    assert db_reader.reev_tank_l() == expected


def test_an_unknown_model_keeps_the_old_assumption(tmp_path, monkeypatch):
    """A model nobody has measured has to fall somewhere; 50 L is where it always fell."""
    _db(tmp_path, monkeypatch, "B05", [])
    assert db_reader.reev_tank_l() == 50.0


def test_the_capacity_lookup_survives_a_database_that_cannot_answer(monkeypatch):
    """It is a fallback — it must never be the thing that raises."""
    monkeypatch.setattr(db_reader, "get_vehicle", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert db_reader.reev_tank_l() == 50.0


# ── The tank state on the Rifornimenti page ──────────────────────────────────────

def test_the_tank_level_uses_the_cars_own_litres(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, "C10", [(_t(0), 42.0, 19.95), (_t(60), 40.0, 19.0)])
    assert db_reader.latest_fuel_liters(1) == 19.0


def test_without_them_it_falls_back_to_the_models_capacity(tmp_path, monkeypatch):
    """A car from before v2.14.1: percentage only. 40 % of a C10 is 19 L, not 20."""
    _db(tmp_path, monkeypatch, "C10", [(_t(0), 40.0, None)])
    assert db_reader.latest_fuel_liters(1) is None
    assert round(db_reader.latest_fuel_pct(1) / 100.0 * db_reader.reev_tank_l(), 1) == 19.0


# ── A detected refuel ────────────────────────────────────────────────────────────

_RISE = [(_t(600), 18.0, 8.55), (_t(660), 18.0, 8.55), (_t(720), 43.0, 20.43),
         (_t(780), 42.6, 20.24), (_t(840), 42.0, 19.95)]


def test_a_detected_refuel_reports_the_litres_the_car_counted(tmp_path, monkeypatch):
    """20.43 − 8.55 = 11.88 measured, which here happens to agree with the percentage against a
    correct 47.5 L tank — the point is that it no longer DEPENDS on the tank being right."""
    _db(tmp_path, monkeypatch, "C10", _RISE)
    assert db_reader.scan_fuel_refuels(1) == 1
    (d,) = db_reader.list_fuel_detected(1)
    assert abs(d["liters"] - 11.88) < 1e-6


def test_the_measurement_wins_over_the_percentage(tmp_path, monkeypatch):
    """Where the two disagree the car's counter is the one to believe: it resolves to millilitres,
    while the gauge percentage steps at 0.1 % — and it is the number that argues with a pump."""
    rise = [(_t(600), 18.0, 8.55), (_t(660), 18.0, 8.55), (_t(720), 43.0, 22.97),
            (_t(780), 42.6, 22.80), (_t(840), 42.0, 22.50)]
    _db(tmp_path, monkeypatch, "C10", rise)
    db_reader.scan_fuel_refuels(1)
    (d,) = db_reader.list_fuel_detected(1)
    assert abs(d["liters"] - 14.42) < 1e-6            # 22.97 − 8.55, NOT 25 % × 47.5
