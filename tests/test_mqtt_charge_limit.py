"""Writable Home Assistant charge-limit (target SoC) over MQTT — GitHub #77.

A `number` platform entity (50–100 %, step 1) that shows the configured charge limit
Mate already reads from the car (charge_limit_percent) AND sets it on tap. Covers the
discovery config, the command-topic routing, and the poller-side dispatch (range-validated).
Mirrors test_mqtt_trunk_toggle.py (#71).

The dispatch does a READ-MODIFY-WRITE through set_charge_schedule — never the lib's
api.set_charge_limit, which wipes an enabled start-time-only plan (leapmotor-api #18). That
was fixed web-side in v2.5.8; this MQTT path was still calling the lib directly.
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


def _service(prefix="leapmotor"):
    svc = M.MqttService("broker", 1883, topic_prefix=prefix, get_setting=lambda k, d="": d)
    svc.client = _FakeClient()
    return svc


# ── discovery ──────────────────────────────────────────────────────────────────

def test_discovery_publishes_a_charge_limit_number():
    svc = _service()
    svc.publish_discovery(types.SimpleNamespace(vin="VINTEST"))
    topic = "homeassistant/number/leapmotor_mate_vintest/charge_limit/config"
    assert topic in svc.client.published
    conf = json.loads(svc.client.published[topic])
    assert conf["command_topic"] == "leapmotor/VINTEST/charge_limit/set"
    assert conf["state_topic"] == "leapmotor/VINTEST/charge_limit"
    assert conf["min"] == 50 and conf["max"] == 100 and conf["step"] == 1
    assert conf["unit_of_measurement"] == "%"


def test_charge_limit_number_respects_topic_prefix():
    svc = _service(prefix="myprefix")
    svc.publish_discovery(types.SimpleNamespace(vin="VINTEST"))
    topic = "homeassistant/number/myprefix_mate_vintest/charge_limit/config"
    conf = json.loads(svc.client.published[topic])
    assert conf["command_topic"] == "myprefix/VINTEST/charge_limit/set"
    assert conf["state_topic"] == "myprefix/VINTEST/charge_limit"


# ── command-topic routing (mqtt _on_message) ────────────────────────────────────

def test_set_topic_routes_to_on_command():
    svc = _service()
    seen = []
    svc.on_command = lambda vin, cmd, val: seen.append((vin, cmd, val))
    msg = types.SimpleNamespace(topic="leapmotor/VIN9/charge_limit/set", payload=b"70")
    svc._on_message(None, None, msg)
    assert seen == [("VIN9", "charge_limit", "70")]


# ── poller-side dispatch (poller/main._handle_mqtt_command) ──────────────────────

def _poller_main():
    """Load poller/main.py under its own name (it collides with web/main.py otherwise)."""
    path = pathlib.Path(__file__).parents[1] / "poller" / "main.py"
    spec = importlib.util.spec_from_file_location("poller_main", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_PLAN = {"chargeEnable": 1, "starttime": "22:00", "endtime": "06:00",
         "cycles": "1,1,1,1,1,0,0", "circulation": 1, "recharge": 0}


def _dispatch(value, tmp_path, schedule=_PLAN):
    """Dispatch a charge_limit command. `schedule` is what the car currently reports."""
    import db as D
    pm = _poller_main()
    api = types.SimpleNamespace(calls=[])
    api.get_charge_schedule = lambda vin: schedule
    api.set_charge_schedule = lambda vin, **kw: api.calls.append(("set_charge_schedule", vin, kw))
    # Present but must never be used from here — it wipes start-time-only plans.
    api.set_charge_limit = lambda vin, pct: api.calls.append(("set_charge_limit", vin, pct))
    client = types.SimpleNamespace(_api=api)
    service = types.SimpleNamespace(last_climate_on=None,
                                    publish_state=lambda vin, k, v: None)
    db = D.Database(str(tmp_path / "t.db"))
    pm._handle_mqtt_command(client, service, db, "VIN1", "charge_limit", value)
    return api.calls


def test_dispatch_sets_target_soc_preserving_the_plan(tmp_path):
    calls = _dispatch("70", tmp_path)
    assert len(calls) == 1
    name, vin, kw = calls[0]
    assert (name, vin) == ("set_charge_schedule", "VIN1")
    assert kw["soc_limit"] == 70                 # only the SoC moves…
    assert kw["enabled"] is True                 # …everything else round-trips untouched
    assert kw["start_time"] == "22:00" and kw["end_time"] == "06:00"
    assert kw["cycles"] == "1,1,1,1,1,0,0"
    assert kw["circulation"] == 1 and kw["recharge"] == 0


def test_start_time_only_plan_is_not_wiped(tmp_path):
    """Regression (leapmotor-api #18): the cloud omits `cycles` for an ENABLED start-time-only plan.
    The lib's set_charge_limit falls into its all-defaults branch there — charge_enable=0 and
    starttime reset to 00:00 — silently killing the plan. Fixed web-side in v2.5.8; this MQTT path
    (the Home Assistant number) was still calling the lib directly."""
    calls = _dispatch("80", tmp_path, schedule={"chargeEnable": 1, "starttime": "23:30"})
    assert [c[0] for c in calls] == ["set_charge_schedule"]   # never the lib helper
    kw = calls[0][2]
    assert kw["enabled"] is True                 # NOT disabled
    assert kw["start_time"] == "23:30"           # NOT reset to 00:00
    assert kw["soc_limit"] == 80
    assert kw["cycles"] == "1,1,1,1,1,1,1"       # sane default: keeps charging every day


def test_dispatch_never_calls_the_lib_helper(tmp_path):
    assert all(c[0] != "set_charge_limit" for c in _dispatch("70", tmp_path))


def test_no_existing_plan_still_applies_the_limit(tmp_path):
    """A car with no schedule at all: the limit is still set, with safe defaults and nothing enabled."""
    kw = _dispatch("90", tmp_path, schedule=None)[0][2]
    assert kw["soc_limit"] == 90
    assert kw["enabled"] is False
    assert kw["start_time"] == "00:00" and kw["end_time"] == "08:00"


def test_dispatch_accepts_float_string(tmp_path):
    # HA `number` entities may publish "80.0".
    assert _dispatch("80.0", tmp_path)[0][2]["soc_limit"] == 80


def test_dispatch_rejects_out_of_range(tmp_path):
    assert _dispatch("120", tmp_path) == []     # above max
    assert _dispatch("10", tmp_path) == []      # below min


def test_dispatch_ignores_garbage(tmp_path):
    assert _dispatch("WOBBLE", tmp_path) == []
