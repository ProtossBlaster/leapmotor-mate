"""Both energies over the period the reader chose (@michapr, beta #11).

The period card already answered the electric half for any window — getEC takes a begin and an end.
The petrol half had nowhere to come from: getPlugIn measures both, but only over ITS six weeks (the
request carries the VIN and nothing else), so a chosen period has to be answered from the trips.

get_fuel_totals_between is that answer, and it goes through _reev_trip_fuel like everything else —
which prefers the car's OWN litre counter (signal 3263) and only falls back to tank-% × assumed
capacity on trips recorded before v2.14.1.
"""
import json
import pathlib
from datetime import datetime, timezone

import db as D
import db_reader
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
EB_HTML = (ROOT / "web" / "templates" / "partials" / "energy_breakdown.html").read_text()
MAIN = (ROOT / "web" / "main.py").read_text()


def _ts(iso: str) -> int:
    return int(datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp())


WIN = (_ts("2026-07-01T00:00:00"), _ts("2026-07-31T23:59:59"))


@pytest.fixture
def reev(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    database = D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    database._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'V','C10')")
    database.set_setting("is_reev", "1")
    database._conn.commit()
    return database


def _trip(db, day, km, f0, f1, l0=None, l1=None, month=7):
    db._conn.execute(
        "INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc,"
        " fuel_start_pct, fuel_end_pct, fuel_start_l, fuel_end_l) VALUES (1,?,?,?,80,70,?,?,?,?)",
        (f"2026-{month:02d}-{day:02d}T08:00:00+00:00", f"2026-{month:02d}-{day:02d}T09:00:00+00:00",
         km, f0, f1, l0, l1))
    db._conn.commit()


def test_the_litres_come_from_the_cars_own_counter_when_it_has_one(reev):
    """Not tank-% × an assumed capacity: the trip carries fuel_start_l/fuel_end_l, so the answer is
    what the car counted — 3.40 L, whatever we believe the tank holds."""
    _trip(reev, 5, 120.0, 80.0, 72.0, l0=38.00, l1=34.60)
    assert db_reader.get_fuel_totals_between(*WIN)["fuel_l"] == 3.40


def test_a_trip_outside_the_window_is_not_counted(reev):
    """The whole point of the function — a period the reader picked, not everything ever."""
    _trip(reev, 5, 120.0, 80.0, 72.0, l0=38.00, l1=34.60)
    _trip(reev, 5, 300.0, 72.0, 60.0, l0=34.60, l1=28.00, month=8)
    assert db_reader.get_fuel_totals_between(*WIN)["trip_count"] == 1


def test_an_electric_only_window_reports_nothing(reev):
    """A REEV fortnight driven on the battery must not produce a zero-litre tile."""
    _trip(reev, 5, 120.0, 80.0, 80.0)
    t = db_reader.get_fuel_totals_between(*WIN)
    assert t["fuel_l"] == 0 and t["trip_count"] == 0


def test_a_bev_costs_one_query_and_returns_zero(tmp_path, monkeypatch):
    """Every BEV owner opens this card. No fuel data must mean no work and no exception."""
    path = str(tmp_path / "b.db")
    db = D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    db._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'W','B10')")
    db._conn.execute(
        "INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc)"
        " VALUES (1,'2026-07-05T08:00:00+00:00','2026-07-05T09:00:00+00:00',100,80,70)")
    db._conn.commit()
    assert db_reader.get_fuel_totals_between(*WIN) == {"fuel_l": 0.0, "engine_km": 0.0, "trip_count": 0}


def test_several_trips_add_up(reev):
    _trip(reev, 5, 120.0, 80.0, 72.0, l0=38.00, l1=34.60)
    _trip(reev, 9, 90.0, 72.0, 68.0, l0=34.60, l1=32.90)
    assert db_reader.get_fuel_totals_between(*WIN)["fuel_l"] == 5.10


# ── wiring ───────────────────────────────────────────────────────────────────

def test_the_card_is_given_the_fuel_for_the_same_window():
    body = MAIN.split("def _enrich_eb_with_trip_totals(", 1)[1].split("\ndef ", 1)[0]
    assert "db_reader.get_fuel_totals_between(begin_ts, end_ts)" in body, \
        "the fuel must be scoped to the SAME begin/end as the electric half"


def test_the_l_per_100km_is_over_the_windows_whole_distance():
    """Same denominator as the electric half beside it, and as the car's own figure a card above."""
    body = MAIN.split("def _enrich_eb_with_trip_totals(", 1)[1].split("\ndef ", 1)[0]
    assert '_f["fuel_l"] / dist_km * 100' in body
    assert '_f["engine_km"] * 100' not in body


def test_the_tile_appears_only_with_litres_and_only_on_a_beta_reev():
    assert "{% if is_reev and research and eb.fuel_l %}" in EB_HTML


@pytest.mark.parametrize("lang", ["en", "it", "fr", "de", "nl", "pl", "pt-PT"])
def test_the_labels_exist_in_every_language(lang):
    d = json.loads((ROOT / "web" / "locales" / f"{lang}.json").read_text())["translations"]
    for key in ("eb_fuel", "eb_fuel_over_engine_km"):
        assert d.get(key), f"{lang} is missing {key}"
