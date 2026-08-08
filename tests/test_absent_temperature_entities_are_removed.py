"""#144, the other half: a sensor the car does not have loses its Home Assistant ENTITY too.

The discussion @staffhotel-beep opened is titled *"Unsupported entities for T03 model"* — the
complaint is about the entities. Hiding a row in the web UI and leaving behind an HA sensor that
reads `unknown` for ever answers half of it, and the half he did not ask about.

The mechanism is the one the seat entities already use (v2.6.1): a retained EMPTY discovery config,
which tells HA to drop the entity. → [[feedback-gate-a-feature-find-every-copy]]
"""
import json

import db as D                     # poller/db.py
import mqtt as M                   # poller/mqtt.py
import pytest

_DISC = "homeassistant"


class FakeClient:
    """Records every publish. `is_connected` is True because the bridge refuses to do anything at
    all until it is — a False here made an early version of these tests green over zero publishes."""
    def __init__(self):
        self.sent = []

    def is_connected(self):
        return True

    def publish(self, topic, payload=None, retain=False, **kw):
        self.sent.append((topic, payload))

    def subscribe(self, *a, **k): pass
    def loop_start(self, *a, **k): pass


class Frame:
    """Only what the bridge reads off a frame. Hand-written on purpose: importing VehicleData drags
    in the poller's client, and every attribute the bridge touches is exercised below anyway."""
    def __init__(self, vin="LFZT03TEST000001"):
        self.vin = vin
        for name in ("soc", "range_km", "odometer_km", "speed_kmh", "charge_power_kw",
                     "charge_voltage_v", "charge_current_a", "remaining_charge_min",
                     "charge_limit_percent", "fan_level", "tire_fl_bar", "tire_fr_bar",
                     "tire_rl_bar", "tire_rr_bar", "inside_temp", "battery_min_temp",
                     "climate_target_temp"):
            setattr(self, name, None)
        self.gear = "P"
        self.vehicle_state = "parked"
        self.charging_status = 0
        self.ac_port_mode = 0
        self.charge_current_a = 0.0
        self.charge_voltage_v = 0.0
        for flag in ("ready", "is_locked", "climate_on", "recirculation", "plug_connected",
                     "security_active"):
            setattr(self, flag, False)
        self.climate_mode_label = ""

    def __getattr__(self, name):        # anything else the bridge reads → absent, never a crash
        return None


@pytest.fixture
def bridge():
    b = M.MqttService(broker="h", port=1883, discovery_enabled=True, get_setting=lambda *a, **k: "")
    b.client = FakeClient()
    return b


def _configs(bridge, key):
    """Every discovery payload published for one entity key, in order."""
    tail = f"/{key}/config"
    return [p for t, p in bridge.client.sent if t.startswith(_DISC) and t.endswith(tail)]


# ── the entity goes away, and comes back ──────────────────────────────────────

@pytest.mark.parametrize("key", ["inside_temp", "ac_target_temp", "battery_temp"])
def test_a_sensor_the_car_never_reports_loses_its_entity(bridge, key):
    bridge.publish_status(Frame(), absent_temps={key})
    assert _configs(bridge, key) == [""], "an empty retained config is what removes it from HA"


def test_the_sensors_that_work_keep_theirs(bridge):
    bridge.publish_status(Frame(), absent_temps={"inside_temp"})
    for key, unit in (("ac_target_temp", "°C"), ("battery_temp", "°C")):
        cfg = json.loads(_configs(bridge, key)[-1])
        assert cfg["device_class"] == "temperature" and cfg["unit_of_measurement"] == unit


def test_nothing_measured_yet_removes_nothing(bridge):
    """🔑 `None` is not an empty set. A fresh install, a DB that cannot be read, an older poller —
    all arrive here with no answer, and none of them is a reason to delete a working entity.
    → [[signal-absent-is-not-signal-zero]]"""
    bridge.publish_status(Frame())
    for key in ("inside_temp", "ac_target_temp", "battery_temp"):
        assert _configs(bridge, key) and _configs(bridge, key)[-1] != ""


def test_the_answer_changing_reaches_home_assistant_without_a_reconnect(bridge):
    """⚠️ The trap in this whole design. Discovery runs ONCE per connection, and this answer needs 50
    polls of evidence before it says anything — so the first pass always shows all three, and the
    removal falls due half an hour later. Bound to discovery alone, the entity would only go away at
    the next restart."""
    bridge.publish_status(Frame(), absent_temps=set())               # below the floor → all shown
    assert _configs(bridge, "inside_temp")[-1] != ""
    bridge.publish_status(Frame(), absent_temps={"inside_temp"})      # …evidence arrives
    assert _configs(bridge, "inside_temp")[-1] == "", "removed on the poll that learned it"
    bridge.publish_status(Frame(), absent_temps=set())                # …and it starts working again
    assert _configs(bridge, "inside_temp")[-1] != "", "the entity comes back"


def test_no_new_measurement_leaves_the_last_answer_standing(bridge):
    """The distinction the test above almost lost: `None` is not `set()`. A poll that brings no
    answer — an unreadable DB, a caller that does not measure — must neither delete an entity nor
    silently resurrect one that was removed on evidence."""
    bridge.publish_status(Frame(), absent_temps={"inside_temp"})
    before = len(_configs(bridge, "inside_temp"))
    bridge.publish_status(Frame())                                    # no answer this time
    assert len(_configs(bridge, "inside_temp")) == before, "nothing new to say, so nothing is said"
    assert _configs(bridge, "inside_temp")[-1] == "", "and the removal stands"


def test_an_unchanged_answer_is_not_republished_every_poll(bridge):
    """Retained configs are re-read by every HA restart; publishing three of them every 30 s would
    be noise on the broker for no new information."""
    for _ in range(5):
        bridge.publish_status(Frame(), absent_temps={"inside_temp"})
    assert len(_configs(bridge, "inside_temp")) == 1


def test_the_device_is_the_same_one_the_other_entities_hang_off(bridge):
    """A second, drifted device descriptor would put these three sensors under a SECOND HA device
    for the same car. Same identifiers, or the fix is a new defect."""
    bridge.publish_status(Frame(), absent_temps=set())
    temp = json.loads(_configs(bridge, "inside_temp")[-1])
    soc = json.loads(_configs(bridge, "soc")[-1])
    assert temp["device"] == soc["device"]


# ── the measurement behind it ─────────────────────────────────────────────────

def _polls(database, n, **cols):
    keys = ", ".join(["vehicle_id", "recorded_at", "soc"] + list(cols))
    marks = ", ".join(["?"] * (3 + len(cols)))
    for i in range(n):
        database._conn.execute(f"INSERT INTO positions ({keys}) VALUES ({marks})",
                               (1, f"2026-07-01T{i // 60:02d}:{i % 60:02d}:00+00:00", 50.0,
                                *cols.values()))
    database._conn.commit()


@pytest.fixture
def database(tmp_path):
    d = D.Database(str(tmp_path / "p.db"))
    d._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'VIN_T03','T03')")
    d._conn.commit()
    return d


def test_the_poller_measures_the_same_thing_the_page_does(database):
    _polls(database, 200, inside_temp=None, battery_min_temp=31.0, climate_target_temp=None)
    absent = database.never_reported_temps()
    assert absent == {"inside_temp", "ac_target_temp"}, "MQTT topic keys, not the web's"


def test_a_fresh_install_deletes_nothing(database):
    """The floor, again — and it matters more here than on the page: a hidden row comes back on the
    next render, a deleted HA entity takes its history and every automation referencing it."""
    _polls(database, 10, inside_temp=None, battery_min_temp=None, climate_target_temp=None)
    assert database.never_reported_temps() == set()


def test_one_reading_is_enough_to_keep_the_entity(database):
    _polls(database, 199, inside_temp=None)
    _polls(database, 1, inside_temp=24.0)
    assert "inside_temp" not in database.never_reported_temps()


def test_a_database_without_the_table_says_nothing(database):
    """A partial or migrating DB must not answer "everything is absent" and wipe the entities."""
    database._conn.execute("DROP TABLE positions")
    assert database.never_reported_temps() == set()


def test_the_poll_loop_actually_hands_the_measurement_to_the_bridge(database, monkeypatch):
    """🔑 The wiring, which is the one line no other test here touches: `db.never_reported_temps()`
    measured and `publish_status` gated are both correct and still do nothing if the loop never
    passes one to the other. → [[feedback-a-successful-call-is-not-a-correct-result]]"""
    import importlib.util
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("poller_main_144", root / "poller" / "main.py")
    pmain = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pmain)      # under its own name — a bare `main` is the WEB's

    _polls(database, 200, inside_temp=None, battery_min_temp=31.0, climate_target_temp=None)
    for k, v in (("mqtt_enabled", "1"), ("mqtt_broker", "h"), ("mqtt_discovery", "1")):
        database.set_setting(k, v)
    seen = {}
    service = M.MqttService(broker="h", port=1883, discovery_enabled=True)
    service.client = FakeClient()
    # ⚠️ Without the matching signature the tick REBUILDS the service, and the assertion below then
    # watches an object the loop threw away — green code, wrong subject.
    service.config_sig = pmain._mqtt_config_sig(database)
    monkeypatch.setattr(service, "publish_status",
                        lambda data, absent_temps=None: seen.update(got=absent_temps))
    pmain._mqtt_tick(database, None, Frame(), service)
    assert seen.get("got") == {"inside_temp", "ac_target_temp"}


def test_the_two_sides_describe_the_same_three_columns():
    """🔴 Two copies of one rule, in two trees that cannot import each other. The keys differ on
    purpose — the MQTT ones are entity ids people already have — but the COLUMNS must not, or the
    page and Home Assistant will disagree about the same car. This test is the only thing holding
    them together. → [[feedback-gate-a-feature-find-every-copy]]"""
    import db_reader
    assert set(D.ABSENT_TEMP_COLUMNS.values()) == set(db_reader._OPTIONAL_TEMPS.values())
    assert D.ABSENT_TEMP_MIN_POLLS == db_reader._ABSENT_SENSOR_MIN_POLLS
    assert D.ABSENT_TEMP_WINDOW == db_reader._ABSENT_SENSOR_WINDOW
