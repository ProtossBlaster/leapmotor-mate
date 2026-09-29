"""A trip's own opening frame is inside the trip.

The recorder saves every frame to `positions` first and only then lets the state machine decide
what it means, so the row of the frame that opens a trip is written a moment BEFORE the trip exists.
Stamped with its own clock read, the trip started after that row, and `trip_end_from_last_seen`
("rows recorded since the trip started") never saw it. A trip whose opening frame was the last
thing the car said — one frame in D, then the cloud repeating it until the frozen-drive guard gave up
— therefore found nothing to end on, and ended half an hour later, when the guard fired.

The test rig of the other trip tests reads one fixed time per poll, so the row and the trip share a
timestamp there and the defect cannot show. This clock ticks on every read, as a real one does.
"""
from datetime import timedelta

import pytest
import state_machine as SM
from test_a_trip_ends_when_the_car_last_spoke import T0, _ms, _vd, make_rig


@pytest.fixture
def ticking(tmp_path, monkeypatch):
    return make_rig(tmp_path, monkeypatch, tick=timedelta(milliseconds=1))


def test_the_opening_row_is_inside_the_trip(ticking):
    db, rec, poll, _wall = ticking
    poll(0, _vd(ts=_ms(T0), gear="P", speed=0.0))
    poll(30, _vd(ts=_ms(T0 + timedelta(seconds=30))))
    trip_id = rec._active_trip_id
    assert trip_id is not None
    assert db.trip_last_seen(trip_id) is not None, "the frame that opened the trip is not in it"


def test_a_trip_that_only_heard_its_opening_ends_there_not_when_the_guard_fires(ticking):
    db, _rec, poll, _wall = ticking
    poll(0, _vd(ts=_ms(T0), gear="P", speed=0.0))
    opened = T0 + timedelta(seconds=30)
    poll(30, _vd(ts=_ms(opened)))                      # one fresh frame in D…
    for _ in range(int(SM.FROZEN_DRIVE_LIMIT_S / 10) + 6):
        poll(10, _vd(ts=_ms(opened)))                  # …then the cloud repeats it
    trip = db._conn.execute("SELECT * FROM trips").fetchone()
    assert trip["ended_at"] is not None, "the frozen-drive guard did not close the trip"
    assert trip["duration_min"] == pytest.approx(0.0, abs=0.1), (
        "the trip carries the half hour the guard waited, not the moment the car last spoke")
