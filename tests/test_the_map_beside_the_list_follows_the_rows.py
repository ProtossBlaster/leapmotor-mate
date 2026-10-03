"""The map of the Events list shows on demand and follows the rows, one place lit at a time.

It is hidden at first, the list at full width; 🗺 shows it (beside the list from 1280 px, above it on
a phone or a small laptop) and the choice is kept. A row's 🌍 shows the map if it was hidden, lights the row's point,
brings it into view without changing the zoom, and lights the row and the other half of its pair,
not every row of the place; the row stays where it was on screen while the list reflows around the
map, and on a phone it is not left under the map. A click on a point chooses that place: the point
and its rows are lit, and the list scrolls to its newest row, loading the parts before it if they
are not there yet. The row under the pointer lights its point for as long as it is there.
Whatever the order of these, at most one point is lit. A point stands for a ~110 m square, or for
a whole charging place at the place's own position, two places of the same name being two points.
Neither width scrolls sideways.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import db_reader
import pytest
from events_fixture import Car, event_row, grouped, serve, web

pw = pytest.importorskip("playwright.sync_api")

ZONE = ZoneInfo("Europe/Warsaw")
DAY = datetime.now(ZONE).date() - timedelta(days=1)
HOME, WORK, AWAY = (52.2297, 21.0122), (52.1800, 20.9500), (52.3000, 21.1000)
LIT = "#fbbf24"
POINTS = "#events-map path.leaflet-interactive"


def _local(hh, mm=0):
    return datetime(DAY.year, DAY.month, DAY.day, hh, mm, tzinfo=ZONE).astimezone(timezone.utc)


def _seed(car):
    for name, (lat, lon) in (("Dom", HOME), ("Dom", AWAY)):           # two places of one name
        car.db._conn.execute("INSERT INTO charging_places (vehicle_id, name, latitude, longitude, radius_m, rate)"
                             " VALUES (?, ?, ?, ?, 100, 0.6)", (car.vid, name, lat, lon))
    car.db._conn.commit()
    event_row(car, "trunk", _local(8), latitude=HOME[0], longitude=HOME[1])
    event_row(car, "trunk", _local(8, 2), state=0, latitude=HOME[0] + 0.00045, longitude=HOME[1])  # 50 m on, same place
    event_row(car, "unlocked", _local(17), latitude=WORK[0], longitude=WORK[1])
    event_row(car, "unlocked", _local(17, 5), state=0, latitude=0.0, longitude=0.0)               # no fix
    event_row(car, "cable", _local(19), latitude=AWAY[0] + 0.0002, longitude=AWAY[1])


def test_a_charging_place_is_one_point_where_it_stands(tmp_path, monkeypatch):
    car = Car(tmp_path)
    web(car, monkeypatch)
    _seed(car)
    ev = grouped(date_from=DAY.isoformat(), date_to=DAY.isoformat())
    assert [(p["key"], p["lat"], p["lon"], p["row"]) for p in ev["points"]] == [
        ("place:2", *AWAY, "ev-signal-5"), ("52.180,20.950", *WORK, "ev-signal-3"), ("place:1", *HOME, "ev-signal-2")]


def _open(p, client, width, query=f"date_from={DAY}&date_to={DAY}", fresh=True):
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": width, "height": 800})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/*", serve(client))
    page.goto(f"http://mate.test/events?{query}")
    if fresh:
        page.evaluate("() => localStorage.clear()")
        page.reload()
    return browser, page, errors


def _lit(page):
    return page.locator(POINTS).evaluate_all("ps => ps.filter(p => p.getAttribute('stroke') === '#fbbf24').length")


def _lit_in_view(page):
    box = page.locator("#events-map").bounding_box()
    dot = page.locator(f'{POINTS}[stroke="{LIT}"]').bounding_box()
    cx, cy = dot["x"] + dot["width"] / 2, dot["y"] + dot["height"] / 2
    return box["x"] <= cx <= box["x"] + box["width"] and box["y"] <= cy <= box["y"] + box["height"]


@pytest.mark.parametrize("width", [390, 1024, 1280])
def test_the_map_shows_on_demand_and_the_choice_is_kept(tmp_path, monkeypatch, width):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    _seed(car)
    with pw.sync_playwright() as p:
        browser, page, errors = _open(p, client, width)
        assert not page.locator("#events-map").is_visible()
        main = page.locator("main").bounding_box()
        assert page.locator("#events-list").bounding_box()["width"] > main["width"] - 80, "the list takes the width"
        page.locator("#events-map-toggle").click()
        page.locator(POINTS).first.wait_for()
        assert page.locator(POINTS).count() == 3, "three places, the fixless row none"
        map_box, list_box = page.locator("#events-map").bounding_box(), page.locator("#events-list").bounding_box()
        if width >= 1280:
            assert map_box["x"] > list_box["x"] + list_box["width"] - 1, "a column beside the list"
        else:
            assert map_box["y"] < list_box["y"] and map_box["height"] == 180, "above the list"
        assert page.evaluate("() => [document.documentElement.scrollWidth <= innerWidth,"
                             " (m => m.scrollWidth <= m.clientWidth)(document.querySelector('main'))]") == [True, True]
        page.reload()
        page.locator(POINTS).first.wait_for()
        assert page.locator("#events-map").is_visible(), "the choice is kept"
        page.locator("#events-map-toggle").click()
        assert not page.locator("#events-map").is_visible()
        page.reload()
        assert not page.locator("#events-map").is_visible()
        assert errors == []
        browser.close()


def test_a_pin_shows_the_map_and_brings_its_point_into_view(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    _seed(car)
    with pw.sync_playwright() as p:
        browser, page, errors = _open(p, client, 1280)
        page.locator("#ev-signal-3 button[data-p]").click()
        page.locator(POINTS).first.wait_for()
        assert page.locator("#events-map").is_visible() and _lit(page) == 1 and _lit_in_view(page)
        box = page.locator("#events-map").bounding_box()
        for _ in range(3):                                            # drag the map far away from every point
            page.mouse.move(box["x"] + 20, box["y"] + 20)
            page.mouse.down()
            page.mouse.move(box["x"] + box["width"] - 20, box["y"] + box["height"] - 20, steps=5)
            page.mouse.up()
        page.locator("#ev-signal-1").hover()
        assert _lit(page) == 1 and not _lit_in_view(page)
        page.locator("#ev-signal-1 button[data-p]").click()
        page.wait_for_function("() => !document.querySelector('.leaflet-pan-anim')")
        page.wait_for_timeout(400)
        assert _lit(page) == 1 and _lit_in_view(page), "panned to it"
        assert errors == []
        browser.close()


def test_a_point_chooses_its_place_and_a_row_lights_its_own_for_a_while(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    _seed(car)
    with pw.sync_playwright() as p:
        browser, page, errors = _open(p, client, 1280)
        page.locator("#events-map-toggle").click()
        page.locator(POINTS).first.wait_for()
        home = page.locator(POINTS).nth(2)
        home.dispatch_event("click")
        assert _lit(page) == 1
        assert page.locator(".event-row.ev-hl").evaluate_all("rs => rs.map(r => r.id)") == ["ev-signal-2", "ev-signal-1"]
        page.locator("#ev-signal-3").hover()                          # WORK under the pointer
        assert _lit(page) == 1 and page.locator(f'{POINTS}[stroke="{LIT}"]').count() == 1
        page.mouse.move(0, 0)
        assert _lit(page) == 1 and page.locator(".event-row.ev-hl").count() == 2, "back to the place chosen"
        assert errors == []
        browser.close()


def test_never_two_points_lit(tmp_path, monkeypatch):
    """Before: a tap on a point (a click with no mouseout after it) lit it for good, and a row of
    another place under the pointer lit a second one."""
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    _seed(car)
    with pw.sync_playwright() as p:
        browser, page, errors = _open(p, client, 1280)
        page.locator("#ev-signal-1 button[data-p]").click()
        page.locator(POINTS).first.wait_for()
        assert _lit(page) == 1
        page.locator("#ev-signal-3").hover()
        assert _lit(page) == 1
        page.locator(POINTS).nth(0).dispatch_event("click")
        assert _lit(page) == 1
        page.locator("#events-search-q").press_sequentially("cable")
        page.wait_for_url("**q=cable*")
        page.wait_for_function("() => document.querySelectorAll('#events-map path.leaflet-interactive').length === 1")
        assert _lit(page) <= 1
        assert errors == []
        browser.close()


def test_a_search_redraws_the_points_and_keeps_a_place_still_listed(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    _seed(car)
    with pw.sync_playwright() as p:
        browser, page, errors = _open(p, client, 1280)
        page.locator("#events-map-toggle").click()
        page.locator(POINTS).first.wait_for()
        page.locator(POINTS).nth(2).dispatch_event("click")         # home
        page.locator("#events-search-q").press_sequentially("tailgate")
        page.wait_for_url("**q=tailgate*")
        page.wait_for_function("() => document.querySelectorAll('#events-map path.leaflet-interactive').length === 1")
        assert _lit(page) == 1 and page.locator(".event-row.ev-hl").count() == 2
        assert errors == []
        browser.close()


def test_a_point_whose_newest_row_is_not_loaded_yet_loads_the_list_to_it(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    event_row(car, "trunk", _local(6), latitude=WORK[0], longitude=WORK[1])
    event_row(car, "trunk", _local(6, 1), state=0, latitude=WORK[0], longitude=WORK[1])
    for k in range(30):
        event_row(car, "unlocked", _local(7) + timedelta(minutes=15 * k), latitude=HOME[0], longitude=HOME[1])
        event_row(car, "unlocked", _local(7, 5) + timedelta(minutes=15 * k), state=0, latitude=HOME[0], longitude=HOME[1])
    monkeypatch.setattr(db_reader, "EVENTS_PART_ROWS", 10)
    with pw.sync_playwright() as p:
        browser, page, errors = _open(p, client, 1280)
        page.locator("#events-map-toggle").click()
        page.locator(POINTS).first.wait_for()
        # Let the list come to rest before reading what is loaded. A part loads when the end of the
        # list comes into view, and the part that lands can bring the end into view again, so the
        # loading cascades until it does not — which is geometry, and settles after three parts
        # here. The first point appearing is not that moment: on a loaded machine the map takes long
        # enough to draw that the cascade is still running, and then "not loaded yet" is a race and
        # not a fact. The condition below is the cascade's own stopping rule.
        page.wait_for_function("""() => {
            const more = document.querySelector('.ev-more');
            if (!more) return true;                                   // nothing left to load
            if (document.querySelector('.htmx-request')) return false;
            const r = more.getBoundingClientRect();
            return r.top >= innerHeight || r.bottom <= 0;             // the end is out of view
        }""")
        assert page.locator("#ev-signal-2").count() == 0
        page.locator(POINTS).nth(1).dispatch_event("click")           # the points come newest place first
        page.locator("#ev-signal-2").wait_for()
        page.wait_for_function("() => { const r = document.getElementById('ev-signal-2').getBoundingClientRect();"
                               " return r.top >= 0 && r.bottom <= innerHeight; }")
        assert page.locator(".event-row.ev-hl").evaluate_all("rs => rs.map(r => r.id)") == ["ev-signal-2", "ev-signal-1"]
        assert errors == []
        browser.close()


def test_a_rows_globe_lights_it_and_its_pair_not_the_place(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    _seed(car)
    with pw.sync_playwright() as p:
        browser, page, errors = _open(p, client, 1280)
        page.locator("#ev-signal-1 button[data-p]").click()
        page.locator(POINTS).first.wait_for()
        assert page.locator("#ev-signal-1 button[data-p]").inner_text() == "🌍"
        assert page.locator(".event-row.ev-pair").evaluate_all("rs => rs.map(r => r.id)") == ["ev-signal-2", "ev-signal-1"]
        assert page.locator(".event-row.ev-hl").count() == 0 and _lit(page) == 1
        page.locator("#ev-signal-5 button[data-p]").click()             # a state still on: the row alone
        assert page.locator(".event-row.ev-pair").evaluate_all("rs => rs.map(r => r.id)") == ["ev-signal-5"]
        assert errors == []
        browser.close()


def _settled_top(page, row):
    """Where the row stands once the rows around it are laid out: content-visibility lays them out
    in later frames, later still on a busy machine."""
    seen = [None, row.evaluate("r => r.getBoundingClientRect().top")]
    while seen[-1] != seen[-2]:
        page.wait_for_timeout(100)
        seen.append(row.evaluate("r => r.getBoundingClientRect().top"))
    return seen[-1]


@pytest.mark.parametrize("width", [390, 1280])
def test_opening_the_map_leaves_the_row_where_it_was(tmp_path, monkeypatch, width):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    for k in range(30):              # states at once and long rows, as on a real list: they wrap anew when the map comes
        at = _local(0) + timedelta(minutes=40 * k)
        for kind, on, off in (("cable", 0, 30), ("climate", 1, 20), ("trunk", 2, 3)):
            event_row(car, kind, at + timedelta(minutes=on), latitude=HOME[0], longitude=HOME[1], soc=40.0,
                      inside_temp=17.0, climate_target_temp=21.5, outside_temp=8.0)
            event_row(car, kind, at + timedelta(minutes=off), state=0, latitude=HOME[0], longitude=HOME[1], soc=60.0,
                      inside_temp=21.5)
        car.db._conn.execute("INSERT INTO charges (vehicle_id, started_at, ended_at, energy_added_kwh) VALUES (?, ?, ?, 5.0)",
                             (car.vid, (at + timedelta(minutes=12)).isoformat(), (at + timedelta(minutes=25)).isoformat()))
    car.db._conn.commit()
    with pw.sync_playwright() as p:
        browser, page, errors = _open(p, client, width)
        row = page.locator("#ev-signal-61")
        row.scroll_into_view_if_needed()
        top = _settled_top(page, row)
        row.locator("button[data-p]").click()
        page.locator(POINTS).first.wait_for()
        if width >= 1280:                                      # once the frames hold() takes have passed
            page.wait_for_function(f"() => Math.abs(document.getElementById('ev-signal-61').getBoundingClientRect().top"
                                   f" - {top}) <= 1", timeout=3000)   # the list narrowed around the row, which stayed put
        else:
            now = row.evaluate("r => r.getBoundingClientRect().top")
            assert now >= page.locator("#events-map").bounding_box()["y"] + 180, "below the map, not under it"
        assert errors == []
        browser.close()


@pytest.mark.parametrize("opener", ["#events-map-toggle", "#ev-signal-41 button[data-p]"])
def test_a_row_near_the_top_of_a_scrolled_list_is_not_left_under_the_map(tmp_path, monkeypatch, opener):
    """On a phone the map sticks above the list; with the list scrolled, the row at the top of the
    screen comes out below it, whether the map opens from 🗺 or from the row's 🌍."""
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    for k in range(40):
        event_row(car, "trunk", _local(6) + timedelta(minutes=15 * k), latitude=HOME[0], longitude=HOME[1])
        event_row(car, "trunk", _local(6, 2) + timedelta(minutes=15 * k), state=0, latitude=HOME[0], longitude=HOME[1])
    with pw.sync_playwright() as p:
        browser, page, errors = _open(p, client, 390)
        page.evaluate("""() => { const r = document.getElementById('ev-signal-41'), m = document.querySelector('main');
            (m.scrollHeight > m.clientHeight ? m : window).scrollBy(0, r.getBoundingClientRect().top - 190); }""")
        page.locator(opener).click()
        page.locator(POINTS).first.wait_for()
        page.wait_for_function("""() => document.getElementById('ev-signal-41').getBoundingClientRect().top
            >= document.getElementById('events-map-box').getBoundingClientRect().bottom""", timeout=3000)  # below, not under
        assert errors == []
        browser.close()


@pytest.mark.parametrize("width", [390, 1280])
def test_the_map_sticks_below_the_bars_at_the_top(tmp_path, monkeypatch, width):
    """No password set: the red bar stays at the top of every page (here with the bar of a car not
    set up yet), and the map sticks below the tallest, not under it; once the bars are gone, the map
    takes their place."""
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    for k in range(40):
        event_row(car, "trunk", _local(6) + timedelta(minutes=15 * k), latitude=HOME[0], longitude=HOME[1])
        event_row(car, "trunk", _local(6, 2) + timedelta(minutes=15 * k), state=0, latitude=HOME[0], longitude=HOME[1])
    with pw.sync_playwright() as p:
        browser, page, errors = _open(p, client, width)
        page.locator("#events-map-toggle").click()
        page.locator(POINTS).first.wait_for()
        page.locator("#ev-signal-1").scroll_into_view_if_needed()                   # far down: both are stuck now
        top = page.locator("main").bounding_box()["y"]                             # below the phone's header
        bar = page.locator("#auth-bar").bounding_box()
        box = page.locator("#events-map-box").bounding_box()
        assert bar["y"] == top and box["y"] >= bar["y"] + bar["height"] - 0.5, "below the bar"
        assert box["y"] + box["height"] <= 800 + 0.5, "and still all on screen"
        page.evaluate("() => document.querySelectorAll('main > [id$=\"-bar\"]').forEach(b => b.remove())")
        page.wait_for_function(f"() => document.getElementById('events-map-box').getBoundingClientRect().top < {top} + 20")
        assert errors == []
        browser.close()
