"""A trip must not stay open because the cloud stopped talking — #233, @riri19.

Ending a trip needs the cloud to SAY gear P, six times running. When it instead freezes on a frame
that says D — it re-serves the last frame it holds, indefinitely — those six readings never come.
The car is parked in the drive, and Mate reports "Driving" for the rest of the day with the parked
hours swallowed into the trip. He measured it himself: *«stuck in Drive mode for hours, sometimes
all day, even though it is properly parked and locked»*.

🔑 The threshold was measured on his bundle rather than chosen: 40 246 polls over 12 days.

- While the car is genuinely moving, a fresh frame arrives every **18 s** (median), 36 s at the
  95th percentile, **9.4 min at the 99th** — even on a link as poor as his.
- Of the **17** frozen stretches longer than 10 minutes, **all 17** ended with the odometer on the
  exact value it started at. Not one was a car still driving.

30 minutes therefore sits three times beyond the worst gap real driving produces, and still catches
the eight stretches that had left a trip open for one to six hours. If a car ever really is moving
through a dead zone that long, the cost is one drive recorded as two — which Mate can merge — against
a trip that stays open all day, which it cannot.

⚠️ Frame IDENTITY, never age: the same test the recorder uses for #128, so a host clock skewed
against the cloud (−48 s seen in the wild) cannot fool it either way.
"""
import time

import pytest
import state_machine as SM
from client import VehicleData
from state_machine import State, StateMachine


def _vd(*, gear="D", speed=50.0, ts=1000, soc=80.0, odo=1000):
    return VehicleData(
        vin="TESTVIN", timestamp_ms=ts, soc=soc, range_km=300, odometer_km=odo,
        speed_kmh=speed, gear=gear, vehicle_state="driving",
        charging_status=0, charge_power_kw=0.0, latitude=45.0, longitude=9.0,
        outside_temp=None, inside_temp=20.0, climate_target_temp=21.0, battery_min_temp=15.0,
        is_locked=True, climate_on=False, climate_cooling=False, climate_heating=False,
        climate_defrost=False, trunk_open=False, windows_open=False, sunshade_open=False,
        any_door_open=False, plug_connected=False, remaining_charge_min=0,
        charge_voltage_v=0.0, charge_current_a=0.0,
    )


@pytest.fixture
def clock(monkeypatch):
    """time.monotonic under our control — the state machine's only notion of elapsed time."""
    now = {"t": 10_000.0}
    monkeypatch.setattr(SM.time, "monotonic", lambda: now["t"])
    return now


def _driving(clock, ts=1000):
    sm = StateMachine()
    sm.update(_vd(ts=ts))               # first frame: fingerprint baseline
    clock["t"] += 10
    sm.update(_vd(ts=ts + 1))           # a second, fresh frame → DRIVING
    assert sm.state == State.DRIVING
    return sm


def _hold(sm, clock, minutes, *, ts, step=10):
    """Re-serve the SAME frame for `minutes`, the way the cloud does."""
    for _ in range(int(minutes * 60 / step)):
        clock["t"] += step
        sm.update(_vd(ts=ts))
    return sm


# ── the close ─────────────────────────────────────────────────────────────────

def test_a_trip_frozen_for_half_an_hour_is_closed(clock):
    sm = _driving(clock)
    _hold(sm, clock, 31, ts=1001)
    assert sm.state == State.PARKED_ACTIVE


def test_it_closes_once_and_not_once_every_poll(clock, caplog):
    """🔴 The bug this guard nearly shipped with, and the reason to replay a real outage instead of
    trusting a unit test.

    Closing the trip does not stop the cloud re-serving that same "gear D" frame. The next poll read
    it as a fresh departure, opened a new trip, found it 30 minutes stale on the spot, and closed it
    again — **one open/close every 10 seconds**, which over @riri19's six-hour outage is a couple of
    thousand one-second trips in his history.

    ⚠️ `test_a_trip_frozen_for_half_an_hour_is_closed` above could not see it: the state oscillates,
    so asserting the FINAL state reads DRIVING whether the latch is there or not. Count the events,
    not the endpoint. Seen red with the `_frozen_closed_ts` latch removed: 199 closes instead of 1.
    """
    sm = _driving(clock)
    closes = 0
    for _ in range(360):                     # an hour of the same frame, at the driving cadence
        clock["t"] += 10
        for ev in sm.update(_vd(ts=1001)):
            if ev.from_state == State.DRIVING and ev.to_state == State.PARKED_ACTIVE:
                closes += 1
    assert closes == 1, f"the trip must be given up on once, not {closes} times"


def test_a_fresh_frame_lifts_the_latch(clock):
    """The latch is on the FRAME, not on the car: when the link comes back and the car really is
    still driving, the next drive must open normally."""
    sm = _driving(clock)
    _hold(sm, clock, 31, ts=1001)
    assert sm.state == State.PARKED_ACTIVE
    clock["t"] += 10
    sm.update(_vd(ts=7777))                  # the cloud starts talking again, car still in D
    assert sm.state == State.DRIVING


def test_and_it_says_so_in_the_log(clock, caplog):
    """The poller log ships inside the bundle: this line is how the next one gets answered."""
    sm = _driving(clock)
    with caplog.at_level("WARNING", logger="state_machine"):
        _hold(sm, clock, 31, ts=1001)
    assert any("repeated" in r.getMessage() for r in caplog.records), \
        "closing a trip on a frozen frame must be visible"


# ── and everything it must NOT do ─────────────────────────────────────────────

def test_twenty_nine_minutes_is_not_enough(clock):
    """The boundary, from the side that must not fire. Seen red at FROZEN_DRIVE_LIMIT_S = 60."""
    sm = _driving(clock)
    _hold(sm, clock, 29, ts=1001)
    assert sm.state == State.DRIVING


def test_a_long_drive_with_fresh_frames_is_never_touched(clock):
    """Two hours of real driving. The frame id advances every poll, so the counter never builds —
    this is the case a plain age-based timeout would have got wrong."""
    sm = _driving(clock)
    for i in range(720):                    # 2 h at 10 s
        clock["t"] += 10
        sm.update(_vd(ts=2000 + i))
    assert sm.state == State.DRIVING


def test_one_fresh_frame_resets_the_clock(clock):
    """A dead zone the car drives back out of: 25 minutes frozen, one fresh frame, 25 more. Neither
    stretch reaches the limit, so the drive stays whole."""
    sm = _driving(clock)
    _hold(sm, clock, 25, ts=1001)
    clock["t"] += 10
    sm.update(_vd(ts=5555))                 # the link comes back
    _hold(sm, clock, 25, ts=5555)
    assert sm.state == State.DRIVING


def test_a_parked_car_is_not_dragged_anywhere_by_this(clock):
    """The guard lives in the DRIVING branch only. A parked car re-served the same frame for hours
    is ordinary — it is what every sleeping car looks like — and must not generate events."""
    sm = StateMachine()
    sm.update(_vd(gear="P", speed=0.0, ts=1000))
    before = sm.state
    for _ in range(400):
        clock["t"] += 30
        sm.update(_vd(gear="P", speed=0.0, ts=1000))
    assert sm.state in (before, State.PARKED_ACTIVE, State.PARKED_SLEEP)
    assert sm.state != State.DRIVING


def test_gear_p_still_closes_a_trip_the_normal_way(clock):
    """The ordinary path must be untouched: six P readings, no waiting for any limit."""
    sm = _driving(clock)
    for i in range(SM.PARKED_CONFIRM):
        clock["t"] += 10
        sm.update(_vd(gear="P", speed=0.0, ts=3000 + i))
    assert sm.state == State.PARKED_ACTIVE


def test_a_frame_with_no_timestamp_never_freezes_the_counter(clock):
    """Old rows and partial payloads carry no frame id. A missing id cannot prove two frames are
    the same one, so it must never be allowed to close a trip on its own."""
    sm = _driving(clock)
    for _ in range(400):
        clock["t"] += 10
        sm.update(_vd(ts=0))
    assert sm.state == State.DRIVING
