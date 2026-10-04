"""A trip row on a phone keeps room for its clock and duration. Beside the thumbnail, the distance column
and the energy source label, the middle column was squeezed to no width at all, and its times ran under
the consumption pill. On a phone the label now takes a line of its own; a wide screen keeps one line.
Measured in a real browser, because only layout says how wide a column came out.
Skips where it cannot run (no playwright, no Chromium), like the other browser tests.
"""
import pytest

pytest.importorskip("fastapi", reason="web/main.py needs fastapi (absent in the minimal CI env)")
pytest.importorskip("uvicorn", reason="the page has to be SERVED, not rendered in-process")
sync_api = pytest.importorskip("playwright.sync_api", reason="needs playwright + `playwright install chromium`")

from web_in_a_browser import chromium, seed_database, served

# On a battery car an efficiency and a distance make Mate's own estimate, labelled with its source.
_TRIP = ("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, duration_min, start_soc, end_soc,"
         " efficiency_kwh_100km) VALUES (1,1,'2026-07-04T08:00:00+00:00','2026-07-04T08:30:00+00:00',12,30,84,70,16)")
_MIDDLE = """() => { const m = document.querySelector('#trips-day-drawer .trip-row .min-w-0');
  return {width: m.clientWidth, needs: m.scrollWidth}; }"""


@pytest.fixture(scope="module")
def mate(tmp_path_factory):
    data = tmp_path_factory.mktemp("trip-row")
    db = data / "leapmotor_mate.db"
    seed_database(db, "LVIN0000000000001", [("INSERT INTO settings (key, value) VALUES ('timezone', 'UTC')", ()),
                                            ("INSERT INTO settings (key, value) VALUES ('is_reev', '0')", ()),
                                            (_TRIP, ())])
    with served(data, db) as url:
        yield url


@pytest.mark.parametrize("width", [390, 1280])
def test_the_middle_of_a_trip_row_is_as_wide_as_what_it_holds(mate, width):
    pw, browser = chromium(sync_api)
    try:
        page = browser.new_page(viewport={"width": width, "height": 900})
        assert page.goto(mate + "/trips?highlight=1").status == 200
        page.locator("#trips-day-drawer .trip-row").filter(has_text="Mate").wait_for()
        middle = page.evaluate(_MIDDLE)
        assert middle["width"] > 0 and middle["needs"] <= middle["width"], middle
    finally:
        browser.close()
        pw.stop()
