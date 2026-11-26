"""Under the temperature chart, the slope of a straight line too — D#498, @Dawe02.

The curve is a cubic (5.2.0) and its four coefficients say little to someone reading the page.
What an owner wants to carry away is one figure: so many kWh/100 km for every 10 °C. That is the
slope of the straight line through the same trips, weighed by their kilometres like the curve. It
is printed with its own r², which tells how little of the spread a line explains (4 % on one
install's 412 trips, against the cubic's 29.7 %). It is not drawn and does not go to MQTT.
"""
import pathlib

import pytest

import db as D
import db_reader

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "web" / "templates"


def _setup(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    database = D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    database._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1, 'VINX', 'B10')")
    database._conn.commit()
    return database


def _trip(db, tid, eff, t, km=50.0):
    db._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km,"
        " efficiency_kwh_100km, outside_temp_start_c, outside_temp_end_c)"
        " VALUES (?,1,?,?,?,?,?,?)",
        (tid, f"2026-07-{1 + tid // 24:02d}T{tid % 24:02d}:00:00+00:00",
         f"2026-07-{1 + tid // 24:02d}T{tid % 24:02d}:30:00+00:00", km, eff, t, t))
    db._conn.commit()


def test_a_straight_line_gives_its_slope_per_ten_degrees(tmp_path, monkeypatch):
    db = _setup(tmp_path, monkeypatch)
    for i, t in enumerate(range(-5, 40, 3)):
        _trip(db, i + 1, 20.0 - 0.15 * t, float(t))
    line = db_reader.get_efficiency_vs_temp()["line"]
    assert line["slope10"] == pytest.approx(-1.5, abs=1e-9)
    assert line["r2"] == 1.0


def test_the_line_weighs_each_trip_by_its_kilometres(tmp_path, monkeypatch):
    """Two clusters that disagree: the long trips pull the line. The weighted least-squares slope is
    Σw(t−t̄)(y−ȳ) / Σw(t−t̄)², t̄ and ȳ weighted means — computed here by hand."""
    db = _setup(tmp_path, monkeypatch)
    pts = [(0.0, 20.0, 100.0), (2.0, 18.0, 100.0), (4.0, 19.0, 5.0), (6.0, 15.0, 5.0),
           (8.0, 17.0, 100.0), (10.0, 14.0, 5.0), (12.0, 16.0, 100.0), (14.0, 12.0, 5.0)]
    for i, (t, y, km) in enumerate(pts):
        _trip(db, i + 1, y, t, km=km)
    w = sum(p[2] for p in pts)
    tm = sum(p[0] * p[2] for p in pts) / w
    ym = sum(p[1] * p[2] for p in pts) / w
    slope = (sum(p[2] * (p[0] - tm) * (p[1] - ym) for p in pts)
             / sum(p[2] * (p[0] - tm) ** 2 for p in pts))
    line = db_reader.get_efficiency_vs_temp()["line"]
    assert line["slope10"] == pytest.approx(10 * slope, rel=1e-9)
    assert slope != pytest.approx(-0.5, abs=0.01)    # the unweighted fit would land elsewhere


def test_no_line_where_there_is_no_curve(tmp_path, monkeypatch):
    """The page shows the line beside the curve; with too few trips for the curve there is neither."""
    db = _setup(tmp_path, monkeypatch)
    for i, t in enumerate(range(0, 21, 3)):           # 7 trips
        _trip(db, i + 1, 20.0 - 0.15 * t, float(t))
    out = db_reader.get_efficiency_vs_temp()
    assert out["trend"] is None and out["line"] is None


def test_the_line_does_not_go_to_mqtt(tmp_path, monkeypatch):
    db = _setup(tmp_path, monkeypatch)
    for i, t in enumerate(range(-5, 40, 3)):
        _trip(db, i + 1, 20.0 - 0.15 * t, float(t))
    db_reader.store_efftemp_trends()
    raw = db_reader.get_setting(db_reader.efftemp_trend_key("VINX"), "")
    assert "slope10" not in raw


def test_the_page_prints_both_lines():
    src = (TEMPLATES / "statistics.html").read_text(encoding="utf-8")
    assert "stats_efftemp_line_elec" in src and "efftemp.line" in src
    assert "stats_efftemp_line_fuel" in src and "efftemp.fuel_line" in src
