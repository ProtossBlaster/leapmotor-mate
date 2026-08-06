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


def _trip(pdb, tid, day, km, p0, p1, l0=None, l1=None, merged_into=None, hh=8):
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc,"
        " fuel_start_pct, fuel_end_pct, fuel_start_l, fuel_end_l, merged_into_id)"
        " VALUES (?,1,?,?,?,80,70,?,?,?,?,?)",
        (tid, f"2026-07-{day:02d}T{hh:02d}:00:00+00:00", f"2026-07-{day:02d}T{hh:02d}:50:00+00:00",
         km, p0, p1, l0, l1, merged_into))
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
        # Either it filters on BOTH signals, or — better — it does not filter on fuel at all and
        # lets `_reev_trip_fuel` decide per trip. `reev_fuel_summary` took the second road for
        # beta #26: its L/100 km has to divide by every kilometre driven, so it cannot let the SQL
        # throw the electric trips away before the denominator ever sees them.
        # ⚠️ Anchored to the WHERE clause, not to the function body: `fuel_start_pct` also appears
        # in the SELECT list and in the `_reev_trip_fuel(...)` call, and the loose version of this
        # assertion failed on correct code. Fifth time this substring trap has bitten on this repo.
        where = body.split("WHERE", 1)[1].split(",\n", 1)[0] if "WHERE" in body else ""
        assert "_REEV_FUEL_ANY_DROP_SQL" in body or "fuel_" not in where, \
            f"{fn} filters the rows on fuel without using the shared condition: {where!r}"


# ── the fourth round, and the one that was actually his ───────────────────────

def test_a_merged_trip_keeps_its_petrol(reev):
    """@michapr's own nine rows, beta #23, 05/08/26 — and his own guess: *«Maybe it related to a
    merged trip?»*

    Joining two trips only ever writes `merged_into_id`; it rewrites nothing else, which is exactly
    what makes this bite. The child keeps the fuel drop, the parent keeps its own — and his parent
    (07:45, 2 km) burned NOTHING while his child (07:56, 57 km) burned 3.7 L. `get_fuel_totals_between`
    was the one aggregate filtering `merged_into_id IS NULL`, so those 3.7 L vanished while the 57 km
    stayed in the divisor beside them.

    9.6 L is what every other total says, and what his own SQL says. 5.9 is what the period card on
    the Statistics page showed him — 9.6 − 3.7, to the decimal. Three of my theories died before
    this one; his held."""
    from datetime import datetime, timezone
    rows = [(10, 16, 11.0, 75.3, 74.9, None, 12), (11, 16, 12.0, 74.9, 74.0, None, 14),
            (14, 21,  1.0, 74.0, 74.0, None,  7), (15, 21,  6.0, 74.0, 73.3, None,  7),
            (16, 21,  7.0, 73.3, 72.7, None,  8), (26, 28,  2.0, 72.7, 72.7, None,  7),
            (27, 28, 57.0, 72.7, 65.3,   26,  9),          # ← merged into 26: 3.7 L lived here
            (28, 28, 68.0, 65.3, 56.1, None, 11), (29, 28,  2.0, 56.1, 56.1, None, 13)]
    for tid, day, km, p0, p1, mg, hh in rows:
        _trip(reev, tid, day, km, p0, p1, merged_into=mg, hh=hh)

    b = int(datetime(2024, 5, 4, tzinfo=timezone.utc).timestamp())
    e = int(datetime(2026, 8, 4, 23, 59, tzinfo=timezone.utc).timestamp())
    period = db_reader.get_fuel_totals_between(b, e)["fuel_l"]

    assert db_reader.reev_fuel_summary()["total_l"] == pytest.approx(9.6, abs=0.05)
    assert period == pytest.approx(9.6, abs=0.05), \
        f"the period card lost the merged trip's petrol: {period} instead of 9.6"


def test_the_kilometres_and_the_litres_of_a_period_come_from_the_same_trips(reev):
    """The half of the defect that made it visible: distance never filtered merged children, fuel
    did. So the card divided a short litre total by a full distance and printed an L/100 km that
    belonged to neither."""
    from datetime import datetime, timezone
    _trip(reev, 1, 3, 2.0, 72.7, 72.7, hh=7)
    _trip(reev, 2, 3, 58.0, 72.7, 65.3, merged_into=1, hh=9)
    b = int(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp())
    e = int(datetime(2026, 7, 31, 23, 59, tzinfo=timezone.utc).timestamp())
    fuel = db_reader.get_fuel_totals_between(b, e)
    dist = db_reader.get_trip_totals_between(b, e)["distance_km"]
    assert dist == 60.0, "distance already counts the merged child"
    assert fuel["fuel_l"] == pytest.approx(3.7, abs=0.05), "and the litres must come from the same rows"
