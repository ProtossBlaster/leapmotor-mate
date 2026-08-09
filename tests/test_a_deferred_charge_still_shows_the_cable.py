"""A charge deferred to its scheduled window must still show the cable — and must still close.

THE BUG (#243 @rop12770, C10, and reproduced on Silvio's own B10 the same evening): the
Overview drew the car with no charge cable while the official Leapmotor app, reading the
*same* cloud frame, drew it plugged into a wallbox.

MEASURED on that frame (2026-08-09, 19:11 Italian time, frozen and re-served for 3h38m):

    47   acInputSlowCharge           = 1        the AC input is there
    1149 chargeState                 = 4        <-- and four was never mapped
    1178 packCurrent                 = 0.1      not charging
    3737 chargeScheduleCancelledOnce = 1
    get_charge_schedule() → chargeEnable=1, starttime="01:50", endtime="12:00"

`_is_plugged_in` accepted 1/2/3 and rejected 0/5, so **4 fell into "unplugged" by
exclusion** and every consumer of `plug_connected` — the Overview image, the status colour,
the MQTT sensor — said the cable was out while it was physically in. The car reports 4 when
the cable is connected but charging is deferred to the programmed window; Silvio produced it
by enabling the schedule mid-charge, and the car stopped and switched 2 → 4.

THE FIX: 4 means the cable IS connected (`plug_connected` True) and it is explicitly NOT
charging (`charge_deferred` True). The second half matters as much as the first: entering
CHARGING is gated on `charge_active`, so 4 alone can never open a session — but *leaving*
CHARGING is gated on `charging_status > 0 or plug_connected`, so without the deferred flag
the session Silvio ended at 19:10 would have stayed open for the six hours until 01:50.

CI-safe: pure client / state-machine / recorder logic, no fastapi.
"""
import sqlite3

import client
import db as poller_db
import db_reader
import recorder as R
from state_machine import State, StateMachine, _PARKED_STATES


def _sig(**kw):
    """Signals defaulting to PARKED and stationary; override per case."""
    base = {"1010": 0, "1319": 0}
    base.update(kw)
    return base


# ── The frame Silvio's car was actually serving ─────────────────────────────────
def _frozen_frame():
    """The real 19:11 frame, trimmed to the signals these predicates read."""
    return {"1010": 0, "1319": 0.0, "100003": 59.4, "1204": 59,
            "47": 1, "1149": 4, "1178": 0.1, "1177": 422.6, "2188": 238}


# ── 1) the poller reads the cable ───────────────────────────────────────────────
def test_the_deferred_state_reads_as_plugged():
    """1149 == 4: cable connected, charge deferred to the scheduled window."""
    assert client._is_plugged_in(_sig(**{"1149": 4})) is True


def test_a_deferred_cable_at_rest_is_not_charging():
    """Waiting for the window: cable in, current at the noise floor → no charge."""
    assert client._is_charging(_sig(**{"1149": 4, "1178": 0.1, "1177": 422.6})) is False


def test_a_deferred_cable_that_starts_drawing_IS_charging():
    """The window opens and the current arrives while the code is still 4.

    Nobody has watched that moment — 4 has only ever been measured at rest — so the code must not
    assume the car relabels itself to 2. Treating 4 as "never charging" would drop the whole
    scheduled charge in silence, which is far worse than the blank cable this fixes. The current
    is the evidence; the cable code only says the cable is there."""
    assert client._is_charging(_sig(**{"1149": 4, "1178": 16, "1177": 230, "1200": 120})) is True


def test_the_deferred_state_is_still_motion_gated():
    """Same gate as every other connection code: you cannot be plugged in while moving."""
    assert client._is_plugged_in({"1010": 3, "1319": 0, "1149": 4}) is False
    assert client._is_plugged_in({"1010": 0, "1319": 40, "1149": 4}) is False


def test_the_frozen_frame_parses_to_cable_in_and_not_charging():
    d = client._parse_signal("VIN123", _frozen_frame())
    assert d.plug_connected is True
    assert d.charging_status == 0
    assert d.charge_deferred is True


def test_an_ordinary_connection_is_not_flagged_deferred():
    """Only 4 is deferred. 1/2/3 keep their existing meaning exactly."""
    for code in (1, 2, 3):
        d = client._parse_signal("VIN123", _sig(**{"1149": code, "100003": 55}))
        assert d.plug_connected is True, code
        assert d.charge_deferred is False, code


# ── 2) the session must still close when the schedule takes over ────────────────
def _charging_sig(soc=58.8):
    return {"1010": 0, "1319": 0, "100003": soc, "1149": 2,
            "1178": 16, "1177": 230, "1200": 120}


def _deferred_sig(soc=59.4):
    return {"1010": 0, "1319": 0, "100003": soc, "1149": 4, "1178": 0.1, "1177": 422.6}


def test_a_charge_closes_when_the_schedule_defers_it():
    """Silvio's 19:10: charging, he enables the schedule, the car stops and reports 4.
    The session must END. Before the fix 4 read as unplugged and it closed by accident;
    the danger is fixing the plug half and leaving the session open until 01:50."""
    sm = StateMachine()
    sm.update(client._parse_signal("V", _charging_sig()))
    assert sm.state == State.CHARGING
    sm.update(client._parse_signal("V", _deferred_sig()))
    assert sm.state != State.CHARGING


def test_a_deferred_cable_never_opens_a_charge():
    """Parked with the cable in, waiting for 01:50 — no session may open."""
    sm = StateMachine()
    sm.update(client._parse_signal("V", _sig(**{"100003": 59.4})))
    assert sm.state in _PARKED_STATES
    sm.update(client._parse_signal("V", _deferred_sig()))
    assert sm.state != State.CHARGING


def test_a_current_dip_still_keeps_the_session_open():
    """The guard must not cost us the dip tolerance: 1149 stays 2, current drops → open."""
    sm = StateMachine()
    sm.update(client._parse_signal("V", _charging_sig()))
    assert sm.state == State.CHARGING
    sm.update(client._parse_signal("V", {"1010": 0, "1319": 0, "100003": 59,
                                         "1149": 2, "1178": 0.5, "1177": 230}))
    assert sm.state == State.CHARGING


def test_a_restart_while_deferred_does_not_resume_the_charge():
    """Poller restart with the cable in and the charge deferred: a session left open by the
    previous run must be CLOSED, not resumed. There has to be a real open row for this to be
    able to fail — on an empty database `_resume_or_close` has nothing to resume and the test
    would pass whatever the predicate said."""
    db = poller_db.Database(":memory:")
    db._conn.execute("INSERT INTO vehicles (id, vin) VALUES (1, 'V')")
    db._conn.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, start_soc) "
        "VALUES (1, 1, '2026-08-09T17:05:03+00:00', 58.8)")
    db._conn.commit()
    assert db.get_open_charge(1) is not None          # the session really is open

    rec = R.Recorder(db, vehicle_id=1)
    rec._resume_or_close(client._parse_signal("V", _deferred_sig()))

    assert rec._sm.state != State.CHARGING
    assert rec._active_charge_id is None


# ── 3) the web copy must agree — two readers of 1149, one meaning ───────────────
def _web_db(tmp_path, monkeypatch):
    path = str(tmp_path / "web.db")
    poller_db.Database(path)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO vehicles (id, vin) VALUES (1, 'VIN123')")
    con.commit(); con.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return path


def _latest(path, column):
    con = sqlite3.connect(path); con.row_factory = sqlite3.Row
    row = con.execute(f"SELECT {column} FROM positions ORDER BY id DESC LIMIT 1").fetchone()
    con.close()
    return row[column]


def test_the_web_records_the_deferred_cable_as_connected(tmp_path, monkeypatch):
    path = _web_db(tmp_path, monkeypatch)
    db_reader.save_fresh_signals(_frozen_frame())
    assert _latest(path, "plug_connected") == 1


def test_the_web_does_not_record_a_deferred_cable_as_charging(tmp_path, monkeypatch):
    path = _web_db(tmp_path, monkeypatch)
    db_reader.save_fresh_signals(_frozen_frame())
    assert _latest(path, "charging") == 0


# ── 4) and the Overview draws it ────────────────────────────────────────────────
def test_the_overview_draws_the_cable_without_the_charging_animation(tmp_path, monkeypatch):
    """End to end, the way the Overview actually gets there: the frozen frame is stored by the
    web, read back as a status, and composed into the picture. The cable is drawn, the charging
    animation is not. Driven from the real frame rather than a hand-written dict — otherwise it
    pins `car_image` alone and stays green however the plug is read."""
    import car_image
    path = _web_db(tmp_path, monkeypatch)
    db_reader.save_fresh_signals(_frozen_frame())
    status = {"plug_connected": _latest(path, "plug_connected"),
              "charging": _latest(path, "charging")}
    st = car_image._status_obj(status)
    assert st.is_plugged is True
    assert st.is_charging is False
