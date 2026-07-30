"""The CAR's own clock over MQTT — `frame_ts` and `data_age` (#178 @riri19).

`last_seen` is `datetime.now()` at the moment Mate writes the row: it is the POLL clock, and it
stays a few seconds old forever, because Mate polls on a timer and the cloud always answers. When
the car can't reach the cloud, the cloud re-serves the last frame it received — so a fresh
`last_seen` can sit on top of half-hour-old contents. That divergence is the whole subject of #178,
and until now the only place it existed was one log line.

These two topics say how old the CONTENT is. They are deliberately UNGATED, unlike the Overview's
"· data 33m old" tail: that gate (car driving or charging, data behind the polling) exists so a
sleeping car doesn't paint "data 8h old" on the panel every morning — a decision about a screen.
An automation wants the raw number and its own conditions, and @riri19 asked for exactly that.
"""
import json
import time
import types

import pytest

pytest.importorskip("paho.mqtt.client", reason="poller MQTT bridge needs paho (absent in minimal CI)")
import mqtt as M
from client import _parse_signal


class _FakeClient:
    def __init__(self):
        self.published = {}

    def publish(self, topic, payload, retain=False):
        self.published[topic] = payload

    def is_connected(self):
        return True


def _service(prefix="leapmotor"):
    svc = M.MqttService("broker", 1883, topic_prefix=prefix, get_setting=lambda k, d="": d)
    svc.client = _FakeClient()
    return svc


def _data(frame_ms, *, soc=47.4, speed=0.0, gear_signal=0, plug=0):
    """A parsed frame carrying the car's own clock in `sts`. gear_signal 0 = P."""
    sig = {
        "sts": frame_ms,
        "100003": soc,
        "1319": speed,
        "1010": gear_signal,
        "1149": plug,
    }
    return _parse_signal("VINTEST", sig)


def _publish(svc, data):
    svc._publish_sensors(data)
    return svc.client.published


# ── discovery ──────────────────────────────────────────────────────────────────

def test_discovery_publishes_frame_ts_and_data_age():
    svc = _service()
    svc.publish_discovery(types.SimpleNamespace(vin="VINTEST"))
    pub = svc.client.published

    ft = "homeassistant/sensor/leapmotor_mate_vintest/frame_ts/config"
    assert ft in pub, "no discovery config for frame_ts"
    c = json.loads(pub[ft])
    assert c["device_class"] == "timestamp"
    assert c["state_topic"] == "leapmotor/VINTEST/frame_ts"

    da = "homeassistant/sensor/leapmotor_mate_vintest/data_age/config"
    assert da in pub, "no discovery config for data_age"
    c = json.loads(pub[da])
    assert c["device_class"] == "duration" and c["unit_of_measurement"] == "s"
    assert c["state_topic"] == "leapmotor/VINTEST/data_age"


def test_both_survive_an_empty_payload_without_becoming_an_error():
    """A car that doesn't report its clock publishes "" on both topics. Without a value_template
    mapping empty→none, HA tries to parse "" as a timestamp/number and logs an error every poll."""
    svc = _service()
    svc.publish_discovery(types.SimpleNamespace(vin="VINTEST"))
    for key in ("frame_ts", "data_age"):
        c = json.loads(svc.client.published[
            f"homeassistant/sensor/leapmotor_mate_vintest/{key}/config"])
        assert "value_template" in c, f"{key} has no empty-payload guard"
        assert "none" in c["value_template"]


def test_discovery_respects_the_topic_prefix():
    svc = _service(prefix="myprefix")
    svc.publish_discovery(types.SimpleNamespace(vin="VINTEST"))
    assert "homeassistant/sensor/myprefix_mate_vintest/frame_ts/config" in svc.client.published


# ── the values ─────────────────────────────────────────────────────────────────

def test_frame_ts_is_the_cars_clock_not_the_poll_clock():
    svc = _service()
    frame_ms = int((time.time() - 2400) * 1000)          # frame the car sent 40 min ago
    pub = _publish(svc, _data(frame_ms))

    from datetime import datetime, timezone
    got = datetime.fromisoformat(pub["leapmotor/VINTEST/frame_ts"])
    assert abs(got.timestamp() * 1000 - frame_ms) < 1000
    assert got.tzinfo is not None, "a timestamp sensor needs an offset or HA can't place it"
    # and it must NOT be the poll clock, which is what last_seen already carries
    seen = datetime.fromisoformat(pub["leapmotor/VINTEST/last_seen"])
    assert (datetime.now(timezone.utc) - seen).total_seconds() < 5
    assert (seen - got).total_seconds() > 2000, "frame_ts collapsed onto the poll clock"


def test_data_age_measures_the_frame_not_the_poll():
    svc = _service()
    pub = _publish(svc, _data(int((time.time() - 2400) * 1000)))
    assert 2395 <= int(pub["leapmotor/VINTEST/data_age"]) <= 2405


def test_a_fresh_frame_reads_about_zero():
    svc = _service()
    pub = _publish(svc, _data(int(time.time() * 1000)))
    assert 0 <= int(pub["leapmotor/VINTEST/data_age"]) <= 3


def test_a_car_that_never_reports_its_clock_publishes_empty_not_1970():
    """`timestamp_ms` is `int(sig.get("sts") or sig.get("1") or 0)` — absent on the T03/EU
    named-field path. Publishing the 0 would hand Home Assistant 1 January 1970 and an age of
    fifty-six years, on every poll, forever."""
    svc = _service()
    pub = _publish(svc, _data(0))
    assert pub["leapmotor/VINTEST/frame_ts"] == ""
    assert pub["leapmotor/VINTEST/data_age"] == ""


def test_a_car_clock_ahead_of_the_host_reads_zero_not_a_negative_age():
    """Host clocks drift against the cloud — recorder.py records −48 s measured in the wild. With
    the sign the other way the subtraction goes negative, which is not staleness."""
    svc = _service()
    pub = _publish(svc, _data(int((time.time() + 120) * 1000)))
    assert int(pub["leapmotor/VINTEST/data_age"]) == 0


def test_the_age_goes_out_even_when_the_car_is_parked_and_unplugged():
    """The Overview's gate (driving or charging) must NOT travel to MQTT — @riri19 asked to apply
    his own conditions, from his phone, and can't if we've already applied ours."""
    svc = _service()
    pub = _publish(svc, _data(int((time.time() - 3600) * 1000), speed=0.0, gear_signal=0, plug=0))
    assert int(pub["leapmotor/VINTEST/data_age"]) >= 3595
    assert pub["leapmotor/VINTEST/frame_ts"] != ""


# ── the doc that lists the entities ────────────────────────────────────────────

def test_the_features_doc_lists_both_new_sensors():
    """§13b of FEATURES.md is the published-entity inventory: a topic that isn't in it is a topic
    nobody knows exists. That file is kept OUT of the repo (.git/info/exclude) — it's the internal
    reference, not shipped — so this skips where it isn't present instead of failing CI on a file
    that was never cloned. Green here means the local copy is in step; it can never be green-by-
    accident, because a missing file skips rather than passes."""
    import pathlib
    doc = pathlib.Path(__file__).resolve().parent.parent / "docs" / "FEATURES.md"
    if not doc.exists():
        pytest.skip("docs/FEATURES.md is a local-only reference (git-excluded)")
    text = doc.read_text()
    assert "frame_ts" in text and "data_age" in text
