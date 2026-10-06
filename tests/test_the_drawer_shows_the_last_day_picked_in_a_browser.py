"""The drawer under a calendar shows the day picked last, whichever answer arrives last. Requests from two
day cells used to run side by side, so a slow answer for the first day landed after the second and showed
it under the second day's ring; so did a slow merge view. A day that failed offers no retry once another
day is on its way; a block that refreshes itself keeps its retry while the refresh is on its way. Skips where it cannot run (no playwright, no Chromium), like the other browser tests.
"""
from datetime import datetime, timezone

import pytest

pytest.importorskip("fastapi", reason="web/main.py needs fastapi (absent in the minimal CI env)")
pytest.importorskip("uvicorn", reason="the page has to be SERVED, not rendered in-process")
sync_api = pytest.importorskip("playwright.sync_api", reason="needs playwright + `playwright install chromium`")

from web_in_a_browser import CHARGE_SQL, TRIP_SQL, chromium, seed_database, served

_MONTH = datetime.now(timezone.utc).date().replace(day=1)
# The first day's answer is held back, as a slow add-on would hold it.
_SLOW = """day => {
  const open = XMLHttpRequest.prototype.open, send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (m, u) { this._u = String(u); return open.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function (b) {
    if (this._u.endsWith('&day=' + day)) { setTimeout(() => send.call(this, b), 1500); return; }
    return send.call(this, b); }; }"""


def _at(day, hour):
    return f"{_MONTH.replace(day=day)}T{hour:02d}:00:00+00:00"


@pytest.fixture(scope="module")
def mate(tmp_path_factory):
    data = tmp_path_factory.mktemp("last-day")
    db = data / "leapmotor_mate.db"
    seed_database(db, "LVIN0000000000001", [
        ("INSERT INTO settings (key, value) VALUES ('timezone', 'UTC')", ()),
        (TRIP_SQL, (1, _at(3, 8), _at(3, 9))), (TRIP_SQL, (2, _at(5, 8), _at(5, 9))),
        (CHARGE_SQL, (1, _at(3, 20), _at(3, 22))), (CHARGE_SQL, (2, _at(5, 20), _at(5, 22))),
    ])
    with served(data, db) as url:
        yield url


@pytest.mark.parametrize("name", ["trips", "charges"])
def test_a_slow_answer_for_the_day_before_does_not_replace_the_day_picked(mate, name):
    pw, browser = chromium(sync_api)
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        assert page.goto(f"{mate}/{name}").status == 200
        page.wait_for_load_state("networkidle")
        page.evaluate(_SLOW, 3)
        page.locator('.cal-day[hx-get$="&day=3"]').click()
        page.wait_for_timeout(200)
        page.locator('.cal-day[hx-get$="&day=5"]').click()
        page.wait_for_timeout(2500)                       # past the first day's late answer
        assert page.locator(f"#{name}-day-drawer").inner_text().startswith(_MONTH.replace(day=5).strftime("%d %b"))
    finally:
        browser.close()
        pw.stop()


def _held(page, match):
    """Requests matching `match`, kept on their way until the test lets them go."""
    held = []
    page.route(match, lambda route: held.append(route))
    return held


def _let_go(held):
    for route in held:
        try:
            route.continue_()
        except sync_api.Error:
            pass                                          # the page gave up on it


def test_a_slow_merge_view_does_not_replace_the_day_picked_after_it(mate):
    pw, browser = chromium(sync_api)
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        assert page.goto(f"{mate}/trips").status == 200
        page.wait_for_load_state("networkidle")
        page.locator('.cal-day[hx-get$="&day=3"]').click()
        page.wait_for_selector('#trips-day-drawer button[hx-get*="merge=1"]')
        held = _held(page, lambda url: "merge=1" in url)
        page.locator('#trips-day-drawer button[hx-get*="merge=1"]').click()        # 🔗, held back
        page.wait_for_timeout(200)
        page.locator('.cal-day[hx-get$="&day=5"]').click()
        page.wait_for_timeout(500)
        _let_go(held)
        page.wait_for_timeout(500)
        assert held and page.locator("#trips-day-drawer").inner_text().startswith(_MONTH.replace(day=5).strftime("%d %b"))
    finally:
        browser.close()
        pw.stop()


def test_a_failed_day_offers_no_retry_once_another_day_is_on_its_way(mate):
    """Its retry would replace the request for the day picked after it."""
    pw, browser = chromium(sync_api)
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        assert page.goto(f"{mate}/trips").status == 200
        page.wait_for_load_state("networkidle")
        day = lambda n: lambda url: "/calendar/day?" in url and url.endswith(f"&day={n}")
        page.route(day(3), lambda route: route.fulfill(status=500, body="failed"))
        page.locator('.cal-day[hx-get$="&day=3"]').click()
        page.wait_for_selector(".lm-load-error")
        page.unroute(day(3))
        held = _held(page, day(5))
        page.locator('.cal-day[hx-get$="&day=5"]').click()
        page.wait_for_timeout(300)
        assert held and page.locator(".lm-load-error").count() == 0
        _let_go(held)
        page.wait_for_timeout(500)
        assert page.locator("#trips-day-drawer").inner_text().startswith(_MONTH.replace(day=5).strftime("%d %b"))
    finally:
        browser.close()
        pw.stop()


def test_a_block_that_refreshes_itself_keeps_its_retry_while_the_refresh_is_on_its_way(mate):
    """The refresh asks for what failed again: the strip stays until its answer replaces it."""
    pw, browser = chromium(sync_api)
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.clock.install()                              # the status card refreshes every 30 s
        page.route("**/api/status-card", lambda route: route.fulfill(status=500, body="failed"))
        assert page.goto(f"{mate}/").status == 200
        page.wait_for_selector("#status-card ~ .lm-load-error")
        page.unroute("**/api/status-card")
        held = _held(page, "**/api/status-card")
        page.clock.fast_forward("00:31")
        page.wait_for_timeout(300)
        assert held and page.locator("#status-card ~ .lm-load-error").count() == 1
        _let_go(held)
    finally:
        browser.close()
        pw.stop()
