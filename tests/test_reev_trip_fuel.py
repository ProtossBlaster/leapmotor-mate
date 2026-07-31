"""REEV Phase C — per-trip fuel consumption. The poller records the fuel-tank % (signal 3235) at
trip start/end; the web layer derives litres burned (Δ% × 50 L tank) and L/100km. There's no
'engine on' PID — the range-extender ran iff the fuel level dropped. All inert on a BEV (no fuel).

Pure helper runs with no DB; the poller-capture tests use a tmp_path DB. CI-safe."""
import sqlite3
import types

import db as D
import db_reader


def _pos_db(rows):
    """In-memory positions table with (recorded_at, odometer_km, fuel_level_pct) for vehicle 1 —
    the only columns _reev_engine_on reads. No ambient DB (CI-safe)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE positions (id INTEGER PRIMARY KEY, vehicle_id INT, "
                 "recorded_at TEXT, odometer_km REAL, fuel_level_pct REAL)")
    for i, (ts, odo, fuel) in enumerate(rows):
        conn.execute("INSERT INTO positions VALUES (?,?,?,?,?)", (i, 1, ts, odo, fuel))
    return conn


def _vd(fuel=None, soc=80.0, odo=1000.0):
    return types.SimpleNamespace(soc=soc, odometer_km=odo, latitude=45.0, longitude=9.0,
                                 fuel_level_pct=fuel)


# ── the derivation helper (pure) ──────────────────────────────────────────────
def test_engine_ran_gives_litres_and_l_per_100km():
    out = db_reader._reev_trip_fuel(98.4, 96.2, 17.6)   # gm27271's real engine-on trip
    assert out["engine_ran"] is True
    assert out["fuel_used_l"] == 1.1                    # (2.2/100) × 50 L
    assert out["fuel_l_100km"] == 6.2                   # 1.1 / 17.6 × 100 → ~6.2, matches on-car


def test_no_fuel_data_is_inert():
    assert db_reader._reev_trip_fuel(None, None, 20) == {
        "fuel_used_l": None, "fuel_l_100km": None, "engine_ran": False, "engine_km": None}


# ── the engine-on basis: L/100km over the generator-driving distance (matches the car) ─────────
def test_engine_on_segments_exclude_ev_and_stationary_charge():
    # A mixed trip: 10 km on the generator, 10 km pure-electric, then a stationary battery charge.
    conn = _pos_db([
        ("2026-07-07T20:00:00", 10.0, 96.0),
        ("2026-07-07T20:05:00", 20.0, 95.0),   # +10 km, −1.0%  → generator DRIVING (counts)
        ("2026-07-07T20:15:00", 30.0, 95.0),   # +10 km,  0%    → pure electric (excluded from km)
        ("2026-07-07T20:30:00", 30.0, 94.0),   # +0 km,  −1.0%  → stationary charge (excluded from litres)
    ])
    eng = db_reader._reev_engine_on(conn, 1, "2026-07-07T20:00:00", "2026-07-07T20:30:00")
    assert eng == {"engine_km": 10.0, "engine_fuel_pct": 1.0}


def test_engine_on_basis_matches_car_not_whole_trip():
    eng = {"engine_km": 10.0, "engine_fuel_pct": 1.0}       # from the trip above
    out = db_reader._reev_trip_fuel(96.0, 94.0, 30.0, eng)  # total drop 2.0% over the whole 30 km
    assert out["engine_ran"] is True
    assert out["engine_km"] == 10.0
    assert out["fuel_used_l"] == 1.0                        # total litres that left the tank (2.0% × 50)
    assert out["fuel_l_100km"] == 5.0                       # 1.0% × 50 / 10 km — generator-on basis, realistic
    # the OLD whole-trip method would have shown 1.0 L / 30 km = 3.3 → too low (diluted by the EV km)
    assert db_reader._reev_trip_fuel(96.0, 94.0, 30.0)["fuel_l_100km"] == 3.3


def test_falls_back_to_whole_trip_when_positions_pruned():
    # No engine trail (old trip) → keep the whole-trip distance so history doesn't break.
    out = db_reader._reev_trip_fuel(98.4, 96.2, 17.6, None)
    assert out["fuel_l_100km"] == 6.2 and out["engine_km"] is None


def test_engine_on_none_when_no_fuel_trail():
    conn = _pos_db([("2026-07-07T20:00:00", 10.0, None),
                    ("2026-07-07T20:05:00", 20.0, None)])   # BEV / no fuel column data
    assert db_reader._reev_engine_on(conn, 1, "2026-07-07T20:00:00", "2026-07-07T20:05:00") is None


def test_pure_electric_drive_engine_not_flagged():
    out = db_reader._reev_trip_fuel(80.0, 80.0, 20)     # fuel unchanged → engine didn't run
    assert out["engine_ran"] is False and out["fuel_used_l"] is None


def test_signal_noise_below_floor_ignored():
    out = db_reader._reev_trip_fuel(80.1, 80.0, 20)     # 0.1% = one signal tick, not a burn
    assert out["engine_ran"] is False


def test_short_trip_reports_litres_but_no_per_100km():
    out = db_reader._reev_trip_fuel(90.0, 88.0, 0.3)    # < 0.5 km → litres yes, L/100km withheld
    assert out["fuel_used_l"] == 1.0 and out["fuel_l_100km"] is None


# ── poller capture (create_trip / finalize_trip) ──────────────────────────────
def test_create_trip_stores_fuel_start(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    tid = db.create_trip(1, _vd(fuel=96.2))
    assert db._conn.execute("SELECT fuel_start_pct FROM trips WHERE id=?", (tid,)).fetchone()[0] == 96.2


def test_bev_trip_has_null_fuel(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    tid = db.create_trip(1, _vd(fuel=None))             # BEV — no fuel signal
    assert db._conn.execute("SELECT fuel_start_pct FROM trips WHERE id=?", (tid,)).fetchone()[0] is None


def test_finalize_trip_stores_fuel_end(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    db.set_battery_capacity(60.0)
    tid = db.create_trip(1, _vd(fuel=98.4, odo=1000.0))
    db.finalize_trip(tid, _vd(fuel=96.2, soc=75.0, odo=1017.6))
    row = db._conn.execute(
        "SELECT fuel_start_pct, fuel_end_pct FROM trips WHERE id=?", (tid,)).fetchone()
    assert row["fuel_start_pct"] == 98.4 and row["fuel_end_pct"] == 96.2


# ── REEV Phase D — per-trip ELECTRIC from getEC (beta #10 step 2) ──────────────────────────────

def test_reev_elec_from_getec_over_full_distance():
    # gm27271's real getEC driving energy (2.1 kWh) over the whole 19 km the motor drove.
    out = db_reader._reev_trip_elec(2.1, 19.0, True)
    assert out["reev_elec_kwh"] == 2.1
    assert out["reev_elec_kwh_100km"] == 11.1          # 2.1 / 19 * 100, over the FULL distance


def test_reev_elec_uses_full_distance_not_a_generator_subset():
    # Unlike fuel (normalised over the generator-on km), the motor drives the WHOLE trip → full distance.
    assert db_reader._reev_trip_elec(5.0, 40.0, True)["reev_elec_kwh_100km"] == 12.5


def test_reev_elec_inert_without_engine():
    # Pure-electric REEV trip (generator never ran) → this block doesn't apply.
    assert db_reader._reev_trip_elec(2.1, 19.0, False) == {"reev_elec_kwh": None, "reev_elec_kwh_100km": None}


def test_reev_elec_inert_without_getec():
    # BEV, or an engine-on trip the cloud hasn't enriched yet → no getEC → 'pending', not a fake number.
    assert db_reader._reev_trip_elec(None, 19.0, True) == {"reev_elec_kwh": None, "reev_elec_kwh_100km": None}


def test_reev_elec_zero_distance_is_safe():
    assert db_reader._reev_trip_elec(2.1, 0, True) == {"reev_elec_kwh": None, "reev_elec_kwh_100km": None}


# ── beta #20 (@michapr): merging a trip must not lose the petrol ──────────────
#
# He was told to merge two trips to recover the official combined figure, did so, and the fuel
# disappeared: 3.7 L and 7.14 € of petrol gone, the trip's cost falling from 7.53 € to 0.50 €.
# _trip_group_stats aggregated SoC, distance, duration, regen and elevation but not fuel, and the
# detail read the tank off the PARENT row — and merge_trips makes the EARLIER trip the parent. His
# earlier segment was a 2 km electric hop with a flat tank, so the group inherited "no fuel burned"
# from the one segment that had burned none.

def _merged_pair(tmp_path, monkeypatch):
    """His shape: a short electric hop at 09:45, then the long generator-on drive at 09:56."""
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    pdb._conn.execute(
        "INSERT INTO trips (id,vehicle_id,started_at,ended_at,distance_km,start_soc,end_soc,"
        " start_odometer_km,end_odometer_km,fuel_start_pct,fuel_end_pct) "
        "VALUES (1,1,'2026-07-28T09:45:00+00:00','2026-07-28T09:53:00+00:00',2.0,89.9,88.0,"
        " 903,905,72.7,72.7)")                                  # hop: tank flat
    pdb._conn.execute(
        "INSERT INTO trips (id,vehicle_id,started_at,ended_at,distance_km,start_soc,end_soc,"
        " start_odometer_km,end_odometer_km,fuel_start_pct,fuel_end_pct,merged_into_id) "
        "VALUES (2,1,'2026-07-28T09:56:00+00:00','2026-07-28T10:38:00+00:00',57.0,87.8,79.6,"
        " 905,962,72.7,65.3,1)")                                # generator ran: 7.4 points burned
    pdb._conn.commit()
    return pdb


def test_a_merged_trip_keeps_the_petrol_its_later_segment_burned(tmp_path, monkeypatch):
    _merged_pair(tmp_path, monkeypatch)
    d = db_reader.get_trip_detail(1)
    assert d["fuel_start_pct"] == 72.7
    assert d["fuel_end_pct"] == 65.3, "the group took the parent's flat tank — the petrol is gone"
    assert d["engine_ran"] is True
    assert d["fuel_used_l"] and d["fuel_used_l"] > 3.0


def test_the_merged_trip_in_the_list_shows_the_engine_too(tmp_path, monkeypatch):
    """The list builds its row from the same helper, so it carried the same loss — the ⛽ flag on a
    merged generator-on trip simply stopped appearing."""
    _merged_pair(tmp_path, monkeypatch)
    rows = [t for t in db_reader.get_trips(limit=50) if t["id"] == 1]
    assert rows and rows[0]["engine_ran"] is True


def test_an_unmerged_trip_is_unaffected(tmp_path, monkeypatch):
    """The single-trip path must read exactly as before — no children, nothing to span."""
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    pdb._conn.execute(
        "INSERT INTO trips (id,vehicle_id,started_at,ended_at,distance_km,start_soc,end_soc,"
        " fuel_start_pct,fuel_end_pct) VALUES (1,1,'2026-07-28T09:56:00+00:00',"
        " '2026-07-28T10:38:00+00:00',57.0,87.8,79.6,72.7,65.3)")
    pdb._conn.commit()
    d = db_reader.get_trip_detail(1)
    assert (d["fuel_start_pct"], d["fuel_end_pct"]) == (72.7, 65.3)
    assert d["engine_ran"] is True


def test_a_bev_group_stays_inert(tmp_path, monkeypatch):
    """No segment carries a tank → nothing invented, and the fuel card stays away."""
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    pdb._conn.execute(
        "INSERT INTO trips (id,vehicle_id,started_at,ended_at,distance_km,start_soc,end_soc) "
        "VALUES (1,1,'2026-07-28T09:45:00+00:00','2026-07-28T09:53:00+00:00',2.0,89.9,88.0)")
    pdb._conn.execute(
        "INSERT INTO trips (id,vehicle_id,started_at,ended_at,distance_km,start_soc,end_soc,"
        " merged_into_id) VALUES (2,1,'2026-07-28T09:56:00+00:00','2026-07-28T10:38:00+00:00',"
        " 57.0,87.8,79.6,1)")
    pdb._conn.commit()
    d = db_reader.get_trip_detail(1)
    assert d["fuel_start_pct"] is None and d["fuel_end_pct"] is None
    assert d["engine_ran"] is False
