"""Home Assistant can set the A/C target temperature, as the Commands page can (#489).

@michapr (B10): the *AC Target* sensor is there, but nothing in Home Assistant sets it. The page has
had it all along (`/api/climate-temp`, `command_client.set_climate_temp`, confirmed on-car
2026-06-21). The bridge now offers a writable number, *AC Target Temperature*, which reads the same
topic as the sensor and sends the page's own body: the target changes and the rest of the panel
(mode, recirculation, fan) stays as the last reading had it. A panel in AUTO stays in AUTO; the T03,
which ignores an AUTO write (#67), gets the manual cold the page sends it.

The sensor stays: entities people already use are not taken away.
"""
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("paho.mqtt.client", reason="poller MQTT bridge needs paho")
pytest.importorskip("fastapi", reason="the page's half needs web.main")

from test_absent_temperature_entities_are_removed import FakeClient, Frame
from test_the_climate_context_is_this_cars import PM
from test_the_fan_has_as_many_speeds_as_the_car_says import NINE, Cloud

VIN = "LFZB10TARGET00001"


def _bridge(tmp_path, monkeypatch, *, mode=1, recirc=0, fan=9, target=22):
    import db as D
    import mqtt as M
    monkeypatch.setattr(M.MqttService, "_command_visible", lambda self, vin, key: True)
    database = D.Database(str(tmp_path / "p.db"))
    vid = database.ensure_vehicle(VIN, "B10")
    database._conn.execute(
        "INSERT INTO positions (vehicle_id, recorded_at, climate_mode, recirculation, fan_level, "
        "climate_target_temp) VALUES (?, '2026-11-16T10:00:00+00:00', ?, ?, ?, ?)",
        (vid, mode, recirc, fan, target))
    database._conn.commit()
    service = M.MqttService(broker="h", port=1883, discovery_enabled=True, get_setting=database.get_setting)
    service.client = FakeClient()
    monkeypatch.setattr(service, "fan_top_for", lambda vin: 9)
    return database, service


def _send(database, service, value, monkeypatch, car_type="B10"):
    monkeypatch.setattr(PM, "_mqtt_car_type", lambda client, vin="": car_type)
    cloud = Cloud(NINE)
    PM._handle_mqtt_command(SimpleNamespace(_api=cloud, _vehicle=None), service, database, VIN,
                            "climate_temp", value)
    return cloud.sent


def _configs(service, component, key):
    tail = f"/{component}/leapmotor_mate_{VIN.lower()}/{key}/config"
    return [p for t, p in service.client.sent if t.endswith(tail)]


def test_home_assistant_gets_a_number_on_the_sensors_topic(tmp_path, monkeypatch):
    _db, service = _bridge(tmp_path, monkeypatch)
    service.publish_discovery(Frame(VIN))
    [conf] = [json.loads(p) for p in _configs(service, "number", "climate_temp")]
    assert conf["state_topic"] == f"leapmotor/{VIN}/ac_target_temp"
    assert conf["command_topic"] == f"leapmotor/{VIN}/climate_temp/set"
    assert (conf["min"], conf["max"], conf["step"]) == (18, 32, 1)
    assert conf["unit_of_measurement"] == "°C"


def test_the_sensor_stays(tmp_path, monkeypatch):
    _db, service = _bridge(tmp_path, monkeypatch)
    service.publish_discovery(Frame(VIN))
    assert [p for p in _configs(service, "sensor", "ac_target_temp") if p], "the AC Target sensor is kept"


def test_a_car_that_may_not_set_it_gets_no_number(tmp_path, monkeypatch):
    import mqtt as M
    _db, service = _bridge(tmp_path, monkeypatch)
    asked = []
    monkeypatch.setattr(M.MqttService, "_command_visible",
                        lambda self, vin, key: asked.append(key) or key != "climate_temp")
    service.publish_discovery(Frame(VIN))
    assert "climate_temp" in asked
    assert _configs(service, "number", "climate_temp") == [""], "an empty config removes the entity"


def test_the_command_gates_on_the_temperature_right(tmp_path, monkeypatch):
    import mqtt as M
    import ui_command_access
    _db, service = _bridge(tmp_path, monkeypatch)
    monkeypatch.undo()
    seen = []
    monkeypatch.setattr(ui_command_access, "command_allowed", lambda vin, key, gs: seen.append(key) or True)
    M.MqttService._command_visible(service, VIN, "climate_temp")
    assert seen == ["set_climate_temp"]


def test_a_new_target_keeps_the_rest_of_the_panel(tmp_path, monkeypatch):
    database, service = _bridge(tmp_path, monkeypatch, mode=1, recirc=1, fan=9, target=26)
    assert _send(database, service, "21", monkeypatch) == [
        {"circle": "in", "mode": "cold", "operate": "manual", "position": "all",
         "temperature": "21", "windlevel": "9", "wshld": "0"}]


def test_a_panel_in_auto_stays_in_auto(tmp_path, monkeypatch):
    database, service = _bridge(tmp_path, monkeypatch, mode=0, recirc=0, fan=4)
    [body] = _send(database, service, "23", monkeypatch)
    assert (body["operate"], body["mode"], body["temperature"]) == ("auto", "nohotcold", "23")


def test_a_t03_in_auto_gets_manual_cold(tmp_path, monkeypatch):
    database, service = _bridge(tmp_path, monkeypatch, mode=0)
    [body] = _send(database, service, "23", monkeypatch, car_type="T03")
    assert (body["operate"], body["mode"]) == ("manual", "cold")


@pytest.mark.parametrize("value, sent", [("35", "32"), ("16", "18"), ("21.6", "22"), ("22.0", "22")])
def test_the_value_is_kept_to_what_the_car_takes(tmp_path, monkeypatch, value, sent):
    database, service = _bridge(tmp_path, monkeypatch)
    assert [b["temperature"] for b in _send(database, service, value, monkeypatch)] == [sent]


@pytest.mark.parametrize("value", ["", "warm", "nan", "inf"])
def test_a_value_that_is_not_a_temperature_sends_nothing(tmp_path, monkeypatch, value):
    database, service = _bridge(tmp_path, monkeypatch)
    assert _send(database, service, value, monkeypatch) == []


@pytest.mark.parametrize("mode", [0, 1, 3, 4])
def test_home_assistant_sends_what_the_page_sends(tmp_path, monkeypatch, mode):
    """One car, one panel: the page's set_climate_temp and the bridge's command send the same body."""
    import command_client
    import db as D
    from events_fixture import Car, web
    from events_fixture import VIN as PAGE_VIN
    from test_the_fan_has_as_many_speeds_as_the_car_says import _listed
    car = Car(tmp_path)
    _listed(monkeypatch, car.path, (PAGE_VIN, NINE))
    web(car, monkeypatch)
    car.frame(**{"1941": 6, "3713": mode, "1943": 1})
    page = Cloud(NINE)
    monkeypatch.setattr(command_client, "_session", page)
    assert command_client.set_climate_temp(24)[0]

    import mqtt as M
    monkeypatch.setattr(PM, "_mqtt_car_type", lambda client, vin="": "B10")
    service = M.MqttService(broker="h", port=1883, discovery_enabled=True,
                            get_setting=lambda k, d=None: d)
    monkeypatch.setattr(service, "fan_top_for", lambda vin: 9)
    bridge = Cloud(NINE)
    PM._handle_mqtt_command(SimpleNamespace(_api=bridge, _vehicle=None), service, D.Database(car.path),
                            PAGE_VIN, "climate_temp", "24")
    assert bridge.sent == page.sent and len(page.sent) == 1
