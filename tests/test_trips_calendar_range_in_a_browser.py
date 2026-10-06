"""Picking a range of days on the Trips calendar, in a real browser: a gesture sends one request, for the
range and nothing else, rings the days and fills the drawer; a reload brings the choice back, and so does
the way back from one of its trips. Every request the drawer makes is counted, because htmx opening the
clicked day alone next to the range is exactly the failure to catch. Skips where it cannot run (no
playwright, no Chromium), like the other browser tests.

This month: trips on the 3rd and the 5th, none on the 4th; charges on the 3rd and the 5th.
"""
from datetime import datetime, timezone

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

_MONTH = datetime.now(timezone.utc).date().replace(day=1)


def _at(day, hour):
    return f"{_MONTH.replace(day=day)}T{hour:02d}:00:00+00:00"


@pytest.fixture(scope="module")
def mate(tmp_path_factory):
    data = tmp_path_factory.mktemp("range")
    db = data / "leapmotor_mate.db"
    seed_database(db, "LVIN0000000000001", [
        ("INSERT INTO settings (key, value) VALUES ('timezone', 'UTC')", ()),
        (TRIP_SQL, (1, _at(3, 8), _at(3, 9))),
        (TRIP_SQL, (2, _at(5, 8), _at(5, 9))),
        (CHARGE_SQL, (1, _at(3, 20), _at(3, 22))),
        (CHARGE_SQL, (2, _at(5, 20), _at(5, 22))),
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


class Calendar:
    """One page on this month's calendar, counting what the drawer asks for."""

    def __init__(self, browser, mate, name="trips"):
        self.name, self.asked = name, []
        self.page = browser.new_page(viewport={"width": 1280, "height": 900})
        self.page.add_init_script(COUNT_CALENDAR_SWAPS)
        self.page.on("request", lambda r: f"/api/{name}/calendar/day?" in r.url and self.asked.append(r.url))
        assert self.page.goto(f"{mate}/{name}").status == 200
        self.settle(1)                                    # the block's own load
        # The blocks above the calendar load on their own and push it down: a gesture aimed at a
        # day's coordinates before they arrive lands somewhere else.
        self.page.wait_for_load_state("networkidle")

    def settle(self, n):
        self.page.wait_for_function(f"window.settled >= {n}")

    def swapped(self, act):
        swapped(self.page, act)

    def cell(self, day):
        return self.page.locator(f'.cal-day[hx-get$="&day={day}"]')

    def ringed(self):
        return ringed(self.page)

    def drawer(self):
        return self.page.locator(f"#{self.name}-day-drawer").inner_text()


def _range_heading(day1, day2):
    return f"{day1:02d} – {day2:02d} "


def test_a_shift_click_opens_the_range_with_one_request(browser, mate):
    cal = Calendar(browser, mate)
    cal.swapped(lambda: cal.cell(3).click())
    cal.swapped(lambda: cal.cell(5).click(modifiers=["Shift"]))
    assert len(cal.asked) == 2 and cal.asked[1].endswith("&day=3&to_day=5"), cal.asked
    assert cal.ringed() == [3, 5]
    assert cal.drawer().startswith(_range_heading(3, 5))


def test_a_range_that_never_reaches_the_server_says_so_without_a_script_error(browser, mate):
    cal = Calendar(browser, mate)
    cal.page.evaluate("window.rejected = []; addEventListener('unhandledrejection', e => rejected.push(e))")
    cal.swapped(lambda: cal.cell(3).click())
    cal.page.route(lambda url: "to_day=" in url, lambda route: route.abort("connectionreset"))
    cal.cell(5).click(modifiers=["Shift"])
    cal.page.wait_for_selector(".lm-load-error")
    cal.page.wait_for_timeout(300)
    assert cal.page.evaluate("window.rejected.length") == 0


def test_a_shift_click_backwards_keeps_the_anchor(browser, mate):
    cal = Calendar(browser, mate)
    cal.swapped(lambda: cal.cell(5).click())
    cal.swapped(lambda: cal.cell(3).click(modifiers=["Shift"]))
    assert len(cal.asked) == 2 and cal.asked[1].endswith("&day=3&to_day=5"), cal.asked
    assert cal.ringed() == [3, 5]


def test_a_reloaded_range_keeps_the_day_it_was_picked_from(browser, mate):
    cal = Calendar(browser, mate)
    cal.swapped(lambda: cal.cell(5).click())
    cal.swapped(lambda: cal.cell(3).click(modifiers=["Shift"]))
    cal.page.reload()
    cal.settle(1)
    cal.swapped(lambda: cal.cell(3).click(modifiers=["Shift"]))   # from the 5th again, as before the reload
    assert cal.asked[-1].endswith("&day=3&to_day=5"), cal.asked


def test_a_day_opened_by_a_link_is_where_a_shift_click_starts(browser, mate):
    cal = Calendar(browser, mate)
    cal.swapped(lambda: cal.cell(5).click())
    assert cal.page.goto(f"{mate}/trips?highlight=1").status == 200       # trip 1, on the 3rd
    cal.settle(1)
    cal.page.wait_for_load_state("networkidle")
    assert cal.ringed() == [3]
    cal.swapped(lambda: cal.cell(5).click(modifiers=["Shift"]))
    assert cal.asked[-1].endswith("&day=3&to_day=5"), cal.asked


def test_a_reload_brings_the_range_back_on_its_month(browser, mate):
    cal = Calendar(browser, mate)
    cal.swapped(lambda: cal.cell(3).click())
    cal.swapped(lambda: cal.cell(5).click(modifiers=["Shift"]))
    cal.page.reload()
    cal.settle(1)
    assert cal.ringed() == [3, 5]
    assert cal.drawer().startswith(_range_heading(3, 5))


def test_the_way_back_from_a_trip_in_the_range_opens_the_range(browser, mate):
    cal = Calendar(browser, mate)
    cal.swapped(lambda: cal.cell(3).click())
    cal.swapped(lambda: cal.cell(5).click(modifiers=["Shift"]))
    cal.page.locator('[data-trip-id="2"]').click()        # on the 5th
    cal.page.wait_for_url("**/trips/2")
    cal.page.locator('a[href="trips?highlight=2"]').click()
    cal.settle(1)
    assert cal.ringed() == [3, 5]
    assert cal.drawer().startswith(_range_heading(3, 5))
    assert "inset" in cal.page.locator('[data-trip-id="2"]').evaluate("row => row.style.boxShadow")


def test_a_days_date_under_the_range_opens_that_day_alone(browser, mate):
    cal = Calendar(browser, mate)
    cal.swapped(lambda: cal.cell(3).click())
    cal.swapped(lambda: cal.cell(5).click(modifiers=["Shift"]))
    cal.swapped(lambda: cal.page.locator('#trips-day-drawer [data-cal-day="5"]').click())
    assert cal.asked[-1].endswith("&day=5"), cal.asked
    assert cal.ringed() == [5]
    label = cal.drawer().split()[:3]
    cal.page.reload()
    cal.settle(1)
    assert cal.ringed() == [5]
    assert cal.drawer().split()[:3] == label


def test_the_charges_calendar_opens_one_day_on_a_shift_click(browser, mate):
    cal = Calendar(browser, mate, "charges")
    cal.swapped(lambda: cal.cell(3).click())
    cal.swapped(lambda: cal.cell(5).click(modifiers=["Shift"]))
    assert len(cal.asked) == 2 and cal.asked[0].endswith("&day=3") and cal.asked[1].endswith("&day=5"), cal.asked
    assert cal.ringed() == [5]
