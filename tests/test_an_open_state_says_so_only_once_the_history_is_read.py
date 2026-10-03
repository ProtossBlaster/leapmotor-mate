"""While the history is still being read, a state with no end yet says nothing about it.

An install with months of rows gets its events a batch per poll round, oldest first. Half-way
through, a state that began a year ago may have its start stored and its end not yet: calling it
"(in progress)" would be wrong, and the page says instead that the history is still being read, with
how far it got in rows. Once the detector has read the history through, the same start says
"(in progress)" — given a fresh frame — and a row the web writes meanwhile (the state a command just
set, a refresh) is the next round's, not history.
"""
from datetime import datetime, timedelta, timezone

import db_reader
from events_fixture import Car, grouped, rows, web


def _cable_in_since(car, frames=7):
    """A parked frame, then the cable in for the rest: the newest frame is now."""
    car.t = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=30 * frames)
    car.frame()
    for _ in range(frames - 1):
        car.frame(**{"1149": 1})


def test_half_read_history_shows_the_start_without_a_word_on_its_end(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    _cable_in_since(car)
    car.derive(max_rows=4)
    ev = grouped()
    (row,) = rows(ev)
    assert row["open"] and not row["still_open"] and row["last_frame_hhmm"] is None
    assert not ev["live"]["caught_up"] and ev["live"]["pct"] == 57
    html = client.get("/events").text
    assert "History still being read (57 %)" in html and "(in progress)" not in html


def test_once_the_history_is_read_the_start_says_in_progress_whatever_the_web_writes(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    _cable_in_since(car)
    car.derive(max_rows=4)
    car.derive(max_rows=4)
    db_reader.write_optimistic_status({"is_locked": 0})            # after a command, before the next round
    (row,) = rows(grouped())
    assert row["still_open"]
    html = client.get("/events").text
    assert "(in progress)" in html and "History still being read" not in html


def test_how_far_counts_the_rows_left_after_the_retention(tmp_path, monkeypatch):
    car = Car(tmp_path)
    web(car, monkeypatch)
    _cable_in_since(car, frames=8)
    car.db._conn.execute("DELETE FROM positions WHERE id <= 2")    # the oldest pruned: ids begin at 3
    car.db._conn.commit()
    car.derive(max_rows=3)
    assert grouped()["live"] == {**grouped()["live"], "caught_up": False, "pct": 50}
    car.derive(max_rows=3)                                         # a full batch: maybe more to come
    assert not grouped()["live"]["caught_up"]
    car.derive(max_rows=3)                                         # nothing came: read through
    assert grouped()["live"]["caught_up"]
