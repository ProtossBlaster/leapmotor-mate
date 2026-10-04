"""Regression tests for v1.13.x — GitHub #29: a charge that happens while the car is
asleep/offline to the cloud is never seen live (no plug/current ever polled), so the live
state machine never enters CHARGING and the session is lost. The recorder reconstructs it
from the SoC jump instead. These cover the DB row, the recorder's decision logic, and the
end-to-end seed-from-disk path (a charge during poller downtime)."""
import types

from client import VehicleData
from state_machine import State
import db as D
import recorder as R


# ── helpers ───────────────────────────────────────────────────────────────────
def _vd(soc, *, gear="P", speed=0.0, charging=0, plug=False, lat=45.0, lon=9.0):
    """A parked, not-charging VehicleData by default — override soc/gear/charging/plug."""
    return VehicleData(
        vin="TESTVIN", timestamp_ms=0, soc=soc, range_km=300, odometer_km=1000,
        speed_kmh=speed, gear=gear, vehicle_state="parked",
        charging_status=charging, charge_power_kw=0.0, latitude=lat, longitude=lon,
        outside_temp=None, inside_temp=20.0, climate_target_temp=21.0, battery_min_temp=15.0,
        is_locked=True, climate_on=False, climate_cooling=False, climate_heating=False,
        climate_defrost=False, trunk_open=False, windows_open=False, sunshade_pct=0,
        any_door_open=False, plug_connected=plug, remaining_charge_min=0,
        charge_voltage_v=0.0, charge_current_a=0.0,
    )


class _CountDB:
    """Minimal DB stub: only what _maybe_reconstruct_charge touches, with a call counter."""
    def __init__(self):
        self.reconstructed = []

    def create_reconstructed_charge(self, vid, start_soc, started_at, data):
        self.reconstructed.append((start_soc, data.soc))
        return len(self.reconstructed)


def _rec_in_state(state=State.PARKED_ACTIVE, last_soc=60.0):
    rec = R.Recorder(_CountDB(), vehicle_id=1)
    rec._sm.state = state
    rec._active_charge_id = None
    rec._last_soc, rec._last_soc_ts = last_soc, "2026-06-09T10:00:00+00:00"
    return rec


# ── 1. the DB row a reconstruction writes ─────────────────────────────────────
def test_create_reconstructed_charge_row(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    db.set_battery_capacity(50.0)
    vid = db.ensure_vehicle("TESTVIN", "B10")
    cid = db.create_reconstructed_charge(
        vid, 62.0, "2026-06-09T09:00:00+00:00", _vd(70.0))
    row = db._conn.execute("SELECT * FROM charges WHERE id=?", (cid,)).fetchone()
    assert row["reconstructed"] == 1
    assert row["start_soc"] == 62.0 and row["end_soc"] == 70.0
    assert abs(row["energy_added_kwh"] - (70 - 62) / 100 * 50) < 1e-6   # 4.0 kWh
    assert row["charge_type"] == "AC"
    assert row["ended_at"] is not None and row["cost"] is None
    assert row["max_power_kw"] is None                                   # unknown — never measured


# ── 2. recorder DECIDES to reconstruct on a parked SoC jump ────────────────────
def test_reconstructs_on_parked_soc_jump():
    rec = _rec_in_state(State.PARKED_ACTIVE, last_soc=62.0)
    rec._maybe_reconstruct_charge(_vd(70.0))
    assert rec._db.reconstructed == [(62.0, 70.0)]
    assert rec._last_soc == 70.0          # baseline advanced → won't fire again next poll


# ── 3. never while a live charge owns the session ─────────────────────────────
def test_no_reconstruct_while_charging():
    rec = _rec_in_state(State.CHARGING, last_soc=62.0)
    rec._maybe_reconstruct_charge(_vd(70.0, charging=1, plug=True))
    assert rec._db.reconstructed == []    # the live path records this one


def test_no_reconstruct_when_charge_already_open():
    rec = _rec_in_state(State.PARKED_ACTIVE, last_soc=62.0)
    rec._active_charge_id = 7             # a charge is already being recorded
    rec._maybe_reconstruct_charge(_vd(70.0))
    assert rec._db.reconstructed == []


# ── 4. drops and sub-threshold jitter never invent a charge ───────────────────
def test_no_reconstruct_on_soc_drop():
    rec = _rec_in_state(State.PARKED_ACTIVE, last_soc=62.0)
    rec._maybe_reconstruct_charge(_vd(60.0))   # vampire drain, SoC fell
    assert rec._db.reconstructed == []


def test_no_reconstruct_below_threshold():
    rec = _rec_in_state(State.PARKED_ACTIVE, last_soc=62.0)
    rec._maybe_reconstruct_charge(_vd(63.5))   # +1.5% < 2% floor → BMS jitter, not a charge
    assert rec._db.reconstructed == []


def test_first_poll_has_no_baseline():
    rec = R.Recorder(_CountDB(), vehicle_id=1)   # _last_soc is None until seeded
    rec._sm.state = State.PARKED_ACTIVE
    rec._maybe_reconstruct_charge(_vd(70.0))
    assert rec._db.reconstructed == []           # no baseline → can't call it a jump
    assert rec._last_soc == 70.0                  # but the baseline is now seeded


# ── 5. end-to-end: a charge during poller DOWNTIME is caught on the first poll ─
def test_seed_from_disk_catches_downtime_charge(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    db.set_battery_capacity(50.0)
    vid = db.ensure_vehicle("TESTVIN", "B10")
    # Previous run left the car parked at 20% on disk.
    db.save_position(vid, _vd(20.0))
    # Poller restarts; first poll back shows 35% (it charged while we were down), parked, unplugged.
    rec = R.Recorder(db, vehicle_id=vid)
    rec.process(_vd(35.0))
    charges = db._conn.execute("SELECT * FROM charges WHERE reconstructed=1").fetchall()
    assert len(charges) == 1
    assert charges[0]["start_soc"] == 20.0 and charges[0]["end_soc"] == 35.0


def test_live_charge_makes_no_reconstructed_duplicate(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    db.set_battery_capacity(50.0)
    vid = db.ensure_vehicle("TESTVIN", "B10")
    db.save_position(vid, _vd(20.0))
    rec = R.Recorder(db, vehicle_id=vid)
    # First poll already shows the car charging at a higher SoC → live path opens a real charge,
    # and the SoC jump must NOT also spawn a reconstructed row.
    rec.process(_vd(35.0, charging=1, plug=True))
    assert rec.state == State.CHARGING
    recon = db._conn.execute("SELECT COUNT(*) c FROM charges WHERE reconstructed=1").fetchone()["c"]
    live = db._conn.execute("SELECT COUNT(*) c FROM charges WHERE reconstructed=0").fetchone()["c"]
    assert recon == 0 and live == 1


# ── 6. spurious SoC=0 glitch must NOT reconstruct a "charged from 0%" phantom ──
def test_no_reconstruct_from_spurious_zero(tmp_path):
    """The Telegram ghost-charge bug: a poll returned no SoC → parsed as soc=0.0; on the next
    (recovered) poll the 0→70.7% 'jump' must be rejected, not logged as a +47 kWh charge."""
    db = D.Database(str(tmp_path / "t.db"))
    db.set_battery_capacity(67.1)
    vid = db.ensure_vehicle("TESTVIN", "B10")
    rec = R.Recorder(db, vehicle_id=vid)
    rec.process(_vd(70.7))          # normal
    rec.process(_vd(0.0))           # spurious zero (drop → baseline advances to 0)
    rec.process(_vd(70.7))          # recovery: 0→70.7 jump → MUST be rejected
    recon = db._conn.execute("SELECT COUNT(*) c FROM charges WHERE reconstructed=1").fetchone()["c"]
    assert recon == 0


def test_create_reconstructed_charge_rejects_zero_start(tmp_path):
    """A real EV charge never starts at ~0% — a 0% start is the spurious-SoC signature → no row."""
    db = D.Database(str(tmp_path / "t.db"))
    db.set_battery_capacity(67.1)
    vid = db.ensure_vehicle("TESTVIN", "B10")
    assert db.create_reconstructed_charge(vid, 0.0, "2026-06-09T09:00:00+00:00", _vd(70.7)) is None
    assert db._conn.execute("SELECT COUNT(*) FROM charges").fetchone()[0] == 0


# ── 7. one-time repair cleans phantoms + bogus soc=0 rows already on disk ──────
def test_repair_drops_phantom_zero_soc_charges(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    db.ensure_vehicle("TESTVIN", "B10")
    # A phantom 'charged from 0%' (delete) + a legit reconstructed charge (keep).
    db._conn.execute("INSERT INTO charges (vehicle_id, started_at, ended_at, start_soc, end_soc,"
                     " energy_added_kwh, charge_type, reconstructed) VALUES"
                     " (1,'2026-06-02T09:16:00+00:00','2026-06-02T09:16:20+00:00',0,90,60.4,'AC',1)")
    db._conn.execute("INSERT INTO charges (vehicle_id, started_at, ended_at, start_soc, end_soc,"
                     " energy_added_kwh, charge_type, reconstructed) VALUES"
                     " (1,'2026-06-01T22:00:00+00:00','2026-06-02T06:00:00+00:00',50,80,20.1,'AC',1)")
    # A bogus soc=0 position with the car clearly not empty (null) + a real one (keep).
    db._conn.execute("INSERT INTO positions (vehicle_id, recorded_at, soc, range_km, charging, speed_kmh)"
                     " VALUES (1,'2026-06-02T09:16:10+00:00',0,282,0,0)")
    db._conn.execute("INSERT INTO positions (vehicle_id, recorded_at, soc, range_km, charging, speed_kmh)"
                     " VALUES (1,'2026-06-01T22:00:00+00:00',50,200,0,0)")
    db._conn.execute("DELETE FROM settings WHERE key='charges_zero_soc_repair_v1'")  # __init__ already ran it
    db._conn.commit()
    db._repair_phantom_zero_soc_charges()
    assert [r["start_soc"] for r in
            db._conn.execute("SELECT start_soc FROM charges ORDER BY start_soc")] == [50.0]
    assert db._conn.execute("SELECT COUNT(*) FROM positions WHERE soc=0").fetchone()[0] == 0
    assert db._conn.execute("SELECT COUNT(*) FROM positions WHERE soc=50").fetchone()[0] == 1
