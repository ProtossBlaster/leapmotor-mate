"""A low-pressure alarm must light the wheel it belongs to.

The Vehicle page paired each wheel's alarm flag with its pressure by assumption — "each pressure's
paired state signal moves with it" — after the pressures were re-mapped from a check on two B10s
(#32). That check saw pressures only; nobody had an alarm on at the time. leapmotor-api,
leapmotor-ha and ioBroker all pair the alarms as 2641=FL, 2648=FR, 2655=RL, 2662=RR; the page read
them as FL=2655, FR=2648, RL=2662, RR=2641, so only the right front lit the right wheel.

Still unverified on a car with a real low tyre; the numbers are made up.
"""
import main


def _tyres(**sig):
    base = {"2646": 250, "2653": 250, "2660": 250, "2667": 250}
    base.update(sig)
    return main._parse_vehicle_status(base)["tyres"]


def test_each_alarm_lights_its_own_wheel():
    for sid, wheel in (("2641", "fl"), ("2648", "fr"), ("2655", "rl"), ("2662", "rr")):
        t = _tyres(**{sid: 1})
        assert [w for w in ("fl", "fr", "rl", "rr") if t[w]["low"]] == [wheel], sid


def test_no_alarm_no_light():
    t = _tyres()
    assert not any(t[w]["low"] for w in ("fl", "fr", "rl", "rr"))
