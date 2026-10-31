"""A car's fan has as many speeds as the car declares (#445).

Each car's vehicle-list entry carries a `funcConfig`, and a B10's reads
`{"HVAC": {"fan": {"max": "9", "min": "1", "unit": "gear"}, ...}, "valid": true}`: nine speeds, and
signal 1941 reads 9 on that car. Mate took 1..7 from every car, so the Vehicle page said "9 / 7", the
Commands slider and Home Assistant's Fan Level number ended at 7, and a new temperature or
recirculation sent at speed 9 turned the fan down to 7. The top now comes from the library's climate
contract, which takes 1..7 from every car and a higher declared top too, so a car that declares none,
or fewer, keeps 1..7. It is read from the car's entry in the last vehicle list, the one a command is
checked against, so the controls follow the list and are there from the start.
"""
import json
import re
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi", reason="web.main needs fastapi")
from events_fixture import VIN, Car, web
from test_absent_temperature_entities_are_removed import FakeClient, Frame
from test_the_climate_context_is_this_cars import PM

NINE = {"HVAC": {"fan": {"max": "9", "min": "1", "unit": "gear"},
                 "temperature": {"max": "32", "min": "18", "unit": "celsius"}},
        "valid": True}   # as a B10's vehicle-list entry carries it
FIVE = dict(NINE, HVAC=dict(NINE["HVAC"], fan=dict(NINE["HVAC"]["fan"], max="5")))


class Cloud:
    """The cloud as the adapter reaches it: a climate body goes through the library's contract for
    this car, and is recorded only if the contract takes it."""
    def __init__(self, func_config=None):
        import mate_api  # noqa: F401 — puts the vendored library on the path
        from leapmotor_cloud.mate_compat import Vehicle
        raw = {"vin": VIN, "carType": "B10", "abilities": [6]}
        if func_config is not None:
            raw["funcConfig"] = func_config
        self.car = Vehicle.from_dict(raw, False)
        self.sent = []

    def _remote_control(self, *, vin, action, cmd_content):
        from leapmotor_cloud.command_contracts import prepare
        body = json.loads(cmd_content)
        prepare("170", body, self.car)            # raises when the contract refuses the body
        self.sent.append(body)

    def execute(self, fn):                         # the web's command session
        fn(self, VIN)
        return True, "ok"

    def _target(self):
        return None


def _listed(monkeypatch, path, *cars):
    """The adapter reads the cloud's vehicle list: (vin, funcConfig or None) per car."""
    import api_v2_bridge
    from test_cloud_access_refresh import client
    monkeypatch.setattr(api_v2_bridge, "DB", path)
    api = client()
    entries = [{"vin": vin, "carType": "B10", **({"funcConfig": cfg} if cfg else {})} for vin, cfg in cars]
    api.read = lambda path, body, **kw: {"data": {"bindcars": entries}}
    api._get_vehicle_list()


def _car(tmp_path, monkeypatch, func_config, **signals):
    car = Car(tmp_path)
    _listed(monkeypatch, car.path, (VIN, func_config))
    client = web(car, monkeypatch)
    car.frame(**signals)
    return car, client


@pytest.mark.parametrize("func_config, fan, shown", [(NINE, 9, "9 / 9"), (None, 5, "5 / 7")])
def test_the_climate_card_counts_to_the_cars_top_speed(tmp_path, monkeypatch, func_config, fan, shown):
    import main
    _, client = _car(tmp_path, monkeypatch, func_config)
    monkeypatch.setattr(main.command_client, "get_fresh_signals", lambda: {"1941": fan})
    page = re.sub(r"<[^>]+>|\s+", " ", client.get("/api/vehicle-status").text)
    assert re.search(r"Fan\s+" + re.escape(shown), page), page[page.find("Fan"):][:80]


@pytest.mark.parametrize("func_config, top", [(NINE, 9), (None, 7), (FIVE, 7)], ids=["nine", "none", "five"])
def test_the_commands_slider_spans_every_speed_the_car_takes(tmp_path, monkeypatch, func_config, top):
    _, client = _car(tmp_path, monkeypatch, func_config, **{"1941": 3})
    page = client.get("/api/cmd-grid").text
    tile = page[page.index('id="clim-fan-val"'):page.index('id="clim-fan-res"')]
    assert re.search(rf'</span> / {top}</span>', tile), "the badge counts to the top speed"
    assert f'min="1" max="{top}"' in tile, "the slider ends at it"
    assert re.search(rf"<span>1</span><span>{top}</span>", tile), "and so does its scale"


def test_the_fan_slider_sends_nine(tmp_path, monkeypatch):
    import main
    car, client = _car(tmp_path, monkeypatch, NINE, **{"1941": 3})
    cloud = Cloud(NINE)
    monkeypatch.setattr(main.command_client, "_session", cloud)
    answer = client.post("/api/climate-fan", data={"level": "9"}).text
    assert [b["windlevel"] for b in cloud.sent] == ["9"]
    assert "✓ 9/9" in answer


@pytest.mark.parametrize("change", ["temperature", "recirculation"])
def test_another_setting_keeps_the_fan_at_nine(tmp_path, monkeypatch, change):
    import command_client
    _car(tmp_path, monkeypatch, NINE, **{"1941": 9, "3713": 1})
    cloud = Cloud(NINE)
    monkeypatch.setattr(command_client, "_session", cloud)
    ok, _ = command_client.set_climate_temp(22) if change == "temperature" else command_client.set_recirc(True)
    assert ok and [b["windlevel"] for b in cloud.sent] == ["9"]


# ── Home Assistant ───────────────────────────────────────────────────────────

NINE_VIN, SEVEN_VIN = "LFZB10NINE0000001", "LFZB10SEVEN000002"


def _bridge(tmp_path, monkeypatch, *, polled):
    """The poller's bridge, two cars listed: one declares nine speeds, one none. `polled`: after one
    poll of each, else just restarted, with neither car's first reading in yet."""
    import db as D
    import mqtt as M
    monkeypatch.setattr(M.MqttService, "_command_visible", lambda self, vin, key: True)
    database = D.Database(str(tmp_path / "p.db"))
    for k, v in (("mqtt_enabled", "1"), ("mqtt_broker", "h"), ("mqtt_discovery", "1")):
        database.set_setting(k, v)
    _listed(monkeypatch, str(tmp_path / "p.db"), (NINE_VIN, NINE), (SEVEN_VIN, None))
    service = M.MqttService(broker="h", port=1883, discovery_enabled=True, get_setting=database.get_setting)
    service.client = FakeClient()
    service.config_sig = PM._mqtt_config_sig(database)
    vids = {}
    for vin in (NINE_VIN, SEVEN_VIN):
        vids[vin] = database.ensure_vehicle(vin, "B10")
        database._conn.execute("INSERT INTO positions (vehicle_id, recorded_at, climate_mode, recirculation, "
                               "fan_level, climate_target_temp) VALUES (?, '2026-10-30T10:00:00+00:00', 1, 0, 9, 22)",
                               (vids[vin],))
        database._conn.commit()
        if polled:
            _poll(database, service, vin, vids[vin])
        else:
            service.publish_link(vin, "fetch_failed", {})
    return database, service, vids


def _poll(database, service, vin, vid):
    PM._mqtt_tick(database, None, Frame(vin), service, SimpleNamespace(abilities=None, car_type="B10"), vid)


def _fan_numbers(service, vin):
    tail = f"leapmotor_mate_{vin.lower()}/fan_level/config"
    return [json.loads(p) for t, p in service.client.sent if t.endswith(tail)]


def test_home_assistant_offers_each_car_its_own_speeds(tmp_path, monkeypatch):
    _db, service, _ = _bridge(tmp_path, monkeypatch, polled=True)
    assert [(n["min"], n["max"]) for n in _fan_numbers(service, NINE_VIN)] == [(1, 9)]
    assert [(n["min"], n["max"]) for n in _fan_numbers(service, SEVEN_VIN)] == [(1, 7)]


@pytest.mark.parametrize("polled", [True, False], ids=["polled", "before-the-first-reading"])
@pytest.mark.parametrize("cmd, value", [("fan_level", "9"), ("recirculation", "ON")])
def test_home_assistant_sets_and_keeps_the_fan_at_nine(tmp_path, monkeypatch, cmd, value, polled):
    database, service, _ = _bridge(tmp_path, monkeypatch, polled=polled)
    cloud = Cloud(NINE)
    PM._handle_mqtt_command(SimpleNamespace(_api=cloud, _vehicle=None), service, database, NINE_VIN, cmd, value)
    assert [b["windlevel"] for b in cloud.sent] == ["9"]


def test_a_list_that_changes_the_top_speed_updates_that_cars_number(tmp_path, monkeypatch):
    database, service, vids = _bridge(tmp_path, monkeypatch, polled=True)
    for nine_vins_config in (None, NINE):
        _listed(monkeypatch, str(tmp_path / "p.db"), (NINE_VIN, nine_vins_config), (SEVEN_VIN, None))
        for vin in (NINE_VIN, SEVEN_VIN):
            _poll(database, service, vin, vids[vin])
    assert [n["max"] for n in _fan_numbers(service, NINE_VIN)] == [9, 7, 9]
    assert [n["max"] for n in _fan_numbers(service, SEVEN_VIN)] == [7], "the other car's is not re-sent"
