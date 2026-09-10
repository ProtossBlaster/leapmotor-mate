"""The OTA update notice on MQTT (#277 @HaJeeEs).

Mate already scans the account inbox for a software-update message every 10 minutes and stores the
answer in settings; the Overview shows it. This publishes the same answer as a Home Assistant
binary_sensor so an automation can send a push the moment one arrives — without a single extra
cloud request, because nothing new is fetched.

Two facts these tests pin, because both are easy to lose:
  • the flag is ACCOUNT-level (the inbox is), while topics are per VIN — with two cars the same
    notice is published under both, exactly as the Overview shows it whichever car is selected;
  • it says "there is an update message in the inbox", not "your car has an update pending", and
    carries no version — so the title and the send time travel as attributes, and nothing else.
"""
import json
import types

import pytest

pytest.importorskip("paho.mqtt.client", reason="poller MQTT bridge needs paho (absent in minimal CI)")
import mqtt as M
from client import VehicleData

_TITLE = "Software update available"
_SENT_MS = "1780296848000"          # 2026-06-01T06:54:08Z


def _service(settings=None, prefix="leapmotor"):
    s = {"ota_available": "1", "ota_title": _TITLE, "ota_time": _SENT_MS}
    s.update(settings or {})
    svc = M.MqttService("broker", 1883, topic_prefix=prefix,
                        get_setting=lambda k, d="": s.get(k, d))
    svc.client = _Fake()
    return svc


class _Fake:
    def __init__(self):
        self.published = {}

    def publish(self, topic, payload, retain=False):
        self.published[topic] = payload

    def is_connected(self):
        return True


def _data(vin="VINTEST"):
    return VehicleData(
        vin=vin, timestamp_ms=0, soc=50, range_km=200, odometer_km=1000.0, speed_kmh=0,
        gear="P", vehicle_state="parked", charging_status=0, charge_power_kw=0.0,
        latitude=45.0, longitude=9.0, outside_temp=20, inside_temp=22, climate_target_temp=22,
        battery_min_temp=20, is_locked=True, climate_on=False, climate_cooling=False,
        climate_heating=False, climate_defrost=False, trunk_open=False, windows_open=False,
        sunshade_open=False, any_door_open=False, plug_connected=False,
        remaining_charge_min=0, charge_voltage_v=0.0, charge_current_a=0.0,
    )


def test_a_pending_notice_publishes_on():
    svc = _service()
    svc._publish_sensors(_data())
    assert svc.client.published["leapmotor/VINTEST/ota_notice"] == "ON"


def test_no_notice_publishes_off_not_an_empty_payload():
    """OFF, so the entity has a state from the first poll: a retained blank would leave the
    automation's trigger at `unknown` until the day an update finally arrived."""
    svc = _service({"ota_available": "0", "ota_title": "", "ota_time": ""})
    svc._publish_sensors(_data())
    assert svc.client.published["leapmotor/VINTEST/ota_notice"] == "OFF"


def test_the_title_and_the_send_time_travel_as_attributes():
    svc = _service()
    svc._publish_sensors(_data())
    attrs = json.loads(svc.client.published["leapmotor/VINTEST/ota_notice/attrs"])
    assert attrs["title"] == _TITLE
    assert attrs["sent"] == "2026-06-01T06:54:08+00:00"


def test_attributes_are_published_empty_when_there_is_no_notice():
    """The attributes must be cleared with the state. Left retained, a stale title would sit under
    an OFF entity for ever and read as if an update were still waiting."""
    svc = _service({"ota_available": "0", "ota_title": "", "ota_time": ""})
    svc._publish_sensors(_data())
    attrs = json.loads(svc.client.published["leapmotor/VINTEST/ota_notice/attrs"])
    assert attrs == {"title": None, "sent": None}


def test_a_broken_send_time_still_publishes_the_state():
    """Never let a malformed timestamp cost the notification itself."""
    svc = _service({"ota_time": "not-a-number"})
    svc._publish_sensors(_data())
    assert svc.client.published["leapmotor/VINTEST/ota_notice"] == "ON"
    assert json.loads(svc.client.published["leapmotor/VINTEST/ota_notice/attrs"])["sent"] is None


def test_it_is_announced_as_an_update_binary_sensor_with_its_attributes():
    svc = _service()
    svc.publish_discovery(_data())
    cfg = json.loads(svc.client.published[
        "homeassistant/binary_sensor/leapmotor_mate_vintest/ota_notice/config"])
    assert cfg["device_class"] == "update"
    assert cfg["state_topic"] == "leapmotor/VINTEST/ota_notice"
    assert cfg["json_attributes_topic"] == "leapmotor/VINTEST/ota_notice/attrs"
    assert cfg["payload_on"] == "ON" and cfg["payload_off"] == "OFF"


def test_an_install_without_settings_publishes_off_and_never_raises():
    """The bridge can be built without a get_setting hook (dev/demo). No notice, no crash."""
    svc = M.MqttService("broker", 1883)
    svc.client = _Fake()
    svc._publish_sensors(_data())
    assert svc.client.published["leapmotor/VINTEST/ota_notice"] == "OFF"


def test_two_cars_both_get_the_account_notice():
    """The inbox belongs to the account, not to a car: publishing it under one VIN only would hide
    it from whichever Home Assistant device the driver happens to look at."""
    svc = _service()
    svc._publish_sensors(_data("VINONE"))
    svc._publish_sensors(_data("VINTWO"))
    assert svc.client.published["leapmotor/VINONE/ota_notice"] == "ON"
    assert svc.client.published["leapmotor/VINTWO/ota_notice"] == "ON"
