"""Listing the years that have trips must not build every trip.

Measured on the lab against a real database on 28/09/2026: `get_trip_years` costs **80.5 ms**, on a
production add-on about four times that, and it runs on every open of the Trips page — which was
1.156 s there. It costs that because it asks for every trip through `get_trips(limit=1_000_000)`,
the full pipeline: cloud-energy matching, the getEC columns, the per-trip cost. To collect a set of
years.

The years come from the timestamps alone. The catch is that they are LOCAL years and the column is
UTC, so a drive at 23:30 on 31 December is a January trip for a driver in Rome — which is why this
cannot become a `strftime('%Y')` in SQL.
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
# sys.path is conftest.py's job — it puts web/ before poller/, and BOTH hold a main.py.
# Re-inserting them here once flipped that order and nine other tests could not import the
# web app at all.

import db as D  # noqa: E402
import db_reader  # noqa: E402


@pytest.fixture
def three_years(tmp_path, monkeypatch):
    path = str(tmp_path / "years.db")
    database = D.Database(path)
    conn = database._conn
    conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LVIN0000000000001','B10')")
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('timezone','Europe/Rome')")
    rows = [("2024-06-01T10:00:00+00:00", "2024-06-01T10:30:00+00:00"),
            ("2026-03-02T08:00:00+00:00", "2026-03-02T08:20:00+00:00"),
            # 23:30 UTC on New Year's Eve is already the next year in Rome
            ("2025-12-31T23:30:00+00:00", "2025-12-31T23:50:00+00:00")]
    conn.executemany(
        "INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc)"
        " VALUES (1, ?, ?, 10, 80, 75)", rows)
    conn.commit()
    conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)


def test_the_years_are_the_local_ones(three_years):
    assert db_reader.get_trip_years() == [2026, 2024], (
        "the New Year's Eve drive belongs to the year the driver was living in"
    )


def test_it_does_not_go_through_the_whole_trip_pipeline(three_years, monkeypatch):
    called = []
    real = db_reader.get_trips
    monkeypatch.setattr(db_reader, "get_trips",
                        lambda *a, **k: called.append(k.get("limit")) or real(*a, **k))
    db_reader.get_trip_years()
    assert called == [], (
        f"the year pills still build every trip through get_trips{called}: cloud matching, getEC "
        f"and per-trip cost, to collect three numbers"
    )
