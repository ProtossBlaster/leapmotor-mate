"""The battery-health estimate's own charging-sample probe must not scan the charge either.

Same defect as test_asking_whether_the_cabin_was_used_is_not_a_scan.py, a different predicate:
_charge_energy_below_soc, _charge_has_soc_jump and _charge_temp_odo all gate on
db_reader._charging_sample() — "charging=1, OR current+stationary+park" — which
idx_positions_charging_recorded (`WHERE charging = 1` alone) cannot satisfy, because the OR's
second branch can match a row the simpler index does not cover. Without a matching index, a
charge with no real-power sample in its window (most of them, most of the time) makes SQLite
walk forward through however much of `positions` came after it before concluding there is none.

Measured on a synthetic 1,000-charge/200k-position database: get_battery_health 16.6s -> 0.09s
with idx_positions_charge_sample in place.
"""
import pathlib

import db as D
import db_reader

ROOT = pathlib.Path(__file__).resolve().parent.parent

PROBE = ("SELECT recorded_at FROM positions WHERE vehicle_id = COALESCE(?, vehicle_id) "
         "AND " + db_reader._charging_sample() + " AND recorded_at >= ? AND recorded_at <= ?")


def test_the_probe_is_answered_from_an_index_not_by_reading_the_window(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    db = db_reader._get()
    plan = " | ".join(str(row[-1]) for row in
                      db.execute("EXPLAIN QUERY PLAN " + PROBE, (1, "a", "z")).fetchall())
    assert "idx_positions_charge_sample" in plan, (
        f"a charge with no real-power sample reads every frame after it looking for one: {plan}"
    )


def test_the_index_holds_only_the_charging_sample_rows(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    conn = db_reader._get()
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='positions'"
        " AND name='idx_positions_charge_sample'").fetchone()
    assert sql is not None, "idx_positions_charge_sample does not exist"
    assert "WHERE" in (sql[0] or "").upper(), (
        f"the index is not partial, so it carries every frame ever recorded: {sql[0]}"
    )


def test_a_charge_with_real_power_samples_is_still_measured(tmp_path, monkeypatch):
    """The index must not change the answer, only its cost — same charge shape as
    test_soh_is_not_dragged_down_by_reporting_gaps.py's own dense case."""
    from datetime import datetime, timedelta, timezone
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type, capacity_kwh) VALUES (1,'LFZTEST','B10',67.1)")
    c.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('battery_capacity_nominal_kwh','67.1')")
    start = datetime(2026, 8, 8, 20, 0, tzinfo=timezone.utc)
    minutes, soc_from, soc_to = 320, 42.5, 90.0
    m = 0.0
    while m <= minutes:
        soc = soc_from + (soc_to - soc_from) * m / minutes
        c.execute("INSERT INTO positions (vehicle_id, recorded_at, soc, charging,"
                  " charge_voltage_v, charge_current_a, battery_min_temp, odometer_km, ready)"
                  " VALUES (1,?,?,1,240.0,25.0,22.0,10000,0)",
                  ((start + timedelta(minutes=m)).isoformat(), round(soc, 1)))
        m += 0.5
    c.execute("INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc,"
              " energy_added_kwh, charge_type, location_type)"
              " VALUES (1,1,?,?,?,?,32.0,'AC','HOME')",
              (start.isoformat(), (start + timedelta(minutes=minutes)).isoformat(), soc_from, soc_to))
    c.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", path)

    health = db_reader.get_battery_health()
    assert health["points"], "the charge vanished from the chart"
    assert 66.0 <= health["points"][0]["capacity_kwh"] <= 69.0, health["points"][0]
