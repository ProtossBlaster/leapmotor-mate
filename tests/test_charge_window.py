"""The "actual charging window" must be derived correctly even though get_charges() hands the charge
timestamps in LOCAL offset while positions.recorded_at is UTC (regression for the v1.11.12 TZ bug where
the start showed +2h)."""
import sqlite3

import db_reader


def _con(samples):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE positions (recorded_at TEXT, charging INT)")
    con.executemany("INSERT INTO positions VALUES (?,?)", samples)
    con.execute("ALTER TABLE positions ADD COLUMN vehicle_id INTEGER DEFAULT 1")
    # Parked, as every sample in this fixture means: a charge sample is now "the flag, OR a
    # charging current while stationary" (#341, db_reader._charging_sample).
    con.execute("ALTER TABLE positions ADD COLUMN speed_kmh REAL DEFAULT 0")
    con.execute("ALTER TABLE positions ADD COLUMN gear TEXT DEFAULT 'P'")
    con.execute("ALTER TABLE positions ADD COLUMN charge_current_a REAL")
    con.commit()
    return con


def test_active_window_normalizes_local_bounds_to_utc():
    # Real charging power flowed 16:48:59 → 21:18:36 UTC (idle plug before/after).
    con = _con([
        ("2026-06-02T16:48:39+00:00", 0),   # plugged, no power
        ("2026-06-02T16:48:59+00:00", 1),   # first real power
        ("2026-06-02T19:00:00+00:00", 1),
        ("2026-06-02T21:18:36+00:00", 1),   # last real power
        ("2026-06-02T21:40:00+00:00", 0),   # idle tail before unplug
    ])
    # get_charges() passes LOCAL (+02:00) bounds — the helper must normalize to UTC before comparing.
    rs, re = db_reader._charge_active_window(
        con, "2026-06-02T18:48:39+02:00", "2026-06-02T23:53:43+02:00")
    assert rs == "2026-06-02T16:48:59+00:00"   # the real first-power sample, NOT shifted by the offset
    assert re == "2026-06-02T21:18:36+00:00"


def test_window_display_differs_flag():
    # End differs by ~35 min → show the line.
    con = _con([("2026-06-02T16:48:59+00:00", 1), ("2026-06-02T21:18:36+00:00", 1)])
    assert db_reader._charge_window_display(
        con, "2026-06-02T18:48:39+02:00", "2026-06-02T23:53:43+02:00")["differs"] is True

    # Normal charge — power window ≈ plug window → no extra line.
    con2 = _con([("2026-06-01T15:07:00+00:00", 1), ("2026-06-01T17:13:30+00:00", 1)])
    assert db_reader._charge_window_display(
        con2, "2026-06-01T17:06:40+02:00", "2026-06-01T19:14:07+02:00")["differs"] is False

    # No power samples (pruned/old) → no line.
    assert db_reader._charge_window_display(
        _con([]), "2026-06-01T17:06:40+02:00", "2026-06-01T19:14:07+02:00")["differs"] is False
