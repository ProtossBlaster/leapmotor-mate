"""Asking whether someone sat in the car during a charge must not scan the charge.

Profiled inside the lab container against a real database on 28/09/2026, the whole of
`get_battery_health` came to 214.0 ms and **184.57 ms of it was one query, called eleven times**:

    SELECT 1 FROM positions WHERE vehicle_id = COALESCE(?, vehicle_id)
      AND recorded_at >= ? AND recorded_at <= ?
      AND (climate_cooling = 1 OR climate_heating = 1) LIMIT 1

`_charge_has_active_use`. It is a cheap-looking probe — `LIMIT 1`, no rows returned — and that is
exactly why it is expensive: with no index on those two columns, finding **no** match means reading
every frame in the window. Timed on its own over all 33 charges of that database: **618.05 ms**, and
`EXPLAIN QUERY PLAN` said `SCAN positions`. Not one of those charges had the cabin in use, so all of
it was spent proving a negative.

The same shape as the V2L probe, and the same cure: a partial index holding only the rows where the
cabin heater or cooler was running — 2081 rows out of 374 511 on that database. With it, the plan
becomes a SEARCH and the 33 probes cost **0.10 ms**.

The verdict must not move: a charge someone sat through is still excluded from the SoH figure.
"""
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
# sys.path is conftest.py's job — it puts web/ before poller/, and BOTH hold a main.py.

import db as D  # noqa: E402
import db_reader  # noqa: E402

PROBE = ("SELECT 1 FROM positions WHERE vehicle_id = COALESCE(?, vehicle_id) "
         "AND recorded_at >= ? AND recorded_at <= ? "
         "AND (climate_cooling = 1 OR climate_heating = 1) LIMIT 1")


@pytest.fixture
def parked_week(tmp_path, monkeypatch):
    """One car, a week of frames, the cabin never used. The shape that made the probe a scan."""
    path = str(tmp_path / "cabin.db")
    database = D.Database(path)
    conn = database._conn
    conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LVIN0000000000001','B10')")
    now = datetime.now(timezone.utc)
    conn.executemany(
        "INSERT INTO positions (vehicle_id, recorded_at, soc, charging, climate_cooling,"
        " climate_heating) VALUES (1, ?, 60, 1, 0, 0)",
        [((now - timedelta(seconds=30 * i)).isoformat(),) for i in range(20000)])
    conn.commit()
    conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return path


def test_the_probe_is_answered_from_an_index_not_by_reading_the_window(parked_week):
    """The plan must name the partial index.

    Both `SEARCH … USING INDEX idx_positions_cabin_use` and `SCAN … USING INDEX
    idx_positions_cabin_use` are fine, and which one SQLite picks depends on whether ANALYZE has
    run: either way it reads only the index, and the index holds only the frames where the cabin
    was in use. What must never come back is a plan with no index in it — that one reads the table,
    which is every frame in the charge.
    """
    db = db_reader._get()
    plan = " | ".join(str(row[-1]) for row in
                      db.execute("EXPLAIN QUERY PLAN " + PROBE, (1, "a", "z")).fetchall())
    assert "idx_positions_cabin_use" in plan, (
        f"proving nobody sat in the car reads every frame of the charge: {plan}"
    )


def test_the_index_holds_only_the_rows_where_the_cabin_was_used(tmp_path, monkeypatch):
    """Partial, or it would double the write cost of every frame for a handful of answers."""
    path = str(tmp_path / "narrow.db")
    database = D.Database(path)
    conn = database._conn
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='positions'"
        " AND sql LIKE '%climate_cooling%'").fetchone()
    conn.close()
    assert sql is not None, "no index over the cabin columns exists"
    assert "WHERE" in (sql[0] or "").upper(), (
        f"the index is not partial, so it carries every frame ever recorded: {sql[0]}"
    )


def test_a_charge_someone_sat_through_is_still_left_out(tmp_path, monkeypatch):
    """The whole point of the probe. The index must not change the answer, only its cost."""
    path = str(tmp_path / "sat.db")
    database = D.Database(path)
    conn = database._conn
    conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LVIN0000000000001','B10')")
    start = datetime(2026, 5, 1, 8, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=3)
    conn.execute("INSERT INTO charges (vehicle_id, started_at, ended_at, start_soc, end_soc,"
                 " charge_type) VALUES (1, ?, ?, 40, 90, 'AC')",
                 (start.isoformat(), end.isoformat()))
    rows = []
    for i in range(180):                       # one frame a minute, 8 kW, cabin heater on
        t = (start + timedelta(minutes=i)).isoformat()
        rows.append((t, 40 + i * 50 / 180.0, 1, 400.0, 20.0, 1, 20.0, 1000.0))
    conn.executemany(
        "INSERT INTO positions (vehicle_id, recorded_at, soc, charging, charge_voltage_v,"
        " charge_current_a, climate_heating, battery_min_temp, odometer_km)"
        " VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)

    db = db_reader._get()
    assert db_reader._charge_has_active_use(db, start.isoformat(), end.isoformat()) is True, (
        "a charge with the cabin heater running is no longer recognised"
    )

    health = db_reader.get_battery_health()
    assert health["points"], "the charge vanished from the chart"
    assert health["points"][0]["excluded"] is True
    assert health["points"][0]["exclude_reason"] == "active_use", health["points"][0]
