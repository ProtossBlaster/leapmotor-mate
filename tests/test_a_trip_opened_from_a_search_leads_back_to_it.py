"""A trip opened from a Trips search leads back to the results, at its row; the address says what the page shows.

A search on Trips swaps the calendar for its results, and a result opens its trip page, whose "← Trips" opened the
calendar again: under the Home Assistant ingress there is no browser back button, so the reader typed the search
again. The page keeps its search in its address, a result carries that address, and the trip page's "← Trips"
leads to it: Trips opens on the same search, its fields filled in, back at the row. A reload keeps the search too.
Whatever brings the calendar back takes the search out of the address, and Back to calendar empties the fields.
"""
from datetime import timedelta

import pytest
from events_fixture import Car, serve, web
from test_a_charge_opened_from_a_trip_leads_back_to_it import NOW, _trip

INGRESS = "/api/hassio_ingress/tok"


def _trips(car):
    """Twelve trips, one a day, and one the year before; the oldest of the twelve and a middle one carry the
    note searched for."""
    ids = [_trip(car, NOW - timedelta(days=d, hours=3)) for d in range(12)]
    _trip(car, NOW - timedelta(days=400))
    for trip in (ids[5], ids[11]):
        car.db._conn.execute("UPDATE trips SET note = 'Dentist' WHERE id = ?", (trip,))
    car.db._conn.commit()
    return ids


def _until(page, ready, what):
    """Waits a few seconds for `ready()`, then fails: a page that never sends the request would hang the run."""
    for _ in range(250):
        if ready():
            return
        page.wait_for_timeout(20)
    raise AssertionError(f"timed out waiting for {what}")


def _browse(tmp_path, monkeypatch, check, route=lambda handle: handle):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    ids = _trips(car)
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 844})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("**/*", route(serve(client, INGRESS)))
        check(page, ids)
        assert errors == []
        browser.close()


def test_back_from_a_result_returns_to_the_search_at_its_row(tmp_path, monkeypatch):
    def check(page, ids):
        oldest = ids[11]
        page.goto(f"http://mate.test{INGRESS}/trips")
        page.locator("#trips-search-q").press_sequentially("dentist")
        page.wait_for_url(f"**{INGRESS}/trips?q=dentist")
        page.goto(page.url)     # as a reload opens it: the results, the row below the fold
        row = f"document.getElementById('trip-row-{oldest}').getBoundingClientRect()"
        assert page.evaluate(f"{row}.top") > 844
        page.locator(f'#trips-calendar-month-wrap a[data-trip-id="{oldest}"]').click()
        page.wait_for_url(f"**{INGRESS}/trips/{oldest}?back=*")
        page.get_by_text("← Trips").click()
        page.wait_for_url(f"**{INGRESS}/trips?q=dentist#trip-row-{oldest}")
        assert page.input_value("#trips-search-q") == "dentist"
        assert page.locator("#trips-calendar-month-wrap a[data-trip-id]").count() == 2
        page.wait_for_function(f"() => {row}.top >= 0 && {row}.bottom <= innerHeight")
    _browse(tmp_path, monkeypatch, check)


def test_the_row_comes_into_view_once_the_results_load(tmp_path, monkeypatch):
    """A browser that loses the first paint's jump to the row when the results are swapped in, as WebKit does."""
    held, handlers = [], []

    def hold_the_load(handle):
        handlers.append(handle)
        return lambda r: held.append(r) if "/api/trips/search?" in r.request.url and not held else handle(r)

    def check(page, ids):
        oldest = ids[11]
        page.goto(f"http://mate.test{INGRESS}/trips?q=dentist#trip-row-{oldest}")
        _until(page, lambda: held, "the search the page sends on load")
        page.evaluate("document.querySelector('main').scrollTop = 0")
        row = f"document.getElementById('trip-row-{oldest}').getBoundingClientRect()"
        assert page.evaluate(f"{row}.top") > 844
        handlers[0](held[0])
        page.wait_for_function(f"() => {row}.top >= 0 && {row}.bottom <= innerHeight")
    _browse(tmp_path, monkeypatch, check, hold_the_load)


def test_back_from_a_result_keeps_the_month_the_search_was_made_on(tmp_path, monkeypatch):
    def check(page, ids):
        page.goto(f"http://mate.test{INGRESS}/trips")
        page.locator("#trips-calendar-month button", has_text="◀").click()        # no new page drawn
        page.wait_for_url(lambda url: "month=" in url)
        month = page.locator("[data-cal-month]").get_attribute("data-cal-month")
        page.locator("#trips-search-q").press_sequentially("dentist")
        page.locator(f'#trips-calendar-month-wrap a[data-trip-id="{ids[11]}"]').click()
        page.get_by_text("← Trips").click()
        page.wait_for_url(lambda url: "q=dentist" in url)
        page.locator("#trips-calendar-month-wrap button", has_text="Back to calendar").click()
        page.wait_for_function(f"document.querySelector('[data-cal-month]')?.dataset.calMonth === {month!r}")
    _browse(tmp_path, monkeypatch, check)


def test_the_address_keeps_the_search_the_results_show(tmp_path, monkeypatch):
    """Typed on while the results are on their way, and the next search fails: the results shown stay in the address."""
    held, handlers = [], []

    def slow_then_failing(handle):
        handlers.append(handle)

        def route(r):
            if "/api/trips/search?" in r.request.url and "q=missing" in r.request.url:
                r.fulfill(status=500)
            elif "/api/trips/search?" in r.request.url and not held:
                held.append(r)
            else:
                handle(r)
        return route

    def check(page, ids):
        page.goto(f"http://mate.test{INGRESS}/trips")
        page.locator("#trips-search-q").press_sequentially("dentist")
        _until(page, lambda: held, "the typed search")
        page.locator("#trips-search-q").fill("")
        page.locator("#trips-search-q").press_sequentially("missing")
        page.wait_for_timeout(600)          # past the search's delay: the next search waits for the answer
        handlers[0](held[0])
        page.locator("#trips-calendar-month-wrap a[data-trip-id]").first.wait_for()
        page.wait_for_load_state("networkidle")
        assert "q=dentist" in page.url
    _browse(tmp_path, monkeypatch, check, slow_then_failing)


def test_the_calendar_takes_the_search_out_of_the_address(tmp_path, monkeypatch):
    def check(page, ids):
        page.goto(f"http://mate.test{INGRESS}/trips?q=dentist&km_min=1&eff_max=99&duration_max=999")
        assert page.input_value("#trips-search-q") == "dentist" and page.input_value("[name=km_min]") == "1"
        assert page.locator("#trips-adv-filters").is_visible()
        page.locator("#trips-calendar-month-wrap button", has_text="Back to calendar").click()
        page.wait_for_url(lambda url: all(k not in url for k in ("q=", "km_min", "eff_max", "duration_max")))
        assert page.input_value("#trips-search-q") == "" and page.input_value("[name=km_min]") == ""
        page.locator("#trips-search-q").press_sequentially("dentist")
        page.wait_for_url(f"**{INGRESS}/trips?q=dentist")
        page.locator("button[hx-get*='api/trips/calendar?year=']:not(#trips-calendar-month-wrap *)").first.click()
        page.wait_for_url(lambda url: "q=" not in url)
    _browse(tmp_path, monkeypatch, check)
