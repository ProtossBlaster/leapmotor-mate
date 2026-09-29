"""A trip ends on the last reading written, not on the latest one by our clock.

A trip is closed on the last `positions` row inside it (trip_last_seen): its end time always, and
after a long outage its odometer, SoC and position too. That row was picked by our clock, which can
step back (an NTP correction). The latest time is then an earlier reading: the trip ended before the
car's last frame and, after an outage, short of the odometer that frame gave, while the kilometres
after the silence are measured from the last reading. Nothing held the kilometre between the two.

CI-safe: pure recorder / state-machine / db logic, no fastapi.
"""
from datetime import timedelta

import pytest
import state_machine as SM
import test_an_outage_never_leaves_a_trip_open as outage
from test_a_trip_ends_when_the_car_last_spoke import T0, rig  # noqa: F401  (fixture)


def _drive_across_a_clock_step(poll, wall):
    """Five minutes on fresh frames, 1000 → 1005, with the host clock stepped back five minutes
    before the last one: that row is written last, with a time earlier than the one before it."""
    poll(0, outage._at(0, 1000))
    for i in range(1, 5):
        poll(60, outage._at(i, 1000 + i))
    wall["now"] -= timedelta(minutes=5)
    poll(60, outage._at(5, 1005))


def test_a_trip_parked_after_the_clock_stepped_back_ends_when_the_car_last_spoke(rig):
    db, _rec, poll, wall = rig
    _drive_across_a_clock_step(poll, wall)
    for k in range(1, SM.PARKED_CONFIRM + 1):           # parked, a frame every ten seconds
        poll(10, outage._park(5 + k / 6, 1005))

    trip, = outage._trips(db)
    assert trip["ended_at"] == (T0 + timedelta(minutes=6)).isoformat(), (
        "the trip ended on the frame before the step, not on the last one the car sent")
    assert trip["end_odometer_km"] == 1005


def test_a_long_outage_after_the_clock_stepped_back_closes_on_the_last_reading(rig):
    db, rec, poll, wall = rig
    _drive_across_a_clock_step(poll, wall)
    outage._outage(rec, poll, outage.LONG, outage._park(40, 1025))

    trip, rebuilt = outage._trips(db)
    assert (trip["ended_at"], trip["end_odometer_km"]) == (outage.LAST_SPOKE, 1005)
    assert trip["end_soc"] == pytest.approx(79.0)
    assert trip["end_lat"] == pytest.approx(45.0 + 5 * outage.KM)
    assert rebuilt["reconstructed"] == 1
    assert outage._ledger(db) == [(1000, 1005), (1005, 1025)], "a kilometre belonged to nothing"
