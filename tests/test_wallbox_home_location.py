"""Attribute the home wallbox counter ONLY to charges that happened at the wallbox (#152/#154 root).

A reachable wallbox answers a counter reading even while the car charges far away; if an energy
meter also sees the wallbox's standby, that counter CREEPS with the car elsewhere and its per-poll
rise can accumulate past the 0.05 kWh floor, mislabelling a public charge as a home session. Instead
of cancelling data after the fact, the poller learns where the wallbox is (from charges where it
measured real energy) and skips its counter for a charge KNOWN to be elsewhere — conservatively:
unknown location or a charge without GPS still attributes, so a real home charge is never dropped.
Pure poller.db + a recorder end-to-end → runs in CI, no HA, no network."""
import types
import db as D

HOME = (44.4949, 11.3426)      # Bologna
RIMINI = (44.0594, 12.5683)    # a public station ~100 km away


def _seed_home_charges(db, n=3, kwh=20.0):
    """n closed charges at HOME where the wallbox measured real energy → learnable location."""
    for i in range(n):
        cid = db.create_charge(1, types.SimpleNamespace(
            soc=40, latitude=HOME[0] + i * 1e-4, longitude=HOME[1] - i * 1e-4))
        db._conn.execute("UPDATE charges SET ended_at='2026-07-01T08:00:00+00:00', "
                         "ac_energy_kwh=? WHERE id=?", (kwh, cid))
    db._conn.commit()


# ── learned location ────────────────────────────────────────────────────────
def test_location_unknown_until_enough_real_charges(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    assert db._learned_wallbox_location(1) is None          # empty install
    _seed_home_charges(db, n=1)
    assert db._learned_wallbox_location(1) is None          # 1 < min samples → still unknown


def test_location_learned_from_real_charges_ignores_standby_and_no_gps(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    _seed_home_charges(db, n=3, kwh=20.0)
    # noise that must NOT move the learned point: a standby-sized energy, and a no-GPS charge
    c = db.create_charge(1, types.SimpleNamespace(soc=40, latitude=10.0, longitude=10.0))
    db._conn.execute("UPDATE charges SET ac_energy_kwh=0.04 WHERE id=?", (c,))     # < 2 kWh → standby
    c2 = db.create_charge(1, types.SimpleNamespace(soc=40, latitude=None, longitude=None))
    db._conn.execute("UPDATE charges SET ac_energy_kwh=30.0 WHERE id=?", (c2,))    # real but no GPS
    db._conn.commit()
    lat, lon = db._learned_wallbox_location(1)
    assert abs(lat - HOME[0]) < 0.01 and abs(lon - HOME[1]) < 0.01                 # ≈ home, not (10,10)


# ── the decision ────────────────────────────────────────────────────────────
def test_applies_true_when_location_unknown(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    # No learned home yet → conservative: attribute exactly as the old code did, even far away.
    assert db.wallbox_energy_applies(1, RIMINI[0], RIMINI[1]) is True


def test_applies_true_without_gps_even_when_location_known(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    _seed_home_charges(db)
    assert db.wallbox_energy_applies(1, None, None) is True          # box/garage, no signal


def test_applies_true_at_home_false_far_away(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    _seed_home_charges(db)
    assert db.wallbox_energy_applies(1, HOME[0], HOME[1]) is True    # at the wallbox
    assert db.wallbox_energy_applies(1, RIMINI[0], RIMINI[1]) is False  # 100 km away


# ── recorder end-to-end ─────────────────────────────────────────────────────
def _recorder_charge(db, lat, lon, wb_start, wb_end):
    """Drive a full CHARGING→PARKED cycle through the REAL recorder with a scripted wallbox counter."""
    import recorder as R
    from state_machine import State, StateEvent
    rec = R.Recorder(db, vehicle_id=1)
    readings = iter([wb_start, wb_end])
    rec._read_wallbox_energy = lambda: next(readings)
    data = types.SimpleNamespace(soc=30, latitude=lat, longitude=lon, charge_power_kw=7.0)
    rec._handle_event(StateEvent(State.PARKED_ACTIVE, State.CHARGING, data), data)
    cid = rec._active_charge_id
    # A real charge spans hours; push started_at back so the #46 anti-glitch ceiling admits the
    # end-of-charge rise (with started_at=now the ceiling is ~1 kWh and would reject +8 kWh).
    db._conn.execute("UPDATE charges SET started_at='2026-07-01T02:00:00+00:00' WHERE id=?", (cid,))
    db._conn.commit()
    end = types.SimpleNamespace(soc=55, latitude=lat, longitude=lon, charge_power_kw=0.0)
    rec._handle_event(StateEvent(State.CHARGING, State.PARKED_ACTIVE, end), end)
    return cid


def test_recorder_attributes_wallbox_for_home_charge(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    _seed_home_charges(db)
    cid = _recorder_charge(db, HOME[0], HOME[1], 100.0, 108.0)
    ac, wb = db._conn.execute("SELECT ac_energy_kwh, wallbox_energy_start_kwh "
                              "FROM charges WHERE id=?", (cid,)).fetchone()
    assert ac == 8.0 and wb is not None            # home charge → wallbox energy attributed


def test_recorder_skips_wallbox_for_far_charge(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    _seed_home_charges(db)
    cid = _recorder_charge(db, RIMINI[0], RIMINI[1], 100.0, 100.06)   # counter creeps on standby
    ac, wb = db._conn.execute("SELECT ac_energy_kwh, wallbox_energy_start_kwh "
                              "FROM charges WHERE id=?", (cid,)).fetchone()
    assert ac is None and wb is None               # far → never attributed, stays a public charge


def test_recorder_still_attributes_when_home_not_learned_yet(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))     # no prior home charges → location unknown
    cid = _recorder_charge(db, RIMINI[0], RIMINI[1], 100.0, 108.0)
    ac = db._conn.execute("SELECT ac_energy_kwh FROM charges WHERE id=?", (cid,)).fetchone()[0]
    assert ac == 8.0                               # bootstrap → old behaviour, nothing dropped
