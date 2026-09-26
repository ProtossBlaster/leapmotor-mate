"""Single Home Assistant lock toggle over MQTT — GitHub #37.

A `lock` platform entity that shows the locked state AND locks/unlocks on one tap,
so it works as a single HA button. Covers the discovery config, the command-topic
routing, and the poller-side dispatch to lock/unlock.
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

def test_discovery_publishes_a_lock_entity():
    svc = _service()
    svc.publish_discovery(types.SimpleNamespace(vin="VINTEST"))
    topic = "homeassistant/lock/leapmotor_mate_vintest/door_lock/config"
    assert topic in svc.client.published
    conf = json.loads(svc.client.published[topic])
    assert conf["command_topic"] == "leapmotor/VINTEST/door_lock/set"
    assert conf["state_topic"] == "leapmotor/VINTEST/locked"   # reuses the locked state
    assert conf["payload_lock"] == "LOCK" and conf["payload_unlock"] == "UNLOCK"
    assert conf["state_locked"] == "ON" and conf["state_unlocked"] == "OFF"   # ON = locked


def test_lock_entity_respects_topic_prefix():
    svc = M.MqttService("broker", 1883, topic_prefix="myprefix", get_setting=settings("VINTEST"))
    svc.client = _FakeClient()
    svc.publish_discovery(types.SimpleNamespace(vin="VINTEST"))
    topic = "homeassistant/lock/myprefix_mate_vintest/door_lock/config"
    conf = json.loads(svc.client.published[topic])
    assert conf["command_topic"] == "myprefix/VINTEST/door_lock/set"


# ── command-topic routing (mqtt _on_message) ────────────────────────────────────

def test_set_topic_routes_to_on_command():
    svc = _service()
    seen = []
    svc.on_command = lambda vin, cmd, val: seen.append((vin, cmd, val))
    msg = types.SimpleNamespace(topic="leapmotor/VIN9/door_lock/set", payload=b"LOCK", retain=False)
    svc._on_message(None, None, msg)
    assert seen == [("VIN9", "door_lock", "LOCK")]


# ── poller-side dispatch (poller/main._handle_mqtt_command) ──────────────────────

def _poller_main():
    """Load poller/main.py under its own name (it collides with web/main.py otherwise)."""
    path = pathlib.Path(__file__).parents[1] / "poller" / "main.py"
    spec = importlib.util.spec_from_file_location("poller_main", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _dispatch_cmd(cmd, value, tmp_path):
    import db as D
    pm = _poller_main()
    api = types.SimpleNamespace(calls=[],
                                lock_vehicle=lambda vin: api.calls.append(("lock", vin)),
                                unlock_vehicle=lambda vin: api.calls.append(("unlock", vin)))
    client = types.SimpleNamespace(_api=api)
    pubs = []
    service = types.SimpleNamespace(last_climate_on=None,
                                    publish_state=lambda vin, k, v: pubs.append((vin, k, v)))
    db = D.Database(str(tmp_path / "t.db"))
    pm._handle_mqtt_command(client, service, db, "VIN1", cmd, value)
    return api.calls, pubs


def _dispatch(value, tmp_path):
    return _dispatch_cmd("door_lock", value, tmp_path)


def test_dispatch_lock(tmp_path):
    calls, pubs = _dispatch("LOCK", tmp_path)
    assert calls == [("lock", "VIN1")]
    assert pubs == []                                # no optimistic publish — HA shows only the real polled state


def test_dispatch_unlock(tmp_path):
    calls, pubs = _dispatch("UNLOCK", tmp_path)
    assert calls == [("unlock", "VIN1")]
    assert pubs == []


def test_dispatch_ignores_garbage(tmp_path):
    calls, pubs = _dispatch("WOBBLE", tmp_path)
    assert calls == [] and pubs == []


# ── switch flavour (#38): widgets can toggle a switch, not a lock ────────────────

def test_discovery_publishes_a_toggle_switch():
    svc = _service()
    svc.publish_discovery(types.SimpleNamespace(vin="VINTEST"))
    conf = json.loads(svc.client.published[
        "homeassistant/switch/leapmotor_mate_vintest/lock_toggle/config"])
    assert conf["command_topic"] == "leapmotor/VINTEST/lock_toggle/set"
    assert conf["state_topic"] == "leapmotor/VINTEST/locked"
    assert conf["payload_on"] == "ON" and conf["payload_off"] == "OFF"   # ON = locked


def test_dispatch_lock_toggle_on_locks(tmp_path):
    calls, pubs = _dispatch_cmd("lock_toggle", "ON", tmp_path)
    assert calls == [("lock", "VIN1")] and pubs == []


def test_dispatch_lock_toggle_off_unlocks(tmp_path):
    calls, pubs = _dispatch_cmd("lock_toggle", "OFF", tmp_path)
    assert calls == [("unlock", "VIN1")] and pubs == []


# ── the redundant Lock/Unlock buttons are retired (superseded by lock + switch) ──

def test_lock_unlock_buttons_retired():
    svc = _service()
    svc.publish_discovery(types.SimpleNamespace(vin="VINTEST"))
    pub = svc.client.published
    base = "homeassistant/button/leapmotor_mate_vintest"
    # retained configs CLEARED (empty payload) so HA drops them on existing installs
    assert pub[f"{base}/lock/config"] == "" and pub[f"{base}/unlock/config"] == ""
    # other buttons untouched
    assert pub[f"{base}/open_trunk/config"] != ""
