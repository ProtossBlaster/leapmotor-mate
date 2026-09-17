"""A/C AUTO over MQTT — the plain "turn the climate on" the web UI already has.

The Commands page has six climate tiles; the MQTT bridge advertised five. The missing one was
`ac_on`, the A/C AUTO button (operate=auto + mode=nohotcold: the car self-manages cool/heat toward
the target temperature, climate mode 3713=0). It had no button AND no dispatcher branch, and the
dispatcher ends in `else: return`, so no payload could reach it either — @Kuli1111 asked how to
trigger it from an automation (#292) and the honest answer was that he could not.

Same body as web/command_client.ac_on(), including the T03 fallback (#67): that firmware ignores
operate=auto, so an auto write is rewritten to manual+cold, the one mode confirmed to start it.
"""
import json
import types
import importlib.util
import pathlib

import pytest

pytest.importorskip("paho.mqtt.client", reason="poller MQTT bridge needs paho (absent in minimal CI)")
import mqtt as M


class _FakeClient:
    def __init__(self):
        self.published = {}

    def publish(self, topic, payload, retain=False):
        self.published[topic] = payload


def _service():
    svc = M.MqttService("broker", 1883, get_setting=lambda k, d="": d)
    svc.client = _FakeClient()
    return svc


# ── discovery ──────────────────────────────────────────────────────────────────

def test_discovery_publishes_the_ac_auto_button():
    svc = _service()
    svc.publish_discovery(types.SimpleNamespace(vin="VINTEST"))
    topic = "homeassistant/button/leapmotor_mate_vintest/climate_auto/config"
    assert topic in svc.client.published, "A/C Auto not advertised"
    conf = json.loads(svc.client.published[topic])
    assert conf["payload_press"] == "climate_auto"
    assert conf["name"] == "A/C Auto"


# ── poller-side dispatch ───────────────────────────────────────────────────────

def _poller_main():
    path = pathlib.Path(__file__).parents[1] / "poller" / "main.py"
    spec = importlib.util.spec_from_file_location("poller_main", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _dispatch(cmd, tmp_path, car_type="B10", value=None, panel=None):
    import db as D
    pm = _poller_main()
    if panel is not None:
        pm._climate_ctx_from_db = lambda db, vin="": panel
    api = types.SimpleNamespace(calls=[])
    api._remote_control = lambda vin, action, cmd_content=None: api.calls.append(
        (action, vin, cmd_content))
    client = types.SimpleNamespace(_api=api,
                                   _vehicle=types.SimpleNamespace(car_type=car_type))
    service = types.SimpleNamespace(set_climate_on=lambda vin, v: None,
                                    climate_on_for=lambda vin: None)
    db = D.Database(str(tmp_path / "t.db"))
    pm._handle_mqtt_command(client, service, db, "VIN1", cmd, value)
    return api.calls


def test_ac_auto_sends_the_auto_body(tmp_path):
    calls = _dispatch("climate_auto", tmp_path, panel=("wind", "out", 3, 22))
    assert len(calls) == 1, calls
    action, vin, body = calls[0]
    assert (action, vin) == ("ac_on", "VIN1")
    assert json.loads(body) == {"circle": "in", "mode": "nohotcold", "operate": "auto",
                                "position": "all", "temperature": "22",
                                "windlevel": "5", "wshld": "0"}


def test_ac_auto_carries_the_cars_own_target_temperature(tmp_path):
    calls = _dispatch("climate_auto", tmp_path, panel=("cold", "in", 7, 19))
    assert json.loads(calls[0][2])["temperature"] == "19"


def test_ac_auto_clamps_a_target_temperature_out_of_range(tmp_path):
    calls = _dispatch("climate_auto", tmp_path, panel=("cold", "in", 7, 40))
    assert json.loads(calls[0][2])["temperature"] == "32"


def test_ac_auto_on_a_t03_falls_back_to_manual_cold(tmp_path):
    # #67: the T03 ignores operate=auto, and 'nohotcold' has no manual equivalent.
    calls = _dispatch("climate_auto", tmp_path, car_type="T03", panel=("wind", "out", 3, 22))
    body = json.loads(calls[0][2])
    assert (body["operate"], body["mode"]) == ("manual", "cold")


def test_ac_auto_leaves_the_other_models_in_auto(tmp_path):
    for car in ("B10", "C10", "B05"):
        body = json.loads(_dispatch("climate_auto", tmp_path, car_type=car,
                                    panel=("wind", "out", 3, 22))[0][2])
        assert (body["operate"], body["mode"]) == ("auto", "nohotcold"), car
