"""A real 1.15 kW charge printed 0.00 kW (#307, @arzthilfe).

His C10 on a wallbox turned down from 11 A to 8 A, near 87 % SoC where the charge tapers. The
signals in his bundle at that moment:

    1177 (pack voltage)   718.2 V
    1178 (pack current)    -1.599 A      →  718.2 × 1.599 / 1000 = 1.148 kW

The overview showed **0.00 kW**. `_charge_power_kw` refused to compute below
`_CHARGE_CURRENT_MIN_A`, and that constant is the user's *charge-detection* floor
(`charge_detect_min_a`, 2.0 A by default — the setting whose help text talks about the ~11 A of a
home AC charge). One threshold doing two jobs: deciding WHETHER the car is charging, where a floor
belongs, and measuring HOW MUCH power flows, where it silently prints a zero over a real figure.

His last polls walk straight through it: -3.3, -3.4, -2.0, -2.3, -1.6 A. The reading vanishes
exactly when the charge slows down.

The floor stays where it decides (`_is_charging`), and the magnitude is now simply the magnitude —
which is what the function's own docstring always said it was. Nothing downstream loses a guard:
regen has its own (`charge_current_a < -3.0`, recorder), the stuck-counter sum has
`_WB_STUCK_MIN_KW`, and `max_power_kw` is a maximum, so a small reading can only lose to a bigger one.
"""
import client
import pytest


def _sig(current, voltage):
    return {"1178": current, "1177": voltage}


def test_the_low_current_charge_reports_the_power_it_carries():
    """718.2 V × 1.599 A = 1.148 kW. It was printing 0.00."""
    assert client._charge_power_kw(_sig(-1.599, 718.2)) == pytest.approx(1.148, abs=0.001)


def test_a_current_under_the_detection_floor_is_still_a_measurement():
    """The floor is 2.0 A by default; every one of these is a real flow, not noise."""
    for amps in (-1.6, -1.0, -0.5, 1.0, 1.9):
        kw = client._charge_power_kw(_sig(amps, 718.2))
        assert kw > 0, f"{amps} A at 718.2 V printed {kw} kW"


def test_a_full_speed_charge_is_unchanged(monkeypatch):
    """No regression where the figure was already right: 11 kW stays 11 kW."""
    assert client._charge_power_kw(_sig(-15.3, 718.2)) == pytest.approx(10.988, abs=0.001)


def test_the_magnitude_ignores_the_sign():
    """Charge and regen differ by sign; the recorder reads the sign, this reads the size."""
    assert (client._charge_power_kw(_sig(-4.0, 700.0))
            == client._charge_power_kw(_sig(4.0, 700.0)) == pytest.approx(2.8))


def test_a_missing_reading_is_not_a_zero():
    """A signal the car never sent cannot be multiplied → [[signal-absent-is-not-signal-zero]]."""
    assert client._charge_power_kw(_sig(None, 718.2)) == 0.0
    assert client._charge_power_kw(_sig(-1.599, None)) == 0.0
    assert client._charge_power_kw({}) == 0.0


def test_a_car_at_rest_still_reads_zero():
    """Measured across four installs: a parked, unplugged car reports 0.0 A far more often than
    anything else, and zero current is zero power without needing a floor to say so."""
    assert client._charge_power_kw(_sig(0.0, 718.2)) == 0.0


def test_the_detection_floor_still_decides_whether_it_is_charging(monkeypatch):
    """The separation is the whole fix: raising the floor must still change the DECISION and must
    no longer touch the MEASUREMENT."""
    client.set_charge_current_min(5.0)
    try:
        sig = {"1178": -3.0, "1177": 700.0, "1149": 2, "1010": 0}
        assert client._charge_power_kw(sig) == pytest.approx(2.1), "the power is measured anyway"
        assert client._is_charging(sig) is False, "3.0 A is under a 5.0 A detection floor"
    finally:
        client.set_charge_current_min(2.0)
