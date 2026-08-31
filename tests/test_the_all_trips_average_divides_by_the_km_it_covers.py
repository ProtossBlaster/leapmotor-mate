"""The all-trips consumption card averaged over kilometres its energy does not describe (beta #40).

@michapr's B10 REEV showed, on one page:

    all trips   9.1 kWh/100km  (1172 km)
    July       10.1 kWh/100km  over 395 km of 416
    August     11.1 kWh/100km  over 597 km of 756

The average of everything sat BELOW both months it averages. Reproduced from his own bundle:
1172.0 km driven, 992.0 of them covered by getEC across 80 of 88 trips, 106.20 kWh of getEC energy.
106.20 / 992 = 10.7; 106.20 / 1172 = 9.1. The card divided by every kilometre while its numerator
only described the covered ones — eight trips, 180 km, 15% of the distance, carried no getEC figure
at all.

The months were already right: `_totals_seal` divides by `_ec_km`, the distance of the trips that
HAVE a figure, and the page says so ("over 395 km of 416"). The card was the one place that did not.

Same shape as the SoH gap (partial energy over a whole ΔSoC) and #237 (all-time euros over trip
kilometres): a numerator that covers part of the window, divided by the whole of it.

His bundle also settles the doubt about mixing sources: the cloud's total for the window, 106.20,
is exactly the sum of the two months' getEC. Cloud and trips agree on the energy; only the
denominator differed.

CI-safe: db_reader + sqlite, no fastapi, no cloud.
"""
import sqlite3

import db as poller_db
import db_reader
import pytest


@pytest.fixture()
def michapr(tmp_path, monkeypatch):
    """His two months, to the kilometre: covered and uncovered trips in both."""
    path = str(tmp_path / "m.db")
    poller_db.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO vehicles (id, vin) VALUES (1, 'B10')")
    rows = [
        # (start, km, ec_kwh)  — July: 395 covered of 416, 39.90 kWh
        ("2026-07-05T08:00:00+00:00", 395.0, 39.90),
        ("2026-07-20T08:00:00+00:00", 21.0, None),
        # August: 597 covered of 756, 66.30 kWh
        ("2026-08-05T08:00:00+00:00", 597.0, 66.30),
        ("2026-08-20T08:00:00+00:00", 159.0, None),
    ]
    for start, km, ec in rows:
        con.execute(
            "INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km, duration_min, "
            "ec_kwh, efficiency_kwh_100km) VALUES (1, ?, ?, ?, 60, ?, ?)",
            (start, start.replace("08:00", "09:00"), km, ec,
             (ec / km * 100) if ec else None))
    con.commit(); con.close()
    return path


def _window():
    """The all-time card's own window: it begins at the first trip's local midnight.

    Not earlier — a window starting before the first recorded trip is refused outright by the #105
    guard (all-of-the-car's cloud energy against a fraction of its distance), and that guard is the
    reason this one allows a day of slack."""
    from datetime import datetime, timezone
    b = int(datetime(2026, 7, 5, tzinfo=timezone.utc).timestamp())
    e = int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp())
    return b, e


def test_the_totals_report_the_covered_distance_too(michapr):
    """The card cannot divide by what it is not told."""
    tot = db_reader.get_trip_totals_between(*_window())
    assert tot["distance_km"] == pytest.approx(1172.0)
    assert tot.get("ec_km") == pytest.approx(992.0), \
        "the distance the getEC energy actually speaks for"


def test_the_average_is_over_the_covered_kilometres(michapr):
    """106.20 kWh over 992 km is 10.7 — between the two months, where an average belongs."""
    import main
    b, e = _window()
    eb = main._enrich_eb_with_trip_totals({"total_kwh": 106.20}, b, e)
    assert eb["avg_kwh100"] == pytest.approx(10.7), \
        f"got {eb.get('avg_kwh100')} — 9.1 means it divided by all 1172 km"


def test_the_distance_shown_is_still_the_distance_driven(michapr):
    """Only the denominator of the average changes: the card's Distance is what the car drove."""
    import main
    b, e = _window()
    eb = main._enrich_eb_with_trip_totals({"total_kwh": 106.20}, b, e)
    assert eb["distance_km"] == pytest.approx(1172.0)


def test_the_card_can_say_what_it_averaged_over(michapr):
    """The months print "over 395 km of 416". The card needs the same two numbers to do it."""
    import main
    b, e = _window()
    eb = main._enrich_eb_with_trip_totals({"total_kwh": 106.20}, b, e)
    assert eb["avg_kwh100_km"] == pytest.approx(992.0)
