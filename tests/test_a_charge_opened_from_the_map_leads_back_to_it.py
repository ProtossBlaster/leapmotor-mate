"""A charge opened from the Map leads back to the map, as the reader left it.

A charging place's popup on the Map opens its charges on Charges, which offered no way back: under the Home
Assistant ingress there is no browser back button, and the Map from the menu frames every route again, losing
where the reader had zoomed in. The Map keeps its view in its address, a popup's link carries that address,
and Charges offers "← Map" to it, which opens on the same view; clearing the place filter keeps it.
"""
import pytest
from events_fixture import Car, serve, web
from test_a_charge_opened_from_a_trip_leads_back_to_it import _trip_with_a_charge_after_it


def test_back_from_a_charge_returns_to_the_map_as_it_was(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    _, _, charge = _trip_with_a_charge_after_it(car)
    ingress = "/api/hassio_ingress/tok"
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("**/*", serve(client, ingress))
        page.goto(f"http://mate.test{ingress}/map")
        page.evaluate("map.setView([44.505, 11.355], 15)")
        page.wait_for_url(lambda url: "view=" in url)
        page.locator(".leaflet-marker-icon", has_text="1").first.click()
        page.locator(f'.leaflet-popup a[href^="charges?highlight={charge}"]').click()
        page.wait_for_url(f"**{ingress}/charges?highlight={charge}&back=map%3Fview%3D*")
        page.get_by_text("← Map").click()
        page.wait_for_url(f"**{ingress}/map?view=*")
        assert page.evaluate("map.getZoom()") == 15
        assert abs(page.evaluate("map.getCenter().lat") - 44.505) < 0.001
        assert errors == []
        browser.close()


def test_clearing_the_place_filter_keeps_the_way_back_to_the_map(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    _trip_with_a_charge_after_it(car)
    charges = client.get("/charges", params={"station": "x", "back": "map?view=44.5,11.3,15"}).text
    assert 'href="charges?back=map%3Fview%3D44.5%2C11.3%2C15"' in charges


def test_the_map_frames_the_routes_without_a_view_it_can_read(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    _trip_with_a_charge_after_it(car)
    ingress = "/api/hassio_ingress/tok"
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.route("**/*", serve(client, ingress))
        for view in ("", "x", "1,2", "1,2,z", "44.505,,15", ",11.355,15", "44.505,11.355,"):
            page.goto(f"http://mate.test{ingress}/map?view={view}")
            framed = page.evaluate("[map.getBounds().contains([44.49, 11.34]), map.getBounds().contains([44.51, 11.36])]")
            assert framed == [True, True], view
        browser.close()
