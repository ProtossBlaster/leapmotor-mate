"""The Events page lists where the sunshade stopped, one row per stop, with no line across the page.

Signal 1724 is how far the sunshade is open. It carries no "moving" flag and no target, but a frame
comes about every 12 s while the car is awake and the sunshade travels about 7 % a second, so the
same value in two consecutive frames is a stop. The detector's two-frame rule finds those; a stop
shorter than the time between two frames is not seen.
A stop is a moment, not a span: the sunshade stays open for days, and a pair of rows joined by a
track drew one line through all of them.
"""
import json
from datetime import datetime, timedelta, timezone

import events as E
from events_fixture import Car, event_row, grouped, row_text, rows, web

# A B10, 4 Oct 2026, from 100 %: seconds since the previous frame, and 1724 in that frame. The roof
# was stopped at 50 % and at 20 % from the car's screen, then opened to 100 %.
MORNING = [(0, 100), (11, 100), (9, 100), (13, 100), (12, 100), (12, 50), (8, 50), (13, 50),
           (12, 20), (12, 0), (11, 10), (13, 20), (9, 20), (12, 40), (12, 100), (12, 100), (9, 100)]


def _drive(car, frames):
    stored = [car.frame(seconds=s or 30, **{"1724": v}) for s, v in frames]
    car.derive()
    return stored


def _levels(car):
    return [(state, at) for kind, state, at in car.events() if kind == "sunshade_level"]


def test_the_stops_of_a_real_morning_are_listed_at_their_first_frame(tmp_path):
    car = Car(tmp_path)
    stored = _drive(car, MORNING)
    assert _levels(car) == [(50, stored[5]["recorded_at"]), (20, stored[11]["recorded_at"]),
                            (100, stored[14]["recorded_at"])]


def test_a_level_seen_in_one_frame_only_is_not_listed(tmp_path):
    car = Car(tmp_path)
    _drive(car, [(0, 0), (12, 0), (12, 35), (12, 100), (12, 100)])
    assert [state for state, _ in _levels(car)] == [100]


def test_a_stop_is_a_row_on_its_own(tmp_path, monkeypatch):
    """No track, no "after" and no "from": the row says the level and nothing joins it to another."""
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    at = datetime.now(timezone.utc) - timedelta(hours=2)
    event_row(car, "sunshade_level", at, state=20)
    event_row(car, "sunshade_level", at + timedelta(hours=1), state=0)
    ev = grouped()
    listed = rows(ev)
    assert [e["label"] for e in listed] == ["Sunshade closed", "Sunshade 20% open"]
    assert all(e["start"] is None and e["end"] is None and e["duration_min"] is None for e in listed)
    assert ev["lanes"] == 0 and not any(p.get("joined") for p in ev["lines"])
    page = client.get("/events").text
    assert [row_text(page, e["anchor"]) for e in listed] == ["Sunshade closed", "Sunshade 20% open"]


def test_the_rows_of_the_old_open_or_closed_kind_stay_unread(tmp_path, monkeypatch):
    """A database that kept the sunshade as open/closed, its last state confirmed open and a close
    pending, starts the level from its first two frames: no "1 %" and no row at that baseline."""
    car = Car(tmp_path)
    web(car, monkeypatch)
    old = datetime.now(timezone.utc) - timedelta(hours=3)
    event_row(car, "sunshade", old, state=1)
    first = {"at": old.isoformat(), "frame_ts": None, "lat": None, "lon": None, "soc": None, "odo": None,
             "inside": None, "target": None, "outside": None}
    car.db.set_setting(E.STATE_KEY.format(vehicle_id=car.vid), json.dumps(
        {"cursor_id": 0, "last_frame_ts": None,
         "kinds": {"sunshade": {"confirmed": 1, "pending": 0, "first": first}}}))
    _drive(car, [(0, 100), (12, 100), (12, 100)])
    assert _levels(car) == []
    assert [e["kind"] for e in rows(grouped())] == []


def test_a_stop_ages_out_on_its_own(tmp_path):
    car = Car(tmp_path)
    cut = datetime.now(timezone.utc) - timedelta(days=180)
    for kind, days, state in (("sunshade_level", -9, 50), ("sunshade_level", -3, 0),
                              ("sunshade_level", 2, 100), ("cable", -5, 1)):
        event_row(car, kind, cut + timedelta(days=days), state=state)
    car.db.prune_positions(180)
    assert [(k, s) for k, s, _ in car.events()] == [("sunshade_level", 100), ("cable", 1)]
