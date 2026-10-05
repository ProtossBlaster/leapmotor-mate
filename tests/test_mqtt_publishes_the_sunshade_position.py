"""Home Assistant gets how far the sunshade is open, beside the binary "Sunshade" it already had.

The percent is signal 1724, the number on the car's own screen. The binary sensor stays as it was
(ON for any opening), because automations are built on it.
"""
import json
import types
from dataclasses import replace

import pytest

pytest.importorskip("paho.mqtt.client", reason="poller MQTT bridge needs paho")
from test_mqtt_ready import _data, _service


@pytest.mark.parametrize("pct, position", [(40, "40"), (0, "0"), (None, "")])
def test_the_position_is_published_as_the_car_sent_it(pct, position):
    svc = _service()
    svc._publish_sensors(replace(_data(False), sunshade_pct=pct))
    assert svc.client.published["leapmotor/VINTEST/sunshade_pct"] == position


def test_discovery_announces_the_position_as_a_percent():
    svc = _service()
    svc.publish_discovery(types.SimpleNamespace(vin="VINTEST"))
    conf = json.loads(svc.client.published["homeassistant/sensor/leapmotor_mate_vintest/sunshade_pct/config"])
    assert conf["name"] == "Sunshade Position" and conf["unit_of_measurement"] == "%"
    assert conf["state_topic"] == "leapmotor/VINTEST/sunshade_pct"
