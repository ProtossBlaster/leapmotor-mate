"""Dynamic-pricing mode (GitHub #104, Wartopia — a live HA sensor instead of a static price):
compute_cost() integrates the real power curve exactly like TOU 'split', but prices each interval
by a live sensor's history instead of a static band. These are the "simulazioni" scenarios: a
flat sensor reading, a price that changes mid-session, a brief spike, and every fallback path
(no entity configured, sensor unreachable, in-progress charge, no base price at all).

Fixture: a 2h charge, constant 5.0 kW (250V × 20A), sampled every 15 min → 8 equal 1.25 kWh
intervals, 10.0 kWh total — matches `energy_added_kwh` exactly so expected costs are hand-checkable.
Pure db_reader with ha_client.get_history monkeypatched → no network, CI-safe.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

import db_reader
import ha_client


T0 = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


def _db():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE charges (id INT, location_type TEXT, energy_added_kwh REAL, "
                "cost REAL, ac_energy_kwh REAL, started_at TEXT, ended_at TEXT, vehicle_id INTEGER DEFAULT 1)")
    con.execute("CREATE TABLE positions (recorded_at TEXT, charging INT, "
                "charge_voltage_v REAL, charge_current_a REAL)")
    for i in range(9):   # 0..120 min every 15 min → 8 intervals @ 1.25 kWh = 10 kWh
        t = (T0 + timedelta(minutes=15 * i)).isoformat()
        con.execute("INSERT INTO positions VALUES (?,1,250,20)", (t,))
    con.execute("ALTER TABLE positions ADD COLUMN vehicle_id INTEGER DEFAULT 1")
    # Parked, as every sample in this fixture means: a charge sample is now "the flag, OR a
    # charging current while stationary" (#341, db_reader._charging_sample).
    con.execute("ALTER TABLE positions ADD COLUMN speed_kmh REAL DEFAULT 0")
    con.execute("ALTER TABLE positions ADD COLUMN gear TEXT DEFAULT 'P'")
    con.commit()
    return con


def _charge(ended=True):
    return {
        "location_type": "HOME", "energy_added_kwh": 10.0, "ac_energy_kwh": None,
        "started_at": T0.isoformat(),
        "ended_at": (T0 + timedelta(hours=2)).isoformat() if ended else None,
    }


def _setup(monkeypatch, con, entity_id="sensor.test_price", base_price=0.25):
    monkeypatch.setattr(db_reader, "_get", lambda: con)
    monkeypatch.setattr(db_reader, "get_cost_config", lambda: {"mode": "dynamic", "method": "split", "bands": []})
    monkeypatch.setattr(db_reader, "get_charge_prices", lambda: {"price_home_kwh": base_price} if base_price else {})
    monkeypatch.setattr(db_reader, "get_dynamic_price_entity", lambda: entity_id)


def _ts(minutes):
    return (T0 + timedelta(minutes=minutes)).timestamp()


def test_flat_sensor_reading_matches_simple_multiplication(monkeypatch):
    con = _db()
    _setup(monkeypatch, con)
    monkeypatch.setattr(ha_client, "get_history", lambda eid, lo, hi: [(_ts(0), 0.30)])
    assert db_reader.compute_cost(_charge()) == 3.00   # 10 kWh × 0.30, no matter the base price


def test_price_change_mid_session_weights_by_energy_in_each_period(monkeypatch):
    con = _db()
    _setup(monkeypatch, con)
    # 0.20 for the first 30 min (2 intervals → 2.5 kWh), 0.40 for the remaining 90 min (6 → 7.5 kWh)
    monkeypatch.setattr(ha_client, "get_history",
                         lambda eid, lo, hi: [(_ts(0), 0.20), (_ts(30), 0.40)])
    # weighted avg = (2.5*0.20 + 7.5*0.40) / 10 = 0.35 → cost = 10 * 0.35
    assert db_reader.compute_cost(_charge()) == 3.50


def test_falling_price_is_the_mirror_case(monkeypatch):
    con = _db()
    _setup(monkeypatch, con)
    monkeypatch.setattr(ha_client, "get_history",
                         lambda eid, lo, hi: [(_ts(0), 0.40), (_ts(30), 0.20)])
    # weighted avg = (2.5*0.40 + 7.5*0.20) / 10 = 0.25 → cost = 10 * 0.25
    assert db_reader.compute_cost(_charge()) == 2.50


def test_brief_spike_only_weighs_its_own_slice(monkeypatch):
    con = _db()
    _setup(monkeypatch, con)
    # spike to 1.00 for one 15-min interval (45→60 min = 1.25 kWh), 0.20 everywhere else (8.75 kWh)
    monkeypatch.setattr(ha_client, "get_history",
                         lambda eid, lo, hi: [(_ts(0), 0.20), (_ts(45), 1.00), (_ts(60), 0.20)])
    # weighted = 8.75*0.20 + 1.25*1.00 = 1.75 + 1.25 = 3.00 → avg 0.30 → cost = 10 * 0.30
    assert db_reader.compute_cost(_charge()) == 3.00


def test_price_series_starting_after_window_uses_earliest_known_price(monkeypatch):
    con = _db()
    _setup(monkeypatch, con)
    # HA's history has no "value at window start" reference — first change is at 60 min.
    # Everything before it must fall back to that first known price (0.50), not crash.
    monkeypatch.setattr(ha_client, "get_history", lambda eid, lo, hi: [(_ts(60), 0.50)])
    assert db_reader.compute_cost(_charge()) == 5.00   # 10 kWh × 0.50 throughout


def test_no_entity_configured_falls_back_to_flat_base_price(monkeypatch):
    con = _db()
    _setup(monkeypatch, con, entity_id="")
    called = []
    monkeypatch.setattr(ha_client, "get_history", lambda *a: called.append(1) or [(_ts(0), 0.99)])
    assert db_reader.compute_cost(_charge()) == 2.50   # 10 kWh × base 0.25
    assert not called                                  # never even queries HA without an entity


def test_sensor_unreachable_falls_back_to_flat_base_price(monkeypatch):
    con = _db()
    _setup(monkeypatch, con)
    monkeypatch.setattr(ha_client, "get_history", lambda eid, lo, hi: [])   # HA down / no history
    assert db_reader.compute_cost(_charge()) == 2.50   # 10 kWh × base 0.25


def test_in_progress_charge_falls_back_to_flat_base_price(monkeypatch):
    con = _db()
    _setup(monkeypatch, con)
    called = []
    monkeypatch.setattr(ha_client, "get_history", lambda *a: called.append(1) or [(_ts(0), 0.99)])
    assert db_reader.compute_cost(_charge(ended=False)) == 2.50   # no window to integrate yet
    assert not called


def test_no_base_price_and_no_dynamic_data_returns_none(monkeypatch):
    con = _db()
    _setup(monkeypatch, con, base_price=None)
    monkeypatch.setattr(ha_client, "get_history", lambda eid, lo, hi: [])
    assert db_reader.compute_cost(_charge()) is None   # nothing knowable, not a guessed 0
