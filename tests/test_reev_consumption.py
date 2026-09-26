"""REEV Phase B — _parse_plugin_consumption pulls the current-period consumption out of a
getPlugInLastNweeks100kmEC body: fuel L/100km (`oc100km`) + electric kWh/100km (`ec100km`). The
per-week series carries only `ec100km` and must be ignored. Defensive against unknown nesting."""
import pytest


import command_client as cc


def test_parses_current_period_fuel_and_electric():
    raw = {"result": 0, "data": {
        "hundredKmEC": {"ec100km": 17.8, "oc100km": 6.2, "ecMiKwh": 3.5, "ocMpg": 37.9},
        "weeks": [{"ec100km": "18.1"}, {"ec100km": "16.9"}],   # per-week: electric only → ignored
    }}
    assert cc._parse_plugin_consumption(raw) == {
        "fuel_l_100km": 6.2, "elec_kwh_100km": 17.8, "fuel_mpg": 37.9}


def test_string_values_are_coerced():
    out = cc._parse_plugin_consumption({"data": {"x": {"oc100km": "6.5", "ec100km": "18.0"}}})
    assert out["fuel_l_100km"] == 6.5 and out["elec_kwh_100km"] == 18.0


def test_bev_reports_zero_fuel():
    out = cc._parse_plugin_consumption({"data": {"hundredKmEC": {"ec100km": 18.5, "oc100km": 0.0}}})
    assert out["fuel_l_100km"] == 0.0 and out["elec_kwh_100km"] == 18.5


def test_no_fuel_field_anywhere_returns_none():
    assert cc._parse_plugin_consumption({"data": {"weeks": [{"ec100km": "18.1"}]}}) is None


def test_garbage_input_returns_none():
    assert cc._parse_plugin_consumption(None) is None
    assert cc._parse_plugin_consumption("nope") is None
    assert cc._parse_plugin_consumption({}) is None
