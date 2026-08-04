"""Two Mate installs, one MQTT broker (@ebagnoli, BetaTester #13).

He had MQTT on both the official add-on and the BetaTester one, same parameters, and reported that
the beta *"non ha mai visto la luce"*. It had. The discovery device id is built from the topic prefix
and the VIN, so two installs on the same prefix watching the same car are **indistinguishable** to
Home Assistant: one device, and the second one just rewrites the first one's configs over
themselves. Nothing to see, and nothing that says why.

The part nobody noticed is worse than the invisible one: both subscribe to `<prefix>/+/command`, and
the handler passed the topic's VIN straight to the cloud API. One button press in Home Assistant was
executed **twice**, by two different Leapmotor accounts, on the same car — and since both installs
publish near-identical states, the duplication left no trace on screen.

Three things had to be true for the fix to work at all, and the first one was a defect of its own:
the bridge never re-read its settings, so changing the prefix did nothing while the page answered
"Saved — restart not needed". That was told to a tester as advice before it was checked.
"""
import importlib.util
import json
import pathlib

import pytest

import db as D
import mqtt as pmqtt

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _poller_main():
    """Load poller/main.py under its own name — a bare `import main` gives web/main.py, because
    conftest puts both directories on the path and the web one wins. Alone the file passed; in the
    full suite `main` was already imported and every assertion here ran against the wrong module.
    Same two-main.py trap that once cost 225 phantom type errors."""
    spec = importlib.util.spec_from_file_location("poller_main", ROOT / "poller" / "main.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pmain = _poller_main()

SETTINGS = (ROOT / "web" / "templates" / "settings.html").read_text()
LOCALES = sorted((ROOT / "web" / "locales").glob("*.json"))


# ── the bridge has to follow its own settings ─────────────────────────────────

class _StubService:
    """Stands in for MqttService: records what it was built with, never touches a network."""
    built = []

    def __init__(self, **kw):
        _StubService.built.append(kw)
        self.config_sig = None
        self.on_command = None
        self.on_collision = None
        self.disconnected = False

    def publish_status(self, data):
        pass

    def disconnect(self):
        self.disconnected = True


@pytest.fixture
def db(tmp_path, monkeypatch):
    _StubService.built = []
    monkeypatch.setattr(pmain, "MqttService", _StubService)
    d = D.Database(str(tmp_path / "t.db"))
    d.set_setting("mqtt_enabled", "1")
    d.set_setting("mqtt_broker", "broker.local")
    d.set_setting("mqtt_prefix", "leapmotor")
    return d


def test_an_unchanged_configuration_keeps_the_same_bridge(db):
    first = pmain._mqtt_tick(db, None, None, None)
    assert pmain._mqtt_tick(db, None, None, first) is first
    assert not first.disconnected


def test_changing_the_prefix_reconnects_it(db):
    """The one that was false. The service was built once and never rebuilt, so a new prefix sat in
    the database doing nothing — while the page answered "Saved — restart not needed"."""
    first = pmain._mqtt_tick(db, None, None, None)
    db.set_setting("mqtt_prefix", "leapmotor_beta")
    second = pmain._mqtt_tick(db, None, None, first)
    assert second is not first, "the bridge kept running on the old prefix"
    assert first.disconnected, "the old connection was left open"
    assert _StubService.built[-1]["topic_prefix"] == "leapmotor_beta"


@pytest.mark.parametrize("key,value", [
    ("mqtt_broker", "other.local"), ("mqtt_port", "8883"), ("mqtt_user", "u"),
    ("mqtt_tls", "1"), ("mqtt_tls_insecure", "1"), ("mqtt_discovery", "0"),
])
def test_every_connection_setting_reconnects_it(db, key, value):
    """Not just the prefix: the broker, the port, the credentials and TLS were all read once."""
    first = pmain._mqtt_tick(db, None, None, None)
    db.set_setting(key, value)
    assert pmain._mqtt_tick(db, None, None, first) is not first, f"{key} did not take effect"


def test_the_password_counts_too(db):
    first = pmain._mqtt_tick(db, None, None, None)
    db.set_secret("mqtt_pass", "hunter2")
    assert pmain._mqtt_tick(db, None, None, first) is not first


def test_turning_mqtt_off_still_drops_the_bridge(db):
    first = pmain._mqtt_tick(db, None, None, None)
    db.set_setting("mqtt_enabled", "0")
    assert pmain._mqtt_tick(db, None, None, first) is None and first.disconnected


# ── hearing the other install ─────────────────────────────────────────────────

class _FakeClient:
    def __init__(self):
        self.published = []
        self.subscribed = []

    def publish(self, topic, payload, retain=False):
        self.published.append((topic, payload, retain))

    def subscribe(self, topic):
        self.subscribed.append(topic)


def _svc(instance_id="mine", is_beta=False, prefix="leapmotor"):
    s = pmqtt.MqttService("b", 1883, topic_prefix=prefix,
                          instance_id=instance_id, is_beta=is_beta)
    s.client = _FakeClient()
    return s


def test_our_own_beacon_coming_back_is_not_a_collision():
    """We publish it and we are subscribed to it — the echo must be silent, or every install would
    accuse itself."""
    seen = []
    s = _svc()
    s.on_collision = lambda *a: seen.append(a)
    s._handle_beacon("VIN1", json.dumps({"id": "mine", "beta": False}))
    assert seen == []


def test_a_foreign_beacon_is_a_collision():
    seen = []
    s = _svc()
    s.on_collision = lambda *a: seen.append(a)
    s._handle_beacon("VIN1", json.dumps({"id": "theirs", "beta": True}))
    assert seen == [("theirs", True, "VIN1")]


@pytest.mark.parametrize("payload", ["", "not json", "[]", "{}"])
def test_a_beacon_that_makes_no_sense_is_ignored(payload):
    seen = []
    s = _svc()
    s.on_collision = lambda *a: seen.append(a)
    s._handle_beacon("VIN1", payload)
    assert seen == []


def test_the_beacon_is_never_retained():
    """A retained one would keep accusing an install that was removed months ago. Unretained means
    "it arrived, therefore they are publishing right now" — no timestamps, no clock agreement."""
    s = _svc()
    s._publish_sensors(_data())
    beacon = [p for p in s.client.published if p[0].endswith("/mate_instance")]
    assert len(beacon) == 1, "the beacon is not being published"
    topic, payload, retain = beacon[0]
    assert topic == "leapmotor/VIN1/mate_instance"
    assert retain is False
    assert json.loads(payload) == {"id": "mine", "beta": False}


def test_an_install_with_no_identity_says_nothing():
    s = _svc(instance_id="")
    s._publish_sensors(_data())
    assert not [p for p in s.client.published if p[0].endswith("/mate_instance")]


def test_it_listens_for_the_others(monkeypatch):
    s = _svc()
    s._on_connect(s.client, None, None, 0)
    assert "leapmotor/+/mate_instance" in s.client.subscribed


# ── and refusing a command that is not ours ───────────────────────────────────

class _Msg:
    def __init__(self, topic, payload=""):
        self.topic = topic
        self.payload = payload.encode()


def test_a_command_for_another_car_is_refused():
    """The topic is a wildcard and the VIN went straight to the cloud API. Two installs sharing a
    prefix each executed the other's commands, against a car that is not theirs."""
    ran = []
    s = _svc()
    s.on_command = lambda *a: ran.append(a)
    s._own_vins.add("VIN1")
    s._on_message(None, None, _Msg("leapmotor/VIN2/command", "lock"))
    assert ran == []


def test_our_own_car_still_obeys():
    ran = []
    s = _svc()
    s.on_command = lambda *a: ran.append(a)
    s._own_vins.add("VIN1")
    s._on_message(None, None, _Msg("leapmotor/VIN1/command", "lock"))
    assert ran == [("VIN1", "lock", None)]


def test_before_we_have_published_anything_we_do_not_refuse():
    """`_own_vins` fills on the first publish. An empty set must not mean "refuse everything" —
    that would break a command arriving in the seconds after a reconnect."""
    ran = []
    s = _svc()
    s.on_command = lambda *a: ran.append(a)
    s._on_message(None, None, _Msg("leapmotor/VIN1/command", "lock"))
    assert ran == [("VIN1", "lock", None)]


# ── who gets out of the way ───────────────────────────────────────────────────

def test_the_betatester_moves_itself(tmp_path, monkeypatch):
    monkeypatch.setattr(pmain, "_research_enabled", lambda: True)
    d = D.Database(str(tmp_path / "t.db"))
    d.set_setting("mqtt_prefix", "leapmotor")
    pmain._handle_mqtt_collision(d, "theirs", False, "VIN1")
    assert d.get_setting("mqtt_prefix") == "leapmotor_beta"


def test_the_official_build_never_moves(tmp_path, monkeypatch):
    """Its entities are the ones with automations pointing at them. Renaming those by itself would
    break a working setup to fix a problem the user has not seen yet."""
    monkeypatch.setattr(pmain, "_research_enabled", lambda: False)
    d = D.Database(str(tmp_path / "t.db"))
    d.set_setting("mqtt_prefix", "leapmotor")
    pmain._handle_mqtt_collision(d, "theirs", True, "VIN1")
    assert d.get_setting("mqtt_prefix") == "leapmotor"
    assert json.loads(d.get_setting("mqtt_collision"))["moved_to"] == ""


def test_it_moves_once_and_then_stops(tmp_path, monkeypatch):
    """Already carrying the suffix and still colliding means a second BetaTester on the same broker.
    Appending for ever would be a slow-motion loop."""
    monkeypatch.setattr(pmain, "_research_enabled", lambda: True)
    d = D.Database(str(tmp_path / "t.db"))
    d.set_setting("mqtt_prefix", "leapmotor")
    pmain._handle_mqtt_collision(d, "theirs", False, "VIN1")
    pmain._handle_mqtt_collision(d, "theirs", False, "VIN1")
    assert d.get_setting("mqtt_prefix") == "leapmotor_beta"


def test_what_happened_is_written_down_for_the_page(tmp_path, monkeypatch):
    monkeypatch.setattr(pmain, "_research_enabled", lambda: True)
    d = D.Database(str(tmp_path / "t.db"))
    d.set_setting("mqtt_prefix", "leapmotor")
    pmain._handle_mqtt_collision(d, "theirs", False, "VIN1")
    rec = json.loads(d.get_setting("mqtt_collision"))
    assert rec["prefix"] == "leapmotor" and rec["moved_to"] == "leapmotor_beta"
    assert rec["vin"] == "VIN1" and rec["at"]


# ── and what the page says ────────────────────────────────────────────────────

def test_the_prefix_field_finally_explains_itself():
    """It is the box that separates two installs, and it carried no explanation at all."""
    assert "t('mqtt_prefix_hint')" in SETTINGS


def test_the_warning_sits_next_to_the_field_that_fixes_it():
    i = SETTINGS.index('name="mqtt_prefix"')
    assert 0 < SETTINGS.index("mqtt_collision_warn") - i < 900


@pytest.mark.parametrize("path", LOCALES, ids=lambda p: p.stem)
def test_every_language_has_the_words(path):
    d = json.loads(path.read_text())["translations"]
    for key in ("mqtt_prefix_hint", "mqtt_collision_warn", "mqtt_collision_moved"):
        assert d.get(key), f"{path.stem} is missing {key}"
    assert "{prefix}" in d["mqtt_collision_warn"]
    assert "{prefix}" in d["mqtt_collision_moved"]


# ── a minimal VehicleData stand-in ────────────────────────────────────────────

def _data():
    """Built from the real dataclass, so a new required field cannot make these pass for the wrong
    reason. Zeros rather than Nones: _publish_sensors does arithmetic on some of them, and a fixture
    that dies on unrelated maths proves nothing about the beacon."""
    import dataclasses

    from client import VehicleData
    kw = {}
    for f in dataclasses.fields(VehicleData):
        if f.default is not dataclasses.MISSING or f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            continue
        kw[f.name] = "VIN1" if f.name == "vin" else 0
    return VehicleData(**kw)
