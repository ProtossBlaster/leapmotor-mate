"""A span's other end is the next row written, not the next row in time.

The host clock can step back between two frames (an NTP correction on the host), and the frame that
closes a span may then carry a time BEFORE the frame that opened it. In write order the pair is still
a pair: the retention keeps or removes both together, and no reading of the table shows the span as
still open because its end sorts before its start.
"""
import events as E
from events_fixture import Car

OPENED, CLOSED = "2026-09-20T10:01:00+00:00", "2026-09-20T10:00:30+00:00"


def _skewed_pair(car):
    car.db._conn.executemany(
        "INSERT INTO events (vehicle_id, kind, at, state) VALUES (?, 'unlocked', ?, ?)",
        [(car.vid, OPENED, 1), (car.vid, CLOSED, 0)])
    car.db._conn.commit()


def _rows(car):
    return [(r[0], r[1]) for r in car.db._conn.execute("SELECT at, state FROM events ORDER BY id")]


def test_a_cut_off_between_the_two_times_keeps_the_pair(tmp_path):
    car = Car(tmp_path)
    _skewed_pair(car)
    assert E.prune(car.db._conn, "2026-09-20T10:00:45+00:00") == 0
    assert _rows(car) == [(OPENED, 1), (CLOSED, 0)]


def test_a_cut_off_after_both_removes_the_pair(tmp_path):
    car = Car(tmp_path)
    _skewed_pair(car)
    assert E.prune(car.db._conn, "2026-09-20T10:02:00+00:00") == 2
    assert _rows(car) == []
