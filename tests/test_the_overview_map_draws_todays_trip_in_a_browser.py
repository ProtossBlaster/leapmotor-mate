"""The Overview map draws the trip under way, under the car, and moves it on as points come.

The map asks for the route with the marker; a reply with the route it has drawn carries no points
and leaves the drawing alone. Measured in a browser, because only a browser draws the line.
Skips where it cannot run (no playwright, no Chromium), like the other browser tests.
"""
import datetime as dt
import sqlite3

import pytest

pytest.importorskip("fastapi", reason="web/main.py needs fastapi (absent in the minimal CI env)")
pytest.importorskip("uvicorn", reason="the page has to be SERVED, not rendered in-process")
sync_api = pytest.importorskip("playwright.sync_api", reason="needs playwright + `playwright install chromium`")

from web_in_a_browser import chromium, seed_database, served  # noqa: E402

START = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=10)
# A zigzag a few hundred metres long, so the view at the car holds it whole and Leaflet keeps every point.
POINT = ("INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude, speed_kmh, soc)"
         " VALUES (1, ?, ?, ?, 40, 70)")


def _point(k):
    return POINT, ((START + dt.timedelta(seconds=30 * k)).isoformat(), 52.40 + k * 0.0003, 16.90 + k % 2 * 0.0003)


@pytest.fixture(scope="module")
def mate(tmp_path_factory):
    data = tmp_path_factory.mktemp("overview-track")
    db = data / "leapmotor_mate.db"
    seed_database(db, "LVIN0000000000001", [
        ("INSERT INTO trips (id, vehicle_id, started_at) VALUES (1, 1, ?)", (START.isoformat(),)),
        ("INSERT INTO positions (vehicle_id, recorded_at, latitude, longitude, soc, gear, speed_kmh, charging)"
         " VALUES (1, ?, 52.4027, 16.9003, 70, 'D', 40, 0)", (dt.datetime.now(dt.timezone.utc).isoformat(),)),
    ] + [_point(k) for k in range(10)])
    with served(data, db) as url:
        yield url, db


@pytest.fixture
def page(mate):
    url, _ = mate
    pw, browser = chromium(sync_api)
    p = browser.new_page()
    p.errors = []
    p.on("pageerror", lambda e: p.errors.append(str(e)))
    p.goto(f"{url}/")
    yield p
    browser.close()
    pw.stop()


_ROUTE = "document.querySelector('.leaflet-overlay-pane path[stroke=\"#e63946\"]')"


def _points(page):
    return page.evaluate(f"({_ROUTE}.getAttribute('d').match(/[ML]/g) || []).length")


def _poll(page):
    """The map asks now, as when its tab comes back into view; returns the address it asked."""
    with page.expect_response(lambda r: "api/last-position" in r.url) as reply:
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    return reply.value.url


def test_the_trip_under_way_is_drawn_under_the_car_and_moves_on(page, mate):
    _, db = mate
    page.wait_for_function(f"{_ROUTE} !== null")
    before = _points(page)
    with sqlite3.connect(db) as conn:
        conn.execute(*_point(10))
    assert "track=" in _poll(page)
    page.wait_for_function(f"({_ROUTE}.getAttribute('d').match(/[ML]/g) || []).length === {before + 1}")
    # The marker is drawn over the line: hit-tested where both are, the marker answers.
    on_top = page.evaluate("""() => {
      const icon = document.querySelector('.leaflet-marker-icon');
      icon.style.pointerEvents = 'auto';
      icon.scrollIntoView({block: 'center'});
      const r = icon.getBoundingClientRect();
      return document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2).closest('.leaflet-marker-icon') === icon;
    }""")
    assert on_top
    assert page.errors == []


def test_an_unchanged_route_is_not_drawn_again(page):
    page.wait_for_function(f"{_ROUTE} !== null")
    page.evaluate(f"window.route = {_ROUTE}")
    assert "track=" in _poll(page)
    page.wait_for_timeout(200)
    assert page.evaluate(f"window.route.isConnected && {_ROUTE} === window.route")
    assert page.errors == []
