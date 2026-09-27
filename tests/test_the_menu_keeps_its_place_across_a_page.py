"""Picking a menu item from the bottom must not throw the menu back to the top.

27/09/2026, Silvio: "il menu si resetta sempre, se seleziono una voce bassa si riporta all'inizio",
and before that "il menu laterale è praticamente inusabile". Measured in a browser on his own data
at 1280x800: the nav is 822 px of items in 524 px of window, so a third of the menu is below the
fold. Every page is a full load, the sidebar is rebuilt scrolled to the top, and the item just used
is off screen again — so reaching Settings or Scheduling means scrolling down every single time.

Only a browser can show this: the HTML is identical either way, the scroll position is not in it.
"""
import pytest

pytest.importorskip("fastapi", reason="web/main.py needs fastapi (absent in the minimal CI env)")
pytest.importorskip("uvicorn", reason="the page has to be SERVED, not rendered in-process")
sync_api = pytest.importorskip("playwright.sync_api",
                               reason="needs playwright + `playwright install chromium`")

from web_in_a_browser import chromium, seed_database, served


@pytest.fixture(scope="module")
def mate(tmp_path_factory):
    data = tmp_path_factory.mktemp("mate-menu")
    db = data / ("leapmotor_mate" + ".db")
    seed_database(db, "LVIN0000000000001")
    with served(data, db) as url:
        yield url


_NAV = "document.querySelector('#sidebar nav') || document.getElementById('sidebar')"


def test_the_menu_is_taller_than_its_window_so_the_place_matters(mate):
    """The premise. If the menu ever fits, this test measures nothing and should say so."""
    pw, browser = chromium(sync_api)
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 700})
        assert page.goto(mate + "/").status == 200
        m = page.evaluate("() => { const n = %s; return {s: n.scrollHeight, c: n.clientHeight}; }" % _NAV)
    finally:
        browser.close()
        pw.stop()
    assert m["s"] > m["c"] + 2, f"the menu fits its window here, so nothing can be lost: {m}"


def test_the_menu_comes_back_where_it_was_left(mate):
    pw, browser = chromium(sync_api)
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 700})
        assert page.goto(mate + "/").status == 200
        page.evaluate("() => { const n = %s; n.scrollTop = n.scrollHeight; "
                      "n.dispatchEvent(new Event('scroll')); }" % _NAV)
        left_at = page.evaluate("() => (%s).scrollTop" % _NAV)
        assert left_at > 0, "could not scroll the menu at all"
        assert page.goto(mate + "/charges").status == 200
        page.wait_for_timeout(300)
        back_at = page.evaluate("() => (%s).scrollTop" % _NAV)
    finally:
        browser.close()
        pw.stop()
    assert abs(back_at - left_at) <= 4, (
        f"the menu went back to {back_at} after being left at {left_at}: the item just used is off "
        f"screen again on every page"
    )
