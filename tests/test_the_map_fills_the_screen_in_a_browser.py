"""The Map page's map takes the height of the screen it is on (D#438, @Tommy73LMB05).

It was 560 px on every screen. On a 28" monitor that left a band of empty page under the legend row;
on a 14" laptop the legend row, where «Trips shown» and «Stations shown» are set, sat below the fold.
The map now takes the whole width of the page and ends where the legend row still fits on screen
(a 28" with only the height grown read 860 × 1140, taller than wide), and never gets shorter than 400 px, so
on a phone or a very low window it stays usable and the page scrolls as before. Resizing the window
resizes the map, and Leaflet's own idea of its size follows, or the tiles would stop half-way.
Measured in a real browser, because only layout says how tall the space is.
Skips where it cannot run (no playwright, no Chromium), like the other browser tests.
"""
import pytest

pytest.importorskip("fastapi", reason="web/main.py needs fastapi (absent in the minimal CI env)")
pytest.importorskip("uvicorn", reason="the page has to be SERVED, not rendered in-process")
sync_api = pytest.importorskip("playwright.sync_api", reason="needs playwright + `playwright install chromium`")

from web_in_a_browser import chromium, seed_database, served

_TRIP = ("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, duration_min, start_soc, end_soc)"
         " VALUES (1,1,'2026-07-04T08:00:00+00:00','2026-07-04T08:30:00+00:00',12,30,84,70)")
_POINT = "INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude) VALUES (1, ?, ?, ?)"

# What a screenshot of the page would show: the map's height, where the legend row ends against the
# bottom of the scrolling area, and the size Leaflet believes its container has.
_MEASURE = """() => {
  const main = document.querySelector('main'), el = document.getElementById('map');
  const legend = document.getElementById('map-top-n').closest('div');
  return {map: el.getBoundingClientRect().height, leaflet: map.getSize().y,
          width: el.getBoundingClientRect().width, mainWidth: main.clientWidth,
          legendBottom: legend.getBoundingClientRect().bottom, mainBottom: main.getBoundingClientRect().bottom,
          scrolls: main.scrollHeight > main.clientHeight + 1}; }"""


@pytest.fixture(scope="module")
def mate(tmp_path_factory):
    data = tmp_path_factory.mktemp("map-height")
    db = data / "leapmotor_mate.db"
    seed_database(db, "LVIN0000000000001", [("INSERT INTO settings (key, value) VALUES ('timezone', 'UTC')", ()),
                                            (_TRIP, ()),
                                            (_POINT, ("2026-07-04T08:00:00+00:00", 45.46, 9.19)),
                                            (_POINT, ("2026-07-04T08:30:00+00:00", 45.47, 9.20))])
    with served(data, db) as url:
        yield url


def _open(browser, url, width, height):
    page = browser.new_page(viewport={"width": width, "height": height})
    assert page.goto(url + "/map").status == 200
    page.wait_for_function("typeof map !== 'undefined' && document.querySelector('#map .leaflet-pane')")
    return page


@pytest.mark.parametrize("width,height", [(1366, 680), (1920, 960), (2560, 1300)])
def test_on_a_computer_the_map_ends_where_the_legend_row_still_fits(mate, width, height):
    pw, browser = chromium(sync_api)
    try:
        m = _open(browser, mate, width, height).evaluate(_MEASURE)
        assert not m["scrolls"], ("the legend row is below the fold", m)
        assert m["mainBottom"] - m["legendBottom"] < 60, ("empty page under the legend row", m)
        assert m["map"] >= 400 and m["leaflet"] == round(m["map"]), m
        # The width too: the pages' 1152 px column made it taller than wide on a 28". Only the side
        # padding (24 px each) and the card's border are left between the map and the edges of the page.
        assert m["mainWidth"] - m["width"] <= 52, ("the map stops at the pages' column", m)
    finally:
        browser.close()
        pw.stop()


def test_on_a_phone_the_map_keeps_its_floor(mate):
    """A phone, or a window dragged short: 400 px, and the page scrolls to the legend row."""
    pw, browser = chromium(sync_api)
    try:
        m = _open(browser, mate, 390, 500).evaluate(_MEASURE)
        assert m["map"] == 400 and m["leaflet"] == 400, m
    finally:
        browser.close()
        pw.stop()


def test_resizing_the_window_resizes_the_map(mate):
    pw, browser = chromium(sync_api)
    try:
        page = _open(browser, mate, 1366, 680)
        small = page.evaluate(_MEASURE)
        page.set_viewport_size({"width": 2560, "height": 1300})
        page.wait_for_function(f"document.getElementById('map').getBoundingClientRect().height > {small['map'] + 100}")
        page.wait_for_function("map.getSize().y === Math.round(document.getElementById('map').getBoundingClientRect().height)")
        big = page.evaluate(_MEASURE)
        assert not big["scrolls"] and big["mainBottom"] - big["legendBottom"] < 60, big
    finally:
        browser.close()
        pw.stop()
