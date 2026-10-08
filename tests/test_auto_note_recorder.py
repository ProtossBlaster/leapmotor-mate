"""Automatic 🧭 note generation for a BRAND-NEW charge — the poller kicks it off the moment the
record closes (live CHARGING→PARKED, or a charge reconstructed from an offline gap), never a
historical sweep. These tests drive the real Recorder through real state transitions and only stub
out _auto_note_charge itself (network + threading — see test_auto_note.py for what it builds).

A trip has no automatic note any more: where it started and ended is an address of its own, looked
up in the background by the web (place_lookup). Closing one writes nothing into its note.
"""
import types

import db as D
import pytest
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
        climate_defrost=False, trunk_open=False, windows_open=False, sunshade_pct=0,
        any_door_open=False, plug_connected=False, remaining_charge_min=0,
        charge_voltage_v=0.0, charge_current_a=0.0,
    )


def _rec(db):
    rec = R.Recorder(db, vehicle_id=1)
    rec._read_wallbox_energy = lambda: None   # no HA wallbox in this test env
    return rec


# ── a trip closes without a note ─────────────────────────────────────────────────

class _Inline:
    """threading.Thread running its target at start(): whatever a close kicks off is done when it returns."""

    def __init__(self, target, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


@pytest.fixture
def quiet(tmp_path, monkeypatch):
    """A recorder whose threads run inline, on a database the web reads too, and every request
    to a geocoding provider written down."""
    import db_reader
    import geocode
    import place_lookup
    asked = []
    monkeypatch.setattr(geocode, "_get", lambda url: asked.append(url) or {})
    monkeypatch.setattr(place_lookup, "maybe_sweep", lambda: None)      # the web's own pass is not the recorder
    monkeypatch.setattr(R, "threading", types.SimpleNamespace(Thread=_Inline))   # the recorder's, not the process's
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    db = D.Database(str(tmp_path / "t.db"))
    db.ensure_vehicle("TESTVIN", "B10")
    db.set_setting("auto_note", "1")
    return db, _rec(db), asked


def test_closing_a_trip_writes_no_note_and_asks_no_provider(quiet):
    db, rec, asked = quiet
    rec._handle_event(StateEvent(State.PARKED_ACTIVE, State.DRIVING, _vd()), _vd())
    end = _vd(odometer_km=1010.0, lat=45.1, lon=9.1)   # 10 km — clears the short-hop floor
    rec._handle_event(StateEvent(State.DRIVING, State.PARKED_ACTIVE, end), end)
    assert [tuple(r) for r in db._conn.execute("SELECT note FROM trips")] == [(None,)]
    assert asked == []


def test_a_reconstructed_trip_writes_no_note_either(quiet):
    db, rec, asked = quiet
    rec._sm.state = State.PARKED_ACTIVE
    rec._odometer_reading = R.OdometerReading(1000.0, 60.0, "2026-06-09T10:00:00+00:00")
    rec._maybe_reconstruct_trip(_vd(soc=60.0, odometer_km=1010.0))   # +10 km, flat SoC → a drive
    assert [tuple(r) for r in db._conn.execute("SELECT note FROM trips")] == [(None,)]
    assert asked == []


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
