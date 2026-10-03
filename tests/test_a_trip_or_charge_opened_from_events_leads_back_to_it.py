"""A trip or a charge opened from the Events page leads back to the same list.

Under the Home Assistant ingress there is no browser back button, and the trip page's own link goes
to Trips: the reader lost the filtered list and the row. The links on a trip's and a charge's rows
carry `back`, the list's URL with its filters and the row's anchor, and the trip page and Charges
offer "← Events" to it. Only a URL of the Events list is taken, so the parameter cannot lead out of
Mate. The row comes back into view below whatever sticks over the list (Mate's bars, the map on a
phone), and a row beyond the first part of the list is loaded first; it is lit.
"""
import html
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import db_reader
import pytest
from events_fixture import Car, event_row, serve, web

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _trip_and_charge(car):
    start = NOW - timedelta(hours=3)
    trip = car.db._conn.execute(
        "INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km, duration_min) VALUES (?, ?, ?, 12.5, 20)",
        (car.vid, start.isoformat(), (start + timedelta(minutes=20)).isoformat())).lastrowid
    charge = car.db._conn.execute(
        "INSERT INTO charges (vehicle_id, started_at, ended_at, energy_added_kwh) VALUES (?, ?, ?, 8.0)",
        (car.vid, (start + timedelta(hours=1)).isoformat(), (start + timedelta(hours=2)).isoformat())).lastrowid
    car.db._conn.commit()
    return trip, charge


def test_the_rows_links_carry_the_filtered_list_and_the_row(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    trip, charge = _trip_and_charge(car)
    page = client.get("/events?f=1&group=driving&group=charging&q=").text
    href = html.unescape(re.search(rf'href="(trips/{trip}\?back=[^"]*%23ev-trip-{trip}-off)"', page).group(1))
    back = client.get("/" + href).text
    m = re.search(r'href="(events\?[^"]*)"[^>]*>([^<]*)<', back)
    target = html.unescape(m.group(1))
    assert m.group(2).strip() == "← Events"
    assert "group=driving" in target and "group=charging" in target and "f=1" in target
    assert target.endswith(f"#ev-trip-{trip}-off")
    assert f'id="{target.split("#")[1]}"' in page, "the anchor names a row of the list"

    href = html.unescape(re.search(rf'href="(charges\?highlight={charge}&amp;back=[^"]*-off#charge-card-{charge})"', page).group(1))
    assert href.endswith(f"#charge-card-{charge}") and "back=events" in href
    charges = client.get("/" + href.split("#")[0]).text
    assert re.search(r'href="events\?[^"]*group=driving[^"]*"[^>]*>\s*← Events', charges)


@pytest.mark.parametrize("back", ["https://example.com/", "//example.com/events", "javascript:alert(1)",
                                  "eventsx", "trips/1", ""])
def test_a_back_that_is_not_the_events_list_is_ignored(tmp_path, monkeypatch, back):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    trip, charge = _trip_and_charge(car)
    trip_page = client.get(f"/trips/{trip}", params={"back": back}).text
    assert "← Events" not in trip_page and f'href="trips?highlight={trip}"' in trip_page
    charges = client.get("/charges", params={"highlight": charge, "back": back}).text
    assert "← Events" not in charges and "example.com" not in trip_page + charges


def test_back_from_a_trip_returns_to_the_filtered_list_at_the_row(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    trip, _ = _trip_and_charge(car)
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 700})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.route("**/*", serve(client))
        page.goto("http://mate.test/events?f=1&group=driving")
        page.locator(f'#ev-trip-{trip}-off a[href^="trips/{trip}"]').click()
        page.wait_for_url(f"**/trips/{trip}?back=*")
        page.get_by_text("← Events").click()
        page.wait_for_url("**/events?*")
        assert "group=driving" in page.url and page.url.endswith(f"#ev-trip-{trip}-off")
        assert page.locator('input[name=group][value="security"]').is_checked() is False
        assert errors == []
        browser.close()


def test_back_to_a_row_beyond_the_first_part_loads_the_list_to_it(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    trip, charge = _trip_and_charge(car)
    for k in range(30):                                   # 60 rows newer than the trip and the charge
        event_row(car, "unlocked", NOW - timedelta(minutes=50 - k))
        event_row(car, "unlocked", NOW - timedelta(minutes=50 - k, seconds=-20), state=0)
    monkeypatch.setattr(db_reader, "EVENTS_PART_ROWS", 10)
    ingress = "/api/hassio_ingress/tok"
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 700})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("**/*", serve(client, ingress))
        for start, anchor in ((f"trips/{trip}", f"ev-trip-{trip}-off"),
                              (f"charges?highlight={charge}", f"ev-charge-{charge}-off")):
            back = quote(f"events?f=1&group=driving&group=charging&group=security#{anchor}")
            page.goto(f"http://mate.test{ingress}/{start}{'&' if '?' in start else '?'}back={back}")
            page.get_by_text("← Events").first.click()
            page.wait_for_url(f"**{ingress}/events?*")
            page.wait_for_function(f"""() => {{ const r = document.getElementById('{anchor}');
                return r && r.classList.contains('ev-pair') && r.getBoundingClientRect().top >= 0
                    && r.getBoundingClientRect().bottom <= innerHeight; }}""")
            assert page.locator(".event-row").count() > 10, "loaded past the first part"
            assert "group=security" in page.url and page.url.endswith(f"#{anchor}")
        assert errors == []
        browser.close()


def _late(client):
    """The page's assets half a second late, as over a network: the page is laid out, and loaded,
    well after its script has run."""
    answer = serve(client)

    def handle(route):
        if re.search(r"tailwind|leaflet|\.(png|svg|ico)\b", route.request.url):
            time.sleep(0.5)
        return answer(route)
    return handle


@pytest.mark.parametrize("width", [390, 1024, 1280])
@pytest.mark.parametrize("map_on", [False, True])
def test_back_leaves_the_row_in_view_below_the_bars_and_the_map(tmp_path, monkeypatch, map_on, width):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    trip, _ = _trip_and_charge(car)
    for k in range(30):                                   # rows above the trip's, so the page scrolls to it
        event_row(car, "unlocked", NOW - timedelta(minutes=50 - k))
        event_row(car, "unlocked", NOW - timedelta(minutes=50 - k, seconds=-20), state=0)
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": 700})
        page.route("**/*", _late(client))
        page.goto("http://mate.test/events")
        page.evaluate(f"() => localStorage.setItem('mate.events.map', '{int(map_on)}')")
        page.goto(f"http://mate.test/trips/{trip}?back=" + quote(f"events?range=3d#ev-trip-{trip}-off"))
        page.get_by_text("← Events").first.click()
        page.wait_for_url("**/events?*")
        place = f"""() => {{ const r = document.getElementById('ev-trip-{trip}-off').getBoundingClientRect();
            const over = [...document.querySelector('main').children, document.getElementById('events-map-box')]
                .filter(el => getComputedStyle(el).position === 'sticky' && el.offsetHeight)
                .map(el => el.getBoundingClientRect()).filter(b => b.left < r.right && b.right > r.left)
                .map(b => b.bottom);
            return [Math.round(r.top), Math.round(r.bottom), Math.round(Math.max(0, ...over))]; }}"""
        seen = [None, page.evaluate(place)]
        while seen[-1] != seen[-2]:                       # once the layout has settled
            page.wait_for_timeout(150)
            seen.append(page.evaluate(place))
        top, bottom, covered = seen[-1]
        assert covered <= top and bottom <= 700, f"row {top}–{bottom}, covered down to {covered}"
        assert page.locator(f"#ev-trip-{trip}-off.ev-pair").count() == 1
        browser.close()
