"""A trip rebuilt from an odometer jump must start when the car LAST SPOKE, not one poll ago.

THE BUG (measured on @riri19's own database, 10/08/2026, while triaging #244):

    2026-08-09T21:37 → 21:37   4.0 km   SoC 70.2→69.9   —min   recon=1

Start and end on the same instant, no duration, and the trip lands at the moment the cloud caught
up rather than when the car drove — which is why these read as "random trip entries" (@wlighter,
#242) rather than as the recovered drives they are.

WHY. `_maybe_reconstruct_trip` takes its `started_at` from the SoC baseline, and that baseline is
re-stamped with `_now_iso()` on EVERY poll. While the link is dark the cloud re-serves one frozen
frame, so the car still looks online: Mate polls every 30 s, gets the same odometer, and moves the
baseline forward each time. When the fresh frame finally arrives, the whole drive is squeezed into
the last polling interval — 4 km in 30 s is 480 km/h, so `create_reconstructed_trip`'s speed guard
throws the duration away. The guard is right; what reaches it is wrong.

THE FIX. The recorder already knows a repeat when it sees one (`stale`, frame-identity, #128).
Keep a second baseline that only moves on a FRESH frame, and start the reconstructed trip there.
The gap then spans the real blackout, so the existing plausibility guard can do its job: a drive
that fits the window keeps its duration, one that clearly does not still loses it.

⚠️ A car whose frames carry no timestamp cannot be judged this way, and must behave exactly as
before — absent is not zero.

CI-safe: pure recorder / db logic, no fastapi.
"""
from datetime import datetime, timedelta, timezone

import db as D
import recorder as R
from client import VehicleData

T0 = datetime(2026, 8, 9, 18, 0, tzinfo=timezone.utc)


def _vd(soc, *, odo, ts_ms, gear="P", speed=0.0):
    return VehicleData(
        vin="TESTVIN", timestamp_ms=ts_ms, soc=soc, range_km=300, odometer_km=odo,
        speed_kmh=speed, gear=gear, vehicle_state="parked",
        charging_status=0, charge_power_kw=0.0, latitude=45.0, longitude=9.0,
        outside_temp=None, inside_temp=20.0, climate_target_temp=21.0, battery_min_temp=15.0,
        is_locked=True, climate_on=False, climate_cooling=False, climate_heating=False,
        climate_defrost=False, trunk_open=False, windows_open=False, sunshade_open=False,
        any_door_open=False, plug_connected=False, remaining_charge_min=0,
        charge_voltage_v=0.0, charge_current_a=0.0,
    )


class _Clock:
    """Mate's own wall clock, set independently of the frame's clock — the whole point here is the
    stretch where the two disagree, because the cloud is re-serving an old frame to a poller whose
    own clock keeps ticking."""

    def __init__(self, monkeypatch):
        self.now = T0.isoformat()
        monkeypatch.setattr(D, "_now_iso", lambda: self.now)
        monkeypatch.setattr(R, "_now_iso", lambda: self.now)

    def at(self, when: datetime, rec, data):
        self.now = when.isoformat()
        rec.process(data)


def _rec(tmp_path, monkeypatch):
    db = D.Database(str(tmp_path / "t.db"))
    db.set_battery_capacity(65.8)
    vid = db.ensure_vehicle("TESTVIN", "B10")
    return db, R.Recorder(db, vehicle_id=vid), _Clock(monkeypatch)


def _trip(db):
    row = db._conn.execute(
        "SELECT started_at, ended_at, distance_km, duration_min, reconstructed"
        "  FROM trips ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def _ms(when: datetime) -> int:
    return int(when.timestamp() * 1000)


def _blackout(rec, clock, *, first_seen: datetime, until: datetime, odo, soc, ts_ms):
    """Re-serve ONE frozen frame every 30 s. The car looks perfectly online the whole time."""
    t = first_seen
    while t <= until:
        clock.at(t, rec, _vd(soc, odo=odo, ts_ms=ts_ms))
        t += timedelta(seconds=30)


# ── the core of it ──────────────────────────────────────────────────────────────
def test_the_start_is_when_the_car_last_spoke_not_the_last_poll(tmp_path, monkeypatch):
    """One frame from 18:00 re-served until 18:45, then a fresh frame 40 km further on."""
    db, rec, clock = _rec(tmp_path, monkeypatch)
    dark_from = T0
    _blackout(rec, clock, first_seen=dark_from, until=T0 + timedelta(minutes=45),
              odo=1000.0, soc=70.0, ts_ms=_ms(T0))

    back = T0 + timedelta(minutes=45, seconds=30)
    clock.at(back, rec, _vd(62.0, odo=1040.0, ts_ms=_ms(back)))

    t = _trip(db)
    assert t is not None and t["reconstructed"] == 1
    assert t["started_at"] == dark_from.isoformat(), (
        "the trip must start when the cloud last told us something new, not one poll ago")


def test_a_blackout_that_fits_the_drive_keeps_its_duration(tmp_path, monkeypatch):
    """40 km across a 45-minute dark window is 53 km/h — plausible, so the duration survives.
    Today it never gets the chance: the gap is one poll, the implied speed is absurd, and
    `create_reconstructed_trip` nulls it."""
    db, rec, clock = _rec(tmp_path, monkeypatch)
    _blackout(rec, clock, first_seen=T0, until=T0 + timedelta(minutes=45),
              odo=1000.0, soc=70.0, ts_ms=_ms(T0))
    back = T0 + timedelta(minutes=45, seconds=30)
    clock.at(back, rec, _vd(62.0, odo=1040.0, ts_ms=_ms(back)))

    assert _trip(db)["duration_min"] == 45.5


def test_a_long_dark_park_still_refuses_the_duration(tmp_path, monkeypatch):
    """riri19's own shape: 4 km discovered after four hours of silence. The window is an upper
    bound padded with parked time — 1 km/h is not a drive, so the duration must stay empty."""
    db, rec, clock = _rec(tmp_path, monkeypatch)
    _blackout(rec, clock, first_seen=T0, until=T0 + timedelta(hours=4),
              odo=1000.0, soc=70.2, ts_ms=_ms(T0))
    back = T0 + timedelta(hours=4, seconds=30)
    clock.at(back, rec, _vd(69.9, odo=1004.0, ts_ms=_ms(back)))

    t = _trip(db)
    assert t["distance_km"] == 4.0        # the kilometres are still recovered
    assert t["duration_min"] is None      # but nothing pretends to know how long it took


def test_without_frame_timestamps_nothing_changes(tmp_path, monkeypatch):
    """A car that sends no frame clock cannot be judged on frame identity. It must land exactly
    where it lands today — on the previous poll — not silently acquire a different start."""
    db, rec, clock = _rec(tmp_path, monkeypatch)
    _blackout(rec, clock, first_seen=T0, until=T0 + timedelta(minutes=45),
              odo=1000.0, soc=70.0, ts_ms=0)
    last_poll = T0 + timedelta(minutes=45)
    back = last_poll + timedelta(seconds=30)
    clock.at(back, rec, _vd(62.0, odo=1040.0, ts_ms=0))

    assert _trip(db)["started_at"] == last_poll.isoformat()


def test_the_kilometres_and_the_energy_are_unchanged(tmp_path, monkeypatch):
    db, rec, clock = _rec(tmp_path, monkeypatch)
    _blackout(rec, clock, first_seen=T0, until=T0 + timedelta(minutes=45),
              odo=1000.0, soc=70.0, ts_ms=_ms(T0))
    back = T0 + timedelta(minutes=45, seconds=30)
    clock.at(back, rec, _vd(62.0, odo=1040.0, ts_ms=_ms(back)))

    t = _trip(db)
    assert t["distance_km"] == 40.0
    assert t["ended_at"] == back.isoformat()


def test_a_live_trip_is_not_touched(tmp_path, monkeypatch):
    """The live path owns a drive it can see. Nothing here may reconstruct on top of it."""
    db, rec, clock = _rec(tmp_path, monkeypatch)
    t = T0
    for odo, soc in ((1000.0, 70.0), (1010.0, 69.0), (1020.0, 68.0)):
        clock.at(t, rec, _vd(soc, odo=odo, ts_ms=_ms(t), gear="D", speed=50.0))
        t += timedelta(minutes=10)

    rows = db._conn.execute("SELECT COUNT(*) FROM trips WHERE reconstructed = 1").fetchone()[0]
    assert rows == 0
