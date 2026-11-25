"""The consumption-vs-temperature trend is a cubic, its coefficients go to MQTT — D#498, @hristouzunovaz.

THE SHAPE. The line was straight. Consumption is not: it climbs in the cold, drops towards a mild
day, and climbs again in the summer, when the car cools its cabin. On the install's own 412 trips
(6–37 °C) the straight line explained 4 % of the spread (r² 0.04) and said consumption RISES with
heat across the whole range; a cubic explains 29.7 % and has its low near 20 °C.

THE NUMBERS ON MQTT. a, b, c, d of `a·T³ + b·T² + c·T + d`, so Home Assistant can multiply
tomorrow's forecast temperature and distance into the energy to charge. They are computed ONCE, in
the web — where the trip temperatures are written — and stored per car; the poller only publishes
them. The same coefficients draw the curve, so the page and Home Assistant cannot disagree.

With the coefficients go the temperatures they were fitted on: outside 6–37 °C a cubic is a guess,
and whoever uses it should be able to see where the data stops.
"""
import json
import importlib.util
import pathlib

import pytest

import db as D
import db_reader


def _setup(tmp_path, monkeypatch, vin="VINX", car_type="B10"):
    path = str(tmp_path / "t.db")
    database = D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    database._conn.execute(
        "INSERT INTO vehicles (id, vin, car_type) VALUES (1, ?, ?)", (vin, car_type))
    database._conn.commit()
    return database


def _trip(db, tid, eff, t, vehicle_id=1, km=50.0):
    db._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km,"
        " efficiency_kwh_100km, outside_temp_start_c, outside_temp_end_c)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (tid, vehicle_id, f"2026-07-01T{tid % 24:02d}:00:00+00:00",
         f"2026-07-01T{tid % 24:02d}:30:00+00:00", km, eff, t, t))
    db._conn.commit()


def _cubic(t, a=0.0007, b=0.005, c=-1.25, d=30.0):
    return a * t ** 3 + b * t ** 2 + c * t + d


# ── the fit ──────────────────────────────────────────────────────────────────

def test_the_trend_recovers_a_known_cubic(tmp_path, monkeypatch):
    """The fit is solved by hand — there is no numpy — so it is checked against a curve it must
    give back to the fourth decimal of `a`: a coefficient that small is the whole bend."""
    db = _setup(tmp_path, monkeypatch)
    for i, t in enumerate(range(-5, 40, 3)):
        _trip(db, i + 1, round(_cubic(t), 6), float(t))
    trend = db_reader.get_efficiency_vs_temp()["trend"]
    assert trend["a"] == pytest.approx(0.0007, abs=1e-6)
    assert trend["b"] == pytest.approx(0.005, abs=1e-5)
    assert trend["c"] == pytest.approx(-1.25, abs=1e-4)
    assert trend["d"] == pytest.approx(30.0, abs=1e-3)
    assert trend["r2"] > 0.99
    assert (trend["n"], trend["t_min"], trend["t_max"]) == (15, -5.0, 37.0)


def test_the_curve_is_the_same_wherever_its_temperatures_sit(tmp_path, monkeypatch):
    """Trips between 29 and 31 °C are the same fit as the same trips thirty degrees colder: a narrow
    range far from 0 °C must not turn the sums into near-copies of one another and bend the curve."""
    def curve(shift):
        (tmp_path / str(shift)).mkdir()
        db = _setup(tmp_path / str(shift), monkeypatch)
        for i in range(21):
            t = -1.0 + 0.1 * i
            _trip(db, i + 1, 16 + 0.05 * (t + 1) ** 2 + ((i * 7) % 5 - 2) * 0.4, round(t + shift, 1))
        tr = db_reader.get_efficiency_vs_temp()["trend"]
        return [tr["a"] * t ** 3 + tr["b"] * t ** 2 + tr["c"] * t + tr["d"]
                for t in (-1.0 + shift, shift, 1.0 + shift)]
    assert curve(30) == pytest.approx(curve(0), abs=1e-6)


def test_the_slope_is_gone(tmp_path, monkeypatch):
    """«≈X every 10 °C» has no single value on a curve: the old keys must not linger beside the new."""
    db = _setup(tmp_path, monkeypatch)
    for i, t in enumerate(range(0, 40, 4)):
        _trip(db, i + 1, round(_cubic(t), 6), float(t))
    trend = db_reader.get_efficiency_vs_temp()["trend"]
    assert "per_10c" not in trend and "at20c" not in trend


def test_four_temperatures_are_the_least_a_cubic_can_be_drawn_on(tmp_path, monkeypatch):
    """Eight trips on three temperatures pin a parabola, not a cubic: the system has no unique
    answer, and a curve drawn from it would be invented."""
    db = _setup(tmp_path, monkeypatch)
    for i in range(9):
        _trip(db, i + 1, 15.0 + i % 3, float(10 + 5 * (i % 3)))
    assert db_reader.get_efficiency_vs_temp()["trend"] is None


def test_fewer_than_eight_trips_still_refuse_a_trend(tmp_path, monkeypatch):
    db = _setup(tmp_path, monkeypatch)
    for i in range(7):
        _trip(db, i + 1, _cubic(i * 5), float(i * 5))
    assert db_reader.get_efficiency_vs_temp()["trend"] is None


# ── stored per car, for the poller ───────────────────────────────────────────

def test_the_web_stores_each_cars_coefficients(tmp_path, monkeypatch):
    db = _setup(tmp_path, monkeypatch)
    for i, t in enumerate(range(-5, 40, 3)):
        _trip(db, i + 1, round(_cubic(t), 6), float(t))
    db_reader.store_efftemp_trends()
    stored = json.loads(db.get_setting(db_reader.efftemp_trend_key("VINX")))
    assert stored["elec"]["a"] == pytest.approx(0.0007, abs=1e-6)
    assert stored["elec"] == db_reader.get_efficiency_vs_temp()["trend"]   # the page's own numbers
    assert stored["fuel"] is None                                         # a BEV has no petrol


def test_a_second_car_gets_its_own_and_not_the_selected_ones(tmp_path, monkeypatch):
    db = _setup(tmp_path, monkeypatch)
    db._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (2, 'VINY', 'T03')")
    for i, t in enumerate(range(-5, 40, 3)):
        _trip(db, i + 1, round(_cubic(t), 6), float(t))
        _trip(db, 100 + i, round(_cubic(t, d=10.0), 6), float(t), vehicle_id=2)
    db_reader.store_efftemp_trends()
    x = json.loads(db.get_setting(db_reader.efftemp_trend_key("VINX")))["elec"]
    y = json.loads(db.get_setting(db_reader.efftemp_trend_key("VINY")))["elec"]
    assert x["d"] == pytest.approx(30.0, abs=1e-3)
    assert y["d"] == pytest.approx(10.0, abs=1e-3)


def test_a_car_without_a_trend_stores_that_it_has_none(tmp_path, monkeypatch):
    """No trend is stored as no trend — never as zeros, which Home Assistant would multiply."""
    db = _setup(tmp_path, monkeypatch)
    _trip(db, 1, 15.0, 10.0)
    db_reader.store_efftemp_trends()
    assert json.loads(db.get_setting(db_reader.efftemp_trend_key("VINX"))) == {"elec": None,
                                                                             "fuel": None}


def test_an_unchanged_trend_is_not_written_again(tmp_path, monkeypatch):
    db = _setup(tmp_path, monkeypatch)
    for i, t in enumerate(range(-5, 40, 3)):
        _trip(db, i + 1, round(_cubic(t), 6), float(t))
    assert db_reader.store_efftemp_trends() == 1
    assert db_reader.store_efftemp_trends() == 0


# ── published by the poller ──────────────────────────────────────────────────

pytest.importorskip("paho.mqtt.client", reason="poller MQTT bridge needs paho")
from test_mqtt_ready import _service as _bare_service  # noqa: E402


def _service():
    svc = _bare_service()
    svc.client.is_connected = lambda: True
    return svc

_CFG = "homeassistant/sensor/leapmotor_mate_vintest/{}/config"
_TREND = {"a": 0.000691, "b": 0.00515, "c": -1.256863, "d": 30.007281, "r2": 0.3, "n": 412,
          "t_min": 6.3, "t_max": 37.3}


def test_the_coefficients_reach_home_assistant_unrounded():
    svc = _service()
    svc.publish_trend("VINTEST", json.dumps({"elec": _TREND, "fuel": None}))
    p = svc.client.published
    assert [p[f"leapmotor/VINTEST/consumption_trend_{k}"] for k in "abcd"] == \
        ["0.000691", "0.00515", "-1.256863", "30.007281"]
    attrs = json.loads(p["leapmotor/VINTEST/consumption_trend/attributes"])
    assert (attrs["t_min"], attrs["t_max"], attrs["n"], attrs["r2"]) == (6.3, 37.3, 412, 0.3)
    assert attrs["unit"] == "kWh/100km"
    conf = json.loads(p[_CFG.format("consumption_trend_a")])
    assert conf["state_topic"] == "leapmotor/VINTEST/consumption_trend_a"
    assert conf["json_attributes_topic"] == "leapmotor/VINTEST/consumption_trend/attributes"
    assert not [t for t in p if "fuel_trend" in t]          # a BEV gets no petrol entities


def test_a_range_extender_gets_its_petrol_trend_too():
    svc = _service()
    svc.publish_trend("VINTEST", json.dumps({"elec": _TREND, "fuel": dict(_TREND, d=5.5)}))
    p = svc.client.published
    assert p["leapmotor/VINTEST/fuel_trend_d"] == "5.5"
    assert json.loads(p["leapmotor/VINTEST/fuel_trend/attributes"])["unit"] == "L/100km"
    assert _CFG.format("fuel_trend_a") in p


def test_a_trend_that_went_away_reads_unknown_not_zero():
    svc = _service()
    svc.publish_trend("VINTEST", json.dumps({"elec": _TREND, "fuel": None}))
    svc.publish_trend("VINTEST", json.dumps({"elec": None, "fuel": None}))
    p = svc.client.published
    assert [p[f"leapmotor/VINTEST/consumption_trend_{k}"] for k in "abcd"] == ["", "", "", ""]
    assert p[_CFG.format("consumption_trend_a")]           # the entity stays, unknown


def test_nothing_is_published_before_the_web_has_computed_anything():
    svc = _service()
    svc.publish_trend("VINTEST", "")
    svc.publish_trend("VINTEST", json.dumps({"elec": None, "fuel": None}))
    assert svc.client.published == {}


def test_an_unchanged_trend_is_not_republished_every_poll():
    svc = _service()
    raw = json.dumps({"elec": _TREND, "fuel": None})
    svc.publish_trend("VINTEST", raw)
    svc.client.published.clear()
    svc.publish_trend("VINTEST", raw)
    assert svc.client.published == {}


def test_a_reconnect_publishes_it_again():
    """A broker that restarted without persistence has lost the retained topics: after a reconnect
    the trend is sent again, like the rest of discovery."""
    svc = _service()
    svc.client.subscribe = lambda *a, **k: None
    raw = json.dumps({"elec": _TREND, "fuel": None})
    svc.publish_trend("VINTEST", raw)
    svc.client.published.clear()
    svc._on_connect(svc.client, None, None, 0)
    svc.publish_trend("VINTEST", raw)
    assert svc.client.published["leapmotor/VINTEST/consumption_trend_a"] == "0.000691"
    assert _CFG.format("consumption_trend_a") in svc.client.published


def _poller_main():
    root = pathlib.Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("poller_main_498", root / "poller" / "main.py")
    pmain = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pmain)          # under its own name — a bare `main` is the WEB's
    return pmain


def test_the_poll_publishes_what_the_web_stored(tmp_path, monkeypatch):
    database = _setup(tmp_path, monkeypatch, vin="VINTEST")
    pmain = _poller_main()
    raw = json.dumps({"elec": _TREND, "fuel": None})
    database.set_setting(db_reader.efftemp_trend_key("VINTEST"), raw)
    for k, v in (("mqtt_enabled", "1"), ("mqtt_broker", "h")):
        database.set_setting(k, v)
    service = _service()
    service.config_sig = pmain._mqtt_config_sig(database)
    monkeypatch.setattr(service, "publish_status", lambda data, **kw: None)
    seen = {}
    monkeypatch.setattr(service, "publish_trend", lambda vin, r: seen.update({vin: r}))

    class Frame:
        vin = "VINTEST"

    pmain._mqtt_tick(database, None, Frame(), service, None, 1)
    assert seen == {"VINTEST": raw}


def test_the_two_trees_name_the_same_setting():
    """🔴 Two copies of one key, in two trees that cannot import each other: a typo on either side
    publishes nothing and no test of one side alone would notice."""
    assert _poller_main().efftemp_trend_key("VinTest") == db_reader.efftemp_trend_key("VinTest")
