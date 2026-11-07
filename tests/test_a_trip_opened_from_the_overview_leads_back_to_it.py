"""A trip opened from the Overview's Recent Trips leads back to the Overview.

Under the Home Assistant ingress there is no browser back button, and the trip page's own link goes to
Trips. A Recent Trips row carries `back`, the Overview's address as the menu has it with the row's anchor,
and the trip page offers "← Overview" to it, as it offers "← Events" to a trip opened from Events: the
Overview comes back with the row in view, below whatever sticks over the page. Only an address of the
Overview itself is taken: the parameter must not lead out of Mate.
"""
from datetime import datetime, timedelta, timezone

import pytest
from events_fixture import Car, serve, web


def _trip(car):
    start = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=3)
    trip = car.db._conn.execute(
        "INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km, duration_min) VALUES (?, ?, ?, 12.5, 20)",
        (car.vid, start.isoformat(), (start + timedelta(minutes=20)).isoformat())).lastrowid
    car.db._conn.commit()
    return trip


def test_back_from_a_recent_trip_returns_to_the_overview(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    trip = _trip(car)
    ingress = "/api/hassio_ingress/tok"
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 700})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("**/*", serve(client, ingress))
        page.goto(f"http://mate.test{ingress}/")
        row = page.locator(f'[data-recent-trips] a[data-trip-id="{trip}"]')
        assert row.bounding_box()["y"] > 700, "the row starts below the first screen"
        row.click()
        page.wait_for_url(f"**{ingress}/trips/{trip}?back=*")
        page.get_by_text("← Overview").click()
        page.wait_for_url(f"http://mate.test{ingress}/#trip-row-{trip}")
        top, bottom, covered = page.evaluate(f"""() => {{
            const r = document.querySelector('[data-recent-trips] a[data-trip-id="{trip}"]').getBoundingClientRect();
            const over = [...document.querySelectorAll('body *')]
                .filter(el => ['sticky', 'fixed'].includes(getComputedStyle(el).position) && el.offsetHeight)
                .map(el => el.getBoundingClientRect()).filter(b => b.top <= 0 && b.left < r.right && b.right > r.left)
                .map(b => b.bottom);
            return [r.top, r.bottom, Math.max(0, ...over)]; }}""")
        assert covered <= top and bottom <= 700, f"row {top}–{bottom}, covered down to {covered}"
        assert errors == []
        browser.close()


@pytest.mark.parametrize("back", ["..", "./", ".evil", ".//example.com", "./trips"])
def test_a_back_that_is_not_the_overview_itself_is_ignored(tmp_path, monkeypatch, back):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    trip = _trip(car)
    page = client.get(f"/trips/{trip}", params={"back": back}).text
    assert "← Overview" not in page and f'href="trips?highlight={trip}"' in page
