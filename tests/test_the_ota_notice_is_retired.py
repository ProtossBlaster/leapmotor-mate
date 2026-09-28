"""The OTA Update Notice is withdrawn — and withdrawn PROPERLY, which means an empty retained config.

#277 (@HaJeeEs) added a binary_sensor fed by the account message inbox. Measured 28/09/2026 across
three real owners' bundles, every successful scan in all of them:

    utente A (B10): 0 notices — 20 scans succeeded, 561 failed (login, TLS cert, HTTP)
    utente B (C10): 0 notices — "1 message(s) in inbox, none is an update notice" ×18
    utente C (T03): 0 notices — the same, ×6

Not once. The one message in those inboxes is the sharing invitation, and that is structural: Mate
is required to run on an account the car is SHARED with (README requirement #1 — one active session
per account, so the phone's official app and Mate would evict each other), and such an account
receives no vehicle notices at all. So the flag cannot turn on, and a sensor that cannot turn on is
not a quiet sensor, it is a promise of a notification that will never come.

🔴 The part that is easy to get wrong: the discovery config is RETAINED. Simply not publishing it
any more leaves it on the broker for ever, and every existing installation keeps the entity — frozen,
never updating, impossible for the user to remove. Withdrawing it means publishing an EMPTY payload
to the same config topic once, the way this bridge already retires a command it may not show and a
sensor for a feature the car has not got. The retained state topics go the same way, or an "OFF"
(or worse, a stale "ON") outlives the entity that explained it.
"""
import pytest

pytest.importorskip("paho.mqtt.client", reason="poller MQTT bridge needs paho (absent in minimal CI)")
import mqtt as M
from client import VehicleData

VIN = "LFZTEST0000000001"
_DEV = f"leapmotor_mate_{VIN.lower()}"


class _Fake:
    def __init__(self):
        self.published = {}

    def publish(self, topic, payload, retain=False):
        self.published[topic] = payload

    def is_connected(self):
        return True


def _discovery():
    svc = M.MqttService("broker", 1883, topic_prefix="leapmotor",
                        get_setting=lambda k, d="": {"ota_available": "1", "ota_title": "Update",
                                                     "ota_time": "1780296848000"}.get(k, d))
    svc.client = _Fake()
    svc.publish_discovery(VehicleData(
        vin=VIN, timestamp_ms=0, soc=50, range_km=200, odometer_km=1000.0, speed_kmh=0,
        gear="P", vehicle_state="parked", charging_status=0, charge_power_kw=0.0,
        latitude=45.0, longitude=9.0, outside_temp=20, inside_temp=22, climate_target_temp=22,
        battery_min_temp=20, is_locked=True, climate_on=False, climate_cooling=False,
        climate_heating=False, climate_defrost=False, trunk_open=False, windows_open=False,
        sunshade_open=False, any_door_open=False, plug_connected=False,
        remaining_charge_min=0, charge_voltage_v=0.0, charge_current_a=0.0))
    return svc.client.published


def test_the_entity_is_withdrawn_from_every_installation_that_has_it():
    pub = _discovery()
    topic = f"homeassistant/binary_sensor/{_DEV}/ota_notice/config"
    assert topic in pub, "the config topic is never touched — the retained entity lives on for ever"
    assert pub[topic] == "", f"an empty payload withdraws it; got {pub[topic]!r}"


def test_no_stale_state_is_left_under_the_withdrawn_entity():
    pub = _discovery()
    for topic in (f"leapmotor/{VIN}/ota_notice", f"leapmotor/{VIN}/ota_notice/attrs"):
        assert pub.get(topic, None) == "", \
            f"{topic} keeps its retained value after the entity is gone: {pub.get(topic)!r}"


def test_the_bridge_no_longer_has_a_publisher_for_it():
    assert not hasattr(M.MqttService, "_publish_ota_notice"), \
        "the publisher is still there — a later change will start feeding the withdrawn entity again"


def test_the_poller_no_longer_asks_the_cloud_for_the_inbox():
    """One request every ten minutes that produced nothing on three real installations, and on one
    of them failed 561 times out of 581."""
    import client as C
    import main as PM
    assert not hasattr(C.LeapmotorMateClient, "check_ota"), "the inbox scan is still in the client"
    assert not hasattr(PM, "_maybe_check_ota"), "the poller still calls the inbox scan"


def test_the_status_no_longer_carries_an_ota_field():
    import db_reader
    assert not hasattr(db_reader, "get_ota_status"), "the web still reads a flag nothing writes"
