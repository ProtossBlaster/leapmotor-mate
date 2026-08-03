"""The car's READY state on MQTT — the moment an automation can actually act on (#220 @Torbynator).

Mate has read signal 1258 (ON3) since the Ready automation shipped, and stores it in positions.ready,
but never published it. The two states that WERE published are both wrong for automating on: `state`
only says "driving" once a gear is engaged or the car moves — by then the car refuses the remote
commands an automation would send — and a door opening happens early, and happens without a drive.

These tests hold the topic, the ON/OFF payloads, and the fact that it is announced as a binary_sensor
so Home Assistant gets an on/off entity rather than a string to parse."""
import json
import types

import pytest

pytest.importorskip("paho.mqtt.client", reason="poller MQTT bridge needs paho (absent in minimal CI)")
import mqtt as M
from client import VehicleData


class _FakeClient:
    def __init__(self):
        self.published = {}

    def publish(self, topic, payload, retain=False):
        self.published[topic] = payload


def _service(prefix="leapmotor"):
    svc = M.MqttService("broker", 1883, topic_prefix=prefix, get_setting=lambda k, d="": d)
    svc.client = _FakeClient()
    return svc


def _data(ready):
    """A real VehicleData, parked and switched off apart from `ready`. Built from the dataclass
    rather than from a hand-written list of fields, so a new signal added to the car cannot make
    these tests fail for a reason that has nothing to do with READY."""
    return VehicleData(
        vin="VINTEST", timestamp_ms=0, soc=50, range_km=200, odometer_km=1000.0, speed_kmh=0,
        gear="P", vehicle_state="parked", charging_status=0, charge_power_kw=0.0,
        latitude=45.0, longitude=9.0, outside_temp=20, inside_temp=22, climate_target_temp=22,
        battery_min_temp=20, is_locked=True, climate_on=False, climate_cooling=False,
        climate_heating=False, climate_defrost=False, trunk_open=False, windows_open=False,
        sunshade_open=False, any_door_open=False, plug_connected=False,
        remaining_charge_min=0, charge_voltage_v=0.0, charge_current_a=0.0,
        ready=ready,
    )


def test_a_powered_up_car_publishes_ready_on():
    svc = _service()
    svc._publish_sensors(_data(True))
    assert svc.client.published["leapmotor/VINTEST/ready"] == "ON"


def test_a_car_that_is_off_publishes_ready_off():
    """OFF, not an empty payload: an automation needs both edges, and a retained blank would make
    Home Assistant show `unknown` on every restart until the driver next switched the car on."""
    svc = _service()
    svc._publish_sensors(_data(False))
    assert svc.client.published["leapmotor/VINTEST/ready"] == "OFF"


def test_ready_is_not_the_same_thing_as_the_state_topic():
    """The whole reason this exists. A car sitting in Park with the ignition on is READY, while
    `state` still says parked — that gap is the window an automation wants."""
    svc = _service()
    svc._publish_sensors(_data(True))
    pub = svc.client.published
    assert pub["leapmotor/VINTEST/ready"] == "ON"
    assert pub["leapmotor/VINTEST/state"] == "parked"


def test_discovery_announces_ready_as_a_binary_sensor():
    svc = _service()
    svc.publish_discovery(types.SimpleNamespace(vin="VINTEST"))
    topic = "homeassistant/binary_sensor/leapmotor_mate_vintest/ready/config"
    assert topic in svc.client.published
    conf = json.loads(svc.client.published[topic])
    assert conf["state_topic"] == "leapmotor/VINTEST/ready"
    assert conf["device_class"] == "running"
    assert (conf["payload_on"], conf["payload_off"]) == ("ON", "OFF")


def test_discovery_follows_the_topic_prefix():
    """A second Mate on the same broker must not collide with the first — the prefix scopes the HA
    device id as well as the state topic, so both move together."""
    svc = _service(prefix="myprefix")
    svc.publish_discovery(types.SimpleNamespace(vin="VINTEST"))
    topic = "homeassistant/binary_sensor/myprefix_mate_vintest/ready/config"
    assert json.loads(svc.client.published[topic])["state_topic"] == "myprefix/VINTEST/ready"
