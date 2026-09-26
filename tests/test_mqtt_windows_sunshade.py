"""Windows + sunshade/roof commands over MQTT.

These physical open/close commands existed on the web UI but were missing from the MQTT
bridge (neither advertised as buttons nor handled by the dispatcher, which dropped them
in its final `else: return`). This covers the four new buttons in discovery and the
poller-side dispatch — windows map the "vent" % to the car's native scale, sunshade is a
direct API call. Like every physical command, the car only actually acts on them in Park.
"""
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

def test_discovery_publishes_window_and_sunshade_buttons():
    svc = _service()
    svc.publish_discovery(types.SimpleNamespace(vin="VINTEST"))
    base = "homeassistant/button/leapmotor_mate_vintest"
    for key in ("open_windows", "close_windows", "open_sunshade", "close_sunshade"):
        topic = f"{base}/{key}/config"
        assert topic in svc.client.published, f"{key} not advertised"
        assert svc.client.published[topic] != "", f"{key} config was cleared (hidden)"


# ── poller-side dispatch (poller/main._handle_mqtt_command) ──────────────────────

def _poller_main():
    """Load poller/main.py under its own name (it collides with web/main.py otherwise)."""
    path = pathlib.Path(__file__).parents[1] / "poller" / "main.py"
    spec = importlib.util.spec_from_file_location("poller_main", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _dispatch(cmd, tmp_path, car_type="B10", value=None):
    import db as D
    pm = _poller_main()
    api = types.SimpleNamespace(calls=[])
    api.windows = lambda vin, value=None: api.calls.append(("windows", vin, value))
    api.open_sunshade = lambda vin, value=None: api.calls.append(("open_sunshade", vin))
    api.close_sunshade = lambda vin, value=None: api.calls.append(("close_sunshade", vin))
    client = types.SimpleNamespace(_api=api,
                                   _vehicle=types.SimpleNamespace(car_type=car_type))
    service = types.SimpleNamespace(last_climate_on=None)
    db = D.Database(str(tmp_path / "t.db"))
    pm._handle_mqtt_command(client, service, db, "VIN1", cmd, value)
    return api.calls


def test_open_windows_uses_native_scale_b10(tmp_path):
    # B10 "fully open" native scale is 10 → 20% vent = round(2.0) = "2"
    assert _dispatch("open_windows", tmp_path, car_type="B10") == [("windows", "VIN1", "2")]


def test_close_windows_is_zero(tmp_path):
    assert _dispatch("close_windows", tmp_path, car_type="B10") == [("windows", "VIN1", "0")]


def test_open_windows_native_scale_t03(tmp_path):
    # T03 has no reduced scale → default 100 → 20% = "20"
    assert _dispatch("open_windows", tmp_path, car_type="T03") == [("windows", "VIN1", "20")]


def test_open_sunshade_dispatches(tmp_path):
    assert _dispatch("open_sunshade", tmp_path) == [("open_sunshade", "VIN1")]


def test_close_sunshade_dispatches(tmp_path):
    assert _dispatch("close_sunshade", tmp_path) == [("close_sunshade", "VIN1")]


def test_unknown_command_still_ignored(tmp_path):
    assert _dispatch("open_roof_hatch", tmp_path) == []
