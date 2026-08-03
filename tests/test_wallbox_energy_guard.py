"""Physical guard on wallbox session energy (GitHub #46). A wallbox kWh counter can read ~0 at
plug-in and then snap back to its LIFETIME total, so the per-poll delta becomes a single absurd
step (tens of thousands of kWh for a 15-minute charge) that inflated both the energy shown and the
cost. Three layers, all in poller/db: a per-poll guard (skip the impossible step, keep counting the
real rises after it), a finalize backstop (drop a still-impossible total → DC billing), and a
one-time repair for rows already in the DB. Pure poller.db → runs in CI."""
import types
from datetime import datetime, timedelta, timezone

import db as D


def _at(**kw):
    return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat()


def test_accumulate_skips_implausible_jump_then_self_corrects(tmp_path):
    """A counter that reads ~0 at start then jumps to its lifetime total: the jump is ignored,
    the baseline advances, and the real rises AFTER it are still summed → the session recovers."""
    db = D.Database(str(tmp_path / "t.db"))
    cid = db.create_charge(1, types.SimpleNamespace(soc=38, latitude=1.0, longitude=2.0))
    db.set_charge_wallbox_start(cid, 0.0)            # entity read 0 at plug-in
    db.accumulate_wallbox_energy(cid, 10570.0)       # snaps to lifetime total → impossible step
    db.accumulate_wallbox_energy(cid, 10570.4)       # +0.4 real
    db.accumulate_wallbox_energy(cid, 10570.7)       # +0.3 real
    ac = db._conn.execute("SELECT ac_energy_kwh FROM charges WHERE id=?", (cid,)).fetchone()[0]
    assert ac == 0.7                                  # the 10,570 jump never counted


def test_finalize_drops_still_impossible_total(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    cid = db.create_charge(1, types.SimpleNamespace(soc=38, latitude=1.0, longitude=2.0))
    # A bogus total that bypassed the per-poll guard, on a 15-minute session.
    db._conn.execute("UPDATE charges SET started_at=?, ac_energy_kwh=10570.0 WHERE id=?",
                     (_at(minutes=15), cid))
    db._conn.commit()
    db.finalize_charge(cid, types.SimpleNamespace(soc=40, latitude=1.0, longitude=2.0),
                       max_power_kw=3.1)
    ac = db._conn.execute("SELECT ac_energy_kwh FROM charges WHERE id=?", (cid,)).fetchone()[0]
    assert ac is None                                 # dropped → charge bills on DC energy


def test_finalize_keeps_plausible_total(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    cid = db.create_charge(1, types.SimpleNamespace(soc=66, latitude=1.0, longitude=2.0))
    db._conn.execute("UPDATE charges SET started_at=?, ac_energy_kwh=6.7 WHERE id=?",
                     (_at(hours=3), cid))
    db._conn.commit()
    db.finalize_charge(cid, types.SimpleNamespace(soc=84, latitude=1.0, longitude=2.0),
                       max_power_kw=3.2)
    ac = db._conn.execute("SELECT ac_energy_kwh FROM charges WHERE id=?", (cid,)).fetchone()[0]
    assert ac == 6.7                                  # real wallbox energy untouched


# ── The mirror: a counter that STOPS (#215 @riri19) ──────────────────────────────
# His Tuya froze for 2h18 while the car charged on at 6.9 kW. A rise that is too small always looks
# plausible, so the ceiling above never sees it: the session was billed on 22.1 kWh instead of 38.9.
# The test is between two MEASUREMENTS — the counter stands still while the CAR reports power — and
# is counted in kWh rather than in polls, so a coarse meter that legitimately sits still is safe.

def _stalled(db, cid, kwh, reading=100.0, step=0.25):
    """Feed `kwh` of car-reported energy in `step` slices while the counter never moves."""
    for _ in range(int(kwh / step)):
        db.accumulate_wallbox_energy(cid, reading, step)


def test_a_counter_that_stops_is_caught_at_close(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    cid = db.create_charge(1, types.SimpleNamespace(soc=28, latitude=1.0, longitude=2.0))
    db._conn.execute("UPDATE charges SET started_at=? WHERE id=?", (_at(hours=6), cid))
    db._conn.commit()
    db.set_charge_wallbox_start(cid, 100.0)
    db.accumulate_wallbox_energy(cid, 122.1, 0.06)     # 22.1 kWh really counted…
    _stalled(db, cid, 16.9)                            # …then frozen through 16.9 kWh the car drew
    assert db._conn.execute("SELECT wb_stuck_kwh FROM charges WHERE id=?",
                            (cid,)).fetchone()[0] >= D._WB_STUCK_KWH

    db.finalize_charge(cid, types.SimpleNamespace(soc=80, latitude=1.0, longitude=2.0),
                       max_power_kw=6.9)
    ac = db._conn.execute("SELECT ac_energy_kwh FROM charges WHERE id=?", (cid,)).fetchone()[0]
    assert ac is None                                  # short by an unknown amount → bill on DC


def test_a_coarse_counter_that_ticks_slowly_is_left_alone(tmp_path):
    """The scenario that must NOT trip it: a 1 kWh-resolution meter at low power sits still for
    many polls and then jumps a whole kWh. Nothing is wrong and its total is exact."""
    db = D.Database(str(tmp_path / "t.db"))
    cid = db.create_charge(1, types.SimpleNamespace(soc=40, latitude=1.0, longitude=2.0))
    db._conn.execute("UPDATE charges SET started_at=? WHERE id=?", (_at(hours=4), cid))
    db._conn.commit()
    db.set_charge_wallbox_start(cid, 100.0)
    reading = 100.0
    for _ in range(8):                                 # eight whole-kWh ticks, 0.25 kWh per poll
        _stalled(db, cid, 1.0, reading=reading)        # flat while the car draws 1 kWh…
        reading += 1.0
        db.accumulate_wallbox_energy(cid, reading, 0.25)   # …then the meter ticks
    db.finalize_charge(cid, types.SimpleNamespace(soc=70, latitude=1.0, longitude=2.0),
                       max_power_kw=3.0)
    ac = db._conn.execute("SELECT ac_energy_kwh FROM charges WHERE id=?", (cid,)).fetchone()[0]
    assert ac == 8.0                                   # kept: it never missed 3 kWh in a row


def test_a_flat_counter_proves_nothing_when_the_car_is_not_drawing(tmp_path):
    """Charge finished, cable still in: the counter is flat because nothing is flowing. The car
    reports no power, so the recorder sends 0 and none of it counts against the meter."""
    db = D.Database(str(tmp_path / "t.db"))
    cid = db.create_charge(1, types.SimpleNamespace(soc=80, latitude=1.0, longitude=2.0))
    db.set_charge_wallbox_start(cid, 100.0)
    for _ in range(80):                                # forty minutes of flat counter, no car power
        db.accumulate_wallbox_energy(cid, 100.0, 0.0)
    assert (db._conn.execute("SELECT wb_stuck_kwh FROM charges WHERE id=?",
                             (cid,)).fetchone()[0] or 0.0) == 0.0


def test_the_stall_latches_once_it_has_gone_too_far(tmp_path):
    """A meter that wakes up again has still missed what it missed — the total it ends on is short,
    so a later rise must not clear the verdict."""
    db = D.Database(str(tmp_path / "t.db"))
    cid = db.create_charge(1, types.SimpleNamespace(soc=30, latitude=1.0, longitude=2.0))
    db.set_charge_wallbox_start(cid, 100.0)
    _stalled(db, cid, 10.0)                            # frozen through 10 kWh
    db.accumulate_wallbox_energy(cid, 101.0, 0.25)     # …and back to life
    assert db._conn.execute("SELECT wb_stuck_kwh FROM charges WHERE id=?",
                            (cid,)).fetchone()[0] >= D._WB_STUCK_KWH


def test_a_brief_gap_clears_itself(tmp_path):
    """One or two polls without a reading is an HA hiccup, not a dead meter: below the threshold a
    real rise wipes the slate, so it never accumulates across a whole session."""
    db = D.Database(str(tmp_path / "t.db"))
    cid = db.create_charge(1, types.SimpleNamespace(soc=30, latitude=1.0, longitude=2.0))
    db.set_charge_wallbox_start(cid, 100.0)
    for i in range(20):
        db.accumulate_wallbox_energy(cid, 100.0 + i, 0.25)   # a rise…
        db.accumulate_wallbox_energy(cid, 100.0 + i, 0.25)   # …then one flat poll
    assert db._conn.execute("SELECT wb_stuck_kwh FROM charges WHERE id=?",
                            (cid,)).fetchone()[0] < D._WB_STUCK_KWH


def test_repair_cleans_bogus_and_rescales_cost(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    con = db._conn
    cols = ("vehicle_id, location_type, started_at, ended_at, start_soc, end_soc, "
            "energy_added_kwh, ac_energy_kwh, cost, max_power_kw, duration_min")
    # Bogus HOME charge: 10,570 kWh AC for a 15-min session, cost billed on it (~0.32 €/kWh).
    con.execute(f"INSERT INTO charges (id, {cols}) VALUES (1,1,'HOME',?,?,38,40,0.7,10570.0,3382.40,3.1,15)",
                (_at(minutes=15), _at(minutes=0)))
    # Real HOME charge: 6.7 kWh AC over ~3h — must be left alone.
    con.execute(f"INSERT INTO charges (id, {cols}) VALUES (2,1,'HOME',?,?,66,84,10.0,6.7,2.15,3.2,176)",
                (_at(hours=3), _at(minutes=0)))
    con.execute("DELETE FROM settings WHERE key='charges_wb_energy_repair_v1'")  # let it run again
    con.commit()

    db._repair_bogus_wallbox_energy()

    bad = con.execute("SELECT ac_energy_kwh, cost FROM charges WHERE id=1").fetchone()
    good = con.execute("SELECT ac_energy_kwh, cost FROM charges WHERE id=2").fetchone()
    assert bad["ac_energy_kwh"] is None
    assert bad["cost"] == round(3382.40 / 10570.0 * 0.7, 2)   # 0.22 — rescaled to DC at same €/kWh
    assert good["ac_energy_kwh"] == 6.7                       # untouched
    assert good["cost"] == 2.15


def test_repair_is_idempotent(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    con = db._conn
    con.execute("INSERT INTO charges (id, vehicle_id, location_type, started_at, ended_at, "
                "start_soc, end_soc, energy_added_kwh, ac_energy_kwh, cost, max_power_kw, duration_min) "
                "VALUES (1,1,'HOME',?,?,38,40,0.7,10570.0,3382.40,3.1,15)",
                (_at(minutes=15), _at(minutes=0)))
    con.execute("DELETE FROM settings WHERE key='charges_wb_energy_repair_v1'")
    con.commit()
    db._repair_bogus_wallbox_energy()
    db._repair_bogus_wallbox_energy()                 # second run is a no-op (flag set)
    assert db.get_setting("charges_wb_energy_repair_v1") == "1"
