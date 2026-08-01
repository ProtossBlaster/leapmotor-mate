"""A charge left open when the car reappears DRIVING must be closed — GitHub #208, @mikeeeeekoo.

THE BUG. A charge is closed in exactly one place: `frm == CHARGING and to in _PARKED_STATES`.
His car charged overnight, the Leapmotor cloud then refused three logins in a row (CHARGING →
OFFLINE), and an hour later the car came back already on the road (OFFLINE → DRIVING). Neither
leg matches that guard, so `Charge #3 started` has no `Charge #3 ended` anywhere in his log: the
row stayed open, and an open charge is in no calendar and in no AC count. The asymmetry is the
tell — entering CHARGING closes an open trip ("plug inserted while driving"), but entering
DRIVING did not close an open charge.

WHICH END VALUES. Not the driving frame's. His charge really ended at 100 %, and by the time
Mate saw the car it read 98.1 % — ten kilometres of road, not two lost points of charge. So the
end comes from the last reading taken WHILE CHARGING, dated with the car's own clock
(`frame_ts`): during a frozen-frame window the cloud keeps re-serving the last real frame, and
that frame IS the last measurement. Closing on the driving frame instead would book 12.8 → 98.1 %
ending three hours late.

THE ONE EXCEPTION. A car that has not moved (odometer unchanged) cannot have spent SoC, so if it
reappears HIGHER than the last charging reading it kept charging while we were blind — that fresh
reading is a measurement too, and it wins.
"""
from datetime import datetime, timezone

from client import VehicleData
from state_machine import State
import db as D
import recorder as R


# ── helpers ───────────────────────────────────────────────────────────────────
def _vd(soc, *, gear="P", speed=0.0, charging=0, plug=False, odo=1000.0, ts_ms=0):
    """Parked and not charging by default. `ts_ms` is the CAR's clock on the frame."""
    return VehicleData(
        vin="TESTVIN", timestamp_ms=ts_ms, soc=soc, range_km=300, odometer_km=odo,
        speed_kmh=speed, gear=gear, vehicle_state="parked",
        charging_status=charging, charge_power_kw=3.5, latitude=45.0, longitude=9.0,
        outside_temp=None, inside_temp=20.0, climate_target_temp=21.0, battery_min_temp=15.0,
        is_locked=True, climate_on=False, climate_cooling=False, climate_heating=False,
        climate_defrost=False, trunk_open=False, windows_open=False, sunshade_open=False,
        any_door_open=False, plug_connected=plug, remaining_charge_min=0,
        charge_voltage_v=230.0, charge_current_a=15.0,
    )


def _charging(soc, *, odo=1000.0, ts_ms=0):
    return _vd(soc, charging=2, plug=True, odo=odo, ts_ms=ts_ms)


def _driving(soc, *, odo=1000.0, ts_ms=0):
    return _vd(soc, gear="D", speed=40.0, odo=odo, ts_ms=ts_ms)


def _ms(iso):
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


class _Clock:
    """Mate's own clock, so a nine-hour night doesn't happen inside one millisecond of test time.
    Without it `started_at` is the real now while the frames carry 2026 timestamps, and every
    comparison between the two is meaningless."""

    def __init__(self, monkeypatch, start="2026-08-01T00:00:00+00:00"):
        self.now = start
        monkeypatch.setattr(D, "_now_iso", lambda: self.now)
        monkeypatch.setattr(R, "_now_iso", lambda: self.now)   # recorder imported it by name

    def poll(self, rec, data):
        """Advance Mate's clock to this frame's own timestamp, then poll."""
        self.now = datetime.fromtimestamp(data.timestamp_ms / 1000, timezone.utc).isoformat()
        rec.process(data)


def _rec(tmp_path, monkeypatch):
    db = D.Database(str(tmp_path / "t.db"))
    db.set_battery_capacity(69.9)
    vid = db.ensure_vehicle("TESTVIN", "C10")
    return db, R.Recorder(db, vehicle_id=vid), _Clock(monkeypatch)


def _charges(db):
    return db._conn.execute("SELECT * FROM charges ORDER BY id").fetchall()


def _go_offline(rec):
    """Three consecutive API errors — what mark_offline does from the poll loop."""
    for _ in range(3):
        rec.mark_offline()


# ── 1. the regression: the charge must not stay open ──────────────────────────
def test_charge_is_closed_when_the_car_reappears_driving(tmp_path, monkeypatch):
    db, rec, clock = _rec(tmp_path, monkeypatch)
    clock.poll(rec, _vd(12.8, plug=True, ts_ms=_ms("2026-08-01T00:06:00+00:00")))
    clock.poll(rec, _charging(12.8, ts_ms=_ms("2026-08-01T00:07:00+00:00")))
    clock.poll(rec, _charging(100.0, ts_ms=_ms("2026-08-01T06:10:00+00:00")))
    assert rec.state == State.CHARGING

    _go_offline(rec)                                   # cloud refuses → CHARGING → OFFLINE
    assert rec.state == State.OFFLINE
    clock.poll(rec, _driving(98.1, odo=1010.0,         # back, already on the road, 10 km on
                             ts_ms=_ms("2026-08-01T09:36:00+00:00")))

    rows = _charges(db)
    assert len(rows) == 1
    assert rows[0]["ended_at"] is not None, "the charge is still open — it is in no calendar"


# ── 2. the end SoC is the last one MEASURED while charging ────────────────────
def test_end_soc_comes_from_the_last_charging_reading_not_the_driving_frame(tmp_path, monkeypatch):
    db, rec, clock = _rec(tmp_path, monkeypatch)
    clock.poll(rec, _vd(12.8, plug=True, ts_ms=_ms("2026-08-01T00:06:00+00:00")))
    clock.poll(rec, _charging(12.8, ts_ms=_ms("2026-08-01T00:07:00+00:00")))
    clock.poll(rec, _charging(100.0, ts_ms=_ms("2026-08-01T06:10:00+00:00")))
    _go_offline(rec)
    clock.poll(rec, _driving(98.1, odo=1010.0, ts_ms=_ms("2026-08-01T09:36:00+00:00")))

    row = _charges(db)[0]
    assert row["end_soc"] == 100.0, "the 1.9 points went into the road, not out of the charge"
    # 12.8 → 100 % of 69.9 kWh ≈ 60.9 kWh; on the driving frame it would read ~59.6.
    assert row["energy_added_kwh"] > 60.0


# ── 3. the end TIME is the car's own clock, not when we noticed ───────────────
def test_end_time_is_the_cars_last_frame_not_the_moment_driving_was_seen(tmp_path, monkeypatch):
    db, rec, clock = _rec(tmp_path, monkeypatch)
    clock.poll(rec, _vd(12.8, plug=True, ts_ms=_ms("2026-08-01T00:06:00+00:00")))
    clock.poll(rec, _charging(12.8, ts_ms=_ms("2026-08-01T00:07:00+00:00")))
    frozen = _charging(100.0, ts_ms=_ms("2026-08-01T06:10:00+00:00"))
    clock.poll(rec, frozen)
    # The car then goes quiet and the cloud re-serves that SAME frame for two hours: Mate's own
    # clock runs on to 08:27 while the car's stops at 06:10. His log shows "Frame age: 8199s".
    for hhmm in ("07:00", "07:45", "08:27"):
        clock.now = "2026-08-01T%s:00+00:00" % hhmm
        rec.process(frozen)
    _go_offline(rec)
    clock.poll(rec, _driving(98.1, odo=1010.0, ts_ms=_ms("2026-08-01T09:36:00+00:00")))

    ended = datetime.fromisoformat(_charges(db)[0]["ended_at"])
    assert ended.astimezone(timezone.utc).hour == 6, \
        "closed at the moment driving was noticed (09:36), not at the car's own 06:10"


# ── 4. a car that never moved and comes back HIGHER kept charging ─────────────
def test_a_stationary_car_that_reappears_higher_kept_charging(tmp_path, monkeypatch):
    # Silvio's case: lost at 80 %, seen at 95 % — with the odometer UNCHANGED the car cannot
    # have spent anything, so 95 % is where the charge got to. It is a measurement, not a guess.
    db, rec, clock = _rec(tmp_path, monkeypatch)
    clock.poll(rec, _vd(80.0, plug=True, odo=1000.0, ts_ms=_ms("2026-08-01T00:06:00+00:00")))
    clock.poll(rec, _charging(80.0, odo=1000.0, ts_ms=_ms("2026-08-01T00:07:00+00:00")))
    _go_offline(rec)
    clock.poll(rec, _driving(95.0, odo=1000.0, ts_ms=_ms("2026-08-01T03:30:00+00:00")))

    assert _charges(db)[0]["end_soc"] == 95.0


# ── 5. an ordinary charge is untouched ────────────────────────────────────────
def test_a_normal_charge_still_closes_on_unplug(tmp_path, monkeypatch):
    db, rec, clock = _rec(tmp_path, monkeypatch)
    clock.poll(rec, _vd(40.0, plug=True, ts_ms=_ms("2026-08-01T00:06:00+00:00")))
    clock.poll(rec, _charging(40.0, ts_ms=_ms("2026-08-01T00:07:00+00:00")))
    clock.poll(rec, _charging(70.0, ts_ms=_ms("2026-08-01T02:00:00+00:00")))
    clock.poll(rec, _vd(70.0, ts_ms=_ms("2026-08-01T02:05:00+00:00")))   # unplugged, parked

    rows = _charges(db)
    assert len(rows) == 1 and rows[0]["ended_at"] is not None
    assert rows[0]["end_soc"] == 70.0
    assert rec._active_charge_id is None


def test_driving_with_no_charge_open_changes_nothing(tmp_path, monkeypatch):
    db, rec, clock = _rec(tmp_path, monkeypatch)
    clock.poll(rec, _vd(60.0, ts_ms=_ms("2026-08-01T00:06:00+00:00")))
    clock.poll(rec, _driving(60.0, odo=1001.0, ts_ms=_ms("2026-08-01T00:16:00+00:00")))
    assert _charges(db) == []


# ── 6. the orphan path: the row already stuck open must close on the RIGHT reading ──
def test_orphan_close_uses_the_last_charging_reading_not_the_morning_after(tmp_path, monkeypatch):
    """@mikeeeeekoo's row is already open. It gets closed at the next poller restart — and until
    now that path took the last position of ANY kind since the charge started, which for him is a
    whole morning of driving: 12.8 → 92.3 % at half past noon, for a charge that ended at 100 %."""
    db, rec, clock = _rec(tmp_path, monkeypatch)
    clock.poll(rec, _vd(12.8, plug=True, ts_ms=_ms("2026-08-01T00:06:00+00:00")))
    clock.poll(rec, _charging(12.8, ts_ms=_ms("2026-08-01T00:07:00+00:00")))
    clock.poll(rec, _charging(100.0, ts_ms=_ms("2026-08-01T06:10:00+00:00")))
    # The poller dies here, so nothing closes the charge. The car then drives all morning and
    # those positions land in the same DB, after the charge's start.
    charge_id = rec._active_charge_id
    for hhmm, soc, odo in (("09:40", 97.8, 1010.0), ("11:08", 95.4, 1020.0), ("11:49", 92.3, 1029.0)):
        clock.now = "2026-08-01T%s:00+00:00" % hhmm
        db.save_position(1, _driving(soc, odo=odo, ts_ms=_ms(clock.now)))

    clock.now = "2026-08-01T12:30:00+00:00"
    assert db.close_orphan_charges(1) == 1

    row = _charges(db)[0]
    assert row["id"] == charge_id
    assert row["end_soc"] == 100.0, "closed on the morning's driving, not on the charge"
    assert datetime.fromisoformat(row["ended_at"]).astimezone(timezone.utc).hour == 6
