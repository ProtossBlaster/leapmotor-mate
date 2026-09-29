"""The power chart of a merged charge draws the whole plug-in, not just its first piece.

@arzthilfe, #341: "the charging power graph always stops exactly when I change the charging power
setting on my wallbox. I always switch from 11A to 8A for the night. In this example it ends at
22:17, even though the charging session continues until 07:09 the next morning."

A charge the car reported as two rows is read back as one session everywhere else — the header, the
kilowatt-hours, the duration all come from `_charge_group_stats`, which walks parent + children. The
curve did not: it took the PARENT row's own `started_at`/`ended_at`, so it ended where the first
piece ended. Worse, the cap that stops an orphan charge from absorbing a later one
(`_next_charge_start_utc`) counted this session's own second piece as "the next charge", which
trimmed even the parent's last sample.

Measured on the shape above — 19:00 plug-in, 11 A, the wallbox turned down to 8 A at 22:17, cable
out at 07:09 — the curve carried 40 of 98 samples, ended at 22:15, and held exactly one power
value: the 8 A half of the night was not on the chart at all.

🔑 Only the CURVE widens. The cost and energy readers next to it (`compute_cost`,
`_custom_kwh_cost`, `_dynamic_sensor_cost`, `_charge_energy_below_soc`) price each piece on its own
and are summed by the group afterwards — widening those would bill the second piece twice.
"""
import datetime as dt

import pytest

import db as D
import db_reader

VIN = "LVIN0000000000001"
PLUGGED_IN = dt.datetime(2026, 9, 20, 19, 0, tzinfo=dt.timezone.utc)
TURNED_DOWN = dt.datetime(2026, 9, 20, 22, 17, tzinfo=dt.timezone.utc)   # 11 A → 8 A
CABLE_OUT = dt.datetime(2026, 9, 21, 7, 9, tzinfo=dt.timezone.utc)
VOLTS = 230.0


def _install(tmp_path, monkeypatch, *, merged=True):
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,?,'C10')", (VIN,))
    for key, value in (("timezone", "UTC"), ("setup_complete", "1"), ("language", "en")):
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
    c.execute("INSERT INTO charges (id,vehicle_id,started_at,ended_at,start_soc,end_soc,"
              "energy_added_kwh,location_type) VALUES (1,1,?,?,20,55,12.0,'HOME')",
              (PLUGGED_IN.isoformat(), TURNED_DOWN.isoformat()))
    c.execute("INSERT INTO charges (id,vehicle_id,started_at,ended_at,start_soc,end_soc,"
              "energy_added_kwh,location_type,merged_into_id) VALUES (2,1,?,?,55,90,12.0,'HOME',?)",
              (TURNED_DOWN.isoformat(), CABLE_OUT.isoformat(), 1 if merged else None))
    t = PLUGGED_IN
    while t <= CABLE_OUT:
        c.execute("INSERT INTO positions (vehicle_id,recorded_at,charging,charge_voltage_v,"
                  "charge_current_a,soc) VALUES (1,?,1,?,?,50)",
                  (t.isoformat(), VOLTS, 11.0 if t < TURNED_DOWN else 8.0))
        t += dt.timedelta(minutes=5)
    c.commit()
    c.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setattr(db_reader, "_current_vehicle_id", lambda: 1)
    return path


def _kw(amps):
    return round(VOLTS * amps / 1000.0, 3)


def test_the_curve_reaches_the_end_of_the_session(tmp_path, monkeypatch):
    _install(tmp_path, monkeypatch)
    curve = db_reader.get_charge_power_curve(1)
    assert curve["times"], "the merged charge has no curve at all"
    assert curve["times"][-1] >= (CABLE_OUT - dt.timedelta(minutes=5)).isoformat(), \
        f"the curve stops at {curve['times'][-1]}, the session ends at {CABLE_OUT.isoformat()}"


def test_both_power_levels_are_on_the_chart(tmp_path, monkeypatch):
    """The reported symptom in one line: the half of the night at 8 A was simply not drawn."""
    _install(tmp_path, monkeypatch)
    drawn = set(db_reader.get_charge_power_curve(1)["power"])
    assert _kw(11.0) in drawn, "the 11 A part is missing — the fixture, not the defect"
    assert _kw(8.0) in drawn, f"the 8 A part of the session is not on the chart: {sorted(drawn)}"


def test_an_unmerged_neighbour_is_still_kept_out(tmp_path, monkeypatch):
    """The guard this must not undo (#24): a charge whose `ended_at` bled past a later, SEPARATE
    charge must not absorb its samples. Same two rows, not merged — the first must stop."""
    _install(tmp_path, monkeypatch, merged=False)
    with db_reader._conn_rw() as db:
        db.execute("UPDATE charges SET ended_at=? WHERE id=1", (CABLE_OUT.isoformat(),))
    curve = db_reader.get_charge_power_curve(1)
    assert _kw(8.0) not in set(curve["power"]), \
        "a separate later charge's samples leaked into this one's curve"


def test_the_child_still_draws_its_own_half(tmp_path, monkeypatch):
    """Asking for the second piece by id is still the second piece, not the whole group again —
    nothing else in Mate should start seeing double."""
    _install(tmp_path, monkeypatch)
    drawn = set(db_reader.get_charge_power_curve(2)["power"])
    assert _kw(8.0) in drawn and _kw(11.0) not in drawn, sorted(drawn)
