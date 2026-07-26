"""The automatic 🧭 note can be switched off; the button never can.

The feature ships on, because a note that has to be asked for every time is barely a feature.
But it is the one thing in Mate that reaches out on its own initiative, and what it sends is a
trip's two endpoints — which for most people are home and work. That deserves a switch rather
than a paragraph in a changelog.

Switching it off stops only the AUTOMATIC path in the poller. The 🧭 button on every trip and
charge keeps working exactly as before: the note is still one tap away, it just stops happening
by itself.

The tests below drive the real Recorder and stub only the note body (network + threading),
matching test_auto_note_recorder.py.
"""
import pathlib

import db as D
import recorder as R
from client import VehicleData
from state_machine import State, StateEvent


def _vd(soc=50.0, odometer_km=1000.0, plug=False, charging=0):
    return VehicleData(
        vin="TESTVIN", timestamp_ms=0, soc=soc, range_km=300, odometer_km=odometer_km,
        speed_kmh=0.0, gear="P", vehicle_state="parked",
        charging_status=charging, charge_power_kw=0.0, latitude=45.0, longitude=9.0,
        outside_temp=None, inside_temp=20.0, climate_target_temp=21.0, battery_min_temp=15.0,
        is_locked=True, climate_on=False, climate_cooling=False, climate_heating=False,
        climate_defrost=False, trunk_open=False, windows_open=False, sunshade_open=False,
        any_door_open=False, plug_connected=plug, remaining_charge_min=0,
        charge_voltage_v=0.0, charge_current_a=0.0,
    )


def _rec(db):
    rec = R.Recorder(db, vehicle_id=1)
    rec._read_wallbox_energy = lambda: None
    return rec


def _drive_and_park(rec, spawned):
    """A real 10 km trip, recording whether the note thread would have been spawned."""
    rec._auto_note_trip_body = lambda tid: spawned.append(tid)
    rec._handle_event(StateEvent(State.PARKED_ACTIVE, State.DRIVING, _vd()), _vd())
    end = _vd(odometer_km=1010.0)
    rec._handle_event(StateEvent(State.DRIVING, State.PARKED_ACTIVE, end), end)


def test_on_by_default(tmp_path, monkeypatch):
    """No setting written at all — a fresh install must behave as the PR intends."""
    db = D.Database(str(tmp_path / "a.db"))
    rec = _rec(db)
    monkeypatch.setattr(R.threading, "Thread", _immediate)
    spawned = []
    _drive_and_park(rec, spawned)
    assert spawned, "the automatic note should run when nothing has been configured"


def test_switched_off_the_poller_does_not_reach_out(tmp_path, monkeypatch):
    db = D.Database(str(tmp_path / "b.db"))
    db.set_setting("auto_note", "0")
    rec = _rec(db)
    monkeypatch.setattr(R.threading, "Thread", _immediate)
    spawned = []
    _drive_and_park(rec, spawned)
    assert not spawned, "switched off, nothing may be looked up on its own"


def test_switched_back_on(tmp_path, monkeypatch):
    db = D.Database(str(tmp_path / "c.db"))
    db.set_setting("auto_note", "0")
    db.set_setting("auto_note", "1")
    rec = _rec(db)
    monkeypatch.setattr(R.threading, "Thread", _immediate)
    spawned = []
    _drive_and_park(rec, spawned)
    assert spawned


def test_a_charge_respects_it_too(tmp_path, monkeypatch):
    db = D.Database(str(tmp_path / "d.db"))
    db.set_setting("auto_note", "0")
    rec = _rec(db)
    monkeypatch.setattr(R.threading, "Thread", _immediate)
    spawned = []
    rec._auto_note_charge_body = lambda cid: spawned.append(cid)
    start = _vd(plug=True, charging=1)
    rec._handle_event(StateEvent(State.PARKED_ACTIVE, State.CHARGING, start), start)
    end = _vd(soc=80.0, plug=True)
    rec._handle_event(StateEvent(State.CHARGING, State.PARKED_ACTIVE, end), end)
    assert not spawned


def test_an_unreadable_setting_leaves_the_feature_on(tmp_path):
    """A settings read that blows up must not silently disable a working feature — and must
    certainly not take recording down with it."""
    db = D.Database(str(tmp_path / "e.db"))
    rec = _rec(db)

    def boom(*a, **k):
        raise RuntimeError("settings table is having a bad day")

    rec._db.get_setting = boom
    assert rec._auto_note_on() is True


def test_the_switch_is_saved_by_its_marker_not_by_the_box():
    """An unticked checkbox submits NOTHING. Read the box alone and every other form that
    happens to post to this route would read as 'turn it off' — so the decision hangs on a
    hidden marker that says 'this form actually showed the switch'."""
    src = (pathlib.Path(__file__).resolve().parent.parent / "web" / "main.py").read_text()
    block = src.split("async def save_geocoder")[1].split("@app.post")[0]
    assert 'if "auto_note_present" in form' in block
    assert 'set_setting("auto_note", "1" if form.get("auto_note") else "0")' in block


def test_the_settings_page_offers_it_and_ships_the_marker():
    html = (pathlib.Path(__file__).resolve().parent.parent
            / "web" / "templates" / "settings.html").read_text()
    assert 'name="auto_note_present"' in html, "without the marker the switch can never turn off"
    assert 'name="auto_note"' in html
    # Default ON: absent setting must still render the box ticked, or an upgrade would look
    # like the feature had been switched off for everybody.
    assert "settings.get('auto_note', '1') != '0'" in html


def test_the_button_is_not_gated_by_the_switch():
    """The switch governs what happens BY ITSELF, and nothing else. The gate therefore lives in
    the poller and must never appear on the web side, where the 🧭 button is served: put it in
    both and switching the automatic note off would quietly take the button with it."""
    root = pathlib.Path(__file__).resolve().parent.parent
    web = (root / "web" / "main.py").read_text()
    poller = (root / "poller" / "recorder.py").read_text()
    assert "_auto_note_on" in poller, "the gate is supposed to be in the poller"
    assert "_auto_note_on" not in web, "the manual button must not consult the automatic switch"
    # …and both manual routes are still there to be pressed.
    assert "/api/trips/{trip_id}/auto-note" in web
    assert "/api/charges/{charge_id}/auto-note" in web


class _immediate:
    """threading.Thread stand-in that runs the body inline, so a test can see the effect."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target, self._args, self._kwargs = target, args, kwargs or {}

    def start(self):
        self._target(*self._args, **self._kwargs)
