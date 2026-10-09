"""Picking a range of days on the Trips calendar, and on the Charges one, in a real browser: a gesture sends one
request, for the range and nothing else, rings the days and fills the drawer; a reload brings the choice back, and so
does the way back from one of its trips. Every request the drawer makes is counted, because htmx opening the
clicked day alone next to the range is exactly the failure to catch. Skips where it cannot run (no
playwright, no Chromium), like the other browser tests.

This month: trips on the 3rd and the 5th, none on the 4th; a charge on the 3rd and nine on the 5th.
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
        *((CHARGE_SQL, (cid, _at(5, cid + 7), _at(5, cid + 8))) for cid in range(3, 11)),
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

    def __init__(self, browser, mate, name="trips", phone=False):
        self.name, self.asked = name, []
        self.page = (browser.new_page(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
                     if phone else browser.new_page(viewport={"width": 1280, "height": 900}))
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

    def centre(self, day):
        box = self.cell(day).bounding_box()
        return {"x": box["x"] + box["width"] / 2, "y": box["y"] + box["height"] / 2}

    def fingers(self, kind, *points):
        if not hasattr(self, "cdp"):
            self.cdp = self.page.context.new_cdp_session(self.page)
        self.cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": list(points)})

    def touch(self, day, *, held=False, move=0, scroll=0):
        """A finger on the day, then lifted: `held` until the day shows it is held (as a reader waits for
        the ring; a loaded machine runs the half-second timer late), then moved `scroll` px up; or moved
        `move` px up at once and left there past the half second."""
        self.cell(day).scroll_into_view_if_needed()
        self.page.wait_for_timeout(300)        # a drawer just filled may still be moving the page
        at = self.centre(day)
        self.fingers("touchStart", at)
        for step in range(1, 6 if move else 0):
            self.fingers("touchMove", {"x": at["x"], "y": at["y"] - move * step / 5})
        if held:
            self.page.wait_for_selector(".cal-day[data-anchor]")
        else:
            self.page.wait_for_timeout(800)
        for step in range(1, 6 if scroll else 0):
            self.fingers("touchMove", {"x": at["x"], "y": at["y"] - scroll * step / 5})
        self.fingers("touchEnd")
        self.page.wait_for_timeout(300)        # whatever the lifted finger is going to send

    def anchored(self):
        return self.page.locator(".cal-day[data-anchor]").count()


def _range_heading(day1, day2):
    return f"{day1:02d} – {day2:02d} "


def test_a_shift_click_opens_the_range_with_one_request(browser, mate):
    cal = Calendar(browser, mate)
    cal.swapped(lambda: cal.cell(3).click())
    cal.swapped(lambda: cal.cell(5).click(modifiers=["Shift"]))
    assert len(cal.asked) == 2 and cal.asked[1].endswith("&day=3&to_day=5"), cal.asked
    assert cal.ringed() == [3, 5]
    assert cal.drawer().startswith(_range_heading(3, 5))


def test_a_mouse_drag_opens_the_range_with_one_request(browser, mate):
    cal = Calendar(browser, mate)
    start, end = cal.cell(3).bounding_box(), cal.cell(5).bounding_box()
    mouse = cal.page.mouse

    def drag():
        mouse.move(start["x"] + start["width"] / 2, start["y"] + start["height"] / 2)
        mouse.down()
        mouse.move(end["x"] + end["width"] / 2, end["y"] + end["height"] / 2, steps=8)
        mouse.up()
    cal.swapped(drag)
    assert len(cal.asked) == 1 and cal.asked[0].endswith("&day=3&to_day=5"), cal.asked
    assert cal.ringed() == [3, 5]
    assert cal.drawer().startswith(_range_heading(3, 5))


def test_a_drag_released_outside_the_window_puts_the_choice_back(browser, mate):
    cal = Calendar(browser, mate)
    cal.swapped(lambda: cal.cell(5).click())
    b5, b3 = cal.cell(5).bounding_box(), cal.cell(3).bounding_box()
    start = {"x": b5["x"] + b5["width"] / 2, "y": b5["y"] + b5["height"] / 2}
    end = {"x": b3["x"] + b3["width"] / 2, "y": b3["y"] + b3["height"] / 2}
    cal.page.mouse.move(**start)
    cal.page.mouse.down()
    cal.page.mouse.move(**end, steps=8)
    assert cal.ringed() == [3, 5]                         # the drag's preview
    cal.page.mouse.move(-50, -50)
    # Back over the calendar with the button released out there: no pointerup ever reached the page.
    cal.page.context.new_cdp_session(cal.page).send(
        "Input.dispatchMouseEvent", {"type": "mouseMoved", **end, "buttons": 0})
    cal.page.wait_for_timeout(300)
    assert len(cal.asked) == 1 and cal.ringed() == [5]


def test_a_drag_on_a_calendar_another_month_replaced_opens_nothing(browser, mate):
    cal = Calendar(browser, mate)
    cal.swapped(lambda: cal.cell(5).click())
    held, months = [], lambda url: "/api/trips/calendar?" in url     # next month, arriving mid-drag
    cal.page.route(months, lambda route: held.append(route))
    cal.page.locator("#trips-calendar-month button[title]").last.click()       # ▶
    b3, b5 = cal.cell(3).bounding_box(), cal.cell(5).bounding_box()
    cal.page.mouse.move(b3["x"] + b3["width"] / 2, b3["y"] + b3["height"] / 2)
    cal.page.mouse.down()
    cal.page.mouse.move(b5["x"] + b5["width"] / 2, b5["y"] + b5["height"] / 2, steps=8)
    cal.swapped(lambda: [route.continue_() for route in held])
    cal.page.mouse.up()
    cal.page.wait_for_timeout(300)
    cal.page.unroute(months)
    cal.swapped(lambda: cal.page.locator("#trips-calendar-month button[title]").first.click())   # ◀ back
    assert len(cal.asked) == 1 and cal.ringed() == [5]


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


def test_the_charges_calendar_opens_the_range_with_one_request(browser, mate):
    cal = Calendar(browser, mate, "charges")
    cal.swapped(lambda: cal.cell(3).click())
    cal.swapped(lambda: cal.cell(5).click(modifiers=["Shift"]))
    assert len(cal.asked) == 2 and cal.asked[1].endswith("&day=3&to_day=5"), cal.asked
    assert cal.ringed() == [3, 5]
    assert cal.drawer().startswith(_range_heading(3, 5))


def test_a_link_to_a_charge_in_the_remembered_range_lands_on_it(browser, mate):
    """The link opens the charge's day, and the page's own request brings the remembered range back around
    it, newest first, so the charge of the 3rd ends up under the nine of the 5th: the page goes to it again."""
    cal = Calendar(browser, mate, "charges")
    cal.swapped(lambda: cal.cell(3).click())
    cal.swapped(lambda: cal.cell(5).click(modifiers=["Shift"]))
    cal.page.goto(f"{mate}/charges?highlight=1")
    cal.settle(1)
    cal.page.wait_for_load_state("networkidle")
    cal.page.wait_for_function("""() => new Promise(r => { const c = document.getElementById('charge-card-1');
      const y = c.getBoundingClientRect().top; setTimeout(() => r(c.getBoundingClientRect().top === y), 300); })""")
    assert cal.ringed() == [3, 5]
    top = cal.page.locator("#charge-card-1").bounding_box()["y"]
    assert 0 <= top < 900 - 60, ("the linked charge is out of sight", top)


def test_holding_a_day_on_the_charges_calendar_waits_for_the_tap_that_ends_the_range(browser, mate):
    cal = Calendar(browser, mate, "charges", phone=True)
    cal.touch(3, held=True)
    assert cal.asked == [] and cal.anchored() == 1
    cal.swapped(lambda: cal.cell(5).tap())
    assert len(cal.asked) == 1 and cal.asked[0].endswith("&day=3&to_day=5"), cal.asked
    assert cal.ringed() == [3, 5]
    assert cal.drawer().startswith(_range_heading(3, 5))


def test_holding_a_day_waits_for_the_tap_that_ends_the_range(browser, mate):
    cal = Calendar(browser, mate, phone=True)
    cal.touch(3, held=True)
    assert cal.asked == [] and cal.anchored() == 1        # nothing opened yet, the day is held
    cal.swapped(lambda: cal.cell(5).tap())
    assert len(cal.asked) == 1 and cal.asked[0].endswith("&day=3&to_day=5"), cal.asked
    assert cal.anchored() == 0
    assert cal.ringed() == [3, 5]
    assert cal.drawer().startswith(_range_heading(3, 5))


def test_a_tap_on_the_held_day_lets_it_go(browser, mate):
    cal = Calendar(browser, mate, phone=True)
    cal.touch(3, held=True)
    cal.cell(3).tap()
    cal.page.wait_for_timeout(300)
    assert cal.asked == [] and cal.anchored() == 0
    cal.swapped(lambda: cal.cell(5).tap())                # an ordinary tap again
    assert len(cal.asked) == 1 and cal.asked[0].endswith("&day=5"), cal.asked


def test_a_finger_that_scrolls_does_not_hold(browser, mate):
    cal = Calendar(browser, mate, phone=True)
    cal.touch(3, move=60)
    assert cal.asked == [] and cal.anchored() == 0


def test_a_held_finger_that_goes_on_to_scroll_lets_the_day_go(browser, mate):
    cal = Calendar(browser, mate, phone=True)
    cal.touch(3, held=True, scroll=60)
    assert cal.anchored() == 0
    cal.swapped(lambda: cal.cell(5).tap())                # an ordinary tap, not the end of a range
    assert len(cal.asked) == 1 and cal.asked[0].endswith("&day=5"), cal.asked


def test_two_fingers_are_not_a_hold(browser, mate):
    cal = Calendar(browser, mate, phone=True)
    errors = []
    cal.page.on("pageerror", lambda e: errors.append(str(e)))
    first, second = {**cal.centre(3), "id": 1}, {**cal.centre(5), "id": 2}
    cal.fingers("touchStart", first)
    cal.fingers("touchStart", first, second)
    cal.page.wait_for_timeout(200)
    cal.fingers("touchEnd")
    cal.page.wait_for_timeout(800)                        # past the hold's half second
    assert errors == [] and cal.anchored() == 0 and cal.asked == []


def test_a_second_finger_beside_one_off_the_days_is_not_a_hold(browser, mate):
    cal = Calendar(browser, mate, phone=True)
    box = cal.page.locator("#trips-calendar-month div.aspect-square", has_text="4").first.bounding_box()
    off = {"x": box["x"] + box["width"] / 2, "y": box["y"] + box["height"] / 2, "id": 1}   # the 4th drove nothing
    cal.fingers("touchStart", off)
    cal.fingers("touchStart", off, {**cal.centre(5), "id": 2})
    cal.page.wait_for_timeout(800)                        # past the hold's half second
    cal.fingers("touchEnd")
    cal.page.wait_for_timeout(300)
    assert cal.anchored() == 0
    cal.swapped(lambda: cal.cell(3).tap())
    assert len(cal.asked) == 1 and cal.asked[0].endswith("&day=3"), cal.asked


def test_choosing_a_day_another_way_lets_a_held_day_go(browser, mate):
    cal = Calendar(browser, mate, phone=True)
    cal.touch(3, held=True)
    cal.swapped(lambda: cal.cell(5).tap())                # the range 3–5
    cal.touch(5, held=True)
    cal.swapped(lambda: cal.page.locator('#trips-day-drawer [data-cal-day="3"]').tap())
    assert cal.anchored() == 0 and cal.ringed() == [3]
    cal.swapped(lambda: cal.cell(3).tap())                # the 3rd again, not the range 3–5
    assert cal.asked[-1].endswith("&day=3"), cal.asked

