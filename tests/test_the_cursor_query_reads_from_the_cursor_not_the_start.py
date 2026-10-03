"""Deriving the events costs what is new, not what is stored.

Once the history has been read, a round of the poll loop has one or two new rows per car; the
query that fetches them starts at the cursor on the (vehicle_id, id) index and never walks the
rows before it. On a 50 000-row history the second call runs that one indexed SELECT and nothing
else against `positions`.
"""
import re

from events_fixture import T0, Car


def _history(car, rows=50_000):
    base = int(T0.timestamp() * 1000)
    car.db._conn.executemany(
        "INSERT INTO positions (vehicle_id, recorded_at, frame_ts, is_locked, gear, speed_kmh)"
        " VALUES (?, ?, ?, 1, 'P', 0)",
        ((car.vid, f"2026-09-{1 + i // 5000:02d}T00:00:00+00:00", base + i * 30_000) for i in range(rows)))
    car.db._conn.commit()


def test_the_positions_query_searches_the_index_from_the_cursor(tmp_path):
    car = Car(tmp_path)
    _history(car)
    while car.derive(5000):
        pass
    statements = []
    car.db._conn.set_trace_callback(statements.append)
    assert car.derive() == 0
    car.db._conn.set_trace_callback(None)
    on_positions = [s for s in statements if re.search(r"\bFROM positions\b", s)]
    assert len(on_positions) == 1, on_positions
    plan = " ".join(r[3] for r in car.db._conn.execute("EXPLAIN QUERY PLAN " + on_positions[0]))
    assert "idx_positions_vehicle_order" in plan and "id>?" in plan.replace(" ", ""), plan
    assert "SCAN" not in plan, plan
