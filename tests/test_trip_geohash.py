"""trips.start_geohash/end_geohash — the similar-trips comparator's fast candidate
pre-filter (see test_similar_trips.py for the matching logic). Written at trip
creation/finalize (pure math on lat/lon already captured, no network call — unlike the
auto-note's reverse-geocoding there's no reason to defer this to a background sweep), and
backfilled once for every trip that predates the columns."""
import types

import db as D
import geohash


def _vd(lat=45.0, lon=9.0, soc=60.0, odometer_km=1000.0, speed_kmh=50.0):
    return types.SimpleNamespace(latitude=lat, longitude=lon, soc=soc,
                                 odometer_km=odometer_km, speed_kmh=speed_kmh)


def test_create_trip_sets_start_geohash(tmp_path):
    d = D.Database(str(tmp_path / "t.db"))
    tid = d.create_trip(1, _vd(45.4642, 9.1900))
    row = d._conn.execute("SELECT start_geohash FROM trips WHERE id=?", (tid,)).fetchone()
    assert row["start_geohash"] == geohash.encode(45.4642, 9.1900)


def test_create_trip_leaves_start_geohash_null_without_a_gps_fix(tmp_path):
    """Mirrors add_trip_position's own (0,0)/missing-fix guard — a null-island geohash
    would be a bogus candidate bucket."""
    d = D.Database(str(tmp_path / "t.db"))
    tid = d.create_trip(1, _vd(lat=0.0, lon=0.0))
    row = d._conn.execute("SELECT start_geohash FROM trips WHERE id=?", (tid,)).fetchone()
    assert row["start_geohash"] is None


def test_finalize_trip_sets_end_geohash(tmp_path):
    d = D.Database(str(tmp_path / "t.db"))
    tid = d.create_trip(1, _vd(45.0, 9.0))
    d.add_trip_position(tid, _vd(45.0, 9.0))
    d.add_trip_position(tid, _vd(45.01, 9.01))
    d.finalize_trip(tid, _vd(45.5, 9.5))
    row = d._conn.execute("SELECT end_geohash FROM trips WHERE id=?", (tid,)).fetchone()
    assert row["end_geohash"] == geohash.encode(45.5, 9.5)


def test_backfill_fills_existing_trips_without_touching_already_set_ones(tmp_path):
    d = D.Database(str(tmp_path / "t.db"))
    # A trip predating the migration: geohash NULL despite having real coordinates.
    d._conn.execute(
        "INSERT INTO trips (id, vehicle_id, start_lat, start_lon, end_lat, end_lon) "
        "VALUES (1, 1, 45.0, 9.0, 45.5, 9.5)")
    # A trip that ALREADY has a (deliberately wrong) geohash — backfill must not overwrite it.
    d._conn.execute(
        "INSERT INTO trips (id, vehicle_id, start_lat, start_lon, start_geohash) "
        "VALUES (2, 1, 45.0, 9.0, 'deadbeef')")
    d._conn.commit()

    d._backfill_trip_geohashes()

    row1 = d._conn.execute("SELECT start_geohash, end_geohash FROM trips WHERE id=1").fetchone()
    assert row1["start_geohash"] == geohash.encode(45.0, 9.0)
    assert row1["end_geohash"] == geohash.encode(45.5, 9.5)
    row2 = d._conn.execute("SELECT start_geohash FROM trips WHERE id=2").fetchone()
    assert row2["start_geohash"] == "deadbeef"   # untouched


def test_backfill_skips_trips_without_coordinates(tmp_path):
    d = D.Database(str(tmp_path / "t.db"))
    d._conn.execute("INSERT INTO trips (id, vehicle_id) VALUES (1, 1)")
    d._conn.commit()
    d._backfill_trip_geohashes()   # must not raise
    row = d._conn.execute("SELECT start_geohash, end_geohash FROM trips WHERE id=1").fetchone()
    assert row["start_geohash"] is None and row["end_geohash"] is None


def test_backfill_leaves_a_null_island_trip_without_a_geohash(tmp_path):
    """_resolve_coord returns 0.0 — never None — when a frame carries no usable GPS fix, so
    trips holding start_lat/start_lon = 0.0 are already sitting in every existing database.
    Geohashing those would drop all of them into one bogus bucket off West Africa, and a trip
    whose FIRST fix happened to be missing would then never match its own siblings on the same
    commute. create_trip already guards exactly this (see the test above); the backfill has to
    guard it identically or the guard only holds for trips recorded from now on."""
    path = str(tmp_path / "t.db")
    d = D.Database(path)
    d._conn.execute("INSERT INTO trips (id, vehicle_id, started_at, start_lat, start_lon, "
                    "end_lat, end_lon) VALUES (1,1,'2026-01-01T10:00:00+00:00',0.0,0.0,45.1,9.1)")
    d._conn.execute("UPDATE trips SET start_geohash=NULL, end_geohash=NULL")
    d._conn.commit()
    d._conn.close()

    d2 = D.Database(path)          # re-open → migrations + backfill run again
    row = d2._conn.execute("SELECT start_geohash, end_geohash FROM trips WHERE id=1").fetchone()
    assert row["start_geohash"] is None
    assert row["end_geohash"] == geohash.encode(45.1, 9.1)   # the real end is still filled


def test_backfill_survives_a_row_with_only_one_coordinate(tmp_path):
    """One coordinate set and the other NULL used to reach geohash.encode(lat, None), which
    raises TypeError out of Database.__init__ — so the poller would not start at all. No code
    path is known to write such a row, so this closes a latent trap rather than a live bug;
    the price is one falsy check that was needed anyway for the (0,0) case above."""
    path = str(tmp_path / "t.db")
    d = D.Database(path)
    d._conn.execute("INSERT INTO trips (id, vehicle_id, started_at, start_lat, start_lon, "
                    "end_lat, end_lon) VALUES (1,1,'2026-01-01T10:00:00+00:00',45.0,NULL,45.1,9.1)")
    d._conn.execute("UPDATE trips SET start_geohash=NULL, end_geohash=NULL")
    d._conn.commit()
    d._conn.close()

    d2 = D.Database(path)          # must not raise
    row = d2._conn.execute("SELECT start_geohash, end_geohash FROM trips WHERE id=1").fetchone()
    assert row["start_geohash"] is None
    assert row["end_geohash"] == geohash.encode(45.1, 9.1)
