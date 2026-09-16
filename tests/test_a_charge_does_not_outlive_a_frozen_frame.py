"""A charge must not stay open because the cloud stopped talking — #289, @juan-conca.

THE BUG. His C10 charges from a 2 kW solar wallbox inside a programmed window, 12:00→16:00. At
16:00:26 the current stops and the cable still reads connected, so the session stays open — which
is deliberate, because a modulating wallbox pauses exactly like that. Thirty seconds later the car
goes to sleep, and the Leapmotor cloud does what it always does when the car is quiet: it re-serves
the LAST frame it holds, poll after poll, for **1 716 polls** — a frame whose age reached 53 873 s,
just under fifteen hours. That frame says `plug=1 chg=0 A=0.1`, so the cable term holds the session
open on a photograph taken at 16:00. The charge finally closed at 06:59 the next morning, when the
car woke and reported the cable as deferred to the next window: **1 138 minutes for a charge of 4
hours**, and no row visible in the meantime, because the list filters on `ended_at IS NOT NULL`.

WHY IT IS NOT THE PAUSE CASE. Frame IDENTITY separates them, with no threshold to tune. A wallbox
pause arrives on FRESH frames — the car is awake and pushing — so the counter never builds. Only a
car that has stopped talking repeats one frame, and a repeated frame is not a reading.

THE THRESHOLD, measured on his bundle (38 412 polls, 12 days) rather than chosen. While current is
genuinely flowing the frame is 2 s old (median), 5 s at the 95th, 44 s at the 99th, and **832 s
(13.9 min) at its worst**. Thirty minutes therefore sits twice beyond the worst gap real charging
produced, and matches FROZEN_DRIVE_LIMIT_S, its twin on the trip side (#233).

WHY ONLY WHEN THE FROZEN FRAME READS NO CURRENT. Of 16 040 readings taken while the state was
CHARGING, a frozen frame claimed current **zero** times: 9 224 frozen polls all read `chg=0`. A
frozen frame that still reads current would be a different animal — a car charging in a dead zone,
whose SoC will jump when it returns — and the reconstruction owns that case, so this guard leaves
it exactly as it is today.

WHICH END VALUE. The moment the guard fires is not the end of the charge either: that would bury
half an hour of silence inside it, the same mistake `trip_end_from_last_seen` was written to undo
(#233). The end is the last reading taken WHILE CHARGING, dated with the car's own clock — which
Mate already knows how to find (`charge_end_from_last_charging`, #208).
"""
from datetime import datetime, timezone

import pytest
import state_machine as SM
from client import VehicleData
from state_machine import State, StateMachine
import db as D
import recorder as R


# ── helpers ───────────────────────────────────────────────────────────────────
def _vd(*, charging=0, plug=True, deferred=False, ts=1000, soc=80.0, odo=1000.0):
    """Parked, cable in. `ts` is the CAR's clock on the frame — frame identity, not age."""
    return VehicleData(
        vin="TESTVIN", timestamp_ms=ts, soc=soc, range_km=300, odometer_km=odo,
        speed_kmh=0.0, gear="P", vehicle_state="parked",
        charging_status=charging, charge_power_kw=1.9, latitude=45.0, longitude=9.0,
        outside_temp=None, inside_temp=20.0, climate_target_temp=21.0, battery_min_temp=15.0,
        is_locked=True, climate_on=False, climate_cooling=False, climate_heating=False,
        climate_defrost=False, trunk_open=False, windows_open=False, sunshade_open=False,
        any_door_open=False, plug_connected=plug, remaining_charge_min=0,
        charge_voltage_v=230.0, charge_current_a=-5.3, charge_deferred=deferred,
    )


@pytest.fixture
def clock(monkeypatch):
    """time.monotonic under our control — the state machine's only notion of elapsed time."""
    now = {"t": 10_000.0}
    monkeypatch.setattr(SM.time, "monotonic", lambda: now["t"])
    return now


def _charging(clock, ts=1000):
    """A session open on real current, the way 12:00:34 opened his."""
    sm = StateMachine()
    sm.update(_vd(charging=2, ts=ts))
    assert sm.state == State.CHARGING
    return sm


def _current_stops(sm, clock, ts):
    """16:00:26 — current gone, cable still connected. One FRESH frame: the session must hold."""
    clock["t"] += 30
    sm.update(_vd(charging=0, plug=True, ts=ts))
    assert sm.state == State.CHARGING, "a dip with the cable in must not close the session"
    return sm


def _hold(sm, clock, minutes, *, ts, charging=0, step=30):
    """Re-serve the SAME frame for `minutes`, the way the cloud does while the car sleeps."""
    for _ in range(int(minutes * 60 / step)):
        clock["t"] += step
        sm.update(_vd(charging=charging, plug=True, ts=ts))
    return sm


# ── the close ─────────────────────────────────────────────────────────────────

def test_a_charge_frozen_for_half_an_hour_is_closed(clock):
    """Parked, not PARKED_ACTIVE: the guard hands the car back to the parked branch, which then
    puts a car that has not moved a signal in half an hour to sleep — which is what it is doing."""
    sm = _charging(clock)
    _current_stops(sm, clock, ts=1001)
    _hold(sm, clock, 31, ts=1001)
    assert sm.state in (State.PARKED_ACTIVE, State.PARKED_SLEEP)


def test_it_closes_once_and_not_once_every_poll(clock):
    """The trip guard nearly shipped opening and closing one row every poll. Count the events."""
    sm = _charging(clock)
    _current_stops(sm, clock, ts=1001)
    closes = 0
    for _ in range(240):                     # two hours of the same frame, parked cadence
        clock["t"] += 30
        for ev in sm.update(_vd(charging=0, plug=True, ts=1001)):
            if ev.from_state == State.CHARGING and ev.to_state == State.PARKED_ACTIVE:
                closes += 1
    assert closes == 1, f"the charge must be given up on once, not {closes} times"


def test_and_it_says_so_in_the_log(clock, caplog):
    """The poller log ships inside the bundle: this line is how the next one gets answered."""
    sm = _charging(clock)
    _current_stops(sm, clock, ts=1001)
    with caplog.at_level("WARNING", logger="state_machine"):
        _hold(sm, clock, 31, ts=1001)
    assert any("repeated" in r.getMessage() for r in caplog.records), \
        "closing a charge on a frozen frame must be visible"


# ── and everything it must NOT do ─────────────────────────────────────────────

def test_twenty_nine_minutes_is_not_enough(clock):
    """The boundary, from the side that must not fire."""
    sm = _charging(clock)
    _current_stops(sm, clock, ts=1001)
    _hold(sm, clock, 29, ts=1001)
    assert sm.state == State.CHARGING


def test_a_wallbox_pause_on_fresh_frames_still_keeps_the_session_whole(clock):
    """The deliberate behaviour this guard must not touch: a modulating wallbox drops the current
    for minutes at a time while the car stays awake and keeps pushing NEW frames. Two hours of it."""
    sm = _charging(clock)
    for i in range(240):
        clock["t"] += 30
        sm.update(_vd(charging=0, plug=True, ts=2000 + i))
    assert sm.state == State.CHARGING


def test_one_fresh_frame_resets_the_clock(clock):
    """25 minutes asleep, one frame, 25 more: neither stretch reaches the limit."""
    sm = _charging(clock)
    _current_stops(sm, clock, ts=1001)
    _hold(sm, clock, 25, ts=1001)
    clock["t"] += 30
    sm.update(_vd(charging=0, plug=True, ts=5555))
    _hold(sm, clock, 25, ts=5555)
    assert sm.state == State.CHARGING


def test_a_frozen_frame_that_still_reads_current_is_left_alone(clock):
    """Measured on his bundle: never happened once in 9 224 frozen polls. If it ever does, it is a
    car charging out of reach whose SoC will jump on return — the reconstruction's case, not this
    guard's."""
    sm = _charging(clock)
    _hold(sm, clock, 61, ts=1000, charging=2)
    assert sm.state == State.CHARGING


def test_a_frame_with_no_timestamp_never_freezes_the_counter(clock):
    """A missing frame id cannot prove two frames are the same one."""
    sm = _charging(clock)
    _current_stops(sm, clock, ts=0)
    _hold(sm, clock, 61, ts=0)
    assert sm.state == State.CHARGING


def test_the_cable_coming_out_still_closes_it_at_once(clock):
    """The ordinary path must be untouched: no current and no cable → closed on the spot."""
    sm = _charging(clock)
    clock["t"] += 30
    sm.update(_vd(charging=0, plug=False, ts=1001))
    assert sm.state == State.PARKED_ACTIVE


def test_the_deferred_cable_still_closes_it_at_once(clock):
    """1149 == 4 (#243): cable in, charge postponed to the programmed window. Closes immediately,
    without waiting for any limit."""
    sm = _charging(clock)
    clock["t"] += 30
    sm.update(_vd(charging=0, plug=True, deferred=True, ts=1001))
    assert sm.state == State.PARKED_ACTIVE


# ── the end stamp: the car's own clock, not the moment we gave up ─────────────

def _ms(iso):
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


class _Clock:
    """Mate's own clock, so fifteen hours of silence don't happen inside one millisecond.

    Both of Mate's clocks move together: the ISO one that dates the rows, and the monotonic one the
    state machine measures elapsed time with. Driving only the first would leave the guard reading a
    few real milliseconds and never firing."""

    def __init__(self, monkeypatch, start="2026-09-15T10:00:00+00:00"):
        self.now = start
        monkeypatch.setattr(D, "_now_iso", lambda: self.now)
        monkeypatch.setattr(R, "_now_iso", lambda: self.now)
        monkeypatch.setattr(SM.time, "monotonic", lambda: self._mono())

    def _mono(self):
        return datetime.fromisoformat(self.now).timestamp()

    def poll(self, rec, data):
        self.now = datetime.fromtimestamp(data.timestamp_ms / 1000, timezone.utc).isoformat()
        rec.process(data)

    def resend(self, rec, data, iso):
        """Mate polls again; the cloud hands back the SAME frame."""
        self.now = iso
        rec.process(data)


def test_the_charge_ends_when_the_car_last_reported_current(tmp_path, monkeypatch):
    """His numbers: open at 12:00, current gone at 16:00, guard fires at 16:31. The end must be
    16:00 — anything later buries pure silence inside the charge."""
    db = D.Database(str(tmp_path / "t.db"))
    db.set_battery_capacity(69.9)
    vid = db.ensure_vehicle("TESTVIN", "C10")
    rec = R.Recorder(db, vehicle_id=vid)
    clock = _Clock(monkeypatch)

    clock.poll(rec, _vd(charging=2, soc=78.5, ts=_ms("2026-09-15T12:00:00+00:00")))
    clock.poll(rec, _vd(charging=2, soc=89.0, ts=_ms("2026-09-15T16:00:00+00:00")))
    assert rec.state == State.CHARGING
    stopped = _vd(charging=0, plug=True, soc=89.0, ts=_ms("2026-09-15T16:00:30+00:00"))
    clock.poll(rec, stopped)                       # fresh frame, current gone, cable in
    for minute in range(1, 40):                    # …and then the car sleeps
        clock.resend(rec, stopped, "2026-09-15T16:%02d:30+00:00" % minute)

    row = db._conn.execute("SELECT * FROM charges ORDER BY id").fetchone()
    assert row["ended_at"] is not None, "the charge is still open — it is in no list and no count"
    ended = datetime.fromisoformat(row["ended_at"]).astimezone(timezone.utc)
    assert (ended.hour, ended.minute) == (16, 0), \
        f"ended at {ended:%H:%M} — the guard's own moment, not the car's last reading"
    assert row["duration_min"] == pytest.approx(240, abs=2), \
        "the duration must be the charge, not the charge plus the silence"
