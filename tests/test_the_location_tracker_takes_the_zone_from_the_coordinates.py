"""The Location tracker's state is the zone of the car's coordinates (#453).

Its value_template answered `home` whenever the payload carried a latitude, and Home Assistant shows
a state sent on the state topic instead of the zone of the coordinates: a car 200 km away read
`home`. Measured on Home Assistant 2026.9.4 with this config: the template now answers `None`, the
tracker's default reset payload, which clears the sent state so Home Assistant takes the zone from
the `latitude`/`longitude` attributes (`home`, `not_home`, or a named zone such as `Lavoro`); with no
GPS it still reads `not_home`. An install that had the old config follows on the next location
message, with no restart. Dropping the state topic instead left an existing entity showing the raw
JSON payload as its state until Home Assistant restarted.
"""
import json
import types

import pytest

pytest.importorskip("paho.mqtt.client", reason="poller MQTT bridge needs paho (absent in minimal CI)")
from jinja2 import Environment
import mqtt as M
from cloud_access_fixture import settings

# Home Assistant's MQTT device_tracker clears a state sent on its state topic on this payload.
HA_DEFAULT_PAYLOAD_RESET = "None"


class _FakeClient:
    def __init__(self):
        self.published = {}

    def publish(self, topic, payload, retain=False):
        self.published[topic] = payload


def _tracker_config():
    svc = M.MqttService("broker", 1883, get_setting=settings("VINTEST"))
    svc.client = _FakeClient()
    svc.publish_discovery(types.SimpleNamespace(vin="VINTEST"))
    return json.loads(svc.client.published["homeassistant/device_tracker/leapmotor_mate_vintest/location/config"])


def _state(conf, payload):
    """The state the tracker's value_template gives for one location payload, as Home Assistant renders it."""
    return Environment().from_string(conf["value_template"]).render(value_json=json.loads(payload)).strip()


def test_with_coordinates_the_sent_state_is_cleared_so_the_zone_comes_from_them():
    conf = _tracker_config()
    assert "payload_reset" not in conf or conf["payload_reset"] == HA_DEFAULT_PAYLOAD_RESET
    for lat, lon in ((43.0, 11.0), (44.5, 9.0)):
        assert _state(conf, json.dumps({"latitude": lat, "longitude": lon})) == HA_DEFAULT_PAYLOAD_RESET


def test_with_no_gps_it_reads_not_home():
    conf = _tracker_config()
    assert _state(conf, json.dumps({"latitude": None, "longitude": None})) == "not_home"


def test_the_coordinates_stay_its_attributes_on_the_same_topic():
    conf = _tracker_config()
    assert conf["state_topic"] == conf["json_attributes_topic"] == "leapmotor/VINTEST/location"
    assert conf["source_type"] == "gps"
