"""The three places that count a range-extender's litres have to agree (@michapr, BetaTester #23).

v3.6.6 corrected `_reev_trip_fuel` — the litres of ONE trip — and shipped with the two aggregates
still filtering their rows on the coarse signal:

    AND fuel_start_pct - fuel_end_pct > 0.2

A trip below that floor never reached the reader at all, so the car's own millilitre counter had
nothing to be preferred over. The trips list was right and every total was not. On his B10 the
all-time figure stayed at **5.9 L against 9.64 measured off his own signals** — 39 % missing, on the
release that existed to fix exactly this.

What these tests hold is not the arithmetic, it is the *agreement*: whatever the per-trip reader
says, the all-time summary and the period card say the same. Three copies of a rule, one of them
corrected, is how this happened; a test that only checked one of them is why nobody saw it.
"""
import db as D
import db_reader
import pytest


@pytest.fixture
def reev(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    pdb._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'V','B10')")
    pdb.set_setting("is_reev", "1")
    pdb._conn.commit()
    return pdb


def _trip(pdb, tid, day, km, p0, p1, l0=None, l1=None):
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc,"
        " fuel_start_pct, fuel_end_pct, fuel_start_l, fuel_end_l) VALUES (?,1,?,?,?,80,70,?,?,?,?)",
        (tid, f"2026-07-{day:02d}T08:00:00+00:00", f"2026-07-{day:02d}T09:00:00+00:00",
         km, p0, p1, l0, l1))
    pdb._conn.commit()


def _sum_of_trips():
    """What the trips list itself shows — the figure v3.6.6 made right."""
    return round(sum(t.get("fuel_used_l") or 0 for t in db_reader.get_trips(limit=1000)), 3)


# ── the trip only the fine signal can see ─────────────────────────────────────

def test_a_drop_under_the_percentage_floor_still_counts(reev):
    """0.1 % of a 50 L tank is 50 mL — one step of signal 3235, and under the old floor. The car's
    own counter says 100 mL, exactly and without rounding. This trip used to be dropped by the
    query, before anything could read it."""
    _trip(reev, 1, 3, 12.0, 50.0, 49.9, l0=25.000, l1=24.900)
    s = db_reader.reev_fuel_summary()
    assert s and s["total_l"] == 0.1


def test_the_all_time_total_matches_the_trips_it_is_made_of(reev):
    """The invariant that was broken. Two of these three trips are invisible to the tank
    percentage; all three are plain in the litre counter."""
    _trip(reev, 1, 3, 12.0, 50.0, 49.9, l0=25.000, l1=24.900)     # 0.10 L, under the old floor
    _trip(reev, 2, 5, 30.0, 49.9, 49.4, l0=24.900, l1=24.650)     # 0.25 L, over it
    _trip(reev, 3, 9, 8.0, 49.4, 49.35, l0=24.650, l1=24.620)     # 0.03 L, well under
    assert db_reader.reev_fuel_summary()["total_l"] == pytest.approx(_sum_of_trips(), abs=0.05)


def test_the_period_card_matches_them_too(reev):
    """Same defect, same shape, in the card built the day before for this very tester."""
    from datetime import datetime, timezone
    _trip(reev, 1, 3, 12.0, 50.0, 49.9, l0=25.000, l1=24.900)
    _trip(reev, 2, 5, 30.0, 49.9, 49.4, l0=24.900, l1=24.650)
    b = int(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp())
    e = int(datetime(2026, 7, 31, 23, 59, tzinfo=timezone.utc).timestamp())
    assert db_reader.get_fuel_totals_between(b, e)["fuel_l"] == pytest.approx(_sum_of_trips(), abs=0.05)


def test_the_proportion_that_was_being_lost(reev):
    """His case in miniature: most of the fuel moves in steps the percentage cannot resolve. Before
    the fix the total read 0.25 of 0.38 L — the same 30-40 % hole he saw."""
    _trip(reev, 1, 3, 12.0, 50.0, 49.9, l0=25.000, l1=24.900)
    _trip(reev, 2, 5, 30.0, 49.9, 49.4, l0=24.900, l1=24.650)
    _trip(reev, 3, 9, 8.0, 49.4, 49.35, l0=24.650, l1=24.620)
    assert db_reader.reev_fuel_summary()["total_l"] == 0.4      # 0.10 + 0.25 + 0.03, rounded


# ── and the things that must NOT change ───────────────────────────────────────

def test_a_trip_that_burned_nothing_is_still_not_a_fuel_trip(reev):
    """A flat tank and a flat counter: no engine, no row in any total. The floors moved to the
    reader, they did not disappear."""
    _trip(reev, 1, 3, 40.0, 50.0, 50.0, l0=25.000, l1=25.000)
    assert db_reader.reev_fuel_summary() is None


def test_a_millilitre_of_counter_wobble_is_not_a_fuel_trip(reev):
    """1 mL over 40 km is the counter twitching, not a generator."""
    _trip(reev, 1, 3, 40.0, 50.0, 50.0, l0=25.000, l1=24.999)
    assert db_reader.reev_fuel_summary() is None


def test_a_refuel_never_counts_as_negative_consumption(reev):
    """A trip that ends fuller than it started is a purchase. Widening the query must not let one
    in through the new branch."""
    _trip(reev, 1, 3, 12.0, 50.0, 49.9, l0=25.000, l1=24.900)
    _trip(reev, 2, 5, 5.0, 20.0, 95.0, l0=10.000, l1=47.500)      # a fill-up mid-trip
    assert db_reader.reev_fuel_summary()["total_l"] == 0.1


def test_a_bev_is_untouched(tmp_path, monkeypatch):
    path = str(tmp_path / "b.db")
    pdb = D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    pdb._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'W','B10')")
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc)"
        " VALUES (1,1,'2026-07-03T08:00:00+00:00','2026-07-03T09:00:00+00:00',40,80,70)")
    pdb._conn.commit()
    assert db_reader.reev_fuel_summary() is None


# ── one rule, not three copies of it ──────────────────────────────────────────

def test_the_summary_no_longer_works_the_litres_out_by_itself():
    """It had its own copy of "counter where there is one, tank-% where there is not" — the exact
    rule that had just been corrected somewhere else."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent / "web" / "db_reader.py").read_text()
    body = src.split("def reev_fuel_summary(", 1)[1].split("\ndef ", 1)[0]
    assert "_reev_trip_fuel(" in body, "the summary must go through the one reader"
    assert "fuel_start_pct'] - r['fuel_end_pct']" not in body.replace('"', "'")


def test_no_total_filters_rows_on_the_coarse_signal_alone():
    """The shape of the defect, held directly: a query that admits a trip only when the TANK
    PERCENTAGE moved has already thrown away everything the litre counter could have told it."""
    import pathlib
    import re
    src = (pathlib.Path(__file__).resolve().parent.parent / "web" / "db_reader.py").read_text()
    for fn in ("reev_fuel_summary", "get_fuel_totals_between"):
        body = src.split(f"def {fn}(", 1)[1].split("\ndef ", 1)[0]
        assert re.search(r"AND\s+fuel_start_pct - fuel_end_pct > \?", body) is None, \
            f"{fn} still filters on the tank percentage alone"
        assert "_REEV_FUEL_ANY_DROP_SQL" in body, f"{fn} does not use the shared condition"
