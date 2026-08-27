"""B (disc #261): a trip's temperature comes from the LIVE samples the poller recorded along it
(positions.outside_temp), not just the two hourly Open-Meteo endpoints — and the post-trip lookup is
the fallback only when the live feature was off, so the trip carries no sample.
"""
import pathlib

import db as D
import db_reader
import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    d = D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    d._conn.execute("INSERT INTO vehicles (id,vin,car_type) VALUES (1,'V','B10')")
    d._conn.execute("INSERT INTO trips (id,vehicle_id,started_at,ended_at,distance_km) "
                    "VALUES (7,1,'2026-07-05T08:00:00+00:00','2026-07-05T09:00:00+00:00',50)")
    d._conn.commit()
    return d


def _pos(d, ts, outside_temp):
    d._conn.execute("INSERT INTO positions (vehicle_id,recorded_at,outside_temp) VALUES (1,?,?)",
                    (ts, outside_temp))
    d._conn.commit()


def test_first_and_last_sample_are_the_trip_temperature(db):
    _pos(db, "2026-07-05T08:05:00+00:00", 18.0)
    _pos(db, "2026-07-05T08:30:00+00:00", 20.5)
    _pos(db, "2026-07-05T08:55:00+00:00", 22.0)
    assert db_reader.trip_outside_temp_samples(7) == (18.0, 22.0)


def test_samples_outside_the_trip_are_ignored(db):
    _pos(db, "2026-07-05T07:00:00+00:00", 5.0)     # before the trip started
    _pos(db, "2026-07-05T08:30:00+00:00", 21.0)    # during
    _pos(db, "2026-07-05T10:00:00+00:00", 30.0)    # after it ended
    assert db_reader.trip_outside_temp_samples(7) == (21.0, 21.0)


def test_no_samples_returns_none_so_the_lookup_takes_over(db):
    _pos(db, "2026-07-05T08:30:00+00:00", None)     # live feature off → position carries no reading
    assert db_reader.trip_outside_temp_samples(7) == (None, None)


def test_the_enrichment_prefers_the_samples_then_falls_back():
    """elevation_enrich reads the live samples first and only calls the post-trip Open-Meteo lookup
    when there are none — so a trip with samples costs zero extra weather calls."""
    src = (pathlib.Path(db_reader.__file__).resolve().parent / "elevation_enrich.py").read_text()
    assert "db_reader.trip_outside_temp_samples(seg_id)" in src
    # the post-trip Open-Meteo lookup runs only when the samples came back empty
    assert "if temp_start is None and temp_end is None:" in src
