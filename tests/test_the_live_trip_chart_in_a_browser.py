"""The chart of the trip in progress on the Trips page is redrawn with every poll of its panel, as the chart of the
charge in progress is on Charges: each swap destroys the chart it replaces (ApexCharts lets go of its window
listener only in destroy()), a poll that fails keeps it, and the new chart is drawn in the step that swaps it in,
so the browser never paints the card without it. Once the trip the page was drawn with is no longer the one in
progress (it ended, or the next one began between two polls), the page reloads once to show it among the trips, but
not underneath a choice the reader is part-way through: the reload waits, as the idle auto-refresh does, and comes
back where the reader was on the page.

Measured in a real browser, like the other chart tests; skips where it cannot run.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi", reason="web/main.py needs fastapi (absent in the minimal CI env)")
pytest.importorskip("uvicorn", reason="the page has to be SERVED, not rendered in-process")
sync_api = pytest.importorskip("playwright.sync_api", reason="needs playwright + `playwright install chromium`")

from test_the_live_charge_chart_in_a_browser import _CANVASES, _COUNT, _alive
from web_in_a_browser import chromium, seed_database, served

VIN = "LVIN0000000000001"
STARTED = datetime(2026, 10, 2, 6, 13, tzinfo=timezone.utc)


def _readings(trip, start):
    rows = []
    for k in range(6):
        at = (start + timedelta(seconds=11 * k)).isoformat()
        rows.append(("INSERT INTO positions (vehicle_id, recorded_at, gear, speed_kmh, soc, odometer_km)"
                     " VALUES (1, ?, 'D', 50, ?, 2128)", (at, 88.3 - 0.1 * k)))
        rows.append(("INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude, speed_kmh, soc, power_kw)"
                     " VALUES (?, ?, ?, 9.0, 50, ?, 12)", (trip, at, 45 + 0.001 * k, 88.3 - 0.1 * k)))
    return rows


def _a_trip_in_progress():
    """Trip 7 in progress, after trip 6 the same morning (the day's drawer has a trip to merge)."""
    return [("INSERT INTO settings (key, value) VALUES ('timezone', 'UTC')", ()),
            ("INSERT INTO trips (id, vehicle_id, started_at, ended_at, start_soc, end_soc, distance_km, duration_min)"
             " VALUES (6, 1, ?, ?, 90, 88.3, 9, 30)",
             ((STARTED - timedelta(hours=1)).isoformat(), (STARTED - timedelta(minutes=30)).isoformat())),
            ("INSERT INTO trips (id, vehicle_id, started_at, start_soc, start_odometer_km) VALUES (7, 1, ?, 88.3, 2127)",
             (STARTED.isoformat(),))] + _readings(7, STARTED)


@pytest.fixture
def mate(tmp_path_factory):
    data = tmp_path_factory.mktemp("mate-live-trip")
    db = data / "leapmotor_mate.db"
    seed_database(db, VIN, _a_trip_in_progress())
    with served(data, db) as url:
        yield url, db


_DRAWN = "() => { const e = document.querySelector('#trip-live-panel #trip-profile'); return e && e._c && e.querySelector('.apexcharts-canvas'); }"
_SWAP = "() => htmx.ajax('GET', 'api/trip-live-chart', { target: '#trip-live-panel', swap: 'innerHTML' })"
_FAILED = "() => htmx.ajax('GET', 'api/trip-live-chart/nowhere', { target: '#trip-live-panel', swap: 'innerHTML' })"
# A poll as the panel is set up for, looked at right after the swap's own step: a timer set then fires
# before any the swap leaves behind.
_DRAWN_AFTER_ITS_SWAP = """() => new Promise(done => {
  const panel = document.getElementById('trip-live-panel');
  panel.addEventListener('htmx:afterSwap', () => setTimeout(() => done(!!(%s)()), 0), { once: true });
  htmx.ajax('GET', 'api/trip-live-chart', { source: panel });
})""" % _DRAWN

# A poll of the panel, as its timer makes it; resolves once the answer is swapped in.
_POLLED = """() => new Promise(done => {
  const panel = document.getElementById('trip-live-panel');
  panel.addEventListener('htmx:afterSwap', () => done(), { once: true });
  htmx.ajax('GET', 'api/trip-live-chart', { source: panel });
})"""


def _sql(db, *statements):
    con = sqlite3.connect(db)
    for sql, args in statements:
        con.execute(sql, args)
    con.commit()
    con.close()


_ENDED = ("UPDATE trips SET ended_at = ?, end_soc = 87.8, distance_km = 1 WHERE id = 7",
          ((STARTED + timedelta(minutes=1)).isoformat(),))
_NEXT = [("INSERT INTO trips (id, vehicle_id, started_at, start_soc, start_odometer_km) VALUES (8, 1, ?, 87.8, 2128)",
          ((STARTED + timedelta(minutes=3)).isoformat(),))] + _readings(8, STARTED + timedelta(minutes=3))


def test_a_redrawn_chart_does_not_outlive_its_element(mate):
    url, _ = mate
    pw, browser = chromium(sync_api)
    try:
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(_COUNT)
        assert page.goto(f"{url}/trips").status == 200
        page.wait_for_function(_DRAWN)
        drawn_once = _alive(page)

        for _ in range(3):
            page.evaluate(_SWAP)
            page.wait_for_function(_DRAWN)
        assert _alive(page) == drawn_once, "every redraw left the previous chart's listener behind"
        assert page.evaluate(_CANVASES) == 1

        page.evaluate(_FAILED)   # a poll that fails: nothing is swapped, so nothing may be destroyed
        assert page.evaluate(_CANVASES) == 1 and _alive(page) == drawn_once, \
            "a failed poll took the chart down although its element stayed"
        assert errors == []
    finally:
        browser.close()
        pw.stop()


def test_a_redrawn_chart_is_in_place_before_the_browser_paints(mate):
    url, _ = mate
    pw, browser = chromium(sync_api)
    try:
        page = browser.new_page()
        assert page.goto(f"{url}/trips").status == 200
        page.wait_for_function(_DRAWN)
        assert page.evaluate(_DRAWN_AFTER_ITS_SWAP), "the card was swapped in without its chart"
    finally:
        browser.close()
        pw.stop()


@pytest.mark.parametrize("in_progress", ["", "8"], ids=["it ends", "the next one begins"])
def test_the_page_reloads_once_the_trip_it_shows_is_over(mate, in_progress):
    url, db = mate
    pw, browser = chromium(sync_api)
    try:
        page = browser.new_page()
        page.clock.install()            # the reload waits for 20 s without an interaction
        assert page.goto(f"{url}/trips").status == 200
        page.wait_for_function(_DRAWN)
        page.evaluate("window.__before_the_end = true")
        _sql(db, _ENDED, *(_NEXT if in_progress else []))
        with page.expect_event("load"):           # a reload; every swap also rewrites the address in place
            page.evaluate(_POLLED)
            page.clock.fast_forward("00:21")
        assert page.evaluate("window.__before_the_end === undefined"), "the page did not reload"
        assert page.locator("#trip-live-state").get_attribute("data-trip") == in_progress
    finally:
        browser.close()
        pw.stop()


def test_the_reload_comes_back_where_the_reader_was(mate):
    url, db = mate
    pw, browser = chromium(sync_api)
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 500})
        page.clock.install()
        assert page.goto(f"{url}/trips?month={STARTED:%Y-%m}").status == 200
        page.wait_for_function(_DRAWN)
        page.locator(f'.cal-day[hx-get$="&day={STARTED.day}"]').click()
        page.wait_for_selector("#trips-day-drawer")
        page.evaluate("window.__before_the_end = true")
        _sql(db, _ENDED)
        page.evaluate(_POLLED)
        # At the bottom of the open day: deeper than the page is before the day comes back with its own request.
        place = page.evaluate("() => { const m = document.querySelector('main'); m.scrollTop = m.scrollHeight; return m.scrollTop; }")
        with page.expect_event("load"):
            page.clock.fast_forward("00:21")
        assert page.evaluate("window.__before_the_end === undefined"), "the page did not reload"
        page.wait_for_selector("#trips-day-drawer")
        page.wait_for_load_state("networkidle")
        assert place > 0 and page.evaluate("document.querySelector('main').scrollTop") == place, \
            "the reload did not take the reader back to their place in the open day"
    finally:
        browser.close()
        pw.stop()


def test_a_choice_in_progress_keeps_the_page_until_it_is_over(mate):
    """Merge mode, open when the trip ends, stays; the page reloads once the reader leaves it."""
    url, db = mate
    pw, browser = chromium(sync_api)
    try:
        page = browser.new_page()
        page.clock.install()
        assert page.goto(f"{url}/trips?month={STARTED:%Y-%m}").status == 200
        page.wait_for_function(_DRAWN)
        page.locator(f'.cal-day[hx-get$="&day={STARTED.day}"]').click()
        page.locator('#trips-day-drawer [hx-get*="&merge=1"]').click()
        page.wait_for_selector("[data-holds-selection]")
        page.evaluate("window.__before_the_end = true")
        _sql(db, _ENDED)
        page.evaluate(_POLLED)
        page.clock.fast_forward("00:21")
        page.wait_for_timeout(1500)
        assert page.evaluate("window.__before_the_end === true") and page.locator("[data-holds-selection]").count(), \
            "the end of the trip took merge mode with it"
        page.locator("#trips-day-drawer button", has_text="✕").click()
        page.wait_for_selector("[data-holds-selection]", state="detached")
        with page.expect_event("load"):
            page.clock.fast_forward("00:21")
        assert page.evaluate("window.__before_the_end === undefined"), "the page did not reload after the choice"
    finally:
        browser.close()
        pw.stop()
