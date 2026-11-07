"""Only a frame newer than every frame before can argue for a hemisphere flip, or teach a sign.

Far from the line, a reading on the other side of it is believed after ten in a row (#232). The
cloud re-serves the last frame it has — clock and all — while the car sleeps or has no signal, and
it has replayed older frames: counted per poll, one frame with its minus dropped became ten
witnesses of a move. Now a repeat, or a frame older than the newest one seen, is the same
observation again: it is placed by what the memory holds, and counts and teaches nothing. The
newest usable clock on record seeds this after a restart: not the last row's, which can be a late
frame's, nor one too far ahead to be a clock.

A frame without a fix is heard all the same. A frame without a usable clock — none, not positive,
or more than five minutes ahead of ours — cannot be ordered and counts as the poll it arrived in, as
before: refusing it for good could hold a car that really moved on the wrong side of the line
forever.
"""
import time

import client
import gps_position
import pytest
from test_a_trip_ends_when_the_car_last_spoke import make_rig, _ms, T0
from test_gps_sign_is_rederived_from_history import poller_main

NOW = _ms(T0)
PORTO = {"2": -8.61, "3": 41.15, "3724": 8.61, "3725": 41.15,
         "1318": 2008, "1010": 0, "1319": 0.0, "100003": 62.6}
MIRRORED = dict(PORTO, **{"2": 8.61, "1318": 2009})           # a kilometre on, its minus dropped
MILAN = {"2": 9.12, "3": 45.44, "3724": 9.12, "3725": 45.44, "1318": 2008, "100003": 60.0}


def _at(frame, ts):
    return dict(frame, **{"1": ts})


@pytest.fixture(autouse=True)
def _clean_memory(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: NOW / 1000)
    yield
    client._gps.clear()


def _lon(vin, sig):
    return client._parse_signal(vin, sig).longitude


def test_a_frame_the_cloud_keeps_re_serving_is_one_observation():
    _lon("VINF1", _at(PORTO, NOW - 600_000))
    stuck = _at(MIRRORED, NOW - 300_000)
    for _ in range(30):
        assert _lon("VINF1", stuck) == -8.61
    assert client.get_coord_signs("VINF1")["lon"] == -1.0


def test_two_old_frames_replayed_in_turn_do_not_add_up():
    """Each differs from the frame before it, and neither is newer than the newest seen."""
    _lon("VINF2", _at(MIRRORED, NOW - 900_000))                   # an old mirrored frame…
    _lon("VINF2", _at(PORTO, NOW - 300_000))                      # …then the newest, west
    for i in range(30):
        _lon("VINF2", _at(MIRRORED, NOW - (900_000 if i % 2 else 600_000)))
    assert client.get_coord_signs("VINF2")["lon"] == -1.0


def test_a_late_frame_does_not_teach_its_sign():
    """Even a negative reading, which is otherwise believed at once: it is where the car was."""
    _lon("VINF3", _at(MILAN, NOW - 60_000))
    assert _lon("VINF3", _at(PORTO, NOW - 120_000)) == -8.61       # shown as it came…
    assert client.get_coord_signs("VINF3")["lon"] == 1.0          # …and taught nothing


def test_a_frame_without_a_fix_is_heard_too():
    """It places the car nowhere, but a frame older than it is still a late one."""
    _lon("VINF7", _at(MILAN, NOW - 600_000))
    _lon("VINF7", _at({k: v for k, v in MILAN.items() if k not in ("3", "3725")}, NOW - 60_000))
    _lon("VINF7", _at(PORTO, NOW - 300_000))
    assert client.get_coord_signs("VINF7")["lon"] == 1.0


def test_new_frames_still_argue_their_way_through():
    _lon("VINF4", _at(PORTO, NOW - 600_000))
    for i in range(gps_position.FLIP_CONFIRMATIONS - 1):
        assert _lon("VINF4", _at(MIRRORED, NOW - 500_000 + i)) == -8.61
    assert _lon("VINF4", _at(MIRRORED, NOW - 400_000)) == 8.61


@pytest.mark.parametrize("clock", [None, 0, -1])
def test_frames_without_a_clock_count_per_poll_as_before(clock):
    _lon("VINF5", _at(PORTO, NOW - 600_000))
    for _ in range(gps_position.FLIP_CONFIRMATIONS - 1):
        _lon("VINF5", _at(MIRRORED, clock))
    assert _lon("VINF5", _at(MIRRORED, clock)) == 8.61


@pytest.mark.parametrize("ahead_s,usable", [(300 - 1, True), (300 + 1, False)])
def test_a_clock_more_than_five_minutes_ahead_is_no_clock(ahead_s, usable):
    """A usable stamp becomes the newest, and the frames after it are older; a bogus one must not,
    or every real frame after it could never count again."""
    _lon("VINF6", _at(PORTO, NOW - 600_000))
    _lon("VINF6", _at(PORTO, NOW + ahead_s * 1000))
    for i in range(gps_position.FLIP_CONFIRMATIONS):
        _lon("VINF6", _at(MIRRORED, NOW - 500_000 + i))
    assert client.get_coord_signs("VINF6")["lon"] == (-1.0 if usable else 1.0)


def test_a_stamp_far_ahead_does_not_hold_a_crossing_back(monkeypatch):
    """One frame stamped two hours ahead, then an hour's drive east across the meridian at 130 km/h
    (0.54 km a frame): the crossing is learned near the line, and past 1° the car is still east."""
    host = [NOW - 3_600_000]
    monkeypatch.setattr(time, "time", lambda: host[0] / 1000)

    def drive(i, stamp):
        lon = round(-0.5 + i * 0.54 / 69.4, 4)
        return dict(PORTO, **{"1": stamp, "2": lon, "3": 51.5, "3724": abs(lon), "3725": 51.5,
                              "1318": 2008 + int(i * 0.54)})
    _lon("VINF8", drive(0, host[0] - 5_000))
    _lon("VINF8", drive(1, host[0] + 7_200_000))
    for i in range(2, 240):
        host[0] += 15_000
        shown = _lon("VINF8", drive(i, host[0] - 5_000))
    assert shown > 1.0 and client.get_coord_signs("VINF8")["lon"] == 1.0


def test_after_a_restart_the_frame_on_record_is_not_new(tmp_path, monkeypatch):
    """The car fell asleep on a frame without its minus, and the poller restarted. That frame is
    re-served first; nine new ones after it are not yet ten."""
    db, rec, poll, _ = make_rig(tmp_path, monkeypatch)
    db.set_gps_signs("TESTVIN", "1.0", "-1.0")
    poll(0, client._parse_signal("TESTVIN", _at(PORTO, NOW - 600_000)))
    poll(30, client._parse_signal("TESTVIN", _at(MIRRORED, NOW - 300_000)))
    client._gps.clear()                                           # the poller restarts
    poller_main.reconcile_coord_signs(db, rec._vehicle_id, "TESTVIN")
    _lon("TESTVIN", _at(MIRRORED, NOW - 300_000))                  # re-served
    for i in range(gps_position.FLIP_CONFIRMATIONS - 1):
        _lon("TESTVIN", _at(MIRRORED, NOW - 200_000 + i))
    assert client.get_coord_signs("TESTVIN")["lon"] == -1.0


def _restart(db, rec):
    client._gps.clear()
    poller_main.reconcile_coord_signs(db, rec._vehicle_id, "TESTVIN")


def test_a_late_frame_stored_last_does_not_take_the_newest_back(tmp_path, monkeypatch):
    """Frames older than the newest one heard before the restart count for nothing after it, even
    when a late frame is the last row on record; ten newer ones still argue their way through."""
    db, rec, poll, _ = make_rig(tmp_path, monkeypatch)
    db.set_gps_signs("TESTVIN", "1.0", "-1.0")
    poll(0, client._parse_signal("TESTVIN", _at(PORTO, NOW - 300_000)))
    poll(30, client._parse_signal("TESTVIN", _at(PORTO, NOW - 900_000)))
    assert db.get_last_frame_ts(rec._vehicle_id) == NOW - 900_000
    _restart(db, rec)
    for i in range(3 * gps_position.FLIP_CONFIRMATIONS):
        _lon("TESTVIN", _at(MIRRORED, NOW - 800_000 + i))
    assert client.get_coord_signs("TESTVIN")["lon"] == -1.0
    for i in range(gps_position.FLIP_CONFIRMATIONS):
        _lon("TESTVIN", _at(MIRRORED, NOW - 200_000 + i))
    assert client.get_coord_signs("TESTVIN")["lon"] == 1.0


def test_a_clock_far_ahead_on_record_is_no_clock_after_a_restart(tmp_path, monkeypatch):
    """Stored as it came, a frame stamped a year ahead must not become the newest one at startup
    either, or no real frame after it could ever count."""
    db, rec, poll, _ = make_rig(tmp_path, monkeypatch)
    db.set_gps_signs("TESTVIN", "1.0", "-1.0")
    poll(0, client._parse_signal("TESTVIN", _at(PORTO, NOW - 300_000)))
    poll(30, client._parse_signal("TESTVIN", _at(PORTO, NOW + 365 * 86_400_000)))
    assert db.get_last_frame_ts(rec._vehicle_id) == NOW + 365 * 86_400_000
    _restart(db, rec)
    for i in range(gps_position.FLIP_CONFIRMATIONS):
        _lon("TESTVIN", _at(MIRRORED, NOW - 200_000 + i))
    assert client.get_coord_signs("TESTVIN")["lon"] == 1.0
