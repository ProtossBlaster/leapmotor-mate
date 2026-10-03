"""A stop in P with the car still switched on is a stop INSIDE the trip, not its end.

You go to pick someone up. You do not switch off — you put it in P and wait. Mate used to close the
trip after a minute of P and open a second one when you drove on, so one errand came out as two
drives, and neither of them could be given the cloud's official energy figure: the cloud measures a
driving session from READY-on to power-off, so its figure covers the two halves together and belongs
to neither alone. The six P readings now only start counting once the car itself says the drive is
over (READY 1 → 0, signal 1258).

What does not change: a car that never reports READY closes on the sixth P reading exactly as
before, and so does a trip whose P reading is a frame the cloud has been repeating for half an hour
— that is a photograph of a car which may have been off for hours, and `FROZEN_DRIVE_LIMIT_S`
already owns it. The cable still ends a trip the moment it goes in.

The one trip that cannot end this way is the one whose car keeps READING on without ever moving:
`PARKED_READY_LIMIT_S` closes it after half a day — far outside any wait a person makes — and dates
it where the car stopped, not twelve hours later (`Database.trip_stood_still`).

CI-safe: pure recorder / state-machine / db logic, no fastapi.
"""
import pytest
import state_machine as SM
from test_a_trip_ends_when_the_car_last_spoke import _ms, _vd, rig  # noqa: F401  (rig: fixture)
from test_a_trip_ends_on_the_reading_that_shows_the_car_off import (  # noqa: F401  (car: fixture)
    _drive, _park, _points, _trips, car)
from test_an_outage_never_leaves_a_trip_open import _ledger

WAIT = [True] * 12          # two minutes in P with the car on — twice what used to close a trip


def test_a_wait_in_park_leaves_the_trip_open(car):
    db, _rec, send, _repeat, _wall = car
    odo = _drive(send)
    _park(send, odo, WAIT)
    trip, = _trips(db)
    assert trip["ended_at"] is None, "the driver is waiting, the drive is not over"


def test_driving_on_after_the_wait_is_one_trip(car):
    """The errand: five kilometres there, a wait, three kilometres back, then switched off."""
    db, _rec, send, _repeat, _wall = car
    odo = _drive(send)
    _park(send, odo, WAIT)
    odo = _drive(send, start=odo, km=3)
    times = _park(send, odo, [True] + [False] * SM.PARKED_CONFIRM)
    trips = _trips(db)
    assert len(trips) == 1, "one errand, one trip"
    assert trips[0]["ended_at"] == times[1].isoformat()
    assert trips[0]["end_odometer_km"] == 1008 and trips[0]["distance_km"] == pytest.approx(8.0)
    assert _ledger(db) == [(1000, 1008)], "no kilometre is counted twice, and none is lost"


def test_the_wait_is_inside_the_trip(car):
    """Its readings belong to the drive — the trip keeps them, as it keeps a red light's."""
    db, _rec, send, _repeat, _wall = car
    odo = _drive(send)
    _park(send, odo, WAIT)
    _park(send, odo, [False] * SM.PARKED_CONFIRM)
    assert _points(db) == 6 + len(WAIT) + 1, "the drive, the wait, and the reading it ended on"


def test_a_car_that_does_not_report_ready_ends_on_the_sixth_p_reading(car):
    """Without the signal there is nothing better to close on, so nothing changes for that car."""
    db, _rec, send, _repeat, _wall = car
    odo = _drive(send, ready=None)
    times = _park(send, odo, [None] * SM.PARKED_CONFIRM)
    assert _trips(db)[0]["ended_at"] == times[-1].isoformat()


def test_a_repeated_p_reading_still_ends_the_trip(car):
    """The cloud freezes on a frame that says "P, READY on". The car may have been switched off
    hours ago — the half-hour guard decides, exactly as before, and the trip ends where the car was
    last heard."""
    db, _rec, send, repeat, _wall = car
    odo = _drive(send)
    stopped = send(10, odo=odo, gear="P", ready=True)
    repeat(SM.FROZEN_DRIVE_LIMIT_S + 10 * SM.PARKED_CONFIRM)
    trip, = _trips(db)
    assert trip["ended_at"] == stopped.isoformat()


def test_half_a_day_standing_in_park_closes_the_trip_where_it_stopped(car):
    """A car that keeps reading ON without moving: no wait is half a day, so the trip is given up
    on — dated on the reading it stopped at, with the twelve hours of standstill left out of it."""
    db, _rec, send, _repeat, _wall = car
    odo = _drive(send)
    stopped, *_rest = _park(send, odo, WAIT)
    send(SM.PARKED_READY_LIMIT_S, odo=odo, gear="P", ready=True)
    trip, = _trips(db)
    assert trip["ended_at"] == stopped.isoformat()
    assert trip["end_odometer_km"] == 1005
    assert trip["duration_min"] == pytest.approx(5.2, abs=0.1), "the drive's minutes, not the day's"
    assert _points(db) == 6 + 1, "the standstill's points are no part of the drive"


def test_the_half_day_starts_over_when_the_car_moves_again(car):
    """Two long waits in one drive are two waits, not a day: the clock runs from the standstill the
    car is in, and driving on forgets it."""
    db, _rec, send, _repeat, _wall = car
    odo = _drive(send)
    send(SM.PARKED_READY_LIMIT_S - 600, odo=odo, gear="P", ready=True)   # 11 h 50 m in P
    odo = _drive(send, start=odo, km=2)
    send(SM.PARKED_READY_LIMIT_S - 600, odo=odo, gear="P", ready=True)   # and again
    trip, = _trips(db)
    assert trip["ended_at"] is None and len(_trips(db)) == 1


def test_the_guard_leaves_the_end_alone_when_it_would_lose_a_kilometre(car):
    """The cloud catches up during the standstill and shows another kilometre. Closing where the car
    stopped would leave it out of the trip, so the end stays the usual one — the same rule the
    switch-off follows."""
    db, _rec, send, _repeat, _wall = car
    odo = _drive(send)
    _park(send, odo, WAIT)
    last = send(SM.PARKED_READY_LIMIT_S, odo=odo + 1, gear="P", ready=True)
    trip, = _trips(db)
    assert trip["ended_at"] == last.isoformat() and trip["end_odometer_km"] == 1006


def test_a_cable_during_the_wait_ends_the_trip_at_once(car):
    """Plugged in while waiting: the drive is over, and it does not wait for the car to go off."""
    db, _rec, send, _repeat, _wall = car
    odo = _drive(send)
    _park(send, odo, WAIT)
    plugged = send(10, odo=odo, gear="P", ready=True, plug_connected=True)
    assert _trips(db)[0]["ended_at"] == plugged.isoformat()
