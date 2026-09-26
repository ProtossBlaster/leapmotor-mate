"""A signal the cloud sends as an empty string is unknown, not a poll that fails.

Six signals were read with a bare `int()` or `float()` behind an `is not None` guard: the cabin
heater power (1348), the climate mode (3713) and the four range-extender fields (3235 fuel %,
3263 fuel mL, 3259 fuel range, 3261 combined range). The guard lets "" through, and the cloud has
sent "" for a signal before; `int("")` then raised out of the parser and the whole poll of that
car failed, on a BEV as much as on a REEV. Every other signal already goes through `_si` / `_sf`,
which turn an unreadable value into None.
"""
import client
import pytest


def _sig(**kw):
    base = {"1010": 0, "1319": 0}
    base.update(kw)
    return base


@pytest.mark.parametrize("sid,field", [("1348", "climate_power"), ("3713", "climate_mode"),
                                       ("3235", "fuel_level_pct"), ("3263", "fuel_liters"),
                                       ("3259", "fuel_range_km"), ("3261", "combined_range_km")])
@pytest.mark.parametrize("value", ["", "n/a"])
def test_an_unreadable_value_parses_as_unknown(sid, field, value):
    vd = client._parse_signal("VIN", _sig(**{sid: value}))
    assert getattr(vd, field) is None


@pytest.mark.parametrize("sid,field,raw,parsed", [("1348", "climate_power", "1350", 1350),
                                                  ("3713", "climate_mode", "3", 3),
                                                  ("3235", "fuel_level_pct", "42.5", 42.5),
                                                  ("3263", "fuel_liters", "34416", 34.416),
                                                  ("3259", "fuel_range_km", "310", 310.0),
                                                  ("3261", "combined_range_km", "512", 512.0)])
def test_a_readable_value_still_parses(sid, field, raw, parsed):
    assert getattr(client._parse_signal("VIN", _sig(**{sid: raw})), field) == pytest.approx(parsed)


def test_an_unreadable_fuel_level_keeps_the_car_a_range_extender():
    """The FIELD marks the model — a BEV never sends 3235 at all — and the poll loop writes the flag
    per car on every poll. A range-extender whose reading is unreadable for one frame must not be
    re-filed as a plain electric car, losing its pages and its SoC-rise charge detection."""
    good, blank, again = (client._parse_signal("VIN", _sig(**{"3235": v})) for v in ("42", "", "41"))
    assert (good.is_reev, blank.is_reev, again.is_reev) == (True, True, True)
    assert (good.fuel_level_pct, blank.fuel_level_pct, again.fuel_level_pct) == (42.0, None, 41.0)


def test_a_car_without_the_fuel_field_is_not_a_range_extender():
    assert client._parse_signal("VIN", _sig()).is_reev is False
