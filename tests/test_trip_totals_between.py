"""get_trip_totals_between() feeds the Distanza/Durata/Media row shown alongside the getEC energy
split (Statistics "Since last charge" + day/week/month/custom-range/all-time cards) — mirrors the
car's own equivalent screen. Must sum only trips STARTED within [begin_ts, end_ts], excluding
in-progress trips (ended_at IS NULL) and trips outside the window."""
import sqlite3
from datetime import datetime, timezone

import db_reader


def _db(trips):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    # efficiency_kwh_100km is part of the real trips schema and the query now sums
    # distance x efficiency as the window's local energy (#212) — the stand-in table has to carry
    # it too, or these tests pass/fail on a column that only exists here.
    con.execute(
        "CREATE TABLE trips (id INT, started_at TEXT, ended_at TEXT, distance_km REAL, duration_min REAL, "
        "efficiency_kwh_100km REAL, vehicle_id INTEGER DEFAULT 1)"
    )
    con.executemany(
        "INSERT INTO trips (id, started_at, ended_at, distance_km, duration_min) VALUES (?,?,?,?,?)",
        trips,
    )
    con.commit()
    return con


def test_sums_only_trips_within_window(monkeypatch):
    con = _db([
        (1, "2026-06-30T10:00:00+00:00", "2026-06-30T10:20:00+00:00", 10.0, 20),  # before window
        (2, "2026-06-30T16:00:00+00:00", "2026-06-30T16:15:00+00:00", 5.0, 15),   # inside
        (3, "2026-06-30T18:00:00+00:00", "2026-06-30T18:30:00+00:00", 8.0, 30),   # inside
        (4, "2026-07-01T08:00:00+00:00", "2026-07-01T08:10:00+00:00", 3.0, 10),   # after window
    ])
    monkeypatch.setattr(db_reader, "_get", lambda: con)
    begin_ts = int(datetime(2026, 6, 30, 12, 0, 0, tzinfo=timezone.utc).timestamp())
    end_ts = int(datetime(2026, 6, 30, 20, 0, 0, tzinfo=timezone.utc).timestamp())
    tot = db_reader.get_trip_totals_between(begin_ts, end_ts)
    assert tot["trip_count"] == 2
    assert tot["distance_km"] == 13.0
    assert tot["duration_min"] == 45


def test_excludes_in_progress_trip(monkeypatch):
    con = _db([
        (1, "2026-06-30T16:00:00+00:00", "2026-06-30T16:15:00+00:00", 5.0, 15),
        (2, "2026-06-30T17:00:00+00:00", None, 100.0, 999),  # still driving — must not be counted
    ])
    monkeypatch.setattr(db_reader, "_get", lambda: con)
    begin_ts = int(datetime(2026, 6, 30, 0, 0, 0, tzinfo=timezone.utc).timestamp())
    end_ts = int(datetime(2026, 6, 30, 23, 59, 59, tzinfo=timezone.utc).timestamp())
    tot = db_reader.get_trip_totals_between(begin_ts, end_ts)
    assert tot["trip_count"] == 1
    assert tot["distance_km"] == 5.0


def test_empty_window_returns_zero_count(monkeypatch):
    con = _db([])
    monkeypatch.setattr(db_reader, "_get", lambda: con)
    tot = db_reader.get_trip_totals_between(0, 1)
    assert tot["trip_count"] == 0
    assert tot["distance_km"] is None
