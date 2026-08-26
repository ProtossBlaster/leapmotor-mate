"""The monthly report stops being electric-only on a range-extender (Silvio's call, beta #11/#22).

Until now the report had exactly one REEV branch — it HID the regen tile — and added nothing. An
owner who spent half the month on the generator got a report about the other half, with no litres,
no L/100 km and no fuel spend anywhere.

Two questions, kept apart on purpose:
  • litres BURNED — from the trips, over the generator-on distance
  • litres and € BOUGHT — from the refuels the owner typed in, their own table and their own pass

A tank is filled on one day and burned over the following fortnight, so a month can hold a refuel
and no engine-on driving, or engine-on driving and no refuel. Adding the two would answer neither.
"""
import json
import pathlib

import db as D
import db_reader
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
REPORT_HTML = (ROOT / "web" / "templates" / "report.html").read_text()


@pytest.fixture
def reev(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    database = D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    database._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'V','C10')")
    database.set_setting("is_reev", "1")
    database._conn.commit()
    return database


def _trip(db, km, fuel_from, fuel_to, day=5):
    db._conn.execute(
        "INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc,"
        " fuel_start_pct, fuel_end_pct) VALUES (1,?,?,?,80,70,?,?)",
        (f"2026-07-{day:02d}T08:00:00+00:00", f"2026-07-{day:02d}T09:00:00+00:00",
         km, fuel_from, fuel_to))
    db._conn.commit()


def _refuel(db, litres, cost, day=20):
    db_reader._ensure_fuel_purchases(db._conn)
    db._conn.execute(
        "INSERT INTO fuel_purchases (vehicle_id, ts, liters, price_per_l, total_cost)"
        " VALUES (1,?,?,?,?)",
        (f"2026-07-{day:02d}T12:00:00+00:00", litres, round(cost / litres, 4), cost))
    db._conn.commit()


def _month(db, key="2026-07"):
    return db_reader._collect_monthly_buckets()[key]


def test_the_litres_burned_reach_the_month(reev):
    _trip(reev, 200.0, 80.0, 70.0)
    assert _month(reev)["fuel_l_burned"] > 0


def test_a_refuel_is_counted_as_bought_not_as_burned(reev):
    """The distinction the whole block rests on: filling the tank is not using it."""
    _refuel(reev, litres=30.0, cost=52.50)
    b = _month(reev)
    assert (b["refuel_count"], b["refuel_l"], b["refuel_cost"]) == (1, 30.0, 52.50)
    assert b["fuel_l_burned"] == 0


def test_a_month_can_hold_a_refuel_and_no_engine_driving(reev):
    """Filled on the 31st, burned in August. The bucket must exist rather than the refuel vanishing
    because no trip created that month."""
    _refuel(reev, litres=40.0, cost=70.0, day=31)
    assert _month(reev)["refuel_l"] == 40.0


def test_the_refuel_spend_lands_on_its_own_day(reev):
    """The month's per-day cost strip is what the little chart draws — petrol is money out too."""
    _refuel(reev, litres=30.0, cost=52.50, day=20)
    assert _month(reev)["_days"][20]["cost"] == 52.50


def test_an_electric_only_month_stays_empty_on_the_fuel_side(reev):
    """A REEV driven entirely on the battery must not sprout four zero tiles."""
    _trip(reev, 100.0, 80.0, 80.0)
    b = _month(reev)
    assert b["fuel_l_burned"] == 0 and b["refuel_count"] == 0


def test_a_bev_is_untouched(tmp_path, monkeypatch):
    """No fuel columns, no refuels table — the collector must not raise on the pages every BEV
    owner opens."""
    path = str(tmp_path / "b.db")
    db = D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    db._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'W','B10')")
    db._conn.execute(
        "INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc,"
        " efficiency_kwh_100km) VALUES (1,'2026-07-05T08:00:00+00:00','2026-07-05T09:00:00+00:00',"
        "100,80,70,15.0)")
    db._conn.commit()
    b = db_reader._collect_monthly_buckets()["2026-07"]
    assert b["fuel_l_burned"] == 0 and b["refuel_count"] == 0
    assert b["total_km"] == 100


# ── the page ─────────────────────────────────────────────────────────────────

def test_the_block_is_gated_on_a_range_extender_and_on_beta():
    assert "{% if is_reev and research and (c.fuel_l_burned or c.refuel_count) %}" in REPORT_HTML


def test_the_l_per_100km_uses_the_whole_distance():
    """The basis the car itself uses (getPlugIn's oc100km), so the month agrees with what the owner
    reads in the official app instead of quietly answering a different question."""
    assert "c.fuel_l_burned / c.total_km * 100" in REPORT_HTML
    assert "fuel_engine_km * 100" not in REPORT_HTML


@pytest.mark.parametrize("lang", ["en", "it", "fr", "de", "nl", "pl", "pt-PT"])
def test_the_labels_exist_in_every_language(lang):
    d = json.loads((ROOT / "web" / "locales" / f"{lang}.json").read_text())["translations"]
    for key in ("report_fuel", "report_fuel_burned", "report_fuel_per100",
                "report_fuel_bought", "report_fuel_spend"):
        assert d.get(key), f"{lang} is missing {key}"


# ── the consumption arrow ────────────────────────────────────────────────────

def test_the_measured_average_covers_generator_trips_too(reev):
    """avg_efficiency skips them by construction (efficiency_kwh_100km is blank there); the measured
    twin reads the cloud's own per-trip figure, so a month driven partly on petrol still has an
    electric average instead of one covering only the battery-only days."""
    _trip(reev, 200.0, 80.0, 70.0)                       # generator ran → efficiency NULL
    reev._conn.execute("UPDATE trips SET ec_kwh = 24.0 WHERE distance_km = 200.0")
    reev._conn.commit()
    b = _month(reev)
    assert b["avg_efficiency"] is None, "premise: the ordinary average has nothing to work with"
    assert b["avg_efficiency_measured"] == 12.0          # 24 kWh over 200 km


def test_a_trip_the_cloud_has_not_answered_for_is_left_out(reev):
    """NULL means "not measured yet". Counting its kilometres with zero energy would drag the
    average down for free, and it would recover on its own hours later — a figure that moves
    without the car having moved."""
    _trip(reev, 100.0, 80.0, 70.0, day=5)
    _trip(reev, 100.0, 70.0, 60.0, day=6)
    reev._conn.execute("UPDATE trips SET ec_kwh = 15.0 WHERE started_at LIKE '2026-07-05%'")
    reev._conn.commit()
    assert _month(reev)["avg_efficiency_measured"] == 15.0   # the unanswered 100 km stay out


def test_the_arrow_prefers_the_measured_basis():
    """It sits beside a tile fed by getEC over the month bounds. Computing it from another quantity
    is how an arrow ends up disagreeing with the number next to it."""
    src = (ROOT / "web" / "db_reader.py").read_text()
    block = src.split("deltas = None", 1)[1][:900]
    assert 'cur.get("avg_efficiency_measured") or cur["avg_efficiency"]' in block
