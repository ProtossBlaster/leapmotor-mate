"""A command left RETAINED on the broker is never executed (hardening, 18/09/2026).

The command topics are subscribed on every connect, and the broker hands a retained message to every
new subscription. Home Assistant sends its commands unretained, but anything else on the broker — a
script, an automation publishing with retain, a test from the command line — can leave one there, and
Mate executed it again at every restart and every reconnect: an UNLOCK, a trunk, a climate start, on a
car that may be parked anywhere. A command is an action for *now*; a retained one is by definition
from some other time.
"""
import types

import pytest

pytest.importorskip("paho.mqtt.client", reason="poller MQTT bridge needs paho (absent in minimal CI)")
import mqtt as M


def _service():
    svc = M.MqttService("broker", 1883, get_setting=lambda k, d="": d)
    svc.calls = []
    svc.on_command = lambda vin, cmd, value: svc.calls.append((vin, cmd, value))
    return svc


def _msg(topic, payload, retain):
    return types.SimpleNamespace(topic=topic, payload=payload, retain=retain)


@pytest.mark.parametrize("topic,payload", [
    ("leapmotor/VIN1/command", b"climate_cool"),
    ("leapmotor/VIN1/door_lock/set", b"UNLOCK"),
])
def test_a_retained_command_is_ignored(topic, payload):
    svc = _service()
    svc._on_message(None, None, _msg(topic, payload, retain=True))
    assert svc.calls == []


def test_a_live_button_press_still_runs():
    svc = _service()
    svc._on_message(None, None, _msg("leapmotor/VIN1/command", b"climate_cool", retain=False))
    assert svc.calls == [("VIN1", "climate_cool", None)]


def test_a_live_switch_still_runs():
    svc = _service()
    svc._on_message(None, None, _msg("leapmotor/VIN1/door_lock/set", b"LOCK", retain=False))
    assert svc.calls == [("VIN1", "door_lock", "LOCK")]
