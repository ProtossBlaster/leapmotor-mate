"""Automatic 🧭 note generation for a BRAND-NEW trip/charge — the poller kicks it off the
moment the record closes (live DRIVING→PARKED / CHARGING→PARKED, or a reconstructed trip/
charge from an offline gap), never a historical sweep. These tests drive the real Recorder
through real state transitions and only stub out _auto_note_trip/_auto_note_charge
themselves (network + threading — see test_auto_note.py for what those actually build).
"""
import types

import db as D
import recorder as R
from client import VehicleData
from state_machine import State, StateEvent


def _vd(soc=50.0, lat=45.0, lon=9.0, odometer_km=1000.0):
    return VehicleData(
        vin="TESTVIN", timestamp_ms=0, soc=soc, range_km=300, odometer_km=odometer_km,
        speed_kmh=0.0, gear="P", vehicle_state="parked",
        charging_status=0, charge_power_kw=0.0, latitude=lat, longitude=lon,
        outside_temp=None, inside_temp=20.0, climate_target_temp=21.0, battery_min_temp=15.0,
        is_locked=True, climate_on=False, climate_cooling=False, climate_heating=False,
        climate_defrost=False, trunk_open=False, windows_open=False, sunshade_open=False,
        any_door_open=False, plug_connected=False, remaining_charge_min=0,
        charge_voltage_v=0.0, charge_current_a=0.0,
    )


def _rec(db):
    rec = R.Recorder(db, vehicle_id=1)
    rec._read_wallbox_energy = lambda: None   # no HA wallbox in this test env
    return rec


# ── live trip close ──────────────────────────────────────────────────────────────

def test_live_trip_close_triggers_auto_note(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    rec = _rec(db)
    calls = []
    rec._auto_note_trip = lambda tid: calls.append(tid)

    rec._handle_event(StateEvent(State.PARKED_ACTIVE, State.DRIVING, _vd()), _vd())
    trip_id = rec._active_trip_id
    end = _vd(odometer_km=1010.0)   # 10 km — clears the 0.5 km short-hop floor
    rec._handle_event(StateEvent(State.DRIVING, State.PARKED_ACTIVE, end), end)

    assert calls == [trip_id]


def test_short_hop_trip_is_discarded_without_auto_note(tmp_path):
    """A trip under the 0.5 km floor gets deleted outright — auto-noting a row that no
    longer exists would just be a wasted network call for nothing."""
    db = D.Database(str(tmp_path / "t.db"))
    rec = _rec(db)
    calls = []
    rec._auto_note_trip = lambda tid: calls.append(tid)

    rec._handle_event(StateEvent(State.PARKED_ACTIVE, State.DRIVING, _vd()), _vd())
    end = _vd(odometer_km=1000.1)   # 0.1 km — below the short-hop floor
    rec._handle_event(StateEvent(State.DRIVING, State.PARKED_ACTIVE, end), end)

    assert calls == []


# ── live charge close ────────────────────────────────────────────────────────────

def test_live_charge_close_triggers_auto_note(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    rec = _rec(db)
    calls = []
    rec._auto_note_charge = lambda cid: calls.append(cid)

    rec._handle_event(StateEvent(State.PARKED_ACTIVE, State.CHARGING, _vd()), _vd())
    charge_id = rec._active_charge_id
    end = _vd(soc=80.0)
    rec._handle_event(StateEvent(State.CHARGING, State.PARKED_ACTIVE, end), end)

    assert calls == [charge_id]


# ── reconstructed charge (offline SoC jump, #29) ─────────────────────────────────

def test_reconstructed_charge_triggers_auto_note(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    db.set_battery_capacity(50.0)
    db.ensure_vehicle("TESTVIN", "B10")
    rec = _rec(db)
    calls = []
    rec._auto_note_charge = lambda cid: calls.append(cid)
    rec._sm.state = State.PARKED_ACTIVE
    rec._last_soc, rec._last_soc_ts = 60.0, "2026-06-09T10:00:00+00:00"

    rec._maybe_reconstruct_charge(_vd(soc=70.0))

    assert len(calls) == 1
    row = db._conn.execute("SELECT id FROM charges WHERE id=?", (calls[0],)).fetchone()
    assert row is not None


# ── reconstructed trip (offline odometer jump, #118) ─────────────────────────────

def test_reconstructed_trip_triggers_auto_note(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    db.ensure_vehicle("TESTVIN", "B10")
    rec = _rec(db)
    calls = []
    rec._auto_note_trip = lambda tid: calls.append(tid)
    rec._sm.state = State.PARKED_ACTIVE
    rec._last_odometer = 1000.0
    rec._last_soc, rec._last_soc_ts = 60.0, "2026-06-09T10:00:00+00:00"

    rec._maybe_reconstruct_trip(_vd(soc=60.0, odometer_km=1010.0))   # +10 km, flat SoC → a drive

    assert len(calls) == 1
    row = db._conn.execute("SELECT id FROM trips WHERE id=?", (calls[0],)).fetchone()
    assert row is not None


# ── the real body: only_if_note_empty guards a note already present ──────────────

def test_auto_note_trip_body_never_overwrites_a_note_typed_in_the_meantime(tmp_path, monkeypatch):
    """Belt-and-suspenders: even though this runs moments after trip-close, prove the
    only_if_note_empty guard actually reaches db_reader.generate_trip_auto_note."""
    import db_reader
    import geocode
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    pdb = D.Database(str(tmp_path / "t.db"))
    pdb._conn.execute(
        "INSERT INTO trips (vehicle_id, started_at, ended_at, start_lat, start_lon, note)"
        " VALUES (1,'2026-07-04T10:00:00+00:00','2026-07-04T10:30:00+00:00',45.0,9.0,"
        "'nota scritta a mano')")
    pdb._conn.commit()
    tid = pdb._conn.execute("SELECT MAX(id) AS id FROM trips").fetchone()[0]
    monkeypatch.setattr(geocode, "reverse_geocode", lambda lat, lon, provider, api_key: "Some address")

    rec = R.Recorder(pdb, vehicle_id=1)
    rec._auto_note_trip_body(tid)

    row = pdb._conn.execute("SELECT note FROM trips WHERE id=?", (tid,)).fetchone()
    assert row[0] == "nota scritta a mano"
