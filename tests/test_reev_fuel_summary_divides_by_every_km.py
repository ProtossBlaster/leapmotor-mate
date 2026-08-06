"""The Trips header's L/100 km divides by every kilometre driven, not by the generator trips' own.

@michapr, beta #26: *"It seems that the gasoline consumption refers to the running time of the
generator, and not to the total mileage. Naturally, this is confusing."* Measured on the trips.csv
in his bundle — 44 finished trips, 479 km, 9.60 L, of which 6 trips (164 km) had the generator run:

    2.00 L/100 km   over all 479 km          ← what the calendar strip shows
    5.85 L/100 km   over the 164 km of the generator trips   ← what he sees, "6 L/100km"
   15.24 L/100 km   over the 63 km the generator actually drove

His car agrees with the first: the cloud's own `oc100km` reads **2.9 L/100 km** over its six-week
window. Nowhere near 6.

v3.6.9 changed this figure's basis to the whole distance in four places, and the line here was
changed with them — its comment still says *"Over ALL the kilometres, like the car's own figure"*.
But the SQL above it filters the rows to trips whose tank dropped, so `total_km` could only ever add
up the generator trips. 🔑 The denominator was corrected; the set of rows it sums over was not.

⚠️ `engine_km` stays exactly as it was — how far the generator drove is a real thing an owner wants
to know. It is reported, never divided by.
"""
import pathlib

import db as PollerDB
import db_reader
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def trips(tmp_path, monkeypatch):
    pdb = PollerDB.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    pdb.ensure_vehicle("LVIN0000000000001", "C10", 2025)
    db_reader.set_setting("is_reev", "1")

    def add(day, km, litres=None):
        """`litres=None` → a purely electric trip: the tank never moves."""
        l0 = 40.0
        l1 = l0 - litres if litres else l0
        pdb._conn.execute(
            "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, start_soc,"
            " end_soc, fuel_start_pct, fuel_end_pct, fuel_start_l, fuel_end_l)"
            " VALUES (?,1,?,?,?,80,60,?,?,?,?)",
            (day, f"2026-07-{day:02d}T08:00:00+00:00", f"2026-07-{day:02d}T10:00:00+00:00",
             km, l0 / 0.5, l1 / 0.5, l0, l1))
        pdb._conn.commit()
    return add


def test_the_electric_kilometres_count_in_the_denominator(trips):
    """One generator trip of 100 km burning 6 L, and 300 km driven on the battery. 6 L over 400 km
    is 1.5 — over the generator trip alone it would read 6.0, four times too high."""
    trips(1, km=100.0, litres=6.0)
    trips(2, km=300.0)
    s = db_reader.reev_fuel_summary()
    assert s["total_l"] == 6.0
    assert s["avg_l_100km"] == 1.5, f"still dividing by the generator trips only: {s}"


def test_his_own_numbers(trips):
    """His bundle, reduced: 9.6 L, 164 km with the generator, 315 km without. His car reads 2.9."""
    trips(1, km=164.0, litres=9.6)
    trips(2, km=315.0)
    s = db_reader.reev_fuel_summary()
    assert s["avg_l_100km"] == 2.0
    assert s["avg_l_100km"] != 5.9, "the figure he reported"


def test_the_generator_distance_is_still_reported(trips):
    """It answers a different question — how far the generator drove — and it stays."""
    trips(1, km=100.0, litres=6.0)
    trips(2, km=300.0)
    s = db_reader.reev_fuel_summary()
    assert s["engine_km"] > 0
    assert s["engine_trips"] == 1, "the COUNT is of generator trips, and that is right"


def test_a_month_without_the_generator_says_nothing(trips):
    """No fuel burned is not 0.0 L/100 km — there is nothing to report at all."""
    trips(1, km=100.0)
    trips(2, km=300.0)
    assert db_reader.reev_fuel_summary() is None


def test_no_trips_at_all(trips):
    assert db_reader.reev_fuel_summary() is None


def test_it_agrees_with_the_calendar_strip(trips):
    """Both are on the Trips page, one above the other. They divided by different distances, which
    is the whole of beta #26."""
    trips(1, km=100.0, litres=6.0)
    trips(2, km=300.0)
    header = db_reader.reev_fuel_summary()["avg_l_100km"]
    strip = db_reader.get_trips_calendar_month(2026, 7)["total"]["fuel_l_100km"]
    assert header == strip, f"header {header} vs strip {strip}"


def test_the_rows_are_no_longer_filtered_before_being_summed():
    """Read on the source: the SQL used to restrict to trips whose tank dropped, so the denominator
    could not see an electric kilometre even after v3.6.9 renamed it 'ALL the kilometres'."""
    src = (ROOT / "web" / "db_reader.py").read_text()
    body = src.split("def reev_fuel_summary", 1)[1].split("\ndef ", 1)[0]
    q = body.split('"""', 2)[2]
    assert "_REEV_FUEL_ANY_DROP_SQL" not in q, \
        "the query still drops the electric trips before total_km can count them"
