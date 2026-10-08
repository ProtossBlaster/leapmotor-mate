"""A trip row on a phone keeps room for its clock and duration. Beside the thumbnail, the distance column
and the energy source label, the middle column was squeezed to no width at all, and its times ran under
the consumption pill. On a phone the label now takes a line of its own; a wide screen keeps one line.
Where the trip started and ended does the same: a line of its own on a phone, the middle column on a
wide screen, each place cut short on its own so a long street never pushes the short one out; the
trip's own page shows them whole.
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
         " efficiency_kwh_100km, start_lat, start_lon, end_lat, end_lon) VALUES (1,1,'2026-07-04T08:00:00+00:00',"
         "'2026-07-04T08:30:00+00:00',12,30,84,70,16,45.07,7.68,44.64,11.19)")
# An earlier trip of the same day to a short address: two short names leave the line room to spare.
_NEAR = ("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, duration_min, start_soc, end_soc,"
         " efficiency_kwh_100km, start_lat, start_lon, end_lat, end_lon) VALUES (2,1,'2026-07-04T06:00:00+00:00',"
         "'2026-07-04T06:20:00+00:00',3,20,86,84,15,45.07,7.68,45.068,7.693)")
_SHORT = ("INSERT INTO addresses (geohash, latitude, longitude, provider, status, road, house_number, locality,"
          " looked_up_at) VALUES (?, 45.068, 7.693, 'nominatim', 'found', 'Via Po', '3', 'Torino', '')")
_HOME = ("INSERT INTO charging_places (vehicle_id, name, latitude, longitude, radius_m, rate, enabled)"
         " VALUES (1, 'Home', 45.07, 7.68, 100, 0.2, 1)")
_FAR = ("INSERT INTO addresses (geohash, latitude, longitude, provider, status, road, house_number, locality,"
        " looked_up_at) VALUES (?, 44.64, 11.19, 'nominatim', 'found',"
        " 'Viale della Repubblica Partigiana e dei Martiri della Libertà', '128', 'San Giovanni in Persiceto', '')")
_PLACES = """() => [...document.querySelectorAll('#trips-day-drawer .trip-row')].map(row => {
  const shown = [...row.querySelectorAll('[data-trip-places]')].filter(e => e.offsetParent !== null);
  const line = shown[0];
  const [from, , to] = line.children;
  return {copies: shown.length, inside: line.getBoundingClientRect().right <= row.getBoundingClientRect().right + 0.5,
          fromWhole: from.scrollWidth <= from.clientWidth, from: from.textContent,
          toCut: to.scrollWidth > to.clientWidth, toWidth: to.clientWidth, to: to.textContent,
          gap: to.getBoundingClientRect().left - from.getBoundingClientRect().right}; })"""
_MIDDLE = """() => { const m = document.querySelector('#trips-day-drawer .trip-row .min-w-0');
  return {width: m.clientWidth, needs: m.scrollWidth}; }"""


@pytest.fixture(scope="module")
def mate(tmp_path_factory):
    data = tmp_path_factory.mktemp("trip-row")
    db = data / "leapmotor_mate.db"
    import geohash
    seed_database(db, "LVIN0000000000001", [("INSERT INTO settings (key, value) VALUES ('timezone', 'UTC')", ()),
                                            ("INSERT INTO settings (key, value) VALUES ('is_reev', '0')", ()),
                                            (_TRIP, ()), (_NEAR, ()), (_HOME, ()),
                                            (_FAR, (geohash.encode(44.64, 11.19, 8),)),
                                            (_SHORT, (geohash.encode(45.068, 7.693, 8),))])
    with served(data, db) as url:
        yield url


@pytest.mark.parametrize("width", [390, 1280])
def test_the_middle_of_a_trip_row_is_as_wide_as_what_it_holds(mate, width):
    pw, browser = chromium(sync_api)
    try:
        page = browser.new_page(viewport={"width": width, "height": 900})
        assert page.goto(mate + "/trips?highlight=1").status == 200
        page.locator("#trips-day-drawer .trip-row").filter(has_text="Mate").first.wait_for()
        middle = page.evaluate(_MIDDLE)
        assert middle["width"] > 0 and middle["needs"] <= middle["width"], middle
        far, near = page.evaluate(_PLACES)
        for places in (far, near):
            assert places["copies"] == 1, ("one copy of the line per screen width", places)
            assert places["inside"] and places["fromWhole"] and places["from"] == "Home (charging place)", places
            assert places["gap"] < 30, ("the arrow and the destination follow the start", places)
        assert far["toWidth"] > 100 and (far["toCut"] or width > 390), far
        assert near["to"] == "Via Po 3, Torino" and not near["toCut"], near
    finally:
        browser.close()
        pw.stop()


@pytest.mark.parametrize("width", [390, 1280])
def test_the_trip_page_shows_the_whole_destination(mate, width):
    """The list cuts a long place short; the trip's own page is where it is read whole, wrapped if need be."""
    pw, browser = chromium(sync_api)
    try:
        page = browser.new_page(viewport={"width": width, "height": 900})
        assert page.goto(mate + "/trips/1").status == 200
        end = page.locator('[data-trip-place="end"] span').last
        end.wait_for()
        shown = end.evaluate("""e => ({text: e.textContent, whole: e.scrollWidth <= e.clientWidth,
                                       inside: e.getBoundingClientRect().right <= document.documentElement.clientWidth})""")
        assert shown == {"text": "Viale della Repubblica Partigiana e dei Martiri della Libertà 128, "
                                 "San Giovanni in Persiceto", "whole": True, "inside": True}, shown
    finally:
        browser.close()
        pw.stop()
