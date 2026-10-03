"""How far through the history the derivation is: counted as it goes, not queried every round.

Both halves of that percentage used to be a full `COUNT(*)` of `positions` on EVERY round — one for
the rows behind the cursor, one for the rows ahead. On a Raspberry Pi 5 with a 390,000-row database
each costs about 11 ms against a round of roughly 119 ms: a fifth of the work, for a number nobody
is watching that closely.

`done` is what this reader has read — the previous `done` plus this batch — and `total` is read once,
when catch-up begins. It is read again only when `done` passes it, which means rows arrived while we
were reading, and then "how much of the history" has a new answer anyway. Both live in the same
`settings` row as the cursor, written in the same transaction as the events of that round, so a
restart mid-catch-up resumes with the figure it had.

Two things the figure must not do: lie after a deletion, and creep past 100.
"""
import json

import db as D
import events as E
from events_fixture import Car

BATCH = 100


class Counting:
    """The connection, with every statement recorded. Thin on purpose: `consume` is handed a
    connection and nothing else, so counting here counts exactly what it does."""

    def __init__(self, conn):
        self._conn = conn
        self.sql = []

    def execute(self, sql, *a):
        self.sql.append(" ".join(sql.split()))
        return self._conn.execute(sql, *a)

    def executemany(self, sql, *a):
        self.sql.append(" ".join(sql.split()))
        return self._conn.executemany(sql, *a)

    def __enter__(self):
        return self._conn.__enter__()

    def __exit__(self, *e):
        return self._conn.__exit__(*e)

    def counts(self):
        return [s for s in self.sql if "COUNT(*) FROM positions" in s]


def a_history(tmp_path, rows):
    """A car with `rows` stored frames and nothing derived from them yet."""
    car = Car(tmp_path)
    car.db._conn.executemany(
        "INSERT INTO positions (vehicle_id, recorded_at, frame_ts, is_locked, gear, speed_kmh)"
        " VALUES (?, '2026-09-01T00:00:00+00:00', ?, 1, 'P', 0)",
        [(car.vid, 1_760_000_000_000 + i * 30_000) for i in range(rows)])
    car.db._conn.commit()
    return car


def state(car):
    row = car.db._conn.execute("SELECT value FROM settings WHERE key = ?",
                               (E.STATE_KEY.format(vehicle_id=car.vid),)).fetchone()
    return json.loads(row[0])


def test_one_count_for_a_whole_catch_up_not_two_per_round(tmp_path):
    car = a_history(tmp_path, 450)
    conn = Counting(car.db._conn)
    pcts = []
    while E.consume(conn, car.vid, max_rows=BATCH):
        pcts.append(state(car)["pct"])
    assert pcts == [22, 44, 66, 88, 100]          # four full batches, then the 50 that remain
    assert len(conn.counts()) == 1, conn.counts()


def test_the_figure_resumes_where_a_restart_left_it(tmp_path):
    car = a_history(tmp_path, 450)
    E.consume(car.db._conn, car.vid, max_rows=BATCH)
    assert (state(car)["done"], state(car)["total"]) == (100, 450)
    car.restart()                                  # a new poller process, reading the state back
    conn = Counting(car.db._conn)
    E.consume(conn, car.vid, max_rows=BATCH)
    assert state(car)["pct"] == 44
    assert conn.counts() == []                     # the total was already known


def test_rows_arriving_during_the_catch_up_are_counted_once_more(tmp_path):
    """`done` passing `total` is the one thing that can only mean the history grew. The figure then
    costs one more count, and never prints more than 100."""
    car = a_history(tmp_path, 150)
    conn = Counting(car.db._conn)
    E.consume(conn, car.vid, max_rows=BATCH)
    assert (state(car)["done"], state(car)["total"], state(car)["pct"]) == (100, 150, 66)
    car.db._conn.executemany(
        "INSERT INTO positions (vehicle_id, recorded_at, frame_ts, is_locked, gear, speed_kmh)"
        " VALUES (?, '2026-09-02T00:00:00+00:00', ?, 1, 'P', 0)",
        [(car.vid, 1_770_000_000_000 + i * 30_000) for i in range(200)])
    car.db._conn.commit()
    E.consume(conn, car.vid, max_rows=BATCH)
    assert state(car)["done"] == 200 and state(car)["total"] == 350
    assert state(car)["pct"] == 57 <= 100
    assert len(conn.counts()) == 2


def test_catching_up_forgets_the_count_so_the_next_one_starts_fresh(tmp_path):
    car = a_history(tmp_path, 150)
    while E.consume(car.db._conn, car.vid, max_rows=BATCH):
        pass
    assert state(car)["caught_up"] and state(car)["pct"] == 100
    assert "done" not in state(car) and "total" not in state(car)


def test_a_deletion_forgets_the_count_rather_than_reporting_a_shorter_history(tmp_path):
    """`clamp_cursors` runs whenever `positions` rows are deleted, and pulls a cursor back to what
    is left. A `done` counted over rows that are gone would describe a history that no longer
    exists, so it goes with them."""
    car = a_history(tmp_path, 450)
    E.consume(car.db._conn, car.vid, max_rows=BATCH)
    assert state(car)["done"] == 100
    car.db._conn.execute("DELETE FROM positions WHERE vehicle_id = ?", (car.vid,))
    with car.db._conn:
        E.clamp_cursors(car.db._conn)
    assert state(car)["cursor_id"] == 0
    assert "done" not in state(car) and "total" not in state(car)


def test_the_percentage_a_car_shows_is_still_the_one_the_page_reads(tmp_path):
    """The page says "History still being read (N %)"; the figure it reads is this one, and the
    `Database` wrapper the poller calls is the same path."""
    car = a_history(tmp_path, 450)
    assert car.db.derive_events(car.vid, max_rows=BATCH) == BATCH
    assert state(car)["pct"] == 22 and not state(car)["caught_up"]
    assert isinstance(car.db, D.Database)
