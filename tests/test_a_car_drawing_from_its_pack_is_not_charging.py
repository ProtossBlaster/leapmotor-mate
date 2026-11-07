"""A parked, plugged car drawing from its pack is not charging.

The pack current (1178) is negative while the pack charges and positive while it discharges, on
every model. `_is_charging` read only its size, so a plugged car running its climate from the pack
read as charging: Home Assistant's `charging` and `evcc/charging` went on, ABRP got `is_charging`,
the state machine opened a charge, and the web's own copy of the rule flagged the frame it stores
after a command. A charge in progress keeps its session across such a reading, as it does across a
dip below the detection floor, because the cable is still in; the Overview does not draw it as a
charge, and the charge ends where the pack stopped taking current.
"""
from datetime import datetime, timezone

import pytest

import db_reader
from client import _parse_signal
from state_machine import State, StateMachine, _PARKED_STATES
from test_a_charge_in_progress_survives_a_dip_below_the_detection_floor import _poll, bernd  # noqa: F401  (bernd: fixture)
from test_a_charge_records_why_it_closed import _ms, car  # noqa: F401  (car: fixture)
from test_plug_detection import _latest_charging, _web_db


def _frame(amps, soc=80.0, **extra):
    return {"1010": 0, "1319": 0, "100003": soc, "1149": 1, "1177": 700, "1178": amps, **extra}


@pytest.mark.parametrize("sig", [_frame(3.4), _frame(3.4, **{"1200": 0})],
                         ids=["without a charge time", "with a charge time of 0"])
def test_a_current_out_of_the_pack_is_not_a_charge(sig):
    d = _parse_signal("V", sig)
    assert d.plug_connected is True
    assert d.charging_status == 0


def test_a_current_into_the_pack_is_a_charge():
    assert _parse_signal("V", _frame(-10.0, **{"1200": 90})).charging_status == 1


def test_a_parked_car_drawing_from_its_pack_opens_no_charge():
    sm = StateMachine()
    sm.update(_parse_signal("V", _frame(0.1)))
    sm.update(_parse_signal("V", _frame(3.4)))
    assert sm.state in _PARKED_STATES


def test_a_charge_keeps_its_session_across_a_reading_out_of_the_pack():
    sm = StateMachine()
    sm.update(_parse_signal("V", _frame(-10.0, soc=60.0, **{"1200": 90})))
    assert sm.state == State.CHARGING
    sm.update(_parse_signal("V", _frame(3.4, soc=60.1)))
    assert sm.state == State.CHARGING


def test_the_overview_draws_no_charge_while_the_pack_supplies_a_load(bernd):
    """An open charge stays on the Overview across a dip (#307), not across current out of the pack."""
    for amps, flag in ((-10.0, 1), (3.4, 0), (-10.0, 1)):
        _poll(bernd, amps=amps, charging=flag, soc=80.0)
        assert bool(db_reader.get_latest_status()["charging"]) is bool(flag)


def test_a_charge_ends_where_the_pack_stopped_taking_current(car):
    db, rec, clock, _ = car
    for minute, amps, soc, cable in ((0, -10.0, 80.0, 1), (1, -10.0, 80.5, 1), (2, 3.4, 80.5, 1),
                                     (3, 3.4, 80.4, 1), (4, 3.4, 80.4, 0)):
        at = f"2026-09-15T12:0{minute}:00+00:00"
        extra = {"1200": 90} if amps < 0 else {}
        clock.poll(rec, _parse_signal("TESTVIN", {**_frame(amps, soc, **extra), "1149": cable, "1": _ms(at)}))
    row = db._conn.execute("SELECT ended_at, end_soc FROM charges").fetchone()
    assert (datetime.fromisoformat(row["ended_at"]).astimezone(timezone.utc).strftime("%H:%M"),
            row["end_soc"]) == ("12:02", 80.5)


def test_the_web_stores_it_as_not_charging(tmp_path, monkeypatch):
    path = _web_db(tmp_path, monkeypatch)
    db_reader.save_fresh_signals(_frame(3.4))
    assert _latest_charging(path) == 0
    db_reader.save_fresh_signals(_frame(-10.0, **{"1200": 90}))
    assert _latest_charging(path) == 1
