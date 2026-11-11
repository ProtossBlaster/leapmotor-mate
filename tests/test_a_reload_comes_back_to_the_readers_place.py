"""A page that reloads itself while idle comes back where the reader was, also once the blocks it fetches as it loads
(hx-trigger="load") have arrived: its first paint is shorter than the page, so the place it put back at first paint
was cut to that height. A reader who moves before a block arrives keeps their own place.

Only a browser can show this: the scroll position of <main> is in no HTML.
"""
import pytest

pytest.importorskip("fastapi", reason="web/main.py needs fastapi (absent in the minimal CI env)")
pytest.importorskip("uvicorn", reason="the page has to be SERVED, not rendered in-process")
sync_api = pytest.importorskip("playwright.sync_api",
                               reason="needs playwright + `playwright install chromium`")

from web_in_a_browser import chromium, seed_database, served

MAIN = "document.querySelector('main')"


@pytest.fixture(scope="module")
def mate(tmp_path_factory):
    data = tmp_path_factory.mktemp("mate-reload-place")
    db = data / ("leapmotor_mate" + ".db")
    seed_database(db, "LVIN0000000000001")
    with served(data, db) as url:
        yield url


@pytest.fixture
def page(mate):
    pw, browser = chromium(sync_api)
    try:
        page = browser.new_page(viewport={"width": 390, "height": 400})
        page.clock.install()
        page.goto(mate + "/battery")
        page.wait_for_load_state("networkidle")
        yield page
    finally:
        browser.close()
        pw.stop()


def _scrolled_to_the_bottom(page):
    page.evaluate(f"{MAIN}.scrollTop = {MAIN}.scrollHeight")
    left_at = page.evaluate(f"{MAIN}.scrollTop")
    assert left_at > 0, "the page fits its window, so there is no place to lose"
    return left_at


def test_the_reload_comes_back_where_the_reader_was(page):
    left_at = _scrolled_to_the_bottom(page)
    with page.expect_event("load"):
        page.clock.fast_forward("00:31")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(300)
    assert page.evaluate(f"{MAIN}.scrollTop") == left_at


def test_a_reader_who_moves_before_the_block_arrives_keeps_their_place(page):
    left_at = _scrolled_to_the_bottom(page)
    held = []
    page.route("**/api/battery-*", lambda route: held.append(route))
    with page.expect_event("load"):
        page.clock.fast_forward("00:31")
    page.wait_for_function("() => document.querySelector('.htmx-request')")
    page.mouse.move(200, 200)
    page.mouse.wheel(0, -50)
    page.wait_for_timeout(200)
    moved_to = page.evaluate(f"{MAIN}.scrollTop")
    for route in held:
        route.continue_()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(300)
    assert moved_to < left_at
    assert page.evaluate(f"{MAIN}.scrollTop") == moved_to
