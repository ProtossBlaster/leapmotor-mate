"""A wallbox that moved house is found again (#442 @JoseRMorales).

The learned wallbox location is the median of the charges the wallbox measured, and a charge far
from it never gets the wallbox counter. After a move every home charge is "far", so none of them
is ever measured, and the learned location can never follow: his 25 charges at the new house in
three weeks all read "away from the home wallbox".

So a charge away from the learned location still READS the counter, on the side, and keeps it only
if the counter followed the car: more than standby can explain (_WB_HOME_MIN_KWH), and never flat
while the car reported drawing power (_WB_STUCK_KWH). Two measurements, never the DC estimate (see
_WB_STUCK_KWH). Until then nothing reads it as wallbox energy. Pure poller.db + the real recorder."""
import types

import db as D
import recorder as R
from state_machine import State, StateEvent

OLD_HOME = (44.4949, 11.3426)
NEW_HOME = (44.6500, 11.5000)      # ~21 km away: well outside the 1 km radius


def _seed_home_charges(db, n=3, at=OLD_HOME, kwh=20.0):
    for i in range(n):
        cid = db.create_charge(1, types.SimpleNamespace(
            soc=40, latitude=at[0] + i * 1e-4, longitude=at[1] - i * 1e-4))
        db._conn.execute("UPDATE charges SET ended_at='2026-07-01T08:00:00+00:00', "
                         "ac_energy_kwh=? WHERE id=?", (kwh, cid))
    db._conn.commit()


def _frame(at, soc, kw, plugged):
    return types.SimpleNamespace(soc=soc, latitude=at[0], longitude=at[1], charge_power_kw=kw,
                                 plug_connected=plugged, charge_deferred=False)


def _start(db, at, counter):
    rec = R.Recorder(db, vehicle_id=1)
    rec._read_wallbox = lambda: {"energy_kwh": counter}
    data = _frame(at, 30, 7.0, True)
    rec._handle_event(StateEvent(State.PARKED_ACTIVE, State.CHARGING, data), data)
    cid = rec._active_charge_id
    # A real charge spans hours: the #46 ceiling must admit the rise.
    db._conn.execute("UPDATE charges SET started_at='2026-07-01T02:00:00+00:00' WHERE id=?", (cid,))
    db._conn.commit()
    return rec, cid


def _end(rec, at, counter):
    rec._read_wallbox = lambda: {"energy_kwh": counter}
    end = _frame(at, 55, 0.0, False)
    rec._handle_event(StateEvent(State.CHARGING, State.PARKED_ACTIVE, end), end)


def _row(db, cid):
    return db._conn.execute("SELECT * FROM charges WHERE id=?", (cid,)).fetchone()


def test_an_away_charge_the_counter_followed_is_attributed(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    _seed_home_charges(db)
    rec, cid = _start(db, NEW_HOME, 100.0)
    _end(rec, NEW_HOME, 108.0)
    row = _row(db, cid)
    assert row["ac_energy_kwh"] == 8.0
    assert row["wallbox_energy_start_kwh"] is not None


def test_an_open_away_charge_shows_no_wallbox_energy(tmp_path):
    """Until the charge has proved it is at the wallbox, nothing reads its counter as wallbox energy."""
    db = D.Database(str(tmp_path / "t.db"))
    _seed_home_charges(db)
    rec, cid = _start(db, NEW_HOME, 100.0)
    rec._accumulate_wallbox(104.0, 3.5)
    row = _row(db, cid)
    assert row["ac_energy_kwh"] is None and row["wallbox_energy_start_kwh"] is None
    assert row["wb_probe_kwh"] == 4.0


def test_standby_creep_on_an_away_charge_stays_away(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    _seed_home_charges(db)
    rec, cid = _start(db, NEW_HOME, 100.0)
    _end(rec, NEW_HOME, 100.06)
    row = _row(db, cid)
    assert row["ac_energy_kwh"] is None and row["wallbox_energy_start_kwh"] is None


def test_a_counter_that_stood_still_while_the_car_drew_is_another_cars(tmp_path):
    """The house wallbox charging another car while this one charges elsewhere: the counter rises,
    but it stood still for more than _WB_STUCK_KWH of what THIS car drew. Not this charge's."""
    db = D.Database(str(tmp_path / "t.db"))
    _seed_home_charges(db)
    rec, cid = _start(db, NEW_HOME, 100.0)
    for _ in range(4):
        rec._accumulate_wallbox(100.0, 1.0)      # flat while the car draws: 4 kWh missed
    _end(rec, NEW_HOME, 106.0)
    row = _row(db, cid)
    assert row["ac_energy_kwh"] is None and row["wallbox_energy_start_kwh"] is None


def test_the_learned_location_follows_the_move(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    _seed_home_charges(db, n=3)
    assert db.wallbox_energy_applies(1, *NEW_HOME) is False
    for _ in range(4):
        rec, _cid = _start(db, NEW_HOME, 100.0)
        _end(rec, NEW_HOME, 110.0)
    assert db.wallbox_energy_applies(1, *NEW_HOME) is True
    assert db.wallbox_energy_applies(1, *OLD_HOME) is False


def _resume(db):
    rec = R.Recorder(db, vehicle_id=1)
    data = types.SimpleNamespace(soc=40, latitude=NEW_HOME[0], longitude=NEW_HOME[1],
                                 charging_status=1, plug_connected=True, charge_deferred=False,
                                 gear="P", speed_kmh=0)
    rec._resume_or_close(data)
    return rec


def test_a_restart_keeps_measuring_an_away_charge_on_the_side(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    _seed_home_charges(db)
    _rec, cid = _start(db, NEW_HOME, 100.0)
    rec = _resume(db)
    assert rec._active_charge_id == cid and rec._charge_at_wallbox and rec._wb_probe
    _end(rec, NEW_HOME, 108.0)
    assert _row(db, cid)["ac_energy_kwh"] == 8.0


def test_an_away_charge_opened_before_the_upgrade_is_not_measured_halfway(tmp_path):
    """Opened by a version that did not read the counter away from home: reading it from now on
    would measure half a charge and call it the whole."""
    db = D.Database(str(tmp_path / "t.db"))
    _seed_home_charges(db)
    db.create_charge(1, types.SimpleNamespace(soc=30, latitude=NEW_HOME[0], longitude=NEW_HOME[1]))
    rec = _resume(db)
    assert rec._active_charge_id is not None and not rec._charge_at_wallbox
