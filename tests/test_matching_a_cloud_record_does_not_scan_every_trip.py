"""Matching the cloud's trip history must not compare every record with every trip.

Measured on production (Home Assistant add-on, aarch64) on 28/09/2026: `get_trips(3)` — three trips
for the Overview — costs **92 ms**, against 1.73 ms on 3.19.2 where this matching did not exist.
Profiled on a real database: `trip_energy.select_energy` is 32 ms of it and does **over a million**
`min`/`max` calls per ten renders, because for each cloud mileage record it walks every finished
trip in the database looking for the one it belongs to.

It is quadratic in the history, so it gets worse every day the car is driven — not every release.

A record belongs to a trip only when it sits inside that trip's window (`start >= a - 90`), so the
trips worth looking at are those whose start is near the record's. Sorting the trips by start once
and looking only at that neighbourhood gives the identical answer for a fraction of the work — and
"identical" is the part that matters, because this decides which number a user is shown.
"""
import pathlib
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
# sys.path is conftest.py's job — it puts web/ before poller/, and BOTH hold a main.py.
# Re-inserting them here once flipped that order and nine other tests could not import the
# web app at all.

import db as D  # noqa: E402
import trip_energy  # noqa: E402

VIN = "LVIN0000000000001"
START = datetime(2026, 1, 1, 6, 0, tzinfo=timezone.utc)


def _history(conn, trips, extra_records=()):
    """`trips` = [(minutes_from_START, duration_min, km, kwh)] — each with its matching record."""
    conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,?,'B10')", (VIN,))
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('is_reev','0')")
    conn.execute("""CREATE TABLE IF NOT EXISTS api_lab_cloud_history_records
                    (id INTEGER PRIMARY KEY, kind TEXT, payload_json TEXT)""")
    rows, records = [], []
    for index, (offset, duration, km, kwh) in enumerate(trips, start=1):
        a = START + timedelta(minutes=offset)
        b = a + timedelta(minutes=duration)
        rows.append((index, a.isoformat(), b.isoformat(), km))
        records.append((
            '{"vin": "%s", "routeStartTs": %d, "routeEndTs": %d, "totalEnergy": %s, '
            '"totalMileage": %s}' % (VIN, int(a.timestamp() * 1000), int(b.timestamp() * 1000), kwh, km),))
    conn.executemany(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc)"
        " VALUES (?, 1, ?, ?, ?, 80, 70)", rows)
    conn.executemany("INSERT INTO api_lab_cloud_history_records (kind, payload_json)"
                     " VALUES ('mileage', ?)", records + [(r,) for r in extra_records])
    conn.commit()


def _displayed(conn, ids):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM trips WHERE id IN (%s) ORDER BY id" % ",".join("?" * len(ids)), ids)]


@pytest.fixture
def db(tmp_path):
    database = D.Database(str(tmp_path / "history.db"))
    yield database._conn
    database._conn.close()


def test_each_record_lands_on_its_own_trip(db):
    _history(db, [(i * 60, 30, 20.0, 4.0 + i) for i in range(12)])
    displayed = _displayed(db, list(range(1, 13)))
    trip_energy.select_energy(db, displayed)
    got = {t["id"]: t.get("cloud_energy_kwh") or t.get("energy_kwh") for t in displayed}
    for index in range(1, 13):
        assert got[index] == pytest.approx(4.0 + index - 1), (index, got[index])
    assert all(t.get("energy_source") == "cloud" for t in displayed), \
        [t.get("energy_source") for t in displayed]


def test_a_record_that_fits_two_trips_is_given_to_neither(db):
    """The ambiguity guard: whatever makes the search cheaper must not make it blind."""
    a = START + timedelta(minutes=100)
    wide = ('{"vin": "%s", "routeStartTs": %d, "routeEndTs": %d, "totalEnergy": 9.0, '
            '"totalMileage": 40.0}' % (VIN, int(a.timestamp() * 1000),
                                       int((a + timedelta(minutes=30)).timestamp() * 1000)))
    # two trips with the SAME window: a record inside it belongs to neither
    _history(db, [(100, 30, 20.0, 4.0), (100, 30, 20.0, 4.0)], extra_records=(wide,))
    displayed = _displayed(db, [1, 2])
    trip_energy.select_energy(db, displayed)
    assert all(t.get("energy_source") != "cloud" for t in displayed), \
        [(t["id"], t.get("energy_source")) for t in displayed]


def test_the_work_does_not_grow_with_the_square_of_the_history(db):
    """2600 trips, 2600 records, each record on its own trip. Comparing every record with every
    trip is 6.8 million pairs; the neighbourhood of each record is a handful."""
    _history(db, [(i * 60, 30, 20.0, 4.0) for i in range(2600)])
    displayed = _displayed(db, [1, 2, 3])
    started = time.perf_counter()
    trip_energy.select_energy(db, displayed)
    elapsed = time.perf_counter() - started
    assert all(t.get("energy_source") == "cloud" for t in displayed), \
        [t.get("energy_source") for t in displayed]
    assert elapsed < 0.35, (
        f"{elapsed:.2f}s to show three trips out of 2600 — the match is still walking the whole "
        f"history for every record"
    )
