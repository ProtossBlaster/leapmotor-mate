"""ABRP's Telemetry API has a spec, and the frame Mate sends should follow it, not merely resemble it.

The one point of the integration is live consumption calibration and a plan that knows what the
car is doing right now. That needs three fields to be exact: `power`, with the sign the planner
expects; `utc`, the time of the DATA rather than of the send; and the charging flags.
Everything else on the frame is optional and welcome — the pack size, the tyre pressures, the
heater's draw — and Mate already knows most of it, so it may as well say so.

Spec: https://documenter.getpostman.com/view/7396339/SWTK5a8w — "power [kW]: Instantaneous power
output/input to the vehicle. Power output is positive, power input is negative (charging)";
"utc [s]: UTC timestamp of the data (epoch) in seconds"; "is_dcfc: If is_charging, indicate if
this is DC fast charging"; tyre pressures in kPa, hvac_power in kW.
"""
import abrp
import client          # poller/client.py
import pytest


def _sig(**kw):
    base = {"1010": 0, "1319": 0}   # parked, stationary — unrelated gates stay quiet
    base.update(kw)
    return base


def _tlm(**sig):
    return abrp._build_tlm(client._parse_signal("VIN", _sig(**sig)))


# ── power: output positive, input negative ───────────────────────────────────
# Signal 1178 (pack current) already carries that sign on the car — a B10 read +49.9 A on the
# motorway, −20 A under regen braking and −4.2 A on a wallbox. Mate's own `charge_power_kw` is a
# magnitude by design (the recorder decides charge vs regen from the current), so ABRP was told
# "+1.5 kW" while the car was charging and read it as consumption.

def test_a_charging_car_reports_negative_power_to_abrp():
    tlm = _tlm(**{"1178": -4.2, "1177": 360.0})
    assert tlm["power"] == pytest.approx(-1.512)


def test_regen_braking_reports_negative_power_to_abrp():
    tlm = _tlm(**{"1178": -20.0, "1177": 355.0})
    assert tlm["power"] < 0


def test_a_driving_car_reports_positive_power_to_abrp():
    tlm = _tlm(**{"1178": 49.9, "1177": 350.0})
    assert tlm["power"] == pytest.approx(17.465)


def test_a_resting_car_reports_zero_power_not_silence():
    """Consumption calibration wants a power on every point; an idle pack is 0 kW, not unknown."""
    tlm = _tlm(**{"1178": 0.0, "1177": 380.0})
    assert tlm["power"] == 0


def test_without_a_pack_voltage_there_is_no_power_to_report():
    tlm = _tlm(**{"1178": 0.0})
    assert "power" not in tlm
