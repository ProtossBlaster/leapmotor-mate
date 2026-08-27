"""The Report's "cost per 100 km" is the SUM of the per-trip costs — the same machinery the Trips
and Statistics pages use — not charge_cost / km (@michapr, #36 follow-up).

charge_cost/km counts every euro put into the battery over the kilometres driven, so it climbs when
you CHARGE with the car parked (michapr: 2.28 → 2.40 after a ~3 kWh top-up, no driving) and folds in
energy charged but not yet driven. The DRIVING cost is per trip: the trip's electric energy at the
battery's blended €/kWh at that moment (`_localized_trips` → `t["cost"]`), plus the tank's WAC for a
range-extender (`t["fuel_cost"]`). Summing those is exactly `node["cost"]` in `_totals_add`, so the
three pages price a month the same way.
"""
import pathlib

import db as D
import db_reader
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
REPORT_HTML = (ROOT / "web" / "templates" / "report.html").read_text(encoding="utf-8")


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    database = D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    database._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'V','C10')")
    database._conn.commit()
    return database


def _charge(db, day, cost, kwh, ss=20, es=80):
    db._conn.execute(
        "INSERT INTO charges (vehicle_id, started_at, ended_at, start_soc, end_soc, "
        "energy_added_kwh, cost, location_type) VALUES (1,?,?,?,?,?,?,'HOME')",
        (f"2026-07-{day:02d}T09:00:00+00:00", f"2026-07-{day:02d}T11:00:00+00:00", ss, es, kwh, cost))
    db._conn.commit()


def _trip(db, day, km, eff):
    db._conn.execute(
        "INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc, "
        "efficiency_kwh_100km, duration_min) VALUES (1,?,?,?,70,55,?,30)",
        (f"2026-07-{day:02d}T08:00:00+00:00", f"2026-07-{day:02d}T09:00:00+00:00", km, eff))
    db._conn.commit()


def _july(db):
    return db_reader._collect_monthly_buckets()["2026-07"]


def test_the_electric_cost_is_the_sum_of_the_per_trip_costs(db):
    _charge(db, 1, cost=6.0, kwh=30.0)             # a priced charge → a blended rate before the trips
    _trip(db, 5, km=100.0, eff=15.0)
    _trip(db, 6, km=100.0, eff=18.0)
    priced = [t for t in db_reader._localized_trips(db_reader.get_trips(1_000_000))
              if t["_dt"].strftime("%Y-%m") == "2026-07"]
    expected = round(sum(t.get("cost") or 0 for t in priced), 2)
    assert expected > 0, "premise: the trips must be priced at the battery's blended rate"
    assert _july(db)["elec_cost_driven"] == pytest.approx(expected)


def test_charging_with_the_car_parked_does_not_move_the_driving_cost(db):
    """michapr's exact complaint: the cost per 100 km rose from a charge alone. The driving cost is
    per trip, so a top-up with no new trip leaves it untouched — while charge_cost/km would climb."""
    _charge(db, 1, cost=6.0, kwh=30.0)
    _trip(db, 5, km=200.0, eff=15.0)
    before = _july(db)
    _charge(db, 20, cost=5.0, kwh=25.0)            # a top-up AFTER the driving, car parked
    after = _july(db)
    assert after["charge_cost"] > before["charge_cost"], "premise: the charge really was added"
    assert after["elec_cost_driven"] == before["elec_cost_driven"]   # the DRIVING cost did not move
    assert after["total_km"] == before["total_km"]


def test_the_tile_reads_the_driving_cost_not_charge_cost():
    assert "(c.elec_cost_driven + c.fuel_cost_burned) / dist_val(c.total_km) * 100" in REPORT_HTML
    assert "c.charge_cost / dist_val(c.total_km)" not in REPORT_HTML
    assert "c.charge_cost + c.fuel_cost_burned" not in REPORT_HTML
