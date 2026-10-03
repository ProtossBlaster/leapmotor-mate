"""The events age out with the positions they were derived from, span by span.

The retention that prunes `positions` prunes `events` to the same date, but in pairs: a span that
ended before the cut-off goes whole; a span that crosses it, or is still open, keeps its start —
and later its end, so a cable plugged in before the cut-off and pulled after it stays in the kept
period, and the end, deleted on its own, could not reopen the span. Retention 0 keeps everything,
as it does for positions.
"""
from datetime import datetime, timedelta, timezone

import events as E
from events_fixture import Car

NOW = datetime.now(timezone.utc)
CUT = NOW - timedelta(days=180)


def _at(days_from_cut):
    return (CUT + timedelta(days=days_from_cut)).isoformat()


def _seed(car, rows):
    car.db._conn.executemany(
        "INSERT INTO events (vehicle_id, kind, at, state) VALUES (?, ?, ?, ?)",
        [(car.vid, kind, _at(days), state) for kind, days, state in rows])
    car.db._conn.commit()


def _left(car):
    return [(r["kind"], round((datetime.fromisoformat(r["at"]) - CUT).total_seconds() / 86400), r["state"])
            for r in car.db._conn.execute("SELECT kind, at, state FROM events ORDER BY id")]


ROWS = [("trunk", -40, 0),                   # an end without a start: an orphan
        ("cable", -10, 1), ("cable", -9, 0),  # a span that ended before the cut-off
        ("cable", -5, 1), ("cable", +1, 0),   # a span across the cut-off
        ("cable", +3, 1),                     # a span still open
        ("climate", -2, 1)]                   # another kind, open across the cut-off


def test_only_whole_spans_before_the_cut_off_go(tmp_path):
    car = Car(tmp_path)
    _seed(car, ROWS)
    car.db.prune_positions(180)
    assert _left(car) == [("cable", -5, 1), ("cable", 1, 0), ("cable", 3, 1), ("climate", -2, 1)]


def test_retention_zero_keeps_everything(tmp_path):
    car = Car(tmp_path)
    _seed(car, ROWS)
    assert car.db.prune_positions(0) == 0
    assert len(_left(car)) == len(ROWS)


def test_a_kept_start_gets_its_end_after_the_prune_and_a_restart(tmp_path):
    """The cable plugged in before the cut-off is still in; when it comes out, that end belongs
    to the kept start."""
    car = Car(tmp_path)
    car.frame()
    car.frame()
    car.frame(**{"1149": 1})
    car.frame(**{"1149": 1})
    car.derive()
    car.db._conn.execute("UPDATE events SET at = ? WHERE kind = 'cable'", (_at(-2),))
    car.db._conn.commit()
    car.db.prune_positions(180)
    car.restart()
    car.frame()
    car.frame()
    car.derive()
    assert [(k, s) for k, s, _ in car.events()] == [("cable", 1), ("cable", 0)]


def test_the_prune_is_the_modules_own_and_cuts_at_the_given_time(tmp_path):
    car = Car(tmp_path)
    _seed(car, [("cable", -3, 1), ("cable", -2, 0), ("cable", -1, 1)])
    assert E.prune(car.db._conn, _at(0)) == 2
    car.db._conn.commit()
    assert _left(car) == [("cable", -1, 1)]
