"""A span's other end is the next row written, not the next row in time.

The host clock can step back between two frames (an NTP correction on the host), and the frame that
closes a span may then carry a time BEFORE the frame that opened it. In write order the pair is still
a pair: the retention keeps or removes both together, and the page neither shows the start as still
open because its end sorts before it, nor gives the end a length.
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
    assert E.prune(car.db._conn, car.vid, "2026-09-20T10:00:45+00:00") == 0
    assert _rows(car) == [(OPENED, 1), (CLOSED, 0)]


def test_a_cut_off_after_both_removes_the_pair(tmp_path):
    car = Car(tmp_path)
    _skewed_pair(car)
    assert E.prune(car.db._conn, car.vid, "2026-09-20T10:02:00+00:00") == 2
    assert _rows(car) == []


def test_the_page_shows_the_pair_closed_and_the_end_without_a_length(tmp_path, monkeypatch):
    """Sorted by time the end comes first; in write order it still closes the start, which is
    therefore not open, and it has no length to print."""
    from events_fixture import grouped, rows, web
    car = Car(tmp_path)
    web(car, monkeypatch, zone="UTC")
    _skewed_pair(car)
    start, end = rows(grouped(date_from="2026-09-20", date_to="2026-09-20"))
    assert (start["hms"], start["on"], start["open"]) == ("10:01:00", True, False)
    assert (end["hms"], end["on"], end["from_anchor"], end["duration_min"]) == ("10:00:30", False, "ev-signal-1", None)


def test_the_line_joins_the_pair_whichever_row_is_higher(tmp_path, monkeypatch):
    """The start sorts above its end here; the track still runs between the two rows, turning at
    the upper one (the start) and the lower one (the end)."""
    from events_fixture import grouped, web
    car = Car(tmp_path)
    web(car, monkeypatch, zone="UTC")
    _skewed_pair(car)
    ev = grouped(date_from="2026-09-20", date_to="2026-09-20")
    assert ev["lanes"] == 1
    assert [(line["kind"], line["cells"]) for line in ev["lines"]] == [
        ("day", [""]), ("hour", [""]), ("row", ["n-security"]), ("row", ["u-security"])]
