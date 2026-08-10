"""A trip abandoned by the cloud must end when the car last spoke — not half an hour later.

THE HALF THAT WAS LEFT. v3.10.6 stopped a trip from *absorbing* kilometres nobody watched. This is
the other end of the same silence: a trip left open on a frozen "gear D" frame is closed by the
30-minute guard (#233), and it was closed with the clock reading NOW. So the trip carried up to
half an hour in which, by definition, nothing was observed — inflating its duration and deflating
its average speed, on a car that had already been parked in the drive the whole time.

The odometer was never the problem: the frozen frame IS the last real measurement, so the distance
was already right. Only the TIME lied.

THE PATTERN ALREADY EXISTED, one table over. Since #208 a charge the car drove away from is closed
on the last reading taken WHILE CHARGING, dated with the car's own clock (`frame_ts`) rather than
with the poll that noticed — on @mikeeeeekoo's overnight charge, the difference between 06:10 and
09:36. Trips had no equivalent and closed with whatever frame was in hand.

⚠️ The car's clock is preferred but CHECKED, exactly as the charge does: a `frame_ts` from before
the trip opened (host skew, or a partial frame carrying someone else's timestamp) would invert the
session, so it is only taken when it falls inside the trip.

🔑 During a frozen stretch the recorder does not save positions at all while DRIVING (#128), so the
last `positions` row of an abandoned trip already IS the last thing the car said. Nothing new has to
be recorded to know this — it was on disk the whole time.

CI-safe: pure recorder / state-machine / db logic, no fastapi.
"""
from datetime import datetime, timedelta, timezone

import pytest
import state_machine as SM
from client import VehicleData

import db as D
import recorder as R

T0 = datetime(2026, 8, 11, 18, 0, tzinfo=timezone.utc)


def _vd(*, ts, odo=1000, soc=80.0, gear="D", speed=50.0):
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
def rig(tmp_path, monkeypatch):
    """Two clocks, because the code uses two: `time.monotonic` for how long a frame has been
    repeated, and the wall clock for what gets written down."""
    wall = {"now": T0}
    mono = {"t": 10_000.0}
    monkeypatch.setattr(SM.time, "monotonic", lambda: mono["t"])
    monkeypatch.setattr(D, "_now_iso", lambda: wall["now"].isoformat())
    monkeypatch.setattr(R, "_now_iso", lambda: wall["now"].isoformat())

    db = D.Database(str(tmp_path / "t.db"))
    db.set_battery_capacity(65.0)
    vid = db.ensure_vehicle("TESTVIN", "B10")
    rec = R.Recorder(db, vehicle_id=vid)

    def poll(seconds, data):
        wall["now"] += timedelta(seconds=seconds)
        mono["t"] += seconds
        rec.process(data)

    return db, rec, poll, wall


def _trip(db):
    return db._conn.execute("SELECT * FROM trips ORDER BY id DESC LIMIT 1").fetchone()


def _ms(when):
    return int(when.timestamp() * 1000)


def _drive_then_freeze(poll, wall, *, minutes_frozen):
    """Drive with fresh frames for five minutes, then let the cloud repeat one frame."""
    poll(0, _vd(ts=_ms(T0), odo=1000))
    for i in range(1, 6):                                    # five minutes of real driving
        poll(60, _vd(ts=_ms(T0 + timedelta(minutes=i)), odo=1000 + i))
    last_spoke = wall["now"]
    for _ in range(int(minutes_frozen * 6)):                 # …then the same frame, every 10 s
        poll(10, _vd(ts=_ms(T0 + timedelta(minutes=5)), odo=1005))
    return last_spoke


def test_a_trip_frozen_out_ends_when_the_car_last_spoke(rig):
    db, rec, poll, wall = rig
    last_spoke = _drive_then_freeze(poll, wall, minutes_frozen=31)

    t = _trip(db)
    assert t["ended_at"] is not None, "the guard did not close the trip at all"
    assert t["ended_at"] == last_spoke.isoformat(), (
        "the trip ends when the guard fired, not when the car was last heard")


def test_the_duration_stops_counting_the_silence(rig):
    """Five minutes of driving, half an hour of nothing. The trip is five minutes long."""
    db, rec, poll, wall = rig
    _drive_then_freeze(poll, wall, minutes_frozen=31)
    assert _trip(db)["duration_min"] == pytest.approx(5.0, abs=0.2)


def test_the_distance_was_never_wrong_and_stays_put(rig):
    """The frozen frame IS the last real measurement, so the odometer was always right. This pins
    it: the fix must move the clock and nothing else."""
    db, rec, poll, wall = rig
    _drive_then_freeze(poll, wall, minutes_frozen=31)
    t = _trip(db)
    assert t["start_odometer_km"] == 1000 and t["distance_km"] == 5


def test_a_normal_trip_still_ends_now(rig):
    """The overwhelmingly common case: the car reports gear P, six times, on fresh frames. Nothing
    about the ending may move — the last thing it said is also this instant."""
    db, rec, poll, wall = rig
    poll(0, _vd(ts=_ms(T0), odo=1000))
    for i in range(1, 4):
        poll(60, _vd(ts=_ms(T0 + timedelta(minutes=i)), odo=1000 + i))
    for i in range(4, 11):                                   # PARKED_CONFIRM, on fresh frames
        poll(60, _vd(ts=_ms(T0 + timedelta(minutes=i)), odo=1003, gear="P", speed=0.0))
    t = _trip(db)
    # Deliberately not pinned to an exact minute: the closing poll is whichever one completes
    # PARKED_CONFIRM, and hard-coding it makes the test about the constant instead of the rule.
    # The rule is that the end is essentially NOW — never dragged back into the silence.
    assert t["ended_at"] >= (T0 + timedelta(minutes=8)).isoformat()
    assert t["duration_min"] == pytest.approx(9.0, abs=1.1)


def test_the_car_s_own_clock_wins_over_ours(rig, tmp_path, monkeypatch):
    """The heart of the borrowed pattern, and the one thing the other tests could not see: when the
    two clocks DISAGREE, the end comes from the car.

    ⚠️ Every other case here polls at exactly the frame's own time, so preferring one clock or the
    other lands on the same second and the choice is invisible — a mutation that ignored `frame_ts`
    entirely survived on that fixture. Here the poller is deliberately 45 s behind the car."""
    db, rec, poll, wall = rig
    car = T0 + timedelta(seconds=45)                    # the car is ahead of our clock
    poll(0, _vd(ts=_ms(car), odo=1000))
    for i in range(1, 6):
        poll(60, _vd(ts=_ms(car + timedelta(minutes=i)), odo=1000 + i))
    car_last_spoke = car + timedelta(minutes=5)
    for _ in range(31 * 6):
        poll(10, _vd(ts=_ms(car + timedelta(minutes=5)), odo=1005))

    assert _trip(db)["ended_at"] == car_last_spoke.isoformat(), (
        "the end must carry the car's own timestamp, not the moment we happened to poll")


def test_a_frame_clock_from_before_the_trip_is_refused(rig):
    """The guard the charge close already has. A car whose frames carry a timestamp older than the
    trip's own start — host skew, or a partial frame holding someone else's clock — must not invert
    the session: the poller's own reading stands instead."""
    db, rec, poll, wall = rig
    poll(0, _vd(ts=_ms(T0 - timedelta(days=2)), odo=1000))
    for i in range(1, 6):
        poll(60, _vd(ts=_ms(T0 - timedelta(days=2) + timedelta(minutes=i)), odo=1000 + i))
    started_at = db._conn.execute("SELECT started_at FROM trips ORDER BY id DESC LIMIT 1"
                                  ).fetchone()["started_at"]
    for _ in range(31 * 6):
        poll(10, _vd(ts=_ms(T0 - timedelta(days=2) + timedelta(minutes=5)), odo=1005))

    t = _trip(db)
    assert t["ended_at"] > started_at, "a stale frame clock must never end a trip before it began"
