"""A coordinate is a finite number within its range, and a frame missing one of its two axes is no fix.

The cloud has sent "" for a signal before, and nothing stops a frame carrying NaN, "inf" or a value
past the pole. Such a value is no reading: the next signal for the same axis is used, and when none
is left the frame has no coordinate for that axis. A frame without one of the two places the car
nowhere — (0, 0), the no-fix marker the Overview and the trip already skip, so the map keeps the last
fix and says it is stale — and teaches the sign memory nothing, as neither does a frame that fails to
parse. A coordinate the frame did send as 0 is a reading: a car on the equator keeps it.

The poller and the web's write after a command apply the same rule (gps_position).
"""
import client
import db_reader
import gps_position
import pytest
from test_a_trip_ends_when_the_car_last_spoke import make_rig, _ms, T0

PORTO = {"2": -8.61, "3": 41.15, "3724": 8.61, "3725": 41.15,
         "1318": 2008, "1010": 0, "1319": 0.0, "100003": 62.6}
MIRRORED = dict(PORTO, **{"2": 8.61, "1318": 2009})           # a kilometre on, its minus dropped


def _without(frame, *ids):
    return {k: v for k, v in frame.items() if k not in ids}


@pytest.fixture(autouse=True)
def _clean_memory():
    yield
    client._gps.clear()


def _pos(vin, sig):
    data = client._parse_signal(vin, sig)
    return data.latitude, data.longitude


# ── what a coordinate is ──────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["nan", "inf", "-inf", "181", "abc", ""])
def test_a_signed_value_that_is_no_coordinate_gives_way_to_the_magnitude(bad):
    _pos("VINC1", PORTO)                                          # learns lon = −
    assert _pos("VINC1", dict(PORTO, **{"2": bad})) == (41.15, -8.61)


@pytest.mark.parametrize("bad", ["nan", "inf", "95", "abc", ""])
def test_a_frame_without_a_latitude_is_no_fix(bad):
    assert _pos("VINC2", dict(_without(PORTO, "3725"), **{"3": bad})) == (0.0, 0.0)


def test_a_frame_without_a_longitude_signal_at_all_is_no_fix():
    """It used to place the car on the meridian: the latitude was real, the longitude 0."""
    assert _pos("VINC3", _without(PORTO, "2", "3724")) == (0.0, 0.0)


def test_a_coordinate_sent_as_zero_is_kept():
    """Lake Victoria sits on the equator. A 0 the frame sent is a reading, not a gap."""
    equator = {"2": 32.58, "3": "0", "3724": 32.58, "3725": "0", "1318": 10, "100003": 50.0}
    assert _pos("VINC4", equator) == (0.0, 32.58)


def test_an_unreadable_magnitude_gives_way_to_the_next_one():
    assert _pos("VINC5", dict(_without(PORTO, "3"), **{"3725": "nan", "2190": 41.15})) == (41.15, -8.61)


# ── it teaches nothing ────────────────────────────────────────────────────────

def test_a_partial_frame_does_not_count_towards_a_sign_flip():
    """Nine mirrored frames and then one with no latitude: had it counted, it would have been the
    tenth and taught the car the wrong side of the meridian."""
    _pos("VINC6", PORTO)
    for _ in range(gps_position.FLIP_CONFIRMATIONS - 1):
        _pos("VINC6", MIRRORED)
    assert _pos("VINC6", _without(MIRRORED, "3", "3725")) == (0.0, 0.0)
    assert client.get_coord_signs("VINC6") == {"lat": 1.0, "lon": -1.0}


def test_a_frame_that_fails_to_parse_teaches_nothing():
    """A lock state that is not a number fails the frame after its coordinates were read."""
    _pos("VINC7", PORTO)
    for _ in range(gps_position.FLIP_CONFIRMATIONS - 1):
        _pos("VINC7", MIRRORED)
    with pytest.raises(ValueError):
        client._parse_signal("VINC7", dict(MIRRORED, **{"1298": "abc"}))
    assert client.get_coord_signs("VINC7")["lon"] == -1.0
    assert _pos("VINC7", MIRRORED) == (41.15, 8.61)               # the tenth is this one


# ── what the Overview shows, from either writer ───────────────────────────────

def test_the_overview_keeps_the_last_fix_and_says_it_is_stale(tmp_path, monkeypatch):
    db, rec, poll, _ = make_rig(tmp_path, monkeypatch)
    monkeypatch.setattr(db_reader, "DB_PATH", db._path)
    monkeypatch.setattr(db_reader, "_current_vehicle_id", lambda: rec._vehicle_id)
    poll(0, client._parse_signal("TESTVIN", dict(PORTO, **{"1": _ms(T0)})))
    partial = dict(_without(PORTO, "2", "3724"), **{"1": _ms(T0) + 30_000})
    poll(30, client._parse_signal("TESTVIN", partial))
    shown = db_reader.get_latest_status()
    assert (shown["latitude"], shown["longitude"], shown.get("position_stale")) == (41.15, -8.61, True)

    db_reader.save_fresh_signals(partial, "TESTVIN")              # the write after a command
    stored = db._conn.execute("SELECT latitude, longitude FROM positions ORDER BY id DESC LIMIT 1").fetchone()
    assert tuple(stored) == (0.0, 0.0)
    shown = db_reader.get_latest_status()
    assert (shown["latitude"], shown["longitude"], shown.get("position_stale")) == (41.15, -8.61, True)


@pytest.mark.parametrize("frame", [
    _without(PORTO, "2", "3724"), dict(PORTO, **{"3": "nan", "3725": "inf"}), dict(PORTO, **{"2": "inf"}),
    dict(PORTO, **{"2": "", "3724": "190"}), _without(PORTO, "2"), dict(PORTO, **{"3": "0", "3725": "0"}),
], ids=["no-longitude", "latitude-not-a-number", "signed-infinite", "magnitude-out-of-range",
        "unsigned-only", "latitude-zero"])
def test_the_web_places_a_frame_where_the_poller_does(frame, monkeypatch):
    client.seed_coord_signs("VINC8", 1.0, -1.0)
    signs = {"gps_lat_sign_vinc8": "1.0", "gps_lon_sign_vinc8": "-1.0"}
    monkeypatch.setattr(db_reader, "get_setting", lambda key, default=None: signs.get(key, default))
    assert db_reader.position_from_signals(frame, "VINC8") == _pos("VINC8", frame)
