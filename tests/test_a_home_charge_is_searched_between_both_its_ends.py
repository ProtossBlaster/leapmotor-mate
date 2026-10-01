"""Whether a home charge still has a power curve is asked between BOTH ends of the charge.

#363 (@hubcasale): `_wallbox_home_charges_raw` asked it with an EXISTS whose upper bound read
`(c.ended_at IS NULL OR p.recorded_at <= c.ended_at)`. That OR is dead there — the same query
already requires `c.ended_at IS NOT NULL` — but an OR is never an index constraint, so SQLite
searched from the charge's start onwards only, and a charge with no sample at all walked every row
recorded after it before concluding there was none. `charges_with_power` had the same upper bound,
and there the OR is alive: a charge still running has no end yet, and still has to qualify, which is
why it became `COALESCE(c.ended_at, '9999')` and not a plain `<=`.

Measured on a copy of the lab database (382 578 positions, 32 home charges) with one home charge
that has no sample: 17.7 ms -> 0.1 ms for `_wallbox_home_charges_raw`, 17.7 ms -> 0.05 ms for
`charges_with_power`, outputs identical.

The fix went in with no test of its own: with the OR put back in both queries the whole suite stayed
green, 4823 passed. These pin it on the plan of the statement each function actually runs —
captured from its connection, not copied here, so a copy drifting away from the code cannot keep
this green.
"""
import sqlite3

import db as D
import db_reader
import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    """One car, a finished home charge and one still running, each with a sample inside it."""
    path = str(tmp_path / "home.db")
    database = D.Database(path)
    conn = database._conn
    conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1, 'LVIN0000000000001', 'B10')")
    for cid, started, ended in ((1, "2026-09-20T20:00:00+00:00", "2026-09-21T02:00:00+00:00"),
                                (2, "2026-10-01T20:00:00+00:00", None)):
        conn.execute("INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc,"
                     " energy_added_kwh, ac_energy_kwh, location_type)"
                     " VALUES (?, 1, ?, ?, 40, 80, 20.0, 22.0, 'HOME')", (cid, started, ended))
        conn.execute("INSERT INTO positions (vehicle_id, recorded_at, soc, charging)"
                     " VALUES (1, ?, 50, 1)", (started.replace("T20:00", "T20:30"),))
    conn.commit()
    database.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    db_reader._drop_read_connection()
    yield path
    db_reader._drop_read_connection()


def _searches_of_positions(path, ask, monkeypatch):
    """The plan of every statement `ask` sends that searches positions inside a charge."""
    sent = []
    opened = db_reader._conn

    def traced(db_path):
        conn = opened(db_path)
        conn.set_trace_callback(sent.append)
        return conn

    monkeypatch.setattr(db_reader, "_conn", traced)
    db_reader._drop_read_connection()
    ask()
    asked = [sql for sql in sent if "FROM positions p" in sql]
    assert asked, "the function no longer asks positions inside a charge: rewrite this test"
    explain = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return [" | ".join(row[-1] for row in explain.execute("EXPLAIN QUERY PLAN " + sql))
                for sql in asked]
    finally:
        explain.close()


def _bounded_on_both_sides(plan):
    search = [step for step in plan.split(" | ") if step.startswith(("SEARCH p ", "SCAN p"))]
    return (len(search) == 1 and search[0].startswith("SEARCH p ")
            and "recorded_at>?" in search[0] and "recorded_at<?" in search[0])


def test_the_wallbox_history_searches_each_charge_between_both_its_ends(home, monkeypatch):
    for plan in _searches_of_positions(home, db_reader._wallbox_home_charges_raw, monkeypatch):
        assert _bounded_on_both_sides(plan), (
            f"a home charge with no sample walks every row recorded after it: {plan}")


def test_the_power_comparison_searches_each_charge_between_both_its_ends(home, monkeypatch):
    for plan in _searches_of_positions(home, lambda: db_reader.charges_with_power(30), monkeypatch):
        assert _bounded_on_both_sides(plan), (
            f"a home charge with no sample walks every row recorded after it: {plan}")


def test_a_charge_still_running_keeps_its_power_curve(home):
    """The OR in charges_with_power was alive: the bound that replaced it must still let a charge
    with no end through, or the comparison loses the session in progress."""
    assert [row["id"] for row in db_reader.charges_with_power(30)] == [2, 1]
