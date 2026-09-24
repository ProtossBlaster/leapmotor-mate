"""The all-trips card divided the cloud's window energy by a subset of its kilometres (#303).

@arzthilfe's C10, v3.17.4. One screen, three figures:

    tile          20.3 kWh/100km
    all trips     56.1 kWh/100km   over 54 km        (156 km, 30.3 kWh beside it)

30.3 kWh over the 156 km recorded is 19.4, which is what he expected and what the tile agrees
with. 56.1 is 30.3 divided by 54 — the kilometres of the trips that carry a getEC figure of their
own. The numerator is the cloud's total for the WHOLE window (`getLastweekEC` between two dates,
an endpoint that knows nothing about Mate's trips); the denominator is the part of it Mate managed
to attach to single trips. A trip stays without `ec_kwh` for reasons that never take its energy
out of that window (`get_trips_needing_ec`): it started before the feature's cutoff, it did not
settle within the 6-hour re-fetch window, the sweep's 4-trips-per-5-minutes never reached it, or
the reading was refused as implausible. The kilometres stay in `distance_km` either way.

The error scales as 1/coverage with no ceiling: measured on this same code, 100 trips with 2
covered printed 900.0 kWh/100km for a car doing 18.0.

The fix is to divide the energy of the SAME trips the denominator counts — `SUM(ec_kwh)` over the
kilometres of the trips that carry one — which is what the month and day strips have always done
(`_totals_seal`). beta #40 is unaffected: @michapr's window total was exactly the sum of his
months' getEC, so numerator and denominator were already the same set, and 10.7 stays 10.7
(tests/test_the_all_trips_average_divides_by_the_km_it_covers.py).
"""
import sqlite3

import db as poller_db
import db_reader
import pytest


def _main():
    """`web.main` imports fastapi, which the minimal CI env does not install."""
    pytest.importorskip("fastapi", reason="the card lives in web.main")
    import main
    return main


def _db(tmp_path, monkeypatch, rows, name="c.db"):
    """`rows` = (start_iso, km, ec_kwh or None). The efficiency follows the energy, as the
    enrichment writes it: from getEC where there is one, from ΔSoC where there is not."""
    path = str(tmp_path / name)
    poller_db.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO vehicles (id, vin) VALUES (1, 'C10')")
    for start, km, ec in rows:
        con.execute(
            "INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km, duration_min, "
            "ec_kwh, efficiency_kwh_100km) VALUES (1, ?, ?, ?, 20, ?, ?)",
            (start, start.replace("T08:", "T09:"), km, ec,
             (ec / km * 100) if ec else 21.0))
    con.commit(); con.close()
    return path


def _window():
    from datetime import datetime, timezone
    return (int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp()),
            int(datetime(2026, 9, 30, tzinfo=timezone.utc).timestamp()))


@pytest.fixture()
def arzthilfe(tmp_path, monkeypatch):
    """12 trips, 156 km. Four reached the cloud: 54 km carrying 9.72 kWh — 18.0 kWh/100 km."""
    rows = [(f"2026-09-{d:02d}T08:00:00+00:00", 13.5, 13.5 * 18.0 / 100) for d in range(1, 5)]
    rows += [(f"2026-09-{d:02d}T08:00:00+00:00", 12.75, None) for d in range(5, 13)]
    return _db(tmp_path, monkeypatch, rows)


def test_the_totals_report_the_energy_of_the_covered_trips(arzthilfe):
    """The card cannot divide an energy it is not told. 4 trips, 9.72 kWh, 54 km."""
    tot = db_reader.get_trip_totals_between(*_window())
    assert tot["distance_km"] == pytest.approx(156.0)
    assert tot.get("ec_km") == pytest.approx(54.0)
    assert tot.get("ec_kwh_sum") == pytest.approx(9.72), \
        "the energy the covered kilometres actually carry"


def test_the_average_divides_the_energy_of_the_same_trips(arzthilfe):
    """9.72 kWh over 54 km is 18.0. 56.1 means it divided the whole window by part of it."""
    main = _main()
    eb = main._enrich_eb_with_trip_totals({"total_kwh": 30.3}, *_window())
    assert eb["avg_kwh100"] == pytest.approx(18.0), \
        f"got {eb.get('avg_kwh100')} — 56.1 is 30.3 / 54 km"


def test_the_card_still_says_which_kilometres_it_speaks_for(arzthilfe):
    """The figure changes; the sentence under it does not."""
    main = _main()
    eb = main._enrich_eb_with_trip_totals({"total_kwh": 30.3}, *_window())
    assert eb["avg_kwh100_km"] == pytest.approx(54.0)


def test_the_distance_shown_is_still_the_distance_driven(arzthilfe):
    """Only the average is at stake: Distance stays what the car drove."""
    main = _main()
    eb = main._enrich_eb_with_trip_totals({"total_kwh": 30.3}, *_window())
    assert eb["distance_km"] == pytest.approx(156.0)


def test_the_window_energy_is_still_the_one_the_split_describes(arzthilfe):
    """The corner figure and the driving/climate/other split come from the cloud and stay."""
    main = _main()
    eb = main._enrich_eb_with_trip_totals({"total_kwh": 30.3}, *_window())
    assert eb["total_kwh"] == pytest.approx(30.3)


def test_a_fully_covered_window_reads_the_same_as_before(tmp_path, monkeypatch):
    """No regression where coverage is total: numerator and denominator already agreed."""
    main = _main()
    rows = [(f"2026-09-{d:02d}T08:00:00+00:00", 13.0, 13.0 * 18.0 / 100) for d in range(1, 13)]
    _db(tmp_path, monkeypatch, rows, name="full.db")
    eb = main._enrich_eb_with_trip_totals({"total_kwh": 28.08}, *_window())
    assert eb["avg_kwh100"] == pytest.approx(18.0)
    assert eb["avg_kwh100_km"] == pytest.approx(156.0)


def test_thin_coverage_cannot_print_an_impossible_average(tmp_path, monkeypatch):
    """The old formula printed 900.0 kWh/100 km here. No car does 900."""
    main = _main()
    rows = [(f"2026-09-{d:02d}T08:00:00+00:00", 13.0, None) for d in range(1, 11)]
    rows += [(f"2026-09-{d:02d}T08:00:00+00:00", 13.0, 13.0 * 18.0 / 100) for d in range(11, 13)]
    _db(tmp_path, monkeypatch, rows, name="thin.db")
    eb = main._enrich_eb_with_trip_totals({"total_kwh": 28.08}, *_window())
    assert eb["avg_kwh100"] == pytest.approx(18.0), \
        f"got {eb.get('avg_kwh100')} — the window's energy over 26 km"


def test_a_window_holding_merged_trips_pairs_them_too(tmp_path, monkeypatch):
    """Joined charges have their own totals path (`_merged_trip_statistics`, beta #44), and it
    answers for every database that holds a single merge — so it needs the same pair or the card
    silently falls back to the window energy over every kilometre."""
    main = _main()
    path = str(tmp_path / "merged.db")
    poller_db.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO vehicles (id, vin) VALUES (1, 'C10')")
    # A joined trip carrying 9.72 kWh over its 54 combined km, plus 102 km the cloud never saw.
    con.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, "
                "duration_min, ec_kwh, ec_stable, efficiency_kwh_100km, start_odometer_km, "
                "end_odometer_km) VALUES (1, 1, '2026-09-01T08:00:00+00:00', "
                "'2026-09-01T09:00:00+00:00', 27.0, 30, 9.72, 1, 18.0, 1000.0, 1027.0)")
    con.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, "
                "duration_min, merged_into_id, start_odometer_km, end_odometer_km) VALUES "
                "(2, 1, '2026-09-01T09:30:00+00:00', '2026-09-01T10:00:00+00:00', 27.0, 30, 1, "
                "1027.0, 1054.0)")
    con.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, "
                "duration_min, efficiency_kwh_100km) VALUES "
                "(3, 1, '2026-09-05T08:00:00+00:00', '2026-09-05T09:00:00+00:00', 102.0, 90, 21.0)")
    con.commit(); con.close()

    tot = db_reader.get_trip_totals_between(*_window())
    assert tot["distance_km"] == pytest.approx(156.0)
    assert tot["ec_km"] == pytest.approx(54.0), "the joined trip counts once, over its whole length"
    assert tot["ec_kwh_sum"] == pytest.approx(9.72)

    eb = main._enrich_eb_with_trip_totals({"total_kwh": 30.3}, *_window())
    assert eb["avg_kwh100"] == pytest.approx(18.0)
    assert eb["avg_kwh100_km"] == pytest.approx(54.0)


def test_with_no_covered_trip_the_card_still_divides_by_the_distance(tmp_path, monkeypatch):
    """Unchanged fallback: with nothing to pair, the cloud total over the recorded kilometres is
    the only basis there is, and a card with a distance and no average is worse."""
    main = _main()
    rows = [(f"2026-09-{d:02d}T08:00:00+00:00", 13.0, None) for d in range(1, 13)]
    _db(tmp_path, monkeypatch, rows, name="none.db")
    eb = main._enrich_eb_with_trip_totals({"total_kwh": 28.08}, *_window())
    assert eb["avg_kwh100"] == pytest.approx(18.0)
    assert eb["avg_kwh100_km"] == pytest.approx(156.0)
