"""A trip opened from Similar trips leads back to the list, at its row, and the list back to its trip.

Similar trips lists the other trips on a trip's route, each opening its trip page. Under the Home Assistant ingress
there is no browser back button: that trip page offered "← Trips", so the reader lost the comparison. The rows
carry the list's address, and the trip page offers "← Similar trips" to it. The list itself is opened with its
trip's address, so its "← Trip" keeps the way back that trip had.
"""
from datetime import datetime, timedelta, timezone

import pytest
from events_fixture import Car, serve, web
from test_similar_trips import A, B, _line_points, _seed_trip

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def test_back_from_a_similar_trip_returns_to_the_list_and_on_to_where_its_trip_was_opened(tmp_path, monkeypatch):
    car = Car(tmp_path)
    assert car.vid == 1     # _seed_trip writes vehicle 1
    client = web(car, monkeypatch)
    for tid, days_ago in ((1, 14), (2, 7), (3, 0)):
        start = NOW - timedelta(days=days_ago, hours=2)
        _seed_trip(car.db, tid, _line_points(*A, *B), started=start.isoformat(),
                   ended=(start + timedelta(minutes=30)).isoformat())
    ingress = "/api/hassio_ingress/tok"
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 844})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("**/*", serve(client, ingress))
        page.goto(f"http://mate.test{ingress}/")
        page.locator('[data-recent-trips] a[data-trip-id="3"]').click()
        page.wait_for_url(f"**{ingress}/trips/3?back=*")
        page.get_by_text("Similar trips", exact=False).first.click()
        page.wait_for_url(f"**{ingress}/trips/3/similar?back=**")
        page.locator('a[href^="trips/1?"]').click()
        page.wait_for_url(f"**{ingress}/trips/1?back=**")
        page.get_by_text("← Similar trips").click()
        page.wait_for_url(f"**{ingress}/trips/3/similar?back=**#trip-row-1")
        page.get_by_text("← Trip", exact=True).click()
        page.wait_for_url(f"**{ingress}/trips/3?back=*")
        page.get_by_text("← Overview").click()
        page.wait_for_url(f"http://mate.test{ingress}/#trip-row-3")
        assert errors == []
        browser.close()
