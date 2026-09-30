"""A speed or odometer the car did not send is not published as zero.

`positions` stores such a reading as NULL, but the numbers in memory stay 0 because the state
machine and the recorder compare them as numbers. The MQTT bridge and ABRP read the same memory and
sent that 0 on. The bridge now publishes an empty retained payload, with a discovery template that
turns an empty payload into none, and ABRP leaves the field out.

These tests check what Mate publishes; what Home Assistant and EVCC do with it is theirs.

CI-safe: the real parser, the bridge on a recording client, no broker.
"""
import json

import abrp
import client  # poller/client.py
import mqtt as M  # poller/mqtt.py
import pytest
from test_absent_temperature_entities_are_removed import FakeClient

_ABSENT = object()
_MISSING = pytest.mark.parametrize("missing", [_ABSENT, None, ""], ids=["absent", "None", "empty"])


class _RetainClient(FakeClient):
    """Records the retain flag too: an empty retained payload also clears what the broker kept."""
    def publish(self, topic, payload=None, retain=False, **kw):
        self.sent.append((topic, payload, retain))


def _frame(speed=_ABSENT, odometer=_ABSENT):
    sig = {"1010": 0}
    if speed is not _ABSENT:
        sig["1319"] = speed
    if odometer is not _ABSENT:
        sig["1318"] = odometer
    return client._parse_signal("TESTVIN", sig)


@pytest.fixture
def bridge():
    b = M.MqttService(broker="h", port=1883, discovery_enabled=True, get_setting=lambda *a, **k: "")
    b.client = _RetainClient()
    return b


def _published(bridge, frame):
    """(payload, retain) of the speed and odometer states from one status publish."""
    bridge.client.sent.clear()
    bridge.publish_status(frame)
    sent = {t: (p, r) for t, p, r in bridge.client.sent}
    return sent["leapmotor/TESTVIN/speed"], sent["leapmotor/TESTVIN/odometer"]


def test_a_speed_goes_from_80_to_nothing_and_to_a_measured_0(bridge):
    speeds = [_published(bridge, f)[0]
              for f in (_frame(80.0, 1000.0), _frame(odometer=1000.0), _frame(0.0, 1000.0))]
    assert speeds == [("80.0", True), ("", True), ("0.0", True)]


def test_an_odometer_goes_from_1000_to_nothing_and_to_1002(bridge):
    odometers = [_published(bridge, f)[1]
                 for f in (_frame(0.0, 1000.0), _frame(speed=0.0), _frame(0.0, 1002.0))]
    assert odometers == [("1000.0", True), ("", True), ("1002.0", True)]


@_MISSING
def test_a_missing_speed_leaves_the_odometer_alone(bridge, missing):
    assert _published(bridge, _frame(missing, 1000.0)) == (("", True), ("1000.0", True))


@_MISSING
def test_a_missing_odometer_leaves_the_speed_alone(bridge, missing):
    assert _published(bridge, _frame(37.5, missing)) == (("37.5", True), ("", True))


def test_a_measured_zero_is_published_as_a_number_for_both(bridge):
    assert _published(bridge, _frame(0.0, 0.0)) == (("0.0", True), ("0.0", True))


def test_the_discovery_of_both_turns_an_empty_payload_into_none(bridge):
    bridge.publish_status(_frame(0.0, 1000.0))
    for key in ("speed", "odometer"):
        [config] = [p for t, p, _ in bridge.client.sent
                    if t == f"homeassistant/sensor/leapmotor_mate_testvin/{key}/config"]
        assert json.loads(config)["value_template"] == M._EMPTY_NONE, key


@_MISSING
def test_abrp_is_not_told_a_speed_or_odometer_the_car_did_not_send(missing):
    tlm = abrp._build_tlm(_frame(missing, missing))
    assert "speed" not in tlm and "odometer" not in tlm


@_MISSING
def test_abrp_leaves_out_only_the_field_that_is_missing(missing):
    assert "speed" not in abrp._build_tlm(_frame(missing, 1000.0))
    assert abrp._build_tlm(_frame(missing, 1000.0))["odometer"] == 1000.0
    assert "odometer" not in abrp._build_tlm(_frame(37.5, missing))
    assert abrp._build_tlm(_frame(37.5, missing))["speed"] == 37.5


def test_abrp_still_gets_measured_zeros():
    tlm = abrp._build_tlm(_frame(0.0, 0.0))
    assert tlm["speed"] == 0 and tlm["odometer"] == 0
