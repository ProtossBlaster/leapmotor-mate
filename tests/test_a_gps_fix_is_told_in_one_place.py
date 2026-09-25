"""A poll that came back without a GPS fix is stored as (0, 0). `has_gps_fix` is the one test for it:
`get_latest_status` falls back to the last real fix on it, and anything that draws the car reads it
too, instead of each caller deciding what "no position" looks like."""
import db as D
import db_reader
import pytest


@pytest.mark.parametrize("lat, lon, fix", [
    (0.0, 0.0, False),            # the poll had no fix
    (None, 16.9, False),
    (52.4, None, False),
    (52.4, 0.0, True),            # the prime meridian is a real place…
    (0.0, 17.9, True),            # …and so is the equator
    (52.4, 16.9, True),
])
def test_only_the_pair_of_zeros_means_no_fix(lat, lon, fix):
    assert db_reader.has_gps_fix(lat, lon) is fix


@pytest.fixture
def positions(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    pdb._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'V','B10')")
    pdb._conn.commit()

    def add(lat, lon):
        pdb._conn.execute("INSERT INTO positions (vehicle_id, recorded_at, latitude, longitude)"
                          " VALUES (1, datetime('now'), ?, ?)", (lat, lon))
        pdb._conn.commit()
    return add


def test_a_poll_without_a_fix_falls_back_to_the_last_real_one(positions):
    positions(52.4, 16.9)
    positions(0.0, 0.0)
    s = db_reader.get_latest_status()
    assert (s["latitude"], s["longitude"], s.get("position_stale")) == (52.4, 16.9, True)


def test_a_fix_on_the_prime_meridian_is_not_replaced(positions):
    positions(52.4, 16.9)
    positions(51.48, 0.0)
    s = db_reader.get_latest_status()
    assert (s["latitude"], s["longitude"], s.get("position_stale")) == (51.48, 0.0, None)


def test_the_fallback_query_asks_the_same_question(positions):
    """The fallback query keeps the rule in SQL, on the same named bound; this holds it to
    `has_gps_fix`: a fix on the prime meridian is the one a later poll without a fix falls back to."""
    positions(52.4, 16.9)
    positions(51.48, 0.0)
    positions(0.0, 0.0)
    s = db_reader.get_latest_status()
    assert (s["latitude"], s["longitude"], s.get("position_stale")) == (51.48, 0.0, True)
