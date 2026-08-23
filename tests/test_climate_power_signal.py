"""Signal 1348 is the climate PTC power in watts — read it and publish it (unmapped until now).

Measured across the beta channel and confirmed in memory: 1348 carries the cabin PTC heater/AC
power, in whole watts at 50 W steps (48 distinct values, 0..2700). It is NOT the range-extender
generator (disproved by @michapr) and NOT traction. The international app maps it; Mate did not.
So a home charger cannot see how much the car is spending on climate — this exposes it.

Absent → None, never 0: a car that does not send 1348 (or a frame before it arrives) must not read
as "climate drawing 0 W". → [[signal-absent-is-not-signal-zero]]
"""
import client


def _sig(**kw):
    base = {"1010": 0, "1319": 0}
    base.update(kw)
    return base


def test_climate_power_is_read_in_watts():
    assert client._parse_signal("V", _sig(**{"1348": 1500})).climate_power == 1500
    assert client._parse_signal("V", _sig(**{"1348": 0})).climate_power == 0


def test_absent_climate_power_is_none_not_zero():
    assert client._parse_signal("V", _sig()).climate_power is None
