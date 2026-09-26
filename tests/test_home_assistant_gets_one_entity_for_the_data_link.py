"""Home Assistant gets one entity that says whether Mate's readings can be trusted.

The bridge already publishes the car's own clock (`frame_ts`) and its age; what it could not say
is WHY the age grows — a refused session and a sleeping car both left every entity frozen. The
`data_link` sensor carries the Overview's judgement: fresh, no_new_data, age_unknown,
login_refused or fetch_failed, with since-when, the error and the next attempt as attributes.

Two things make it honest. It is published from every branch of the poll, not only from a
successful read, so a refused session reaches HA as `login_refused` and not as an entity that
quietly stopped moving. And it expires on its own: the one way it goes quiet is a poller that
has stopped, and `unavailable` is then the truth — the web's heartbeat check, in HA's terms.
"""
import json
import os

import db as D
import mqtt as M
import pytest
from poll_cycle_fixture import NOW, VIN, frame, make_poll, make_startup, poller_main

PM = poller_main("poller_main_link_entity")
_DISC = "homeassistant"


class FakeClient:
    def __init__(self, connected=True):
        self.sent = []
        self.connected = connected

    def is_connected(self):
        return self.connected

    def publish(self, topic, payload=None, retain=False, **kw):
        self.sent.append((topic, payload))

    def subscribe(self, *a, **k): pass
    def loop_start(self, *a, **k): pass


class Frame:
    """Only what discovery reads off a frame; anything else is absent, never a crash."""

    def __init__(self, vin="LFZB10LINK0000001"):
        self.vin = vin

    def __getattr__(self, name):
        return None


@pytest.fixture
def bridge():
    b = M.MqttService(broker="h", port=1883, discovery_enabled=True, get_setting=lambda *a, **k: "")
    b.client = FakeClient()
    return b


# ── discovery ─────────────────────────────────────────────────────────────────

def test_the_entity_expires_and_carries_attributes(bridge):
    bridge.publish_discovery(Frame())
    (payload,) = [p for t, p in bridge.client.sent
                  if t == f"{_DISC}/sensor/leapmotor_mate_lfzb10link0000001/data_link/config"]
    conf = json.loads(payload)
    assert conf["state_topic"] == "leapmotor/LFZB10LINK0000001/data_link"
    assert conf["expire_after"] == M._LINK_EXPIRE_S == 2 * 600 + 60, \
        "the web's heartbeat rule at the slowest cadence Settings allows: fixed here, set at discovery"
    assert conf["json_attributes_topic"] == "leapmotor/LFZB10LINK0000001/data_link/attributes"


def test_publish_link_announces_the_entity_itself_once(bridge):
    """Discovery rides on a frame; a poller that starts into an outage has none, and the entity
    must still exist for the state it publishes to land somewhere."""
    for _ in range(2):
        bridge.publish_link("LFZB10LINK0000001", "login_refused", {})
    configs = [p for t, p in bridge.client.sent
               if t == f"{_DISC}/sensor/leapmotor_mate_lfzb10link0000001/data_link/config"]
    assert len(configs) == 1
    conf = json.loads(configs[0])
    assert conf["expire_after"] == M._LINK_EXPIRE_S and conf["unique_id"] == "leapmotor_mate_lfzb10link0000001_data_link"
    assert conf["device"] == bridge._device("LFZB10LINK0000001")


# ── publishing ────────────────────────────────────────────────────────────────

def test_publish_link_writes_the_state_and_the_attributes(bridge):
    bridge.publish_link("LFZB10LINK0000001", "login_refused",
                        {"since": "2026-09-17T15:05:11+00:00", "reason": "RuntimeError: refused",
                         "next_retry_ts": 1.0, "bad_creds": False})
    sent = dict(bridge.client.sent)
    assert sent["leapmotor/LFZB10LINK0000001/data_link"] == "login_refused"
    attrs = json.loads(sent["leapmotor/LFZB10LINK0000001/data_link/attributes"])
    assert attrs["since"] == "2026-09-17T15:05:11+00:00" and attrs["bad_creds"] is False


def test_a_reconnect_announces_the_entity_again(bridge):
    """A broker that came back has forgotten what was not retained; discovery goes out once more
    on the next publish, as it does for the frame's entities."""
    bridge.publish_link("LFZB10LINK0000001", "fresh", {})
    bridge._on_connect(bridge.client, None, None, 0)
    bridge.publish_link("LFZB10LINK0000001", "fresh", {})
    configs = [t for t, _ in bridge.client.sent if t.endswith("/data_link/config")]
    assert len(configs) == 2


def test_nothing_is_published_while_the_broker_is_away(bridge):
    bridge.client = FakeClient(connected=False)
    bridge.publish_link("LFZB10LINK0000001", "fresh", {})
    assert bridge.client.sent == []


# ── the judgement, from what the poller holds ────────────────────────────────

@pytest.mark.parametrize("session, fetch, frame_age, expected, whose", [
    ("refused", "ok", 12, "login_refused", "s"),
    ("refused", "failed", None, "login_refused", "s"),
    # a login that never reached the cloud (it timed out on the way) fails every car's fetch
    ("failed", "ok", 12, "fetch_failed", "s"),
    ("failed", "failed", 12, "fetch_failed", "s"),
    ("ok", "failed", 12, "fetch_failed", "f"),
    ("ok", "ok", None, "age_unknown", "s"),
    ("ok", "ok", 12, "fresh", "s"),
    ("ok", "ok", 299, "fresh", "s"),
    ("ok", "ok", 300, "no_new_data", "s"),
    ("ok", "empty", 3600, "no_new_data", "s"),
    (None, None, 12, "fresh", None),
])
def test_the_session_is_judged_before_this_cars_fetch_before_the_frame(session, fetch, frame_age, expected, whose):
    acct = PM.AccountState(link={"state": session, "since": "s"} if session else None)
    state, attrs = PM._link_state(acct, {"state": fetch, "since": "f"} if fetch else None, frame_age)
    assert state == expected
    assert set(attrs) == {"since", "reason", "next_retry_ts", "bad_creds"}
    assert attrs["since"] == whose, "the attributes are the failing layer's"


# ── every branch of the poll publishes it ────────────────────────────────────

class _Recorder:
    """Stands in for the bridge: remembers every link publish."""

    def __init__(self):
        self.links = []

    def publish_link(self, vin, state, attrs):
        self.links.append((vin, state, attrs))


@pytest.fixture
def poll(tmp_path, monkeypatch):
    run = make_poll(PM, tmp_path, monkeypatch)
    rec = _Recorder()
    monkeypatch.setattr(PM, "_mqtt_connect", lambda db, client, service: rec)
    run.rec = rec
    return run


def test_a_fresh_frame_publishes_fresh(poll):
    poll(frame(int((NOW - 12) * 1000)))
    assert [s for _, s, _ in poll.rec.links] == ["fresh"]


def test_an_empty_answer_publishes_the_age_of_the_last_frame_on_record(poll):
    import client as _client
    poll(frame(int((NOW - 12) * 1000)))
    poll(_client.EmptyStatusError("no live signals"), advance=3600)
    assert [s for _, s, _ in poll.rec.links] == ["fresh", "no_new_data"]


def test_a_refused_session_publishes_login_refused_with_its_attributes(poll):
    poll.client.relogin_raises = RuntimeError("Leapmotor login failed: Error occurred")
    poll(ConnectionError("Read timed out"), advance=120)
    (_, state, attrs), = poll.rec.links
    assert state == "login_refused"
    assert attrs["since"] == PM._utc_iso()
    assert attrs["next_retry_ts"] == poll.acct.last_relogin + poll.acct.relogin_wait_s
    assert "login failed" in attrs["reason"]


def test_a_failing_fetch_publishes_fetch_failed(poll):
    poll(ConnectionError("Read timed out"), advance=120)
    assert [s for _, s, _ in poll.rec.links] == ["fetch_failed"]


def test_a_frame_the_recorder_could_not_store_publishes_fetch_failed(poll, monkeypatch):
    """The entity must not go on saying `fresh` — or quietly expire — over a frame that never
    reached the database."""
    def _broken(data):
        raise KeyError("soc")
    monkeypatch.setattr(poll.ctx.recorder, "process", _broken)
    poll(frame(int((NOW - 12) * 1000)))
    (_, state, attrs), = poll.rec.links
    assert state == "fetch_failed" and attrs["reason"] == "KeyError: 'soc'"


def test_a_failure_after_the_frame_is_stored_still_publishes(poll, monkeypatch):
    """The frame is on record and current; what broke after it must not leave the entity to expire."""
    def _broken(db, data):
        raise RuntimeError("comfort state")
    monkeypatch.setattr(PM, "_write_comfort_state", _broken)
    poll(frame(int((NOW - 12) * 1000)))
    assert [s for _, s, _ in poll.rec.links] == ["fresh"]


def test_a_startup_login_the_cloud_refuses_still_reaches_home_assistant(tmp_path, monkeypatch):
    """Restarted into an outage the poller knows its cars from the database; each one's entity
    hears `login_refused` at once and again before it would expire, for as long as the wait goes."""
    run = make_startup(PM, tmp_path, monkeypatch)
    D.Database(os.environ["DB_PATH"]).ensure_vehicle(VIN, "B10")
    rec = _Recorder()
    monkeypatch.setattr(PM, "_mqtt_connect", lambda db, client, service: rec)
    run(refusals=3)          # waits of 5, 10 and 20 s — one publish at the start of each
    assert [(v, s) for v, s, _ in rec.links] == [(VIN, "login_refused")] * 3
    assert all(a["next_retry_ts"] for _, _, a in rec.links)


def test_a_startup_login_that_timed_out_reaches_home_assistant_as_fetch_failed(tmp_path, monkeypatch):
    run = make_startup(PM, tmp_path, monkeypatch)
    D.Database(os.environ["DB_PATH"]).ensure_vehicle(VIN, "B10")
    rec = _Recorder()
    monkeypatch.setattr(PM, "_mqtt_connect", lambda db, client, service: rec)
    run(refusals=1, error=ConnectionError("Read timed out"))
    (_, state, attrs), = rec.links
    assert state == "fetch_failed" and attrs["reason"] == "ConnectionError: Read timed out"


def test_a_bridge_that_is_disabled_costs_nothing(poll, monkeypatch):
    monkeypatch.setattr(PM, "_mqtt_connect", lambda db, client, service: None)
    poll(frame(int((NOW - 12) * 1000)))
    assert poll.rec.links == []
