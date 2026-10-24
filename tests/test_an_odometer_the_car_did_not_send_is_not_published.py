"""An odometer the car did not send is not published as 0 km (PR #358, @arekm).

The parser keeps an absent odometer (1318) as 0 in memory, because the recorder and the state machine
compare it as a number, and `positions` has stored it as unknown since the speed/odometer fix. MQTT
and ABRP read the number in memory: MQTT published a retained `0.0` and ABRP sent `odometer: 0`. The
T03 leaves 1318 out of some frames, and two bundles show it, between readings of 9455 km and of
4605 km. A Home Assistant odometer that drops to 0 and back breaks every statistic built on it.

A frame without an odometer now publishes nothing for it: MQTT keeps the retained last value, as it
does for a frame without a range (#365), and ABRP gets no `odometer` field. An empty retained payload
would not do: it deletes the broker's value, and the sensor turns `unknown` at every such frame.
"""
import pytest

M = pytest.importorskip("mqtt", reason="poller.mqtt needs paho")

import abrp
import client
from test_a_missing_speed_or_odometer_is_stored_as_unknown import _ABSENT, _signal

VIN = "TESTVIN"


class _FakeClient:
    def __init__(self):
        self.published = {}

    def publish(self, topic, payload, retain=False):
        self.published[topic] = (payload, retain)


def _published(odometer):
    svc = M.MqttService("broker", 1883, topic_prefix="leapmotor", get_setting=lambda k, d="": d)
    svc.client = _FakeClient()
    svc._publish_sensors(client._parse_signal(VIN, _signal(speed=0, odometer=odometer)))
    return svc.client.published


@pytest.mark.parametrize("sent", [_ABSENT, None, ""])
def test_mqtt_leaves_the_retained_odometer_alone(sent):
    published = _published(sent)
    assert f"leapmotor/{VIN}/soc" in published, "the frame was published"
    assert f"leapmotor/{VIN}/odometer" not in published


@pytest.mark.parametrize("sent, payload", [(9455, "9455.0"), ("4605.0", "4605.0"), (0, "0.0")])
def test_mqtt_publishes_an_odometer_the_car_sent(sent, payload):
    assert _published(sent)[f"leapmotor/{VIN}/odometer"] == (payload, True)


@pytest.mark.parametrize("sent", [_ABSENT, None, ""])
def test_abrp_gets_no_odometer_the_car_did_not_send(sent):
    tlm = abrp._build_tlm(client._parse_signal(VIN, _signal(speed=0, odometer=sent)))
    assert "soc" in tlm, "the telemetry was built"
    assert "odometer" not in tlm


@pytest.mark.parametrize("sent, km", [(9455, 9455.0), ("4605.0", 4605.0), (0, 0.0)])
def test_abrp_gets_the_odometer_the_car_sent(sent, km):
    assert abrp._build_tlm(client._parse_signal(VIN, _signal(speed=0, odometer=sent)))["odometer"] == km
