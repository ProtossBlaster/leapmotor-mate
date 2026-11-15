"""Home Assistant gets a range-extender's fuel: tank level, litres in it, range on fuel (#481, @michapr).

The values were read from the car (signals 3235, 3263, 3259) and shown on the pages, but never sent
over MQTT. Two rules beside the ones every sensor keeps:

- An empty tank is 0, not unknown. A value the car sent is published as it is, and the sensors are
  declared without the template that turns a falsy value into none: a tank at 0 % is a fact.
- Only a range-extender gets them. A plain electric car is not given three sensors that would stay
  unknown for ever; and a range-extender whose one frame lacks the fuel signal does not lose them
  (an absent answer never deletes an entity).
"""
import json
from dataclasses import replace

import pytest

pytest.importorskip("paho.mqtt.client", reason="poller MQTT bridge needs paho")
from test_mqtt_ready import _data, _service

_DISC = "homeassistant/sensor/leapmotor_mate_vintest/{}/config"
_KEYS = ("fuel_level", "fuel_liters", "fuel_range")


def _reev(**fuel):
    return replace(_data(False), is_reev=True, **fuel)


def test_a_range_extender_publishes_its_fuel():
    svc = _service()
    svc._publish_sensors(_reev(fuel_level_pct=62.0, fuel_liters=30.95, fuel_range_km=410.0))
    p = svc.client.published
    assert (p["leapmotor/VINTEST/fuel_level"], p["leapmotor/VINTEST/fuel_liters"],
            p["leapmotor/VINTEST/fuel_range"]) == ("62.0", "30.95", "410.0")


def test_an_empty_tank_is_zero_not_unknown():
    svc = _service()
    svc._publish_sensors(_reev(fuel_level_pct=0.0, fuel_liters=0.0, fuel_range_km=0.0))
    assert [svc.client.published[f"leapmotor/VINTEST/{k}"] for k in _KEYS] == ["0.0", "0.0", "0.0"]
    svc.publish_discovery(_reev())
    for k in _KEYS:
        conf = json.loads(svc.client.published[_DISC.format(k)])
        assert "value_template" not in conf, f"{k}: a template that makes 0 unknown"


def test_a_value_the_car_did_not_send_keeps_the_last_one():
    svc = _service()
    svc._publish_sensors(_reev(fuel_level_pct=None, fuel_liters=None, fuel_range_km=None))
    assert not any(f"leapmotor/VINTEST/{k}" in svc.client.published for k in _KEYS)


def test_discovery_announces_them_on_a_range_extender():
    svc = _service()
    svc.publish_discovery(_reev())
    level = json.loads(svc.client.published[_DISC.format("fuel_level")])
    litres = json.loads(svc.client.published[_DISC.format("fuel_liters")])
    rng = json.loads(svc.client.published[_DISC.format("fuel_range")])
    assert (level["unit_of_measurement"], litres["unit_of_measurement"], rng["unit_of_measurement"]) == ("%", "L", "km")
    assert level["state_topic"] == "leapmotor/VINTEST/fuel_level"


def test_a_plain_electric_car_gets_none_of_them():
    svc = _service()
    svc._publish_sensors(_data(False))
    svc.publish_discovery(_data(False))
    assert not [t for t in svc.client.published if any(k in t for k in _KEYS)]


def test_a_range_extenders_frame_without_fuel_does_not_take_them_away():
    """Seen as a range-extender once, the car keeps its fuel sensors: a frame without 3235 says the
    signal is missing, not that the tank went."""
    svc = _service()
    svc.publish_discovery(_reev())
    svc.client.published.clear()
    svc.publish_discovery(replace(_data(False), is_reev=False))
    # announced again, never cleared: an empty retained config is how HA is told to drop an entity
    assert all(svc.client.published.get(_DISC.format(k)) for k in _KEYS)


def test_a_range_extender_first_seen_without_fuel_gets_them_when_the_fuel_arrives():
    """Discovery runs once per connection: if it ran on a frame without 3235, the first frame with it
    announces the fuel sensors."""
    svc = _service()
    svc.client.is_connected = lambda: True
    svc.publish_status(_data(False))
    assert _DISC.format("fuel_level") not in svc.client.published
    svc.publish_status(_reev(fuel_level_pct=50.0))
    assert _DISC.format("fuel_level") in svc.client.published
    assert svc.client.published["leapmotor/VINTEST/fuel_level"] == "50.0"
