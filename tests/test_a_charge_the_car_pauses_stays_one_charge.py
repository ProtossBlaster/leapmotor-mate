"""A charge the car pauses stays one charge.

The car's charge state (signal 1149) reads 6 while a charge is paused: the car stops drawing with the
cable still in, for half a minute or so, and goes back to 1 when it resumes. Mate read 6 as the cable
out, so each pause closed the charge and the resume opened another, and one plug-in was recorded as
several charges. Whatever the pack current does during the pause, the session holds until the cable
really comes out.
"""
import pytest

from client import _parse_signal
from test_a_charge_records_why_it_closed import _ms, car  # noqa: F401  (car: fixture)


def _frame(minute, code, amps, soc):
    return {"1010": 0, "1319": 0, "100003": soc, "1149": code, "47": 1, "1177": 430, "1178": amps,
            "1200": 790, "1": _ms(f"2026-10-31T13:{minute:02d}:00+00:00")}


@pytest.mark.parametrize("amps", [2.2, 0.7], ids=["the pack supplying a load", "a current under the floor"])
def test_a_pause_keeps_the_charge_open(car, amps):
    db, rec, clock, _ = car
    for frame in (_frame(0, 1, -4.0, 60.0), _frame(10, 1, -4.0, 61.0), _frame(11, 6, amps, 61.0),
                  _frame(12, 1, -4.0, 61.0), _frame(22, 1, -4.0, 62.0),
                  {**_frame(23, 0, 0.0, 62.0), "47": 0}):
        clock.poll(rec, _parse_signal("TESTVIN", frame))
    assert db._conn.execute("SELECT COUNT(*) FROM charges").fetchone()[0] == 1
