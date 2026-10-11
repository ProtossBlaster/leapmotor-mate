"""A charge session must OPEN only on real charging current — never on the cable alone.

THE BUG (riri19, 2026-07): the state machine opened a CHARGING session on
`is_charging = charging_status > 0 OR plug_connected`, so merely inserting the cable
(signal 1149==1 "connected", no current) started counting a charge. With a scheduled
night charge the cable stays plugged for hours before any current flows, so Mate began
tracking a "charge" at plug-in time instead of when the car actually started charging.

THE FIX: open only on `charge_active = charging_status > 0` (1149==2 "charging", or a
measured current ≥ 2 A). The cable (`plug_connected`) still KEEPS the session open across
brief current dips and CLOSES it on unplug — so one physical charge never fragments — and
still ends the trip immediately on plug-in. But it no longer OPENS a charge on its own.

CI-safe: pure state-machine / recorder logic, no fastapi.
"""
from client import _parse_signal
from state_machine import State, StateMachine, _PARKED_STATES


# ── signal builders (parked, stationary) ────────────────────────────────────────
def _parked(soc=55):
    return {"1010": 0, "1319": 0, "100003": soc}

def _cable_only(soc=55):
    # Cable connected, waiting for a scheduled charge: 1149==1 "connected", NO current.
    return {"1010": 0, "1319": 0, "100003": soc, "1149": 1}

def _charging(soc=55):
    # Real charge: 1149==2 "charging" + 16 A current.
    return {"1010": 0, "1319": 0, "100003": soc, "1149": 2, "1178": 16, "1177": 230, "1200": 120}

def _dip(soc=56):
    # Cable still in (1149==2) but current momentarily below the 2 A floor → charging_status 0,
    # plug_connected still True. Must NOT close the session.
    return {"1010": 0, "1319": 0, "100003": soc, "1149": 2, "1178": 0.5, "1177": 230}

def _unplugged(soc=90):
    return {"1010": 0, "1319": 0, "100003": soc, "1149": 0}


# ── the plug-only signal really is plug-without-current (sanity) ─────────────────
def test_cable_only_is_plugged_but_not_charging():
    d = _parse_signal("V", _cable_only())
    assert d.plug_connected is True      # cable detected
    assert d.charging_status == 0        # but no charging current


# ── state machine: cable alone never enters CHARGING ────────────────────────────
def test_first_poll_cable_only_does_not_open():
    sm = StateMachine()
    sm.update(_parse_signal("V", _cable_only()))
    assert sm.state != State.CHARGING
    assert sm.state in _PARKED_STATES


def test_parked_then_cable_only_stays_parked():
    sm = StateMachine()
    sm.update(_parse_signal("V", _parked()))
    assert sm.state in _PARKED_STATES
    sm.update(_parse_signal("V", _cable_only()))
    sm.update(_parse_signal("V", _cable_only()))
    assert sm.state != State.CHARGING


def test_current_opens_the_charge():
    sm = StateMachine()
    sm.update(_parse_signal("V", _parked()))
    sm.update(_parse_signal("V", _charging()))
    assert sm.state == State.CHARGING


def test_scheduled_charge_opens_only_when_current_flows():
    """The exact scenario: plug in the evening (no current), the car waits for the programmed
    time, THEN current flows. The charge must open at the current, not at the plug-in."""
    sm = StateMachine()
    sm.update(_parse_signal("V", _parked()))
    sm.update(_parse_signal("V", _cable_only()))      # 19:00 plug in
    sm.update(_parse_signal("V", _cable_only()))      # ...waiting for 23:35...
    assert sm.state != State.CHARGING                 # nothing counted yet
    sm.update(_parse_signal("V", _charging()))        # 23:35 current flows
    assert sm.state == State.CHARGING


# ── the cable still keeps the session open (no fragmentation) + closes on unplug ─
def test_current_dip_does_not_close_the_session():
    sm = StateMachine()
    sm.update(_parse_signal("V", _parked()))
    sm.update(_parse_signal("V", _charging()))
    assert sm.state == State.CHARGING
    sm.update(_parse_signal("V", _dip()))             # brief dip, cable in
    assert sm.state == State.CHARGING                 # NOT closed → no fragmenting
    sm.update(_parse_signal("V", _unplugged()))       # cable pulled
    assert sm.state != State.CHARGING


# ── DRIVING: plug ends the trip immediately, but opens no charge until current ───
def test_plug_after_drive_ends_trip_without_opening_charge():
    sm = StateMachine()
    sm.update(_parse_signal("V", _parked()))
    sm.update(_parse_signal("V", {"1010": 3, "1319": 30, "100003": 55}))   # driving
    assert sm.state == State.DRIVING
    sm.update(_parse_signal("V", _cable_only()))      # parked + plugged, no current
    assert sm.state == State.PARKED_ACTIVE            # trip ended, NOT charging
    sm.update(_parse_signal("V", _charging()))        # current → now it charges
    assert sm.state == State.CHARGING


# ── recorder: the DB charge ROW is not created on cable-only, only on current ────
def test_recorder_opens_charge_row_only_on_current(tmp_path):
    import db as D
    import recorder as R

    db = D.Database(str(tmp_path / "t.db"))
    db._conn.execute("INSERT INTO vehicles (id, vin) VALUES (1, 'V')")
    db._conn.commit()
    rec = R.Recorder(db, vehicle_id=1)
    rec._read_wallbox = lambda: {}                    # hermetic (no live HA wallbox)

    rec.process(_parse_signal("V", _parked()))
    rec.process(_parse_signal("V", _cable_only()))    # plugged, waiting — must NOT open a row
    rec.process(_parse_signal("V", _cable_only()))
    assert rec._active_charge_id is None
    assert db.get_open_charge(1) is None

    rec.process(_parse_signal("V", _charging()))      # current flows → row opens
    assert rec._active_charge_id is not None
    assert db.get_open_charge(1) is not None
