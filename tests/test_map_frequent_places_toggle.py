"""#315: hiding frequent places leaves routes, stations and the viewport alone."""
import pathlib

import pytest
from test_the_map_controls_survive_an_empty_map import _render, UN_VIAGGIO

pw = pytest.importorskip("playwright.sync_api")
STATIC = pathlib.Path(__file__).resolve().parent.parent / "web" / "static"


@pytest.fixture
def browser():
    with pw.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def open_map(browser, *, width=1100, empty=False, blocked_storage=False):
    context = browser.new_context(viewport={"width": width, "height": 850})
    if blocked_storage:
        context.add_init_script("Object.defineProperty(window, 'localStorage', {get() {throw new Error('Storage blocked');}})")
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    html = _render(track=[] if empty else UN_VIAGGIO,
                   places=[{"latitude":45.46,"longitude":9.19,"visits":10}],
                   stations=[{"latitude":45.47,"longitude":9.20,"sessions":1,
                              "name":"Test station","kwh":10,"cost_fmt":None,
                              "recent":[],"key":"test"}])

    def serve(route):
        url = route.request.url
        if url == "http://mate.test/map":
            route.fulfill(body=html, content_type="text/html")
        elif "/static/" in url:
            file = STATIC / url.split("/static/", 1)[1].split("?", 1)[0]
            if file.is_file():
                route.fulfill(path=str(file))
            else:
                route.fulfill(status=404)
        else:
            route.fulfill(status=204)
    page.route("**/*", serve)
    page.goto("http://mate.test/map")
    return context, page, errors


def state(page):
    return page.evaluate("""() => {
        let circles=0, routes=0, stations=0;
        map.eachLayer(layer => {
            if (layer instanceof L.CircleMarker) circles++;
            else if (layer instanceof L.Polyline) routes++;
            else if (layer instanceof L.Marker) stations++;
        });
        return {circles, routes, stations, zoom:map.getZoom(), center:map.getCenter()};
    }""")


@pytest.mark.parametrize("width", [390, 1100])
def test_toggle_preserves_other_layers_view_and_preference(browser, width):
    context, page, errors = open_map(browser, width=width)
    toggle = page.get_by_role("checkbox", name="map_frequent_places")
    assert toggle.is_checked()
    before = state(page)
    assert (before["circles"], before["routes"], before["stations"]) == (1, 2, 1)
    toggle.focus()
    page.keyboard.press("Space")
    assert not toggle.is_checked()
    assert state(page) == dict(before, circles=0)
    page.reload()
    assert not toggle.is_checked()
    assert state(page)["circles"] == 0
    for _ in range(3):
        toggle.check()
        assert state(page)["circles"] == 1
        toggle.uncheck()
    toggle.check()
    assert state(page) == before
    assert errors == []
    context.close()


def test_toggle_works_when_storage_is_blocked(browser):
    context, page, errors = open_map(browser, blocked_storage=True)
    toggle = page.get_by_role("checkbox", name="map_frequent_places")
    toggle.uncheck()
    assert state(page)["circles"] == 0
    toggle.check()
    assert state(page)["circles"] == 1
    assert errors == []
    context.close()


def test_empty_map_keeps_existing_controls_and_has_no_script_errors(browser):
    context, page, errors = open_map(browser, empty=True)
    assert page.locator("#map-trip-n").is_visible()
    assert page.locator("#map-top-n").is_visible()
    assert page.get_by_role("checkbox", name="map_frequent_places").count() == 0
    assert errors == []
    context.close()
