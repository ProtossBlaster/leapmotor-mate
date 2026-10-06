"""A reload comes back to the month being looked at, with the day or the range picked there still open: the
month on screen is kept in the address, and today's month as none, so a reload then follows the date. A pick
replaces the ?highlight= link the page was opened by, whose day would otherwise win over it; a month that
failed to load, or search results, leave the address as it was. A refuel added or deleted redraws the month
on screen. Wallbox shares the script but draws its calendar only with Home Assistant set up, so it is covered
through its page render. Skips where it cannot run (no playwright, no Chromium), like the other browser tests.

Last month: trips on the 10th and the 12th, a charge on the 10th. This month: trips on the 3rd and the 5th.
"""
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi", reason="web/main.py needs fastapi (absent in the minimal CI env)")
pytest.importorskip("uvicorn", reason="the page has to be SERVED, not rendered in-process")
sync_api = pytest.importorskip("playwright.sync_api", reason="needs playwright + `playwright install chromium`")

from web_in_a_browser import (
    CHARGE_SQL,
    COUNT_CALENDAR_SWAPS,
    TRIP_SQL,
    chromium,
    ringed,
    seed_database,
    served,
    swapped,
)

_THIS_MONTH = datetime.now(timezone.utc).date().replace(day=1)
_LAST_MONTH = (_THIS_MONTH - timedelta(days=1)).replace(day=1)


def _at(month, day, hour):
    return f"{month.replace(day=day)}T{hour:02d}:00:00+00:00"


@pytest.fixture(scope="module")
def mate(tmp_path_factory):
    data = tmp_path_factory.mktemp("reload-month")
    db = data / "leapmotor_mate.db"
    seed_database(db, "LVIN0000000000001", [
        ("INSERT INTO settings (key, value) VALUES ('timezone', 'UTC')", ()),
        ("INSERT INTO settings (key, value) VALUES ('is_reev', '1')", ()),       # Fuel is a range-extender's page
        (TRIP_SQL, (1, _at(_LAST_MONTH, 10, 8), _at(_LAST_MONTH, 10, 9))),
        (TRIP_SQL, (2, _at(_LAST_MONTH, 12, 8), _at(_LAST_MONTH, 12, 9))),
        (TRIP_SQL, (3, _at(_THIS_MONTH, 3, 8), _at(_THIS_MONTH, 3, 9))),
        (TRIP_SQL, (4, _at(_THIS_MONTH, 5, 8), _at(_THIS_MONTH, 5, 9))),
        (CHARGE_SQL, (1, _at(_LAST_MONTH, 10, 20), _at(_LAST_MONTH, 10, 22))),
    ])
    with served(data, db) as url:
        yield url


@pytest.fixture
def browser():
    pw, b = chromium(sync_api)
    try:
        yield b
    finally:
        b.close()
        pw.stop()


def _open(browser, url):
    """The page, after the calendar block's own load."""
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.add_init_script(COUNT_CALENDAR_SWAPS)
    assert page.goto(url).status == 200
    page.wait_for_function("window.settled >= 1")
    return page


def _reloaded(page):
    page.reload()
    page.wait_for_function("window.settled >= 1")


def _to_last_month(page, name):
    month = f"year={_LAST_MONTH.year}&month={_LAST_MONTH.month}"
    swapped(page, lambda: page.locator(f'#{name}-calendar-month button[hx-get$="calendar?{month}"]').click())


def _month(page, name):
    return page.locator(f"#{name}-calendar-month .text-lg").first.inner_text()


def _cell(page, day):
    return page.locator(f'.cal-day[hx-get$="&day={day}"]')


@pytest.mark.parametrize("name", ["trips", "charges", "fuel"])
def test_a_reload_stays_on_the_month_being_looked_at(browser, mate, name):
    page = _open(browser, f"{mate}/{name}")
    this_month = _month(page, name)
    _to_last_month(page, name)
    shown = _month(page, name)
    assert shown != this_month
    _reloaded(page)
    assert _month(page, name) == shown


@pytest.mark.parametrize("name", ["trips", "charges"])
def test_a_day_picked_in_an_earlier_month_is_open_after_a_reload(browser, mate, name):
    page = _open(browser, f"{mate}/{name}")
    _to_last_month(page, name)
    swapped(page, lambda: _cell(page, 10).click())
    drawer = page.locator(f"#{name}-day-drawer").inner_text()
    _reloaded(page)
    assert ringed(page) == [10]
    assert page.locator(f"#{name}-day-drawer").inner_text() == drawer


def test_a_range_picked_in_an_earlier_month_is_open_after_a_reload(browser, mate):
    page = _open(browser, f"{mate}/trips")
    _to_last_month(page, "trips")
    swapped(page, lambda: _cell(page, 10).click())
    swapped(page, lambda: _cell(page, 12).click(modifiers=["Shift"]))
    drawer = page.locator("#trips-day-drawer").inner_text()
    _reloaded(page)
    assert ringed(page) == [10, 12]
    assert page.locator("#trips-day-drawer").inner_text() == drawer


def test_a_day_picked_replaces_the_link_the_page_was_opened_by(browser, mate):
    page = _open(browser, f"{mate}/trips?highlight=3")        # the 3rd of this month
    assert ringed(page) == [3]
    swapped(page, lambda: _cell(page, 5).click())
    assert "highlight" not in page.url
    _reloaded(page)
    assert ringed(page) == [5]


def test_today_takes_the_month_out_of_the_address(browser, mate):
    page = _open(browser, f"{mate}/trips")
    _to_last_month(page, "trips")
    swapped(page, lambda: page.locator('#trips-calendar-month button[hx-get="api/trips/calendar"]').click())
    assert page.url.endswith("/trips")


def test_a_month_that_failed_to_load_leaves_the_address(browser, mate):
    page = _open(browser, f"{mate}/trips")
    shown = _month(page, "trips")
    page.route("**/api/trips/calendar?*", lambda r: r.fulfill(status=500, body="failed"))
    page.locator("#trips-calendar-month button[title]").first.click()       # ◀, answered with a 500
    page.wait_for_selector(".lm-load-error")
    page.unroute("**/api/trips/calendar?*")
    _reloaded(page)
    assert _month(page, "trips") == shown


def test_search_results_leave_the_month_in_the_address(browser, mate):
    page = _open(browser, f"{mate}/trips")
    _to_last_month(page, "trips")
    shown = _month(page, "trips")
    q = page.locator("#trips-search-q")
    swapped(page, lambda: q.fill("abc") or q.dispatch_event("change"))
    _reloaded(page)
    assert _month(page, "trips") == shown


def test_a_refuel_redraws_the_month_on_screen(browser, mate):
    page = _open(browser, f"{mate}/fuel")
    _to_last_month(page, "fuel")
    shown = _month(page, "fuel")
    swapped(page, lambda: page.evaluate("htmx.trigger(document.body, 'fuelChanged')"))   # what adding one sends
    assert _month(page, "fuel") == shown
