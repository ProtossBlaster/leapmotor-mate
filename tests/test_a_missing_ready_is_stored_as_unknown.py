"""A READY the car did not send is stored as unknown, not as switched off.

The parser turned an absent READY (1258) into False and `positions` stored 0, the same as a car
reported switched off. The history then could not tell a switch-off from a frame that said nothing
about it, and such a 0 became the last reading that saw the car off: where the cloud energy window
of the next drive opens, although the car may already have been on.

The flag in memory stays a bool for MQTT and the READY automation. The stored row and the status
card say "unknown".
"""
from datetime import timedelta

import client
import db as D
import db_reader
import pytest
import recorder as R
from test_a_missing_speed_or_odometer_is_stored_as_unknown import _ABSENT, _signal
from test_a_trip_ends_when_the_car_last_spoke import _ms, _vd, rig  # noqa: F401  (rig: fixture)
from test_absent_temperature_is_not_zero import _render_card, _row


def _with_ready(ready=_ABSENT):
    sig = _signal(speed=0, odometer=12345)
    if ready is not _ABSENT:
        sig["1258"] = ready
    return sig


@pytest.mark.parametrize("sent, stored, on", [(1, 1, True), ("1", 1, True), (0, 0, False),
                                              (_ABSENT, None, False), (None, None, False)])
def test_the_stored_ready_is_the_one_the_car_sent(tmp_path, sent, stored, on):
    db = D.Database(str(tmp_path / "t.db"))
    rec = R.Recorder(db, vehicle_id=db.ensure_vehicle("TESTVIN", "B10"))
    data = client._parse_signal("TESTVIN", _with_ready(sent))
    rec.process(data)
    assert data.ready is on, "the flag in memory stays a bool"
    assert db._conn.execute("SELECT ready FROM positions").fetchone()["ready"] == stored


@pytest.mark.parametrize("fields, ready, reported", [({"bcmKeyPositionOn3": 1}, True, True),
                                                     ({"bcmKeyPositionOn3": 0}, False, True),
                                                     ({"bcmKeyPositionOn3": None}, False, False),
                                                     ({}, False, False)])
def test_named_field_responses_say_the_same(fields, ready, reported):
    """T03 / EU responses carry named fields instead of numeric signals."""
    sig = client._named_fields_to_signal({"preciseSoc": 80.0, "gearStatus": 0, **fields})
    data = client._parse_signal("TESTVIN", sig)
    assert (data.ready, data.ready_reported) == (ready, reported)


@pytest.mark.parametrize("ready, says", [(None, "—"), (0, "ready_state_off"), (1, "ready_state_on")])
def test_the_status_card_says_a_dash_when_the_car_did_not_say(ready, says):
    row = _row(_render_card(ready=ready), "READY")
    assert says in row
    assert ready is not None or "ready_state_off" not in row


def _car(rig):
    """The real recorder, fed a fresh frame every 10 s; READY None is a frame that did not carry it."""
    db, rec, poll, wall = rig
    odo = [1000.0]

    def send(gear="D", ready=True, plug=False):
        odo[0] += 0.2 if gear == "D" else 0.0
        d = _vd(ts=_ms(wall["now"] + timedelta(seconds=10)), odo=odo[0], gear=gear,
                speed=50.0 if gear == "D" else 0.0)
        d.ready, d.ready_reported, d.plug_connected = bool(ready), ready is not None, plug
        poll(10, d)
        return d

    return db, rec, poll, send


def _sessions(db, monkeypatch):
    """The power-on session the web reads for each of the two trips."""
    trips = [dict(t) for t in db._conn.execute("SELECT * FROM trips ORDER BY id")]
    assert [t["ended_at"] is not None for t in trips] == [True, True]
    monkeypatch.setattr(db_reader, "DB_PATH", db._conn.execute("PRAGMA database_list").fetchone()[2])
    return [db_reader.ready_session(trip)["trip_ids"] for trip in trips]


def _epoch(when):
    return int(when.timestamp())


def _only_trip(db, monkeypatch):
    trip, = [dict(t) for t in db._conn.execute("SELECT * FROM trips")]
    assert trip["ended_at"] is not None
    monkeypatch.setattr(db_reader, "DB_PATH", db._conn.execute("PRAGMA database_list").fetchone()[2])
    return trip


def test_a_poll_without_ready_is_not_where_the_energy_window_opens(rig, monkeypatch):
    """The car is seen off, then a poll in P says nothing about READY — it may already be on — and
    then the drive. The getEC window opens at the last zero actually read."""
    db, _rec, _poll, send = _car(rig)
    _db, _rec, _poll, wall = rig
    for _ in range(3):
        send(gear="P", ready=False)
    seen_off = wall["now"]
    send(gear="P", ready=None)
    for _ in range(10):
        send()
    for _ in range(6):
        send(gear="P", ready=False)
    trip = _only_trip(db, monkeypatch)
    assert db_reader.ready_session(trip)["on_lo"] == _epoch(seen_off)
    assert db_reader.trip_ec_window(trip)[0] == _epoch(seen_off)


def test_a_moment_in_p_without_ready_keeps_the_drive_one_power_on(rig, monkeypatch):
    """A stop of a few seconds in P, to switch one-pedal driving on or off, on frames without READY:
    the drive goes on as one trip in one power-on, as it would with a switch-off read for a moment."""
    db, _rec, _poll, send = _car(rig)
    _db, _rec, _poll, wall = rig
    for _ in range(3):
        send(gear="P", ready=False)
    for _ in range(10):
        send()
    started = wall["now"] - timedelta(seconds=90)
    for _ in range(2):
        send(gear="P", ready=None)
    for _ in range(10):
        send()
    last_on = wall["now"]
    for _ in range(6):
        send(gear="P", ready=False)
    session = db_reader.ready_session(_only_trip(db, monkeypatch))
    assert session["trip_ids"] == [1]
    assert session["on"] <= _epoch(started) and session["off"] == _epoch(last_on)


def test_a_trip_the_frozen_frame_guard_closes_in_gear_shares_no_power_on(rig, monkeypatch):
    """The frozen-frame guard closes a trip in gear, after half an hour of one repeated frame. When
    that frame carries no READY, nothing says the car stayed on until the next drive."""
    db, rec, poll, send = _car(rig)
    for _ in range(10):
        send()
    frozen = send(ready=None)
    for _ in range(186):
        poll(10, frozen)
    assert rec._active_trip_id is None, "the guard should have closed the trip"
    for _ in range(10):
        send()
    for _ in range(6):
        send(gear="P", ready=False)
    assert _sessions(db, monkeypatch) == [[1], [2]]
