"""A frame without a GPS fix does not put the car at 0, 0 in Home Assistant or ABRP.

The parser keeps a coordinate it could not read as 0.0, so a frame without a fix is the pair of
zeros, which `has_gps_fix` and the poller's own check read as no position. MQTT and ABRP read the
numbers in memory: Home Assistant got a retained `location` at 0, 0, which its tracker shows as
`not_home` in the Gulf of Guinea, and ABRP a point there.

A frame without a fix now publishes no `location`, so the broker keeps the last one, as for a frame
without an odometer or a range, and ABRP gets no `lat` and `lon`. One zero is still a fix: the
equator or the prime meridian.
"""
import json

import pytest

M = pytest.importorskip("mqtt", reason="poller.mqtt needs paho")

import abrp
import client

VIN = "NOFIXTESTVIN"
NO_FIX = {
    "no coordinates": {},
    "both at 0": {"3": 0, "2": 0, "3725": 0, "3724": 0},
}
FIX = {
    "a fix": ({"3": 45.0, "2": 9.0, "3725": 45.0, "3724": 9.0}, (45.0, 9.0)),
    "the prime meridian": ({"3": 51.48, "2": 0, "3725": 51.48, "3724": 0}, (51.48, 0.0)),
    "the equator": ({"3": 0, "2": 17.9, "3725": 0, "3724": 17.9}, (0.0, 17.9)),
}


class _FakeClient:
    def __init__(self):
        self.sent = []

    def publish(self, topic, payload, retain=False):
        self.sent.append((topic, payload, retain))

    def locations(self):
        return [(json.loads(p), r) for t, p, r in self.sent if t == f"leapmotor/{VIN}/location"]


def _frame(gps):
    return client._parse_signal(VIN, {"1": 1793903914581, "100003": 80.0, "1010": 0, "1319": 0,
                                      "1318": 3628, **gps})


def _service():
    svc = M.MqttService("broker", 1883, topic_prefix="leapmotor", get_setting=lambda k, d="": d)
    svc.client = _FakeClient()
    return svc


@pytest.mark.parametrize("gps", NO_FIX.values(), ids=NO_FIX.keys())
def test_mqtt_leaves_the_retained_location_alone(gps):
    svc = _service()
    svc._publish_sensors(_frame(gps))
    assert any(t == f"leapmotor/{VIN}/soc" for t, _, _ in svc.client.sent), "the frame was published"
    assert svc.client.locations() == []


@pytest.mark.parametrize("gps, at", FIX.values(), ids=FIX.keys())
def test_mqtt_publishes_a_fix(gps, at):
    svc = _service()
    svc._publish_sensors(_frame(gps))
    assert svc.client.locations() == [({"latitude": at[0], "longitude": at[1]}, True)]


def test_mqtt_publishes_the_fix_after_frames_without_one():
    svc = _service()
    for gps in (FIX["a fix"][0], NO_FIX["both at 0"], NO_FIX["no coordinates"], FIX["the prime meridian"][0]):
        svc._publish_sensors(_frame(gps))
    assert [loc for loc, _ in svc.client.locations()] == [{"latitude": 45.0, "longitude": 9.0},
                                                           {"latitude": 51.48, "longitude": 0.0}]


@pytest.mark.parametrize("gps", NO_FIX.values(), ids=NO_FIX.keys())
def test_abrp_gets_no_position_without_a_fix(gps):
    tlm = abrp._build_tlm(_frame(gps))
    assert "soc" in tlm, "the telemetry was built"
    assert "lat" not in tlm and "lon" not in tlm


@pytest.mark.parametrize("gps, at", FIX.values(), ids=FIX.keys())
def test_abrp_gets_a_fix(gps, at):
    tlm = abrp._build_tlm(_frame(gps))
    assert (tlm["lat"], tlm["lon"]) == at
