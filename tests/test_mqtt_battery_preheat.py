"""Battery preheat over MQTT — the Quick-actions tile the bridge never had.

`battery_preheat` sat in the web UI's _COMMANDS and in its Quick actions grid, but the MQTT bridge
neither advertised a button nor accepted the payload (the dispatcher ends in `else: return`). Found
while answering #292 about A/C Auto: same shape of gap, on a command nobody had reported.

Ungated, like the UI tile: the capability profile maps neither an ability nor a feature to it, so it
is published for every car — the web tile is not gated either.
"""
import json
import types
import importlib.util
import pathlib

import pytest

pytest.importorskip("paho.mqtt.client", reason="poller MQTT bridge needs paho (absent in minimal CI)")
import mqtt as M
from cloud_access_fixture import settings


class _FakeClient:
    def __init__(self):
        self.published = {}

    def publish(self, topic, payload, retain=False):
        self.published[topic] = payload


def test_discovery_publishes_the_battery_preheat_button():
    svc = M.MqttService("broker", 1883, get_setting=settings("VINTEST"))
    svc.client = _FakeClient()
    svc.publish_discovery(types.SimpleNamespace(vin="VINTEST"))
    topic = "homeassistant/button/leapmotor_mate_vintest/battery_preheat/config"
    assert topic in svc.client.published, "Preheat battery not advertised"
    conf = json.loads(svc.client.published[topic])
    assert conf["payload_press"] == "battery_preheat"
    assert conf["name"] == "Preheat Battery"


def _poller_main():
    path = pathlib.Path(__file__).parents[1] / "poller" / "main.py"
    spec = importlib.util.spec_from_file_location("poller_main", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_battery_preheat_reaches_the_car(tmp_path):
    import db as D
    pm = _poller_main()
    api = types.SimpleNamespace(calls=[])
    api.battery_preheat = lambda vin: api.calls.append(("battery_preheat", vin))
    client = types.SimpleNamespace(_api=api,
                                   _vehicle=types.SimpleNamespace(car_type="B10"))
    service = types.SimpleNamespace(set_climate_on=lambda vin, v: None)
    db = D.Database(str(tmp_path / "t.db"))
    pm._handle_mqtt_command(client, service, db, "VIN1", "battery_preheat", None)
    assert api.calls == [("battery_preheat", "VIN1")]
