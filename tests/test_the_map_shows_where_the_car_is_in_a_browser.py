"""The Map shows where the car is, as the Overview does, and it can be switched off (D#459, @Coooogz).

The Map drew routes, frequent places and charging stations, and nothing said where the car was:
for that the reader had to go back to the Overview. Now a marker shows the car's last known
position. It comes from the same builder as the Overview's marker (main._last_position, read from
what the poller stored, never from Leapmotor) and follows it while the page is open. A box on the
legend row switches it off, and the choice is remembered like the frequent places' one.
The map is not moved to the car: the reader framed the routes they asked for.

Measured in a browser, because only a browser draws the marker and runs the poll.
Skips where it cannot run (no playwright, no Chromium), like the other browser tests.
"""
import datetime as dt
import sqlite3

import pytest

pytest.importorskip("fastapi", reason="web/main.py needs fastapi (absent in the minimal CI env)")
pytest.importorskip("uvicorn", reason="the page has to be SERVED, not rendered in-process")
sync_api = pytest.importorskip("playwright.sync_api", reason="needs playwright + `playwright install chromium`")

from web_in_a_browser import chromium, seed_database, served  # noqa: E402

_TRIP = ("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, duration_min, start_soc, end_soc)"
         " VALUES (1,1,'2026-07-04T08:00:00+00:00','2026-07-04T08:30:00+00:00',12,30,84,70)")
_POINT = "INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude) VALUES (1, ?, ?, ?)"
_POSITION = ("INSERT INTO positions (vehicle_id, recorded_at, latitude, longitude, soc, gear, speed_kmh, charging)"
             " VALUES (1, ?, ?, ?, 70, 'P', 0, 0)")

# Where Leaflet put the car's marker, or null when there is none on the map.
_CAR = """() => {
  let at = null;
  map.eachLayer(l => { if (l instanceof L.Marker && l.options.car) at = l.getLatLng(); });
  return at && [at.lat, at.lng]; }"""


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


@pytest.fixture(scope="module")
def mate(tmp_path_factory):
    data = tmp_path_factory.mktemp("map-car")
    db = data / "leapmotor_mate.db"
    seed_database(db, "LVIN0000000000001", [(_TRIP, ()),
                                            (_POINT, ("2026-07-04T08:00:00+00:00", 45.46, 9.19)),
                                            (_POINT, ("2026-07-04T08:30:00+00:00", 45.47, 9.20)),
                                            (_POSITION, (_now(), 45.47, 9.20))])
    with served(data, db) as url:
        yield url, db


@pytest.fixture
def page(mate):
    url, _ = mate
    pw, browser = chromium(sync_api)
    p = browser.new_page(viewport={"width": 1366, "height": 800})
    p.errors = []
    p.on("pageerror", lambda e: p.errors.append(str(e)))
    assert p.goto(f"{url}/map").status == 200
    p.wait_for_function("typeof map !== 'undefined' && document.querySelector('#map .leaflet-pane')")
    yield p
    browser.close()
    pw.stop()


def _poll(page):
    """The map asks now, as when its tab comes back into view."""
    with page.expect_response(lambda r: "api/last-position" in r.url):
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")


def test_the_map_shows_where_the_car_is_and_follows_it(page, mate):
    _, db = mate
    assert page.evaluate(_CAR) == [45.47, 9.20]
    view = page.evaluate("[map.getCenter().lat, map.getCenter().lng, map.getZoom()]")
    with sqlite3.connect(db) as conn:
        conn.execute(_POSITION, (_now(), 45.48, 9.21))
    _poll(page)
    page.wait_for_function(f"({_CAR})()[0] === 45.48")
    assert page.evaluate(_CAR) == [45.48, 9.21]
    # The reader's view is theirs: the marker moves, the map does not.
    assert page.evaluate("[map.getCenter().lat, map.getCenter().lng, map.getZoom()]") == view
    assert page.errors == []


def test_the_box_hides_the_car_and_the_page_remembers_it(page):
    box = page.get_by_role("checkbox", name="Last Known Position")
    assert box.is_checked()
    box.uncheck()
    assert page.evaluate(_CAR) is None
    page.reload()
    page.wait_for_function("typeof map !== 'undefined' && document.querySelector('#map .leaflet-pane')")
    box = page.get_by_role("checkbox", name="Last Known Position")
    assert not box.is_checked()
    assert page.evaluate(_CAR) is None
    box.check()
    assert page.evaluate(_CAR) is not None
    assert page.errors == []
