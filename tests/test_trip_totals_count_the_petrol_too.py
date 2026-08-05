"""A day's cost, a month's cost — with the petrol in them (@michapr, beta #11, 05/08/26).

*"Sorry..... but now is missing right costs.... 8.3 litres and 3.5 kWh used - should be more as
0.05€"* — his 28 July read **129 km · 8.3 L · 0.08 €**, and his July strip **416 km · 9.6 L · 9.02 €**
against the ~33 € he worked out by hand.

Neither figure was broken today. `_totals_add` has summed `trip["cost"]` since long before, and the
code says what that field is in its own comment: *"`cost` stays the electric line"*. The petrol lives
in `fuel_cost`, and the sum of the two — `cost_total` — has existed since **v3.2.0** and was read in
exactly **one** place: the trip detail page. Open a trip and the number was right; look at the day,
the month or the calendar and you saw the electric half alone.

What changed is that we put the LITRES next to it (v3.6.9). "8.3 L" beside "0.08 €" is unreadable in
a way that "0.08 €" on its own never was — the defect did not appear, it became visible.

On a BEV nothing moves: `fuel_cost` is None there, so `cost_total` IS `cost`.
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


def _trip(cost=None, fuel_cost=None, km=100.0, eff=None, fuel_l=None):
    """A trip as `get_trips` hands it to the folders — the shape the totals actually see."""
    return {"distance_km": km, "efficiency_kwh_100km": eff, "regen_kwh": 0.0,
            "cost": cost, "fuel_cost": fuel_cost, "fuel_used_l": fuel_l,
            "cost_total": round(sum(c for c in (cost, fuel_cost) if c is not None), 2)
                          if (cost is not None or fuel_cost is not None) else None}


# ── the day and the month ────────────────────────────────────────────────────

def test_a_days_cost_includes_the_petrol(reev):
    """His 28 July in miniature: a battery hop that cost 0.08 € and a generator drive that burned
    8.3 L at 1.93 €/L. The day is not 0.08 €."""
    t = db_reader.trips_totals([
        _trip(cost=0.08, km=2.0, eff=15.0),
        _trip(cost=None, fuel_cost=16.02, km=127.0, fuel_l=8.3),
    ])
    assert t["cost"] == pytest.approx(16.10, abs=0.01)


def test_a_bev_day_is_untouched(reev):
    """No fuel_cost anywhere, so the total is exactly what it always was."""
    t = db_reader.trips_totals([_trip(cost=3.50, km=100.0, eff=15.0),
                                _trip(cost=1.25, km=40.0, eff=14.0)])
    assert t["cost"] == pytest.approx(4.75, abs=0.01)


def test_a_trip_with_petrol_and_no_electricity_still_counts(reev):
    """The generator trip has NO efficiency, so Mate stores no electric cost for it — which is why
    the day could come out at pennies while the tank emptied."""
    t = db_reader.trips_totals([_trip(cost=None, fuel_cost=16.02, km=127.0, fuel_l=8.3)])
    assert t["cost"] == pytest.approx(16.02, abs=0.01)


def test_a_trip_with_neither_adds_nothing(reev):
    t = db_reader.trips_totals([_trip(km=40.0)])
    assert t["cost"] == 0


# ── the same fold, in the source ─────────────────────────────────────────────

def test_the_live_folder_adds_the_two_halves_itself():
    """The two FIELDS, not the derived `cost_total` — and that distinction cost a near-miss.

    Folding `cost_total` looked right and passed every unit test, because the tests built their trip
    dicts by hand and put it there. On Silvio's real database it produced **0.00 €** across three
    months: `cost_total` is computed in `get_trip_detail`, and the trips the calendar folds had never
    been through it. Reading two fields that ARE there beats reading one derived somewhere else.

    Anchored to the code, not to the word 'cost' — that word is also in the comment above it."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent / "web" / "db_reader.py").read_text()
    fold = src.split("def _totals_add(", 1)[1].split("\ndef ", 1)[0]
    assert '(trip.get("cost") or 0)' in fold and '(trip.get("fuel_cost") or 0)' in fold, \
        "_totals_add is not adding the electric and the petrol halves"
    # The CALL, not the word: `cost_total` is named in the comment above these lines, and searching
    # for it bare finds the explanation instead of the code. Fourth time today — anchor to the form.
    assert 'trip.get("cost_total")' not in fold, \
        "cost_total is not computed on the trips this folds — read the two fields instead"


def test_the_other_folder_is_dead_and_stays_that_way():
    """There is a SECOND accumulator with the same shape, `get_trips_grouped._add`, and it also
    sums an electric-only cost — but it computes that cost itself from `energy × rate` and never
    sees a `fuel_cost` at all. Looking for every copy of this defect turned up that it has **no
    callers**: nothing on any page reaches it.

    Left alone rather than half-corrected, and pinned here instead: the day someone wires it to a
    screen, this test fails and says why. A dead function carrying a live defect is the trap."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    callers = []
    for p in list((root / "web").rglob("*.py")) + list((root / "web").rglob("*.html")):
        if p.name == "db_reader.py":
            continue
        if "get_trips_grouped" in p.read_text(encoding="utf-8"):
            callers.append(p.name)
    assert not callers, (
        f"get_trips_grouped is now reached from {callers} — it folds an ELECTRIC-ONLY cost "
        "(energy × rate, no fuel_cost anywhere), so give it the petrol before showing it")


def test_the_trip_detail_page_still_reads_the_same_field():
    """It was the only surface that had it right, and it stays the reference: whatever the day
    shows must be the same quantity a trip shows."""
    import pathlib
    tpl = (pathlib.Path(__file__).resolve().parent.parent / "web" / "templates"
           / "trip_detail.html").read_text()
    assert "trip.cost_total | money" in tpl


# ── and the same thing through the REAL query ────────────────────────────────

def test_the_petrol_reaches_the_day_total_through_the_actual_query(reev):
    """The test that was missing, and the reason a green suite still shipped nothing.

    Everything above builds trip dicts by hand and puts `fuel_cost` in them — so the fold was proved
    and the WIRING was not. Measured against the released code on Silvio's own database, the trips
    the calendar folds carry `fuel_used_l` and **no** `fuel_cost` at all: nobody computed it outside
    the detail page. The day total went on being the electric half with every test green.

    So this one goes through `get_trips_calendar_day` — the path the page uses — with a real refuel
    and a real engine-on trip in the database."""
    reev.set_setting("battery_capacity_kwh", "50.0")
    reev._conn.execute(
        "CREATE TABLE IF NOT EXISTS fuel_purchases (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " vehicle_id INTEGER, ts TEXT NOT NULL, liters REAL NOT NULL, price_per_l REAL NOT NULL,"
        " total_cost REAL, fuel_before_pct REAL, note TEXT, created_at TEXT)")
    reev._conn.execute(
        "INSERT INTO fuel_purchases (vehicle_id, ts, liters, price_per_l, total_cost, fuel_before_pct)"
        " VALUES (1,'2026-07-01T08:00:00+00:00', 40.0, 1.80, 72.0, 5.0)")
    reev._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc,"
        " fuel_start_pct, fuel_end_pct) VALUES (1,1,'2026-07-05T08:00:00+00:00',"
        "'2026-07-05T09:00:00+00:00', 120.0, 60, 58, 80.0, 70.0)")     # 10 % of a 50 L tank = 5 L
    reev._conn.commit()

    trips = db_reader.get_trips_calendar_day(2026, 7, 5)
    assert trips and trips[0].get("fuel_used_l"), "the fixture has to burn something"
    assert "fuel_cost" in trips[0], \
        "the calendar's trips carry no fuel_cost — the day total cannot include the petrol"
    assert trips[0]["fuel_cost"] > 0

    day = db_reader.trips_totals(trips)
    # 5 L at the blended 1.80 €/L
    assert day["cost"] == pytest.approx(9.0, abs=0.3), \
        "the day total still shows the electric half alone"


def test_a_bev_day_never_grows_a_fuel_cost(tmp_path, monkeypatch):
    """The other half of the same check: nothing on a car with no tank."""
    path = str(tmp_path / "b.db")
    pdb = D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    pdb._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'W','B10')")
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc,"
        " efficiency_kwh_100km) VALUES (1,1,'2026-07-05T08:00:00+00:00',"
        "'2026-07-05T09:00:00+00:00',100,80,70,16.0)")
    pdb._conn.commit()
    trips = db_reader.get_trips_calendar_day(2026, 7, 5)
    assert trips and not trips[0].get("fuel_cost")
