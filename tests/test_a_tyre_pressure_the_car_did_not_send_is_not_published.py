"""A tyre pressure the car did not send is unknown in Home Assistant, not 0 bar.

The parser turned an absent tyre pressure (2646, 2653, 2660, 2667) into 0.0 bar and MQTT published
it. No A10 frame in the diagnostics bundles carries any of the four, so an A10 showed four flat
tyres on every poll. ABRP already leaves such a tyre out, and the Vehicle page shows none.

An absent pressure is now None, as an absent temperature is (#144): MQTT publishes an empty payload,
which the tyre sensors' discovery turns into `unknown`. A pressure the car sent goes out as before.
"""
import pytest

M = pytest.importorskip("mqtt", reason="poller.mqtt needs paho")

import client

VIN = "TYRETESTVIN"
TYRES = ("tire_fl", "tire_fr", "tire_rl", "tire_rr")
_ABSENT = object()


class _FakeClient:
    def __init__(self):
        self.published = {}

    def publish(self, topic, payload, retain=False):
        self.published[topic] = (payload, retain)


def _published(front_left=_ABSENT):
    sig = {"1": 1793903914581, "100003": 80.0, "1010": 0, "1319": 0, "1318": 3628, "3": 45.0, "2": 9.0}
    if front_left is not _ABSENT:
        sig["2646"] = front_left
    svc = M.MqttService("broker", 1883, topic_prefix="leapmotor", get_setting=lambda k, d="": d)
    svc.client = _FakeClient()
    svc._publish_sensors(client._parse_signal(VIN, sig))
    return svc.client.published


@pytest.mark.parametrize("sent", [_ABSENT, None, ""])
def test_a_tyre_the_car_did_not_send_is_published_empty(sent):
    published = _published(sent)
    assert {published[f"leapmotor/{VIN}/{key}"] for key in TYRES} == {("", True)}


@pytest.mark.parametrize("sent, payload", [(245, "2.45"), ("238", "2.38"), (0, "0.0")])
def test_a_tyre_pressure_the_car_sent_is_published(sent, payload):
    assert _published(sent)[f"leapmotor/{VIN}/tire_fl"] == (payload, True)
