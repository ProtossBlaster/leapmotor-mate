"""A charge that keeps going below the detection floor must keep its energy (#316).

@arzthilfe, C10, 81.9 kWh pack, home wallbox at ~1.7 kW. His bundle, charge #36:

    2026-09-21T13:50 → +1d 01:45  SoC 78.3→100.0  DC=11.38  max=1.7kW  715min
    frames 1349/1349 distinct · plug=1342/1349 · chg=738/1349 · A min=-2.4 max=1.1

78.3 → 100.0 is 21.7 points, 17.77 kWh on that pack. Mate stored 11.38 kWh — 13.9 points,
so it stopped counting at SoC 92.2 and threw away the last 6.4 kWh. His wallbox measured
21.7 kWh from the wall, which against 17.77 kWh is 82% efficiency: entirely ordinary.

The cause is the snap-to-full guard. On a charge ending at 100% the energy is anchored to
`_last_charging_soc` — the last sample with `charging=1` — because the B10's BMS snaps the
displayed SoC to 100.0 in the very poll where charging stops, ~0.9% of SoC with no energy
behind it. But `charging` is set by the charge-DETECTION floor (his: 2.0 A), and his pack
current runs between 2.4 and 1.1 A: `chg=738/1349`. So a guard written to drop 0.9% of
phantom SoC dropped 7.8% of real charging.

It is the same threshold doing a job that is not its own, for the fourth time: whether a
charge starts, whether it continues (#307), what power to print (#307), and now which SoC
anchors the energy. The anchor here is the physical fact the guard actually needs — the
cable is connected and current is still flowing into the pack — and the floor goes back to
deciding only whether a session is open.
"""
import types

import db as D

CAPACITY = 81.9


def _charge(tmp_path, start_soc, started_at="2026-09-21T13:50:00+00:00"):
    db = D.Database(str(tmp_path / "t.db"))
    db.set_battery_capacity(CAPACITY)
    db._conn.execute(
        "INSERT INTO charges (id,vehicle_id,started_at,start_soc) VALUES (1,1,?,?)",
        (started_at, start_soc))
    db._conn.commit()
    return db


def _pos(db, recorded_at, soc, charging, *, plug=None, current=None):
    db._conn.execute(
        "INSERT INTO positions (vehicle_id,recorded_at,soc,charging,plug_connected,charge_current_a)"
        " VALUES (1,?,?,?,?,?)", (recorded_at, soc, charging, plug, current))
    db._conn.commit()


def _row(db, charge_id=1):
    return db._conn.execute("SELECT * FROM charges WHERE id=?", (charge_id,)).fetchone()


def test_the_energy_follows_the_cable_and_the_current_not_the_detection_floor(tmp_path):
    """His charge, replayed: current under the floor, cable in, SoC still climbing."""
    db = _charge(tmp_path, start_soc=78.3)
    _pos(db, "2026-09-21T20:00:00+00:00", 90.0, 1, plug=1, current=-2.4)
    _pos(db, "2026-09-21T23:00:00+00:00", 92.2, 1, plug=1, current=-2.1)
    # The current drops under his 2.0 A detection floor. The cable is still in, the car is
    # still drawing, and the SoC keeps climbing for another 78 minutes.
    _pos(db, "2026-09-22T00:00:00+00:00", 95.0, 0, plug=1, current=-1.6)
    _pos(db, "2026-09-22T01:00:00+00:00", 98.4, 0, plug=1, current=-1.3)
    _pos(db, "2026-09-22T01:45:00+00:00", 100.0, 0, plug=1, current=-1.1)
    db.finalize_charge(1, types.SimpleNamespace(soc=100.0), max_power_kw=1.7)
    assert _row(db)["energy_added_kwh"] == round((100.0 - 78.3) / 100 * CAPACITY, 3)   # 17.772


def test_the_bms_snap_to_full_is_still_dropped(tmp_path):
    """The guard's own case must survive: at the snap the cable is in but NO current flows."""
    db = _charge(tmp_path, start_soc=92.3)
    _pos(db, "2026-09-21T20:00:00+00:00", 99.1, 1, plug=1, current=-3.0)
    _pos(db, "2026-09-21T20:00:30+00:00", 99.1, 1, plug=1, current=-2.8)
    _pos(db, "2026-09-21T20:01:00+00:00", 100.0, 0, plug=1, current=0.0)   # the snap
    db.finalize_charge(1, types.SimpleNamespace(soc=100.0), max_power_kw=5.07)
    assert _row(db)["energy_added_kwh"] == round((99.1 - 92.3) / 100 * CAPACITY, 3)


def test_a_database_without_current_readings_behaves_as_before(tmp_path):
    """Rows written before the column existed carry no current: fall back to `charging`."""
    db = _charge(tmp_path, start_soc=92.3)
    _pos(db, "2026-09-21T20:00:00+00:00", 99.1, 1)
    _pos(db, "2026-09-21T20:01:00+00:00", 100.0, 0)
    db.finalize_charge(1, types.SimpleNamespace(soc=100.0))
    assert _row(db)["energy_added_kwh"] == round((99.1 - 92.3) / 100 * CAPACITY, 3)


def test_current_flowing_after_the_cable_is_out_is_not_counted(tmp_path):
    """Whatever the car reports once unplugged is not this charge's energy."""
    db = _charge(tmp_path, start_soc=90.0)
    _pos(db, "2026-09-21T20:00:00+00:00", 95.0, 1, plug=1, current=-2.5)
    _pos(db, "2026-09-21T20:30:00+00:00", 100.0, 0, plug=0, current=-1.5)
    db.finalize_charge(1, types.SimpleNamespace(soc=100.0), max_power_kw=1.7)
    assert _row(db)["energy_added_kwh"] == round((95.0 - 90.0) / 100 * CAPACITY, 3)


def _stored_charge(db, charge_id, **cols):
    keys = ",".join(cols)
    marks = ",".join("?" * len(cols))
    db._conn.execute(f"INSERT INTO charges (id,vehicle_id,{keys}) VALUES (?,1,{marks})",
                     (charge_id, *cols.values()))
    db._conn.commit()


def test_charges_already_recorded_are_recomputed_once(tmp_path):
    """His 39 existing charges carry the truncated figure; the repair has to reach them."""
    db = D.Database(str(tmp_path / "t.db"))
    db.set_battery_capacity(CAPACITY)
    _stored_charge(db, 1, started_at="2026-09-21T13:50:00+00:00", ended_at="2026-09-22T01:45:00+00:00",
                   start_soc=78.3, end_soc=100.0, energy_added_kwh=11.384, cost=3.42,
                   location_type="HOME")
    _pos(db, "2026-09-21T23:00:00+00:00", 92.2, 1, plug=1, current=-2.1)
    _pos(db, "2026-09-22T01:45:00+00:00", 100.0, 0, plug=1, current=-1.1)
    db.set_setting("charges_energy_below_floor_repair_v1", "")   # un DB scritto prima della riparazione
    db._repair_charges_anchored_below_the_detection_floor()
    row = _row(db)
    assert row["energy_added_kwh"] == round((100.0 - 78.3) / 100 * CAPACITY, 3)
    # Billed on the DC estimate: the cost follows at the same €/kWh it was written with.
    assert row["cost"] == round(3.42 / 11.384 * row["energy_added_kwh"], 2)
    # Once only: a second pass must not move anything.
    db._conn.execute("UPDATE charges SET energy_added_kwh=99.0 WHERE id=1")
    db._conn.commit()
    db._repair_charges_anchored_below_the_detection_floor()
    assert _row(db)["energy_added_kwh"] == 99.0


def test_the_repair_leaves_a_wallbox_billed_cost_and_a_typed_total_alone(tmp_path):
    """Energy is recomputed; a cost that was measured or typed is not ours to rescale."""
    db = D.Database(str(tmp_path / "t.db"))
    db.set_battery_capacity(CAPACITY)
    _stored_charge(db, 1, started_at="2026-09-21T13:50:00+00:00", ended_at="2026-09-22T01:45:00+00:00",
                   start_soc=78.3, end_soc=100.0, energy_added_kwh=11.384, cost=6.51,
                   location_type="HOME", ac_energy_kwh=21.7)
    _stored_charge(db, 2, started_at="2026-09-23T13:50:00+00:00", ended_at="2026-09-24T01:45:00+00:00",
                   start_soc=78.3, end_soc=100.0, energy_added_kwh=11.384, cost=9.99,
                   location_type="AC", cost_manual=1)
    for at, soc, charging, cur in (("2026-09-21T23:00:00+00:00", 92.2, 1, -2.1),
                                   ("2026-09-22T01:45:00+00:00", 100.0, 0, -1.1),
                                   ("2026-09-23T23:00:00+00:00", 92.2, 1, -2.1),
                                   ("2026-09-24T01:45:00+00:00", 100.0, 0, -1.1)):
        _pos(db, at, soc, charging, plug=1, current=cur)
    db.set_setting("charges_energy_below_floor_repair_v1", "")
    db._repair_charges_anchored_below_the_detection_floor()
    expected = round((100.0 - 78.3) / 100 * CAPACITY, 3)
    assert _row(db, 1)["energy_added_kwh"] == expected and _row(db, 1)["cost"] == 6.51
    assert _row(db, 2)["energy_added_kwh"] == expected and _row(db, 2)["cost"] == 9.99


def test_a_charge_whose_anchor_does_not_move_is_left_exactly_as_it_is(tmp_path):
    """Measured on the lab's B10: all 13 of its 100%-ending charges keep the same anchor (99.1),
    and their stored kWh were written when the pack was declared 67.1 kWh, not today's 65.0.
    Recomputing them from today's capacity would silently rescale six months of history by 2.5%
    under the name of a bug fix. The repair corrects an ANCHOR; where the anchor did not move,
    it must not touch the row."""
    db = D.Database(str(tmp_path / "t.db"))
    db.set_battery_capacity(65.0)
    _stored_charge(db, 1, started_at="2026-06-10T06:29:00+00:00", ended_at="2026-06-10T08:00:00+00:00",
                   start_soc=92.3, end_soc=100.0, energy_added_kwh=4.563, cost=1.37)
    _pos(db, "2026-06-10T07:00:00+00:00", 99.1, 1, plug=1, current=-3.0)
    _pos(db, "2026-06-10T07:30:00+00:00", 100.0, 0, plug=1, current=0.0)
    db.set_setting("charges_energy_below_floor_repair_v1", "")
    db._repair_charges_anchored_below_the_detection_floor()
    row = _row(db)
    assert row["energy_added_kwh"] == 4.563 and row["cost"] == 1.37


def test_the_repair_keeps_the_capacity_the_charge_was_written_with(tmp_path):
    """His charge again, on a database whose declared pack has since changed. The correction is
    the anchor; the kWh-per-point scale is the one that row already carries."""
    db = D.Database(str(tmp_path / "t.db"))
    db.set_battery_capacity(65.0)                 # today's declaration — NOT the one used then
    _stored_charge(db, 1, started_at="2026-09-21T13:50:00+00:00", ended_at="2026-09-22T01:45:00+00:00",
                   start_soc=78.3, end_soc=100.0, energy_added_kwh=11.384)   # written at 81.9 kWh
    _pos(db, "2026-09-21T23:00:00+00:00", 92.2, 1, plug=1, current=-2.1)
    _pos(db, "2026-09-22T01:45:00+00:00", 100.0, 0, plug=1, current=-1.1)
    db.set_setting("charges_energy_below_floor_repair_v1", "")
    db._repair_charges_anchored_below_the_detection_floor()
    assert _row(db)["energy_added_kwh"] == round((100.0 - 78.3) / 100 * 81.9, 3)   # 17.772
