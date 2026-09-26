"""A pack current the car never sent is ABSENT, not 0 A — the #144 rule, applied to signal 1178.

The parser read the current and the voltage as `float(sig.get(id) or 0)`, so a frame without them
said "0 A at 0 V". Nothing downstream could tell that from a car resting with the pack idle — and
anything that wants to send or store a measurement has to know whether there was one. The readers
that only gate on the current (the regen gate, the V2L accumulator) take "unknown" as no current.
"""
import types

import client
import pytest


def _sig(**kw):
    base = {"1010": 0, "1319": 0}
    base.update(kw)
    return base


# ── the parser keeps "unknown" ───────────────────────────────────────────────

@pytest.mark.parametrize("sid,field", [("1178", "charge_current_a"), ("1177", "charge_voltage_v")])
def test_a_reading_the_car_never_sent_is_none(sid, field):
    assert getattr(client._parse_signal("VIN", _sig()), field) is None


@pytest.mark.parametrize("sid,field", [("1178", "charge_current_a"), ("1177", "charge_voltage_v")])
def test_an_unreadable_reading_is_none_too(sid, field):
    assert getattr(client._parse_signal("VIN", _sig(**{sid: ""})), field) is None


def test_a_measured_zero_is_zero():
    vd = client._parse_signal("VIN", _sig(**{"1178": 0, "1177": 380.0}))
    assert vd.charge_current_a == 0.0 and vd.charge_voltage_v == 380.0


# ── the other readers of the current survive "unknown" ───────────────────────

def test_the_mqtt_v2l_accumulator_takes_an_unknown_current_as_no_load():
    pytest.importorskip("paho.mqtt.client", reason="poller MQTT bridge needs paho")
    import mqtt as M
    svc = M.MqttService("broker", 1883, topic_prefix="t", get_setting=lambda k, d="": d)
    frame = types.SimpleNamespace(ac_port_mode=2, charge_current_a=None, charge_voltage_v=None,
                                  vin="VINTEST")
    active, power_w, energy_wh = svc._v2l_live(frame)
    assert active is True and power_w == 0 and energy_wh == 0.0
    frame.ac_port_mode = 0
    assert svc._v2l_live(frame) == (False, 0, 0.0)


class _SpyDB:
    """What a mid-trip recorder touches; nothing is stored."""
    def save_position(self, vid, data): pass
    def add_trip_position(self, trip_id, data): pass
    def get_open_charge(self, vid): return None
    def trip_end_from_last_seen(self, trip_id): return None
    def finalize_trip(self, trip_id, data, regen_kwh=0.0, end_at_override=None): return 17.0


def test_the_regen_gate_takes_an_unknown_current_as_no_regen():
    """`current < -3.0` with None raised TypeError; a frame without a current is simply no regen."""
    import recorder as R
    from client import VehicleData
    from state_machine import State
    rec = R.Recorder(_SpyDB(), vehicle_id=1)
    rec._started = True
    rec._sm.state = State.DRIVING
    rec._active_trip_id = 7
    rec._last_soc, rec._last_odometer = 80.0, 1000.0
    frame = VehicleData(
        vin="TESTVIN", timestamp_ms=1_000, soc=80.0, range_km=300, odometer_km=1000.0,
        speed_kmh=50.0, gear="D", vehicle_state="driving", charging_status=0, charge_power_kw=0.0,
        latitude=51.8, longitude=5.8, outside_temp=None, inside_temp=20.0, climate_target_temp=21.0,
        battery_min_temp=15.0, is_locked=False, climate_on=False, climate_cooling=False,
        climate_heating=False, climate_defrost=False, trunk_open=False, windows_open=False,
        sunshade_open=False, any_door_open=False, plug_connected=False, remaining_charge_min=0,
        charge_voltage_v=None, charge_current_a=None)
    rec.process(frame)
    assert rec._regen_kwh == 0.0
