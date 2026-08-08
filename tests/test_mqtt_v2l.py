"""V2L (vehicle-to-load) monitoring over MQTT — read-only entities (V2L has no remote command).

Covers the discovery configs (v2l_active binary_sensor + v2l_power/v2l_energy_session sensors) and
the live net-power logic in the service: power is gross discharge MINUS the idle baseline frozen at
session start, energy integrates that net power, and a latched 47==2 with no load reads 0 W."""
import json
import types

import pytest

pytest.importorskip("paho.mqtt.client", reason="poller MQTT bridge needs paho (absent in minimal CI)")
import mqtt as M
from client import _parse_signal


class _FakeClient:
    def __init__(self):
        self.published = {}

    def publish(self, topic, payload, retain=False):
        self.published[topic] = payload


def _service(prefix="leapmotor"):
    svc = M.MqttService("broker", 1883, topic_prefix=prefix, get_setting=lambda k, d="": d)
    svc.client = _FakeClient()
    return svc


def D(acmode, i, v=400.0, vin="VINTEST"):
    """⚠️ Carries a vin now: the V2L accumulators are per CAR. One set for the bridge meant two
    cars sharing a running total — a parked car's idle current subtracted from the load of the car
    actually powering something, and one session's energy credited to whichever was polled last."""
    return types.SimpleNamespace(ac_port_mode=acmode, charge_current_a=i, charge_voltage_v=v,
                                 vin=vin)


# ── discovery ──────────────────────────────────────────────────────────────────

def test_discovery_publishes_v2l_entities():
    svc = _service()
    svc.publish_discovery(types.SimpleNamespace(vin="VINTEST"))
    pub = svc.client.published

    bint = "homeassistant/binary_sensor/leapmotor_mate_vintest/v2l_active/config"
    assert bint in pub
    assert json.loads(pub[bint])["state_topic"] == "leapmotor/VINTEST/v2l_active"

    powt = "homeassistant/sensor/leapmotor_mate_vintest/v2l_power/config"
    pc = json.loads(pub[powt])
    assert pc["device_class"] == "power" and pc["unit_of_measurement"] == "W"
    assert pc["state_topic"] == "leapmotor/VINTEST/v2l_power"

    ent = "homeassistant/sensor/leapmotor_mate_vintest/v2l_energy_session/config"
    assert json.loads(pub[ent])["unit_of_measurement"] == "Wh"


def test_discovery_respects_prefix():
    svc = _service(prefix="myprefix")
    svc.publish_discovery(types.SimpleNamespace(vin="VINTEST"))
    assert "homeassistant/sensor/myprefix_mate_vintest/v2l_power/config" in svc.client.published


# ── live net-power logic ─────────────────────────────────────────────────────────

def test_net_power_subtracts_idle_baseline():
    svc = _service()
    svc._v2l_live(D(0, 0.5, 400))                 # idle → baseline I0 = 0.5 A
    active, watt, _ = svc._v2l_live(D(2, 3.0, 400))   # V2L: gross 1200 W, net (3.0-0.5)*400
    assert active is True
    assert watt == 1000                            # NET, not the 1200 gross


def test_inactive_reads_off_and_zero():
    svc = _service()
    active, watt, wh = svc._v2l_live(D(0, 0.7, 400))
    assert active is False and watt == 0 and wh == 0.0


def test_latched_mode_with_no_load_reads_zero():
    # 47==2 but current at/below baseline (mode armed, load off) → net clamped to 0.
    svc = _service()
    svc._v2l_live(D(0, 0.7, 400))
    active, watt, _ = svc._v2l_live(D(2, 0.7, 400))
    assert active is True and watt == 0


def test_energy_accumulates_then_resets(monkeypatch):
    svc = _service()
    clock = {"t": 1000.0}
    monkeypatch.setattr(M.time, "monotonic", lambda: clock["t"])
    svc._v2l_live(D(0, 0.5, 400))                 # baseline 0.5 A
    svc._v2l_live(D(2, 3.0, 400))                 # session start (no dt yet → 0 Wh)
    clock["t"] += 60                               # +60 s at 1000 W net
    _, watt, wh = svc._v2l_live(D(2, 3.0, 400))
    assert watt == 1000 and abs(wh - 1000 * 60 / 3600) < 0.1    # ≈16.7 Wh
    active, watt2, wh2 = svc._v2l_live(D(0, 0.5, 400))          # V2L ends → reset
    assert active is False and watt2 == 0 and wh2 == 0.0


# ── publish integration (real VehicleData via _parse_signal) ─────────────────────

def test_publish_sensors_emits_v2l_net_power():
    svc = _service()
    # idle first so the baseline (0.7 A) is captured, then a 4.1 A / 422.7 V V2L draw.
    svc._publish_sensors(_parse_signal("VIN", {"47": "0", "1178": "0.7", "1177": "422.7", "100003": "79"}))
    svc._publish_sensors(_parse_signal("VIN", {"47": "2", "1178": "4.1", "1177": "422.7", "100003": "79"}))
    pub = svc.client.published
    assert pub["leapmotor/VIN/v2l_active"] == "ON"
    assert float(pub["leapmotor/VIN/v2l_power"]) == round((4.1 - 0.7) * 422.7)   # 1437 W net (gross 1733)


# ── two cars ──────────────────────────────────────────────────────────────────

def test_two_cars_do_not_share_one_v2l_session():
    """🔴 One set of accumulators for the bridge meant exactly this: car A powering a fridge while
    car B sits parked would have had B's idle current frozen as A's baseline, and A's session
    energy handed to whichever car was polled last."""
    svc = _service()
    svc._v2l_live(D(0, 0.5, 400, vin="CAR_A"))          # A idles at 0.5 A
    svc._v2l_live(D(0, 9.0, 400, vin="CAR_B"))          # B idles far higher
    active_a, watt_a, _ = svc._v2l_live(D(2, 3.0, 400, vin="CAR_A"))
    assert active_a is True
    assert watt_a == round((3.0 - 0.5) * 400), "A's baseline is A's own, not B's 9.0 A"
    # …and B is still not in V2L at all
    active_b, watt_b, wh_b = svc._v2l_live(D(0, 9.0, 400, vin="CAR_B"))
    assert (active_b, watt_b, wh_b) == (False, 0, 0.0)


def test_one_cars_energy_is_not_credited_to_the_other():
    svc = _service()
    svc._v2l_live(D(0, 0.0, 400, vin="CAR_A"))
    svc._v2l_live(D(2, 5.0, 400, vin="CAR_A"))          # A's session opens
    svc._v2l_live(D(0, 0.0, 400, vin="CAR_B"))
    _, _, wh_b = svc._v2l_live(D(2, 5.0, 400, vin="CAR_B"))
    assert wh_b == 0.0, "B's session starts at zero, whatever A has accrued"


def test_discovery_is_published_for_each_car():
    """🔴 One flag for the bridge meant the second car's entities never appeared in Home Assistant
    at all: discovery had "already been sent". Discovery and the sensor publish are stubbed — what
    is under test is which cars get one, not what is in it."""
    svc = _service()
    svc.client.is_connected = lambda: True
    seen = []
    svc.publish_discovery = lambda data: seen.append(data.vin)
    svc._publish_sensors = lambda data: None
    svc.publish_status(types.SimpleNamespace(vin="CAR_A", climate_on=False))
    svc.publish_status(types.SimpleNamespace(vin="CAR_B", climate_on=False))
    svc.publish_status(types.SimpleNamespace(vin="CAR_A", climate_on=False))   # again
    assert seen == ["CAR_A", "CAR_B"], "each car once, and only once"
    assert svc._discovery_sent == {"CAR_A", "CAR_B"}


def test_each_car_is_gated_on_its_own_model():
    """The bridge took ONE model at construction. Two cars are two Home Assistant devices, and
    gating both on one car's model puts heated-seat entities on the car that has none."""
    svc = _service()
    svc.client.is_connected = lambda: True
    svc.publish_discovery = lambda data: None
    svc._publish_sensors = lambda data: None
    svc.publish_status(types.SimpleNamespace(vin="CAR_A", climate_on=False),
                       abilities=[1, 53], car_type="B10")
    svc.publish_status(types.SimpleNamespace(vin="CAR_B", climate_on=False),
                       abilities=[1], car_type="T03")
    assert svc._facts("CAR_A") == ([1, 53], "B10")
    assert svc._facts("CAR_B") == ([1], "T03")
    assert svc._facts("CAR_UNSEEN") == (None, ""), "a car never published falls back to the defaults"
