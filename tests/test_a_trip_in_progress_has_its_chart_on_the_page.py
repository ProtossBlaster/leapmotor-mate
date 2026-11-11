"""While the car drives, the Trips page carries the trip in progress at its top: the chart its trip page draws,
live, with the time the trip began, how far it has gone and its SoC so far. The card is there only for a trip in
progress, and only once it has two readings to draw; a finished trip leaves the panel empty and is among the trips.
The page reloads once the trip it was drawn with is no longer the one in progress, so the panel names that trip
whether or not its chart can be drawn, and the page reads it before its history.
"""
import datetime as dt
import re
from types import SimpleNamespace

import db as D
import db_reader
import main
import pytest
from starlette.testclient import TestClient

VIN = "LVIN0000000000001"
STARTED = None     # set as each test starts, so the readings the fixture records are fresh enough for LIVE


@pytest.fixture
def car(tmp_path, monkeypatch):
    """A trip that began three minutes ago at 88.3 % and 2127 km; `car.polls(n)` records its next n polls."""
    global STARTED
    STARTED = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=3)).replace(microsecond=0)
    path = str(tmp_path / "t.db")
    c = D.Database(path)._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,?,'B10')", (VIN,))
    for key, value in (("timezone", "UTC"), ("setup_complete", "1"), ("language", "en")):
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
    c.execute("INSERT INTO trips (id, vehicle_id, started_at, start_soc, start_odometer_km) VALUES (7,1,?,88.3,2127)",
              (STARTED.isoformat(),))
    c.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setattr(db_reader, "_current_vehicle_id", lambda: 1)

    done = 0

    def polls(n):
        nonlocal done
        for k in range(done, done + n):
            at = (STARTED + dt.timedelta(seconds=11 * k)).isoformat()
            soc = round(88.3 - 0.4 * k, 1)
            c.execute("INSERT INTO positions (vehicle_id, recorded_at, gear, speed_kmh, soc, odometer_km)"
                      " VALUES (1,?,'D',50,?,?)", (at, soc, 2127 + k))
            c.execute("INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude, speed_kmh, soc, power_kw)"
                      " VALUES (7,?,?,9.0,50,?,12)", (at, 45 + 0.001 * k, soc))
        done += n
        c.commit()
    yield SimpleNamespace(polls=polls, db=c)
    c.close()


def _card(html):
    return html.split('id="trip-live-panel"')[1].split("<script>")[0] if 'id="trip-live-panel"' in html else html


def test_the_page_draws_the_trip_in_progress(car):
    car.polls(5)
    page = TestClient(main.app).get("/trips").text
    live = _card(page)
    assert 'id="trip-profile"' in live, "the chart of the trip in progress is in the panel"
    assert "Driving" in live and "LIVE" in live and "Trip data" in live
    assert STARTED.strftime("%H:%M") in live and "4 km" in live, "when it began and how far it has gone"
    assert "88.3%" in live and "86.7%" in live and "−1.6%" in live, "the SoC it began from, the SoC now, the change"


def test_the_card_writes_numbers_in_the_readers_language(car):
    car.polls(5)
    car.db.execute("UPDATE settings SET value = 'pl' WHERE key = 'language'")
    car.db.commit()
    live = TestClient(main.app).get("/api/trip-live-chart").text.split("<script>")[0]
    assert "88,3%" in live and "86,7%" in live and "−1,6%" in live


@pytest.mark.parametrize("frozen", [False, True], ids=["one reading", "then a frozen run"])
def test_a_trip_with_one_reading_to_draw_has_no_chart_yet_but_is_still_in_progress(car, frozen):
    """A run the cloud served frozen drops out of the chart, as on the trip page, so a chart can lose its second
    reading while the trip goes on: the panel still names the trip, or the page would reload as if it had ended."""
    car.polls(1)
    if frozen:
        for k in range(1, 8):
            car.db.execute("INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude, speed_kmh, soc, power_kw)"
                           " VALUES (7,?,45,9.0,50,88.3,12)", ((STARTED + dt.timedelta(seconds=11 * k)).isoformat(),))
        car.db.commit()
    polled = TestClient(main.app).get("/api/trip-live-chart").text
    assert 'id="trip-profile"' not in polled and 'data-trip="7"' in polled


def test_readings_that_stopped_coming_take_live_down(car):
    """The card says what the charge in progress says when the car is out of touch: the age of its last reading."""
    car.polls(5)
    for table, col in (("positions", "recorded_at"), ("trip_positions", "recorded_at"), ("trips", "started_at")):
        for rid, at in car.db.execute(f"SELECT id, {col} FROM {table}").fetchall():
            moved = dt.datetime.fromisoformat(at) - dt.timedelta(minutes=20)
            car.db.execute(f"UPDATE {table} SET {col} = ? WHERE id = ?", (moved.isoformat(), rid))
    car.db.commit()
    card = TestClient(main.app).get("/api/trip-live-chart").text.split("<script>")[0]
    assert 'id="trip-profile"' in card and "LIVE" not in card
    assert re.search(r"2\dm ago</span>", card), "the age of the last reading where LIVE was"


def test_a_trip_that_ends_while_the_page_is_drawn_still_reloads_it(car, monkeypatch):
    """Read after the history, the trip would be gone from both: no trip for the next poll to lose, and the old
    history for good."""
    car.polls(5)
    summary = db_reader.get_trips_summary

    def the_trip_ends_meanwhile():
        car.db.execute("UPDATE trips SET ended_at = ?, end_soc = 86.7, distance_km = 4 WHERE id = 7",
                       ((STARTED + dt.timedelta(minutes=1)).isoformat(),))
        car.db.commit()
        return summary()

    monkeypatch.setattr(db_reader, "get_trips_summary", the_trip_ends_meanwhile)
    client = TestClient(main.app)
    assert 'data-trip="7"' in client.get("/trips").text, "the page names the trip it was drawn with"
    assert 'data-trip=""' in client.get("/api/trip-live-chart").text, "so the next poll, with none, reloads it"


def test_a_finished_trip_leaves_the_panel_empty(car):
    car.polls(5)
    car.db.execute("UPDATE trips SET ended_at = ?, end_soc = 86.7, distance_km = 4 WHERE id = 7",
                   ((STARTED + dt.timedelta(minutes=1)).isoformat(),))
    car.db.commit()
    polled = TestClient(main.app).get("/api/trip-live-chart").text
    assert 'id="trip-profile"' not in polled and 'data-trip=""' in polled, \
        "no card, and the marker the page reloads on says no trip runs"
    assert 'id="trip-profile"' not in _card(TestClient(main.app).get("/trips").text)
