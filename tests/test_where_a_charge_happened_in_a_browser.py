"""Where a charge happened, as a browser shows it. The OpenStreetMap credit under a list of charges shows while
a card shows an address from it, also after a card redrew its own 📍 line (✏️, 🔄, assigning a place), which
no route redraws the list for: only the page's own CSS says whether the credit shows. On a phone the line
wraps, so the station, its address and a warning beside them are read whole, and its buttons stay on the card.
Skips where it cannot run (no playwright, no Chromium), like the other browser tests.
"""
import sqlite3

import pytest

pytest.importorskip("fastapi", reason="web/main.py needs fastapi (absent in the minimal CI env)")
pytest.importorskip("uvicorn", reason="the page has to be SERVED, not rendered in-process")
sync_api = pytest.importorskip("playwright.sync_api", reason="needs playwright + `playwright install chromium`")

import geohash
from web_in_a_browser import COUNT_CALENDAR_SWAPS, chromium, seed_database, served

WORK, SHOP, FAR = (45.08, 7.69), (45.09, 7.70), (45.10, 7.71)
_CHARGE = ("INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc, energy_added_kwh,"
           " latitude, longitude, location_type, location_name) VALUES (?, 1, ?, ?, 40, 60, 9, ?, ?, 'AC', ?)")
_ADDRESS = ("INSERT OR REPLACE INTO addresses (geohash, latitude, longitude, provider, status, road, locality,"
            " looked_up_at) VALUES (?, ?, ?, 'nominatim', 'found', ?, ?, '')")
_OFFICE = ("INSERT INTO charging_places (id, vehicle_id, name, latitude, longitude, radius_m, rate, enabled)"
           " VALUES (1, 1, 'Office', 45.2, 7.8, 100, 0.2, 1)")
_LONG = ("Viale della Repubblica Partigiana e dei Martiri della Libertà", "San Giovanni in Persiceto")


def _address(point, road, town):
    return _ADDRESS, (geohash.encode(*point, 8), *point, road, town)


@pytest.fixture(scope="module")
def mate(tmp_path_factory):
    data = tmp_path_factory.mktemp("charge-credit")
    db = data / "leapmotor_mate.db"
    seed_database(db, "LVIN0000000000001", [
        ("INSERT INTO settings (key, value) VALUES ('timezone', 'UTC')", ()), (_OFFICE, ()),
        (_CHARGE, (1, "2026-07-04T08:00:00+00:00", "2026-07-04T09:00:00+00:00", *WORK, None)),
        (_CHARGE, (2, "2026-07-11T08:00:00+00:00", "2026-07-11T09:00:00+00:00", *SHOP, None)),
        _address(SHOP, "Via Po", "Torino"),
        (_CHARGE, (3, "2026-07-18T08:00:00+00:00", "2026-07-18T09:00:00+00:00", *FAR, "Ionity Area di Servizio")),
        _address(FAR, *_LONG)])
    with served(data, db) as url:
        yield url, db


def _open(browser, url, cid, width=1280):
    page = browser.new_page(viewport={"width": width, "height": 900})
    page.add_init_script(COUNT_CALENDAR_SWAPS)
    assert page.goto(f"{url}/charges?highlight={cid}").status == 200
    # The calendar block draws itself again on load: a card touched before that swap is replaced.
    page.wait_for_function("window.settled >= 1")
    page.wait_for_load_state("networkidle")
    page.locator(f"#loc-{cid}").wait_for()
    return page


def _credit_shown(page):
    return page.locator("[data-charges-list] [data-address-credit]").is_visible()


def _assign_office(page, cid):
    page.locator(f"#place-{cid} button").click()
    page.locator(f"#place-{cid} select").select_option("1")
    page.locator(f"#place-{cid} button[type=submit]").click()
    page.locator(f"#place-{cid} select").wait_for(state="detached")       # the picker gave way to the line
    assert "Office ·" in page.locator(f"#place-{cid}").inner_text()


def test_the_credit_appears_when_a_card_redraws_its_line_with_an_address(mate):
    url, db = mate
    pw, browser = chromium(sync_api)
    try:
        page = _open(browser, url, 1)
        assert not _credit_shown(page), "no card shows an address yet"
        with sqlite3.connect(db) as c:                       # the lookup answers while the page is open
            c.execute(*_address(WORK, "Corso Francia", "Torino"))
        page.locator("#loc-1 button", has_text="✏️").click()
        page.locator("#loc-manual-1 input").fill("Colonnina Lingotto")
        page.locator("#loc-manual-1 input").press("Enter")
        page.locator("#loc-1 [data-address-osm]").wait_for()
        assert "Colonnina Lingotto" in page.locator("#loc-1").inner_text()
        assert "Corso Francia, Torino" in page.locator("#loc-1").inner_text()
        assert _credit_shown(page)
        _assign_office(page, 1)
        assert "Corso Francia, Torino" in page.locator("#loc-1").inner_text(), "the station keeps its address"
        assert _credit_shown(page)
    finally:
        browser.close()
        pw.stop()


def test_the_credit_goes_when_the_only_address_gives_way_to_a_place(mate):
    url, _ = mate
    pw, browser = chromium(sync_api)
    try:
        page = _open(browser, url, 2)
        assert "Via Po, Torino" in page.locator("#loc-2").inner_text() and _credit_shown(page)
        _assign_office(page, 2)
        assert "Via Po" not in page.locator("#loc-2").inner_text()
        assert not _credit_shown(page)
    finally:
        browser.close()
        pw.stop()


_LINE = """id => {
  const card = document.querySelector(`#charge-card-${id}`).getBoundingClientRect();
  const loc = document.querySelector(`#loc-${id}`);
  const inside = r => r.width > 0 && r.left >= card.left - 0.5 && r.right <= card.right + 0.5;
  const texts = [...loc.querySelectorAll(':scope > span')];
  return {texts: texts.length, whole: texts.every(e => e.scrollWidth <= e.clientWidth && inside(e.getBoundingClientRect())),
          buttons: [...loc.querySelectorAll(':scope > button')].map(b => inside(b.getBoundingClientRect()))}; }"""


def test_on_a_phone_the_line_wraps_and_its_buttons_stay_on_the_card(mate):
    url, _ = mate
    pw, browser = chromium(sync_api)
    try:
        page = _open(browser, url, 3, width=390)
        assert "Ionity Area di Servizio · " + ", ".join(_LONG) in page.locator("#loc-3").inner_text().replace("\n", " ")
        line = page.evaluate(_LINE, 3)
        assert line == {"texts": 1, "whole": True, "buttons": [True, True]}, line
    finally:
        browser.close()
        pw.stop()


def test_on_a_phone_a_warning_leaves_the_name_whole(mate):
    """An OCM id typed into ✏️ without the OCM key answers with a warning, and nothing is written."""
    url, _ = mate
    pw, browser = chromium(sync_api)
    try:
        page = _open(browser, url, 3, width=390)
        page.locator("#loc-3 button", has_text="✏️").click()
        page.locator("#loc-manual-3 input").fill("OCM-123456")
        page.locator("#loc-manual-3 input").press("Enter")
        page.locator("#loc-3 .text-amber-400").wait_for()
        page.locator("#loc-manual-3").wait_for(state="hidden")      # htmx settled: the form has its new classes
        line = page.evaluate(_LINE, 3)
        assert line == {"texts": 2, "whole": True, "buttons": [True, True]}, line
    finally:
        browser.close()
        pw.stop()
