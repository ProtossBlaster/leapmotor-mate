"""A frame that says nothing about a state neither opens nor closes it.

READY is stored as NULL when the car did not send it; such a row is skipped for that kind, and the
first row that does say something is the baseline, not a change.
"""
import events as E
from events_fixture import Car


def test_a_null_ready_is_skipped_and_the_first_reading_is_the_baseline(tmp_path):
    car = Car(tmp_path)
    for _ in range(3):
        car.frame(**{"1258": None})
    car.frame(**{"1258": 1})
    car.frame(**{"1258": 1})
    car.derive()
    assert car.events() == [], "the first frame that reports READY is where counting starts"
    off = car.frame(**{"1258": 0})
    car.frame(**{"1258": 0})
    car.derive()
    assert car.events() == [("ready", 0, off["recorded_at"])]


def test_the_rules_say_nothing_about_a_null_column():
    row = {"is_locked": None, "plug_connected": None, "ac_port_mode": None,
           "gear": "P", "speed_kmh": 0, "climate_on": None, "ready": None, "trunk_open": None}
    for kind in ("unlocked", "cable", "v2l", "climate", "ready", "trunk"):
        assert E.RULES[kind](row) is None, kind
