"""Single Home Assistant trunk toggle over MQTT — GitHub #71.

A `switch` platform entity (ON = open) that shows the trunk open/closed state AND
opens/closes it on tap — the trunk analog of the Door Lock Toggle (#38), a plain toggle
rather than a cover's open/close arrows. Covers the discovery config, the command-topic
routing, and the poller-side dispatch to open/close trunk. The separate Open/Close Trunk
buttons are kept (existing automations rely on them).
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


def _service():
    svc = M.MqttService("broker", 1883, get_setting=settings("VINTEST"))
    svc.client = _FakeClient()
    return svc


# ── discovery ──────────────────────────────────────────────────────────────────

def test_discovery_publishes_a_trunk_switch():
    svc = _service()
    svc.publish_discovery(types.SimpleNamespace(vin="VINTEST"))
    topic = "homeassistant/switch/leapmotor_mate_vintest/trunk/config"
    assert topic in svc.client.published
    conf = json.loads(svc.client.published[topic])
    assert conf["command_topic"] == "leapmotor/VINTEST/trunk/set"
    assert conf["state_topic"] == "leapmotor/VINTEST/trunk_open"   # reuses the trunk_open state
    assert conf["payload_on"] == "ON" and conf["payload_off"] == "OFF"
    assert conf["state_on"] == "ON" and conf["state_off"] == "OFF"   # ON = open


def test_trunk_switch_respects_topic_prefix():
    svc = M.MqttService("broker", 1883, topic_prefix="myprefix", get_setting=settings("VINTEST"))
    svc.client = _FakeClient()
    svc.publish_discovery(types.SimpleNamespace(vin="VINTEST"))
    topic = "homeassistant/switch/myprefix_mate_vintest/trunk/config"
    conf = json.loads(svc.client.published[topic])
    assert conf["command_topic"] == "myprefix/VINTEST/trunk/set"
    assert conf["state_topic"] == "myprefix/VINTEST/trunk_open"


# ── command-topic routing (mqtt _on_message) ────────────────────────────────────

def test_set_topic_routes_to_on_command():
    svc = _service()
    seen = []
    svc.on_command = lambda vin, cmd, val: seen.append((vin, cmd, val))
    msg = types.SimpleNamespace(topic="leapmotor/VIN9/trunk/set", payload=b"ON", retain=False)
    svc._on_message(None, None, msg)
    assert seen == [("VIN9", "trunk", "ON")]


# ── poller-side dispatch (poller/main._handle_mqtt_command) ──────────────────────

def _poller_main():
    """Load poller/main.py under its own name (it collides with web/main.py otherwise)."""
    path = pathlib.Path(__file__).parents[1] / "poller" / "main.py"
    spec = importlib.util.spec_from_file_location("poller_main", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _dispatch(value, tmp_path):
    import db as D
    pm = _poller_main()
    api = types.SimpleNamespace(calls=[],
                                open_trunk=lambda vin: api.calls.append(("open_trunk", vin)),
                                close_trunk=lambda vin: api.calls.append(("close_trunk", vin)))
    client = types.SimpleNamespace(_api=api)
    pubs = []
    service = types.SimpleNamespace(last_climate_on=None,
                                    publish_state=lambda vin, k, v: pubs.append((vin, k, v)))
    db = D.Database(str(tmp_path / "t.db"))
    pm._handle_mqtt_command(client, service, db, "VIN1", "trunk", value)
    return api.calls, pubs


def test_dispatch_on_opens(tmp_path):
    calls, pubs = _dispatch("ON", tmp_path)
    assert calls == [("open_trunk", "VIN1")]
    assert pubs == []                                    # no optimistic publish — HA shows only the real polled state


def test_dispatch_off_closes(tmp_path):
    calls, pubs = _dispatch("OFF", tmp_path)
    assert calls == [("close_trunk", "VIN1")]
    assert pubs == []


def test_dispatch_is_case_insensitive(tmp_path):
    calls, pubs = _dispatch("on", tmp_path)
    assert calls == [("open_trunk", "VIN1")] and pubs == []


def test_dispatch_ignores_garbage(tmp_path):
    calls, pubs = _dispatch("WOBBLE", tmp_path)
    assert calls == [] and pubs == []


# ── the Open/Close Trunk buttons stay (existing automations rely on them) ────────

def test_open_close_trunk_buttons_kept():
    svc = _service()
    svc.publish_discovery(types.SimpleNamespace(vin="VINTEST"))
    pub = svc.client.published
    base = "homeassistant/button/leapmotor_mate_vintest"
    assert pub[f"{base}/open_trunk/config"] != ""    # still advertised
    assert pub[f"{base}/close_trunk/config"] != ""
