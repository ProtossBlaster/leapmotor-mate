"""A charge opened from a trip's map leads back to the trip, and the trip still leads back to where it was opened.

The trip's map marks the charges at the stop after it, and each opens on Charges. Under the Home Assistant ingress
there is no browser back button, and the trip page is in no menu: Charges offers "← Trip" to it. The link carries
the trip page's own address, its `back` included, so the trip comes back with its own "←" too, as do the ‹ ›
arrows between trips. Only a trip's own address is taken; the trip page checks its `back` in turn.
"""
from datetime import datetime, timedelta, timezone

import pytest
from events_fixture import Car, serve, web

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _trip(car, start, track=()):
    trip = car.db._conn.execute(
        "INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km, duration_min) VALUES (?, ?, ?, 12.5, 20)",
        (car.vid, start.isoformat(), (start + timedelta(minutes=20)).isoformat())).lastrowid
    for i, (lat, lon) in enumerate(track):
        car.db._conn.execute("INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude) VALUES (?, ?, ?, ?)",
                             (trip, (start + timedelta(minutes=2 * i)).isoformat(), lat, lon))
    car.db._conn.commit()
    return trip


def _trip_with_a_charge_after_it(car):
    """An earlier trip, then a trip with a track and a charge at the stop after it."""
    earlier = _trip(car, NOW - timedelta(hours=6))
    start = NOW - timedelta(hours=3)
    trip = _trip(car, start, [(44.49, 11.34), (44.50, 11.35), (44.51, 11.36)])
    charge = car.db._conn.execute(
        "INSERT INTO charges (vehicle_id, started_at, ended_at, energy_added_kwh, latitude, longitude)"
        " VALUES (?, ?, ?, 8.0, 44.51, 11.36)",
        (car.vid, (start + timedelta(minutes=30)).isoformat(), (start + timedelta(minutes=90)).isoformat())).lastrowid
    car.db._conn.commit()
    return earlier, trip, charge


def test_back_from_the_charge_returns_to_the_trip_with_its_own_back(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    earlier, trip, charge = _trip_with_a_charge_after_it(car)
    ingress = "/api/hassio_ingress/tok"
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 844})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("**/*", serve(client, ingress))
        page.goto(f"http://mate.test{ingress}/")
        page.locator(f'[data-recent-trips] a[data-trip-id="{trip}"]').click()
        page.wait_for_url(f"**{ingress}/trips/{trip}?back=*")
        page.locator(".leaflet-marker-icon", has_text="⚡").click()
        page.get_by_text("View charge →").click()
        page.wait_for_url(f"**{ingress}/charges?highlight={charge}&back=**")
        page.get_by_text("← Trip", exact=True).click()
        page.wait_for_url(f"**{ingress}/trips/{trip}?back=*")
        page.get_by_text("← Overview").click()
        page.wait_for_url(f"http://mate.test{ingress}/#trip-row-{trip}")
        assert errors == []
        browser.close()


def test_the_arrows_keep_where_the_trip_was_opened_from(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    earlier, trip, _ = _trip_with_a_charge_after_it(car)
    page = client.get(f"/trips/{trip}", params={"back": f".#trip-row-{trip}"}).text
    assert f'href="trips/{earlier}?back=.%23trip-row-{trip}"' in page


@pytest.mark.parametrize("back", ["trips/", "trips/x", "trips/1x", "trips/1/similar", "//trips/1", "trips/\u0661"])
def test_a_back_that_is_not_a_trip_is_ignored(tmp_path, monkeypatch, back):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    _, _, charge = _trip_with_a_charge_after_it(car)
    charges = client.get("/charges", params={"highlight": charge, "back": back}).text
    assert "← Trip<" not in charges
