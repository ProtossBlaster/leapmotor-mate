"""Home Assistant gets an `update` entity for the car's software.

The poller keeps what the vehicle-update endpoint told the owner's account in a per-car setting.
Published as an MQTT `update` entity it lands where Home Assistant lists every other update, with
the installed version, the waiting one and the release notes. There is no `command_topic`: Mate
installs nothing, and without one Home Assistant offers no Install button.

A car the account does not own has no version to show, and an entity announced while Mate ran on
the owner's account must not go on showing that account's last answer: its retained state is
cleared, and its config where discovery is on.

It is published from every branch of the poll, not with the frame: the version is checked on its
own clock, and a car asleep for days sends no frame to carry it.
"""
import json

import pytest

pytest.importorskip("paho.mqtt.client", reason="poller MQTT bridge needs paho (absent in minimal CI)")
import mqtt as M
from poll_cycle_fixture import NOW, frame, make_poll, poller_main
from test_mqtt_ota_notice import _Fake

PM = poller_main("poller_main_software_entity")

VIN = "VINPOLLCYCLE00001"     # the poll fixture's car, so the poll-path tests below share it
CONFIG = "homeassistant/update/leapmotor_mate_vinpollcycle00001/software/config"
STATE = "leapmotor/VINPOLLCYCLE00001/software"


class _Recorder(_Fake):
    """Every publish in order, retained or not — a clear is an empty payload, and it counts."""

    def __init__(self):
        super().__init__()
        self.log = []

    def publish(self, topic, payload, retain=False):
        super().publish(topic, payload, retain)
        self.log.append((topic, payload, retain))


def _service(software=None, car_type="B10", discovery=True):
    s = {"ota_available": "0"}
    if software is not None:
        s[f"software_{VIN.lower()}"] = json.dumps(software)
    svc = M.MqttService("broker", 1883, discovery_enabled=discovery,
                        get_setting=lambda k, d="": s.get(k, d))
    svc.client = _Recorder()
    svc._car_facts[VIN] = (None, car_type)
    svc.settings = s
    return svc


UP_TO_DATE = {"state": "ok", "installed": "3.41.30", "installed_at_ms": 1790224725000,
              "latest": "3.41.30", "notes": "", "size_bytes": None}
WAITING = dict(UP_TO_DATE, latest="3.42.1", notes="Improvements\nCharging is smoother.",
               size_bytes=3 * 2**30)


def test_it_is_announced_as_a_firmware_update_without_an_install_button():
    svc = _service(UP_TO_DATE)
    svc.publish_software(VIN)
    conf = json.loads(svc.client.published[CONFIG])
    assert conf["device_class"] == "firmware"
    assert conf["state_topic"] == STATE
    assert "command_topic" not in conf and "payload_install" not in conf
    assert conf["unique_id"] == "leapmotor_mate_vinpollcycle00001_software"
    assert conf["device"] == svc._device(VIN)


def test_an_up_to_date_car_reads_as_up_to_date():
    svc = _service(UP_TO_DATE)
    svc.publish_software(VIN)
    state = json.loads(svc.client.published[STATE])
    assert state["installed_version"] == state["latest_version"] == "3.41.30"
    assert state["title"] == "Leapmotor B10 software"
    assert state["release_summary"] == "", \
        "a string, so a retained summary of an installed update is cleared — Home Assistant rejects null"


def test_a_waiting_update_brings_its_version_and_notes():
    svc = _service(WAITING)
    svc.publish_software(VIN)
    state = json.loads(svc.client.published[STATE])
    assert state["installed_version"] == "3.41.30" and state["latest_version"] == "3.42.1"
    assert state["release_summary"] == "Improvements\nCharging is smoother."


def test_long_release_notes_are_cut_to_what_home_assistant_accepts():
    svc = _service(dict(WAITING, notes="x" * 600))
    svc.publish_software(VIN)
    summary = json.loads(svc.client.published[STATE])["release_summary"]
    assert len(summary) == 255 and summary.endswith("…")


def test_the_entity_is_announced_once_not_every_poll():
    svc = _service(UP_TO_DATE)
    for _ in range(3):
        svc.publish_software(VIN)
    assert [t for t, _p, _r in svc.client.log].count(CONFIG) == 1
    assert [t for t, _p, _r in svc.client.log].count(STATE) == 3


@pytest.mark.parametrize("state", ["shared", "refused"])
def test_a_car_the_account_is_not_told_about_has_its_entity_cleared_once(state):
    svc = _service({"state": state, "checked_at": "2026-09-28T08:00:00+00:00"})
    for _ in range(2):
        svc.publish_software(VIN)
    clears = [(t, p, r) for t, p, r in svc.client.log if t in (CONFIG, STATE)]
    assert clears == [(CONFIG, "", True), (STATE, "", True)]


def test_moving_from_the_owners_account_clears_the_announced_entity():
    svc = _service(UP_TO_DATE)
    svc.publish_software(VIN)
    svc.settings[f"software_{VIN.lower()}"] = json.dumps({"state": "shared"})
    svc.publish_software(VIN)
    assert svc.client.published[CONFIG] == "" and svc.client.published[STATE] == ""


def test_nothing_is_published_before_the_first_answer():
    svc = _service(None)
    svc.publish_software(VIN)
    assert CONFIG not in svc.client.published and STATE not in svc.client.published


def test_an_install_without_discovery_still_gets_the_state():
    svc = _service(UP_TO_DATE, discovery=False)
    svc.publish_software(VIN)
    assert CONFIG not in svc.client.published
    assert json.loads(svc.client.published[STATE])["installed_version"] == "3.41.30"


def test_a_model_the_bridge_has_not_heard_of_still_gets_a_title():
    svc = _service(UP_TO_DATE, car_type="")
    svc.publish_software(VIN)
    assert json.loads(svc.client.published[STATE])["title"] == "Leapmotor software"


def test_an_installed_update_clears_its_notes():
    svc = _service(WAITING)
    svc.publish_software(VIN)
    svc.settings[f"software_{VIN.lower()}"] = json.dumps(dict(WAITING, installed="3.42.1", notes=""))
    svc.publish_software(VIN)
    state = json.loads(svc.client.published[STATE])
    assert state["installed_version"] == state["latest_version"] == "3.42.1"
    assert state["release_summary"] == ""


def test_without_discovery_a_refusal_still_clears_the_retained_state():
    """The state is published with discovery off, so it must be cleared with discovery off too —
    or the owner's version and its waiting update stay on the broker for ever."""
    svc = _service(WAITING, discovery=False)
    svc.publish_software(VIN)
    svc.settings[f"software_{VIN.lower()}"] = json.dumps({"state": "refused"})
    svc.publish_software(VIN)
    assert svc.client.published[STATE] == ""
    assert CONFIG not in svc.client.published


def test_nothing_is_published_while_the_broker_is_away():
    svc = _service(UP_TO_DATE)
    svc.client.is_connected = lambda: False
    svc.publish_software(VIN)
    assert svc.client.log == []


# ── every branch of the poll publishes it, the sleeping car's too ────────────

@pytest.fixture
def poll(tmp_path, monkeypatch):
    run = make_poll(PM, tmp_path, monkeypatch)
    bridge = M.MqttService("broker", 1883, get_setting=run.db.get_setting)
    bridge.client = _Recorder()
    monkeypatch.setattr(PM, "_mqtt_connect", lambda db, client, service: bridge)
    run.bridge = bridge
    return run


def test_a_car_that_sends_no_frame_still_gets_its_version_published(poll):
    import client as _client
    poll.db.set_setting(f"software_{VIN.lower()}", json.dumps(WAITING))
    poll(_client.EmptyStatusError("no live signals"))
    assert json.loads(poll.bridge.client.published[STATE])["latest_version"] == "3.42.1"
    assert json.loads(poll.bridge.client.published[CONFIG])["device_class"] == "firmware"


def test_a_refusal_learnt_while_the_car_sleeps_clears_the_entity(poll):
    import client as _client
    poll.db.set_setting(f"software_{VIN.lower()}", json.dumps(UP_TO_DATE))
    poll(frame(int((NOW - 12) * 1000)))
    poll.db.set_setting(f"software_{VIN.lower()}", json.dumps({"state": "refused"}))
    poll(_client.EmptyStatusError("no live signals"), advance=3600)
    assert poll.bridge.client.published[CONFIG] == "" and poll.bridge.client.published[STATE] == ""
