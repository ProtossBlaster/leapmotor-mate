"""Charge schedule over MQTT — GitHub #151 (@chengler).

A `text` entity that takes a JSON plan: {"start","stop","soc","active","days"} — every key optional.
Whatever you omit KEEPS its current value (read-modify-write), so an automation can send just
{"start":"23:00"} without disturbing the rest. It never goes through the library's set_charge_limit,
which wipes start-time-only plans (leapmotor-api #18 — see test_mqtt_charge_limit.py).

The target SoC is the one field we won't invent: when the payload omits it we use the limit the
poller last read from the car, and if even that is unknown the command is refused.
"""
import importlib.util
import json
import pathlib
import types

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

def test_discovery_publishes_a_charge_schedule_text():
    svc = _service()
    svc.publish_discovery(types.SimpleNamespace(vin="VINTEST"))
    topic = "homeassistant/text/leapmotor_mate_vintest/charge_schedule/config"
    assert topic in svc.client.published
    conf = json.loads(svc.client.published[topic])
    assert conf["command_topic"] == "leapmotor/VINTEST/charge_schedule/set"
    assert conf["state_topic"] == "leapmotor/VINTEST/charge_schedule"
    assert conf["mode"] == "text"


def test_set_topic_routes_to_on_command():
    svc = _service()
    seen = []
    svc.on_command = lambda vin, cmd, val: seen.append((vin, cmd, val))
    msg = types.SimpleNamespace(topic="leapmotor/VIN9/charge_schedule/set", payload=b'{"soc":90}')
    svc._on_message(None, None, msg)
    assert seen == [("VIN9", "charge_schedule", '{"soc":90}')]


# ── poller-side dispatch ───────────────────────────────────────────────────────

_PLAN = {"chargeEnable": 1, "starttime": "22:00", "endtime": "06:00",
         "cycles": "1,1,1,1,1,0,0", "circulation": 1, "recharge": 0}


def _poller_main():
    path = pathlib.Path(__file__).parents[1] / "poller" / "main.py"
    spec = importlib.util.spec_from_file_location("poller_main", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _dispatch(value, tmp_path, schedule=_PLAN, car_limit="80"):
    """Returns (calls to the API, states echoed to MQTT)."""
    import db as D
    pm = _poller_main()
    api = types.SimpleNamespace(calls=[])
    api.get_charge_schedule = lambda vin: schedule
    api.set_charge_schedule = lambda vin, **kw: api.calls.append(kw)
    # Present but must never be used — it wipes start-time-only plans.
    api.set_charge_limit = lambda vin, pct: api.calls.append(("LIB", pct))
    client = types.SimpleNamespace(_api=api)
    states = {}
    service = types.SimpleNamespace(last_climate_on=None,
                                    publish_state=lambda vin, k, v: states.__setitem__(k, v))
    db = D.Database(str(tmp_path / "t.db"))
    if car_limit is not None:
        db.set_setting("charge_limit_percent", car_limit)
    pm._handle_mqtt_command(client, service, db, "VIN1", "charge_schedule", value)
    return api.calls, states


def test_full_plan_is_applied(tmp_path):
    calls, _ = _dispatch('{"start":"19:00","stop":"08:00","soc":90,"active":true,'
                         '"days":"1,1,1,1,1,1,1"}', tmp_path)
    assert len(calls) == 1
    kw = calls[0]
    assert kw["start_time"] == "19:00" and kw["end_time"] == "08:00"
    assert kw["soc_limit"] == 90 and kw["enabled"] is True
    assert kw["cycles"] == "1,1,1,1,1,1,1"
    assert kw["circulation"] == 1 and kw["recharge"] == 0     # preserved from the car


def test_partial_update_keeps_everything_else(tmp_path):
    """The whole point: an automation sends ONE key and the rest of the plan survives."""
    calls, _ = _dispatch('{"start":"23:00"}', tmp_path)
    kw = calls[0]
    assert kw["start_time"] == "23:00"        # changed
    assert kw["end_time"] == "06:00"          # kept
    assert kw["enabled"] is True              # kept
    assert kw["cycles"] == "1,1,1,1,1,0,0"    # kept
    assert kw["soc_limit"] == 80              # from the car's current limit, not invented


def test_soc_only_keeps_the_window(tmp_path):
    kw = _dispatch('{"soc":95}', tmp_path)[0][0]
    assert kw["soc_limit"] == 95
    assert kw["start_time"] == "22:00" and kw["end_time"] == "06:00"


def test_disabling_the_plan(tmp_path):
    kw = _dispatch('{"active":false}', tmp_path)[0][0]
    assert kw["enabled"] is False
    assert kw["start_time"] == "22:00"        # the window is still remembered


def test_state_is_echoed_back(tmp_path):
    """Home Assistant shows the plan Mate just wrote instead of a blank box."""
    _, states = _dispatch('{"start":"19:00","soc":90}', tmp_path)
    echoed = json.loads(states["charge_schedule"])
    assert echoed["start"] == "19:00" and echoed["soc"] == 90
    assert echoed["stop"] == "06:00" and echoed["active"] is True


def test_stopping_a_charge_the_reported_way(tmp_path):
    """@chengler's field finding: setting the target BELOW the current SoC stops an ongoing charge."""
    kw = _dispatch('{"start":"20:00","stop":"08:00","soc":50,"active":true,'
                   '"days":"1,1,1,1,1,1,1"}', tmp_path)[0][0]
    assert kw["soc_limit"] == 50 and kw["start_time"] == "20:00"


def test_never_calls_the_library_helper(tmp_path):
    calls, _ = _dispatch('{"soc":90}', tmp_path)
    assert all(not (isinstance(c, tuple) and c[0] == "LIB") for c in calls)


# ── rejections: a bad payload must change nothing ──────────────────────────────

def test_invalid_json_ignored(tmp_path):
    assert _dispatch("not json", tmp_path)[0] == []


def test_non_object_ignored(tmp_path):
    assert _dispatch('["a"]', tmp_path)[0] == []


def test_soc_out_of_range_ignored(tmp_path):
    assert _dispatch('{"soc":120}', tmp_path)[0] == []
    assert _dispatch('{"soc":10}', tmp_path)[0] == []


def test_malformed_time_ignored(tmp_path):
    assert _dispatch('{"start":"25:00"}', tmp_path)[0] == []
    assert _dispatch('{"start":"nope"}', tmp_path)[0] == []
    assert _dispatch('{"stop":"8"}', tmp_path)[0] == []


def test_refuses_when_no_soc_is_known_anywhere(tmp_path):
    """No soc in the payload AND the car never reported a limit (it's absent on some models, e.g.
    the T03) → do nothing, rather than invent a charge target the owner didn't ask for."""
    assert _dispatch('{"start":"23:00"}', tmp_path, car_limit=None)[0] == []


def test_no_existing_plan_uses_safe_defaults(tmp_path):
    kw = _dispatch('{"soc":90,"start":"23:00"}', tmp_path, schedule=None)[0][0]
    assert kw["start_time"] == "23:00"
    assert kw["end_time"] == "08:00"
    assert kw["enabled"] is False             # nothing was enabled before
    assert kw["cycles"] == "1,1,1,1,1,1,1"
