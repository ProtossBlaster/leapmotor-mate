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


# ── utc: the time of the data, not of the send ───────────────────────────────
# A sleeping car answers every poll with the same frame for hours; the frame carries its own
# timestamp (`sts`) and Mate prints its age on every poll line. ABRP was given the send time, so
# a reading from Tuesday night arrived on Wednesday morning dated Wednesday morning.

def test_abrp_gets_the_frames_own_timestamp():
    tlm = _tlm(sts=1790423669401)
    assert tlm["utc"] == 1790423669


def test_a_frame_without_a_timestamp_is_dated_now():
    import time
    tlm = _tlm()
    assert abs(tlm["utc"] - time.time()) < 5


# ── one frame, one point ─────────────────────────────────────────────────────
# A sleeping car repeats one frame for hours, and Mate polled it every 30 s: the same point went
# to ABRP over 2 000 times in a row, each arrival counted as fresh contact, so the car sat
# "connected" in ABRP all night. A frame is sent once; the next one, when the car has said
# something new.

def test_the_same_frame_is_not_a_new_point():
    vd = client._parse_signal("VIN", _sig(sts=1790423669401))
    assert abrp.is_new_point("tok", vd, last_sent=abrp.NOTHING_SENT)
    assert not abrp.is_new_point("tok", vd, last_sent=("tok", 1790423669401))


def test_a_newer_frame_is():
    vd = client._parse_signal("VIN", _sig(sts=1790423699401))
    assert abrp.is_new_point("tok", vd, last_sent=("tok", 1790423669401))


def test_a_frame_without_a_timestamp_cannot_be_told_apart_so_it_always_goes():
    vd = client._parse_signal("VIN", _sig())
    assert abrp.is_new_point("tok", vd, last_sent=abrp.NOTHING_SENT)
    assert abrp.is_new_point("tok", vd, last_sent=("tok", 1790423669401))


def test_a_token_that_has_not_had_the_frame_gets_it():
    """The token is part of "sent": after a change in Settings the new ABRP vehicle has seen
    nothing, and a sleeping car would otherwise leave it empty for hours."""
    vd = client._parse_signal("VIN", _sig(sts=1790423669401))
    assert abrp.is_new_point("B", vd, last_sent=("A", 1790423669401))


# A frame counts as sent only once ABRP has taken it; a failed send leaves it for the next poll.

class _Resp:
    def __init__(self, body): self._b = body
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_a_point_abrp_accepted_is_reported_as_sent(monkeypatch):
    monkeypatch.setattr(abrp.urllib.request, "urlopen", lambda *a, **k: _Resp(b'{"status":"ok"}'))
    assert abrp.send("tok", client._parse_signal("VIN", _sig())) is True


def test_a_point_abrp_refused_is_not(monkeypatch):
    monkeypatch.setattr(abrp.urllib.request, "urlopen",
                        lambda *a, **k: _Resp(b'{"status":"error","errors":["bad token"]}'))
    assert abrp.send("tok", client._parse_signal("VIN", _sig())) is False


def test_a_point_that_never_reached_abrp_is_not(monkeypatch):
    def _down(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr(abrp.urllib.request, "urlopen", _down)
    assert abrp.send("tok", client._parse_signal("VIN", _sig())) is False
