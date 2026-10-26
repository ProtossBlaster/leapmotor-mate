"""The Overview map draws the route of today's last trip under the car.

The map showed only where the car is. It now also draws the selected car's latest trip — while it is
under way, or once it has ended if its last point is from today in the owner's timezone — with the
lines the trip's own page draws: the same points, frozen cloud stretches dropped and signal losses
dashed. The next trip replaces it, and a newest trip without points draws nothing rather than an
older line that ends somewhere the car is not.

The route comes with the marker, from `main._last_position`, and the map asks for it every few
seconds all day. A route is a few kilobytes, so a map that has drawn one says which (`?track=<key>`)
and is told only that it is unchanged (`segments: None`) until its points change.
"""
from datetime import date, datetime, timedelta, timezone

import db_reader
import pytest
from test_the_overview_map_follows_the_car import _drawn, mate  # noqa: F401  (mate: fixture)

TODAY = date(2026, 10, 19)
NOON = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def today(mate, monkeypatch):
    """The day the map counts as today, fixed so no test depends on the hour it runs at."""
    monkeypatch.setattr(db_reader, "today_local", lambda: TODAY)
    db_reader.set_setting("timezone", "UTC")


def _trip(pdb, trip_id, started, ended=None, *, vehicle=1, merged_into=None):
    pdb._conn.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, merged_into_id) VALUES (?,?,?,?,?)",
                      (trip_id, vehicle, started.isoformat(), ended and ended.isoformat(), merged_into))
    pdb._conn.commit()


def _points(pdb, trip_id, start, n, *, first=0, every_s=30, lat=52.40):
    """Points of a car on the move, one every `every_s`; the k-th at lat + k * 0.002."""
    for k in range(first, first + n):
        pdb._conn.execute("INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude, speed_kmh, soc)"
                          " VALUES (?,?,?,16.9,50,?)",
                          (trip_id, (start + timedelta(seconds=every_s * k)).isoformat(), lat + k * 0.002, 80 - k * 0.1))
    pdb._conn.commit()


def _line(n, *, first=0, lat=52.40):
    return [[round(lat + k * 0.002, 5), 16.9] for k in range(first, first + n)]


def _track(client, have=None):
    r = client.get("/api/last-position", params={} if have is None else {"track": have})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    return r.json()["track"]


NOTHING = {"key": None, "segments": []}


def test_a_trip_under_way_is_drawn_and_each_new_point_moves_it_on(mate):
    """Whatever day it started on: one begun before midnight is still the car's route."""
    pdb, client = mate
    start = NOON.replace(hour=0) - timedelta(minutes=5)
    _trip(pdb, 1, start)
    _points(pdb, 1, start, 5)
    before = _track(client)
    assert before["segments"] == [{"points": _line(5), "gap": False}]
    _points(pdb, 1, start, 1, first=5)
    after = _track(client)
    assert after["segments"] == [{"points": _line(6), "gap": False}]
    assert after["key"] != before["key"]
    assert _drawn(client.get("/").text)["track"] == after


@pytest.mark.parametrize("day, drawn", [(0, True), (-1, False)])
def test_a_trip_that_ended_is_drawn_on_the_day_it_ended(mate, day, drawn):
    pdb, client = mate
    start = NOON + timedelta(days=day)
    _trip(pdb, 1, start, start + timedelta(minutes=5))
    _points(pdb, 1, start, 10)
    assert _track(client)["segments"] == ([{"points": _line(10), "gap": False}] if drawn else [])


@pytest.mark.parametrize("zone, drawn", [("Europe/Warsaw", True), ("UTC", False)])
def test_today_is_the_owners_day(mate, zone, drawn):
    """23:30 UTC on the 18th is already the 19th in Warsaw."""
    pdb, client = mate
    db_reader.set_setting("timezone", zone)
    start = datetime(2026, 10, 18, 23, 30, tzinfo=timezone.utc)
    _trip(pdb, 1, start, start + timedelta(minutes=5))
    _points(pdb, 1, start, 10)
    assert bool(_track(client)["segments"]) is drawn


@pytest.mark.parametrize("joined", [False, True])
def test_the_next_trip_replaces_the_line_even_without_points_of_its_own(mate, joined):
    """A trip rebuilt from the cloud has no points. The earlier line would end where the car was
    before it, so nothing is drawn — also when the rebuilt trip was joined to that earlier one."""
    pdb, client = mate
    _trip(pdb, 1, NOON - timedelta(hours=3), NOON - timedelta(hours=2))
    _points(pdb, 1, NOON - timedelta(hours=3), 10)
    _trip(pdb, 2, NOON, NOON + timedelta(minutes=20))
    _points(pdb, 2, NOON, 10, lat=52.50)
    drawn = _track(client)
    assert drawn["segments"] == [{"points": _line(10, lat=52.50), "gap": False}]
    _trip(pdb, 3, NOON + timedelta(hours=1), NOON + timedelta(hours=2), merged_into=2 if joined else None)
    assert _track(client) == NOTHING
    assert _track(client, have=drawn["key"]) == NOTHING


def test_a_trip_ended_today_is_drawn_though_its_frozen_end_began_yesterday(mate):
    """The day is its last reading's, even one the line leaves out: the cloud repeated the 23:58
    reading until 00:04, and the trip ended today."""
    pdb, client = mate
    start = datetime(2026, 10, 18, 23, 50, tzinfo=timezone.utc)
    _trip(pdb, 1, start, start + timedelta(minutes=14))
    _points(pdb, 1, start, 17)
    for k in range(17, 29):
        pdb._conn.execute("INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude, speed_kmh, soc)"
                          " VALUES (1,?,?,16.9,50,?)",
                          ((start + timedelta(seconds=30 * k)).isoformat(), 52.40 + 16 * 0.002, 80 - 16 * 0.1))
    pdb._conn.commit()
    assert _track(client)["segments"] == [{"points": _line(17), "gap": False}]


def test_a_joined_trip_is_drawn_whole_with_its_pause_as_a_gap(mate):
    """Its newest piece is a merged child; the line is the whole group's."""
    pdb, client = mate
    _trip(pdb, 1, NOON, NOON + timedelta(minutes=5))
    _points(pdb, 1, NOON, 10)
    later = NOON + timedelta(minutes=30)
    _trip(pdb, 2, later, later + timedelta(minutes=5), merged_into=1)
    _points(pdb, 2, later, 10, first=10)
    assert _track(client)["segments"] == [{"points": _line(10), "gap": False},
                                          {"points": [_line(1, first=9)[0], _line(1, first=10)[0]], "gap": True},
                                          {"points": _line(10, first=10), "gap": False}]


def test_it_is_the_line_the_trips_own_page_draws(mate):
    """One rule: a stretch the cloud served frozen is dropped and a signal loss is dashed, here as there."""
    pdb, client = mate
    _trip(pdb, 1, NOON, NOON + timedelta(minutes=20))
    _points(pdb, 1, NOON, 6)
    for k in range(6, 10):            # the cloud repeats point 5 for two minutes
        pdb._conn.execute("INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude, speed_kmh, soc)"
                          " VALUES (1,?,?,16.9,50,?)",
                          ((NOON + timedelta(seconds=30 * k)).isoformat(), 52.41, 80 - 5 * 0.1))
    _points(pdb, 1, NOON, 10, first=30)   # ten minutes later, after a signal loss
    segments = _track(client)["segments"]
    assert [len(s["points"]) for s in segments] == [6, 2, 10] and segments[1]["gap"]
    assert segments == db_reader.get_trip_detail(1)["route_segments"]


def test_a_map_that_has_the_line_is_told_only_that_it_is_unchanged(mate):
    pdb, client = mate
    assert _track(client) == NOTHING                      # never None for a map that asked for nothing
    _trip(pdb, 1, NOON)
    _points(pdb, 1, NOON, 5)
    full = _track(client)
    assert _track(client, have=full["key"]) == {"key": full["key"], "segments": None}
    assert _track(client, have="1:4:an older one") == full
    _points(pdb, 1, NOON, 1, first=5)
    assert _track(client, have=full["key"])["segments"] == [{"points": _line(6), "gap": False}]


def test_after_midnight_a_map_with_yesterdays_line_is_told_to_take_it_off(mate, monkeypatch):
    pdb, client = mate
    _trip(pdb, 1, NOON, NOON + timedelta(minutes=5))
    _points(pdb, 1, NOON, 10)
    key = _track(client)["key"]
    monkeypatch.setattr(db_reader, "today_local", lambda: TODAY + timedelta(days=1))
    assert _track(client, have=key) == NOTHING


def test_another_cars_trip_is_not_drawn(mate):
    pdb, client = mate
    pdb._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (2,'W','C10')")
    _trip(pdb, 1, NOON, NOON + timedelta(minutes=5))
    _points(pdb, 1, NOON, 10)
    _trip(pdb, 2, NOON + timedelta(hours=1), vehicle=2)
    _points(pdb, 2, NOON + timedelta(hours=1), 10, lat=52.50)
    assert _track(client)["segments"] == [{"points": _line(10), "gap": False}]
    db_reader.set_active_vehicle("W")
    assert _track(client)["segments"] == [{"points": _line(10, lat=52.50), "gap": False}]
