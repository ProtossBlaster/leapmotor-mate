"""A charge behind a frozen frame starts where the news stopped — GitHub #501, @matttiaromano (T03).

THE CASE. For three and a half days the cloud served one frame: SoC 41, plug 0, current 0. The car
was charged in the meantime; nothing of it was ever read. Then SoC 76 arrived, in one poll. Mate saw
the jump and threw it away twice:

    db: Reconstructed charge skipped — implausible 1512 kW (12.6 kWh in 0.5 min)

and «Recover missed charges» skipped it for the same reason. Both measured the charge from the poll
before — 30 seconds earlier — instead of from when the SoC was last news. A T03 sends no frame
clock, so a repeat of the frame cannot be told from a fresh one: the only thing that says the car
was still at 41 is that it was READ at 41 the first time.

The trip reconstruction already starts where the news stopped (#244); the charge did not.
"""
from datetime import datetime, timedelta

import db as D
import db_reader
import recorder as R
from client import VehicleData
from state_machine import State


def _vd(soc, frame_ts=0):
    return VehicleData(
        vin="TESTVIN", timestamp_ms=frame_ts, soc=soc, range_km=300, odometer_km=1727,
        speed_kmh=0.0, gear="P", vehicle_state="parked",
        charging_status=0, charge_power_kw=0.0, latitude=45.0, longitude=9.0,
        outside_temp=None, inside_temp=20.0, climate_target_temp=21.0, battery_min_temp=15.0,
        is_locked=True, climate_on=False, climate_cooling=False, climate_heating=False,
        climate_defrost=False, trunk_open=False, windows_open=False, sunshade_pct=0,
        any_door_open=False, plug_connected=False, remaining_charge_min=0,
        charge_voltage_v=0.0, charge_current_a=0.0,
    )


class _DB:
    def __init__(self):
        self.reconstructed = []

    def create_reconstructed_charge(self, vid, start_soc, started_at, data):
        self.reconstructed.append((start_soc, started_at, data.soc))
        return 1


def _recorder():
    rec = R.Recorder(_DB(), vehicle_id=1)
    rec._sm.state = State.PARKED_ACTIVE
    rec._active_charge_id = None
    return rec


def _polls(monkeypatch, rec, readings):
    """Feed (our time, VehicleData) pairs to the charge reconstruction, in order."""
    for at, data in readings:
        monkeypatch.setattr(R, "_now_iso", lambda at=at: at)
        rec._maybe_reconstruct_charge(data)


T0 = "2026-11-19T16:17:04+00:00"


# ── the recorder ─────────────────────────────────────────────────────────────

def test_without_a_frame_clock_the_charge_starts_at_the_first_reading_of_that_soc(monkeypatch):
    rec = _recorder()
    _polls(monkeypatch, rec, [
        (T0, _vd(41.0)),
        ("2026-11-21T21:00:00+00:00", _vd(41.0)),
        ("2026-11-23T08:53:34+00:00", _vd(41.0)),
        ("2026-11-23T08:54:05+00:00", _vd(76.0)),
    ])
    assert rec._db.reconstructed == [(41.0, T0, 76.0)]


def test_with_a_frame_clock_a_repeat_is_no_news_and_a_fresh_frame_is(monkeypatch):
    """With the car's clock, a fresh frame still at 41 says the car was at 41 THEN: the charge
    starts after it. Its repeats say nothing."""
    rec = _recorder()
    fresh = "2026-11-21T21:00:00+00:00"
    _polls(monkeypatch, rec, [
        (T0, _vd(41.0, frame_ts=1000)),
        (fresh, _vd(41.0, frame_ts=2000)),
        ("2026-11-23T08:53:34+00:00", _vd(41.0, frame_ts=2000)),
        ("2026-11-23T08:54:05+00:00", _vd(76.0, frame_ts=3000)),
    ])
    assert rec._db.reconstructed == [(41.0, fresh, 76.0)]


def test_a_reconstructed_charge_moves_the_baseline_to_its_end(monkeypatch):
    rec = _recorder()
    end = "2026-11-23T08:54:05+00:00"
    _polls(monkeypatch, rec, [
        (T0, _vd(41.0)), (end, _vd(76.0)), ("2026-11-23T09:30:00+00:00", _vd(76.0)),
    ])
    assert (rec._last_soc, rec._last_soc_ts) == (76.0, end)
    assert len(rec._db.reconstructed) == 1


def test_the_real_row_of_the_issue_is_written(tmp_path):
    """The #501 numbers on the real Database: 36 kWh, 41→76 from the first reading at 41."""
    db = D.Database(str(tmp_path / "t.db"))
    db.set_battery_capacity(36.0)
    vid = db.ensure_vehicle("TESTVIN", "T03")
    cid = db.create_reconstructed_charge(vid, 41.0, T0, _vd(76.0))
    assert cid is not None
    row = db._conn.execute("SELECT start_soc, end_soc, energy_added_kwh FROM charges").fetchone()
    assert (row["start_soc"], row["end_soc"], row["energy_added_kwh"]) == (41.0, 76.0, 12.6)


# ── seeded from disk after a restart ─────────────────────────────────────────

def _pos(db, vid, ts, soc, frame_ts=None):
    db._conn.execute(
        "INSERT INTO positions (vehicle_id, recorded_at, soc, frame_ts, odometer_km, charging, speed_kmh)"
        " VALUES (?,?,?,?,1727,0,0)", (vid, ts, soc, frame_ts))
    db._conn.commit()


def test_a_restart_seeds_the_baseline_where_the_soc_was_first_read(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    vid = db.ensure_vehicle("TESTVIN", "T03")
    _pos(db, vid, "2026-11-19T10:00:00+00:00", 44.0)
    _pos(db, vid, T0, 41.0)
    _pos(db, vid, "2026-11-20T00:00:00+00:00", None)           # a frame without SoC is no news
    _pos(db, vid, "2026-11-23T08:53:34+00:00", 41.0)
    assert db.get_last_soc(vid) == (41.0, T0)


def test_a_restart_with_a_frame_clock_seeds_at_the_last_fresh_frame(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    vid = db.ensure_vehicle("TESTVIN", "B10")
    fresh = "2026-11-21T21:00:00+00:00"
    _pos(db, vid, T0, 41.0, frame_ts=1000)
    _pos(db, vid, fresh, 41.0, frame_ts=2000)
    _pos(db, vid, "2026-11-23T08:53:34+00:00", 41.0, frame_ts=2000)
    assert db.get_last_soc(vid) == (41.0, fresh)


# ── «Recover missed charges» ─────────────────────────────────────────────────

def test_the_scan_finds_the_charge_behind_a_frozen_frame_without_a_clock(tmp_path, monkeypatch):
    path = str(tmp_path / "s.db")
    db = D.Database(path)
    db._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1, 'VIN1', 'T03')")
    db.set_battery_capacity(36.0)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    _pos(db, 1, T0, 41.0)
    start = datetime.fromisoformat(T0)
    for k in range(1, 200):                     # the same frame, every 30 s
        _pos(db, 1, (start + timedelta(seconds=30 * k)).isoformat(), 41.0)
    _pos(db, 1, "2026-11-23T08:53:34+00:00", 41.0)
    _pos(db, 1, "2026-11-23T08:54:05+00:00", 76.0)
    cands = db_reader.scan_missed_charges(apply=False)
    assert [(c["started_at"], c["start_soc"], c["end_soc"], c["energy_kwh"]) for c in cands] == \
        [(T0, 41.0, 76.0, 12.6)]
