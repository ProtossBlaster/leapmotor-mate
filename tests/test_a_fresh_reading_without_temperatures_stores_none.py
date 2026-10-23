"""A fresh reading without temperatures stores none, not 0 °C (#435).

The web's fresh reading (`save_fresh_signals`, after a command or a refresh) wrote the battery
temperature (1182), the cabin's (1349) and the A/C target (2183) as `float(x or 0)`, so a car that
does not send them got 0 °C. The T03 does not send the battery's: one such reading during a charge
made that charge's coldest battery read 0 °C, and the Battery health page left it out as a cold
charge — three of @matttiaromano's four. The poller has kept an absent temperature empty since #144;
the web now follows the same rule, and a real 0 °C still stays 0.
"""
from datetime import datetime, timedelta, timezone

import db as D
import db_reader

VIN = "LT03TEST000000001"
PARKED = {"1204": 50, "1010": 0, "1319": 0}


def _db(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    db = D.Database(path)
    db.set_battery_capacity(36.0)
    db._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1, ?, 'T03')", (VIN,))
    db._conn.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return db


def _last_temps(db):
    return db._conn.execute("SELECT battery_min_temp, inside_temp, climate_target_temp FROM positions "
                            "ORDER BY id DESC LIMIT 1").fetchone()


def test_temperatures_the_car_did_not_send_stay_empty(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    db_reader.save_fresh_signals(dict(PARKED), VIN)
    assert tuple(_last_temps(db)) == (None, None, None)


def test_a_real_zero_degrees_stays_zero(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    db_reader.save_fresh_signals({**PARKED, "1182": 0, "1349": 0.0, "2183": 0}, VIN)
    assert tuple(_last_temps(db)) == (0.0, 0.0, 0.0)


def test_a_fresh_reading_during_a_charge_does_not_make_it_cold(tmp_path, monkeypatch):
    """A T03 AC charge from the poller (no battery temperature), and one fresh reading inside it."""
    db = _db(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=60)
    db._conn.execute("INSERT INTO charges (id,vehicle_id,started_at,ended_at,start_soc,end_soc,charge_type) "
                     "VALUES (1,1,?,?,20,40,'AC')", (start.isoformat(), (now + timedelta(minutes=1)).isoformat()))
    for k in range(120):                    # every 30 s, one whole point every ~3 min, 6.4 kW
        db._conn.execute(
            "INSERT INTO positions (vehicle_id,recorded_at,soc,charging,charge_voltage_v,charge_current_a)"
            " VALUES (1,?,?,1,368,-17.3)", ((start + timedelta(seconds=30 * k)).isoformat(), float(20 + k // 6)))
    db._conn.commit()
    db_reader.save_fresh_signals({**PARKED, "1204": 40, "1149": 2, "1177": 368, "1178": -17.3, "1200": 60}, VIN)
    h = db_reader.get_battery_health()
    assert h["cold_count"] == 0, h["points"]
    assert h["sample_count"] == 1


def _charge_with_temps(db, temps):
    """A 60-minute AC charge 20 → 40 % at 6.4 kW whose samples carry `temps` in turn (None = absent)."""
    start = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    db._conn.execute("INSERT INTO charges (id,vehicle_id,started_at,ended_at,start_soc,end_soc,charge_type) "
                     "VALUES (1,1,?,?,20,40,'AC')", (start.isoformat(), (start + timedelta(minutes=61)).isoformat()))
    for k in range(121):
        db._conn.execute(
            "INSERT INTO positions (vehicle_id,recorded_at,soc,charging,charge_voltage_v,charge_current_a,"
            "battery_min_temp) VALUES (1,?,?,1,368,-17.3,?)",
            ((start + timedelta(seconds=30 * k)).isoformat(), float(20 + k // 6), temps[k % len(temps)]))
    db._conn.commit()
    return db_reader.get_battery_health()


def test_zeros_already_stored_for_a_car_without_battery_temperature_are_not_readings(tmp_path, monkeypatch):
    """The rows the old fresh reading wrote are still in every T03's database: a 0 °C from a car that
    has never sent any other battery temperature is the absence it stands for."""
    db = _db(tmp_path, monkeypatch)
    h = _charge_with_temps(db, [None] * 40 + [0.0])
    assert h["cold_count"] == 0, h["points"]
    assert h["sample_count"] == 1


def test_a_car_that_reports_its_battery_temperature_keeps_a_real_zero(tmp_path, monkeypatch):
    db = _db(tmp_path, monkeypatch)
    db._conn.execute("INSERT INTO positions (vehicle_id,recorded_at,soc,battery_min_temp) "
                     "VALUES (1,'2026-09-01T09:00:00+00:00',60,18.0)")
    h = _charge_with_temps(db, [20.0, 0.0])      # warm but for one real 0 °C: that one decides
    assert h["cold_count"] == 1, h["points"]
