"""A minute without the cloud, mid-drive, must not cost the whole drive (D #331).

@synvoll, B10, after the LeapOS 3.41.30 update of 23 September. His log, that morning, nine times:

    05:51:49  parked_alert → driving        Trip #347 started
    05:58:31  driving      → offline
    05:59:33  offline      → driving        Offline stretch recorded — 2.0 km … attributed to no trip
    05:59:33                                Trip #348 started

Every dropout of about a minute abandoned the open trip and opened another. That morning: nine
`driving → offline`, ten trips opened, **one** closed — the 1 km fragment he could see — and 9 km
filed across seven "attributed to no trip" stretches, out of the 26 km his odometer counted. His
mid-drive dropouts per day tell the rest: 0 on 22 and 23 September, then 3, 2, 4 and 9.

`state_machine` treats OFFLINE → DRIVING exactly like UNKNOWN → DRIVING, a cold start, and the
recorder creates a trip for it. The charge path learned this in #208 and says so in its own
comment — re-entering CHARGING with a charge still open means we never unplugged — and the trip
path never did.

Filing the silent kilometres apart (#130, #233) is right when a trip is OPENING: nothing says the
silence belongs to the drive that is about to start. It is wrong here. The state before the
silence was DRIVING and the state after it is DRIVING, so the hole is inside this drive, and the
trip's own odometer endpoints already measure it.

Bounded by the same half hour that ends a frozen drive: beyond `FROZEN_DRIVE_LIMIT_S` Mate already
declares a drive over, so a longer silence opens a new trip and declares its kilometres, as before.
"""
from datetime import timedelta

import pytest
import state_machine as SM
from test_a_trip_ends_when_the_car_last_spoke import rig, _vd, _ms, T0


def _trips(db):
    return db._conn.execute("SELECT * FROM trips ORDER BY id").fetchall()


def _gaps(db):
    return db._conn.execute("SELECT * FROM offline_gaps ORDER BY id").fetchall()


def _outage(rec, poll, seconds, *, resume_odo, resume_minutes):
    """Three refused polls, the silence, then the cloud answers again with the car still in D."""
    for _ in range(3):
        rec.mark_offline()
    assert rec._sm.state == SM.State.OFFLINE
    poll(seconds, _vd(ts=_ms(T0 + timedelta(minutes=resume_minutes)), odo=resume_odo))


def test_one_drive_with_a_hole_in_it_stays_one_trip(rig):
    db, rec, poll, wall = rig
    poll(0, _vd(ts=_ms(T0), odo=1000))
    poll(60, _vd(ts=_ms(T0 + timedelta(minutes=1)), odo=1005))
    opened = _trips(db)
    assert len(opened) == 1
    _outage(rec, poll, 105, resume_odo=1007, resume_minutes=3)
    poll(60, _vd(ts=_ms(T0 + timedelta(minutes=4)), odo=1012))

    trips = _trips(db)
    assert len(trips) == 1, f"the dropout opened {len(trips)} trips instead of resuming one"
    assert trips[0]["id"] == opened[0]["id"]
    assert rec._active_trip_id == opened[0]["id"]
    assert _gaps(db) == [], "the kilometres of the hole belong to this drive, not to no trip"


def test_the_drive_still_ends_and_carries_the_whole_distance(rig):
    db, rec, poll, wall = rig
    poll(0, _vd(ts=_ms(T0), odo=1000))
    _outage(rec, poll, 105, resume_odo=1007, resume_minutes=3)
    poll(60, _vd(ts=_ms(T0 + timedelta(minutes=4)), odo=1012))
    for i in range(5, 5 + SM.PARKED_CONFIRM + 1):    # park it: PARKED_CONFIRM readings in P
        poll(60, _vd(ts=_ms(T0 + timedelta(minutes=i)), odo=1012, gear="P", speed=0.0))

    trips = _trips(db)
    assert len(trips) == 1 and trips[0]["ended_at"] is not None
    assert trips[0]["distance_km"] == pytest.approx(12.0)


def test_a_silence_longer_than_the_frozen_guard_still_opens_its_own_trip(rig):
    """Beyond half an hour Mate already calls a drive over; nothing says the car kept going."""
    db, rec, poll, wall = rig
    poll(0, _vd(ts=_ms(T0), odo=1000))
    _outage(rec, poll, SM.FROZEN_DRIVE_LIMIT_S + 60, resume_odo=1050, resume_minutes=40)

    assert len(_trips(db)) == 2, "a silence of more than 30 minutes is not one drive"
    assert len(_gaps(db)) == 1, "and its kilometres are declared on their own"


def test_an_outage_while_parked_is_unchanged(rig):
    """The #130/#233 rule stands where it was written for: a trip that is OPENING."""
    db, rec, poll, wall = rig
    poll(0, _vd(ts=_ms(T0), odo=1000, gear="P", speed=0.0))
    _outage(rec, poll, 105, resume_odo=1040, resume_minutes=3)

    assert len(_trips(db)) == 1
    assert len(_gaps(db)) == 1, "kilometres seen while parked and quiet are not this drive's"
