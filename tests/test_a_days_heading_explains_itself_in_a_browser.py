"""A figure in an open day's heading says what it is when it is tapped, on a phone, and when the mouse
rests on it. Only a real browser shows that: rendered HTML says the description is there, not that
anyone gets to read it, and a `title` never shows on touch.
Skips where it cannot run (no playwright, no Chromium), like the other browser tests.
"""
import pytest

pytest.importorskip("fastapi", reason="web/main.py needs fastapi (absent in the minimal CI env)")
pytest.importorskip("uvicorn", reason="the page has to be SERVED, not rendered in-process")
sync_api = pytest.importorskip("playwright.sync_api", reason="needs playwright + `playwright install chromium`")

from web_in_a_browser import chromium, seed_database, served

_TRIP = ("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, duration_min, start_soc, end_soc)"
         " VALUES (?,1,?,?,12,30,?,?)")
_SCROLL_STOPPED = "() => { const n = window.scrolls, same = window.lastN === n; window.lastN = n; return same; }"
_TIP = "() => { const t = document.getElementById('mate-tip'); return t.classList.contains('hidden') ? null : t.textContent; }"


@pytest.fixture(scope="module")
def mate(tmp_path_factory):
    data = tmp_path_factory.mktemp("day-heading")
    db = data / "leapmotor_mate.db"
    seed_database(db, "LVIN0000000000001", [
        ("INSERT INTO settings (key, value) VALUES ('timezone', 'UTC')", ()),
        (_TRIP, (1, "2026-07-04T08:00:00+00:00", "2026-07-04T08:30:00+00:00", 84, 70)),
        (_TRIP, (2, "2026-07-04T17:00:00+00:00", "2026-07-04T17:30:00+00:00", 70, 52)),
    ])
    with served(data, db) as url:
        yield url


@pytest.mark.parametrize("touch", [True, False], ids=["tap", "mouse"])
def test_a_figure_in_the_heading_shows_what_it_is(mate, touch):
    pw, browser = chromium(sync_api)
    try:
        width = 390 if touch else 1280
        page = browser.new_page(viewport={"width": width, "height": 900}, has_touch=touch, is_mobile=touch)
        # ?highlight= opens the trip's day, loads the calendar once more and scrolls smoothly to the row;
        # base.html hides the tip on any scroll, so the tap waits for both to be over. The page scrolls
        # inside <main>, not the window, so settling counts scroll events wherever they happen.
        page.add_init_script("document.addEventListener('htmx:afterSwap', () => { window.swapped = true; });"
                             " window.scrolls = 0; document.addEventListener('scroll', () => { window.scrolls++; }, true)")
        assert page.goto(mate + "/trips?highlight=1").status == 200
        page.wait_for_function("window.swapped === true")
        page.wait_for_function(_SCROLL_STOPPED, polling=250)
        # As a reader gets there: the day is tapped, and keeps the focus the figure's tap takes from it.
        page.evaluate("window.swapped = false")
        day = page.locator('[hx-get$="&day=4"]').first
        day.tap() if touch else day.click()
        page.wait_for_function("window.swapped === true")
        battery = page.locator("#trips-day-drawer [data-tip]").filter(has_text="84.0% → 52.0%")
        assert page.evaluate(_TIP) is None
        # hover() first scrolls the figure into view; that scroll event arrives late under load and would hide
        # the tip right after the pointer showed it, so the figure is brought into view and the page settled first.
        battery.scroll_into_view_if_needed()
        page.wait_for_function(_SCROLL_STOPPED, polling=250)
        battery.tap() if touch else battery.hover()
        assert page.evaluate(_TIP) == "Battery"
    finally:
        browser.close()
        pw.stop()
