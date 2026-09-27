"""The app's one tooltip: any element with `data-tip` shows it on hover and on keyboard focus,
in the one box base.html carries. The browser's own `title` never shows on touch and not at
all inside the Home Assistant app, and that is a thing only a real browser can demonstrate —
rendered HTML says the attribute is there, not that anyone gets to read it.
"""
import pytest

pytest.importorskip("fastapi", reason="web/main.py needs fastapi (absent in the minimal CI env)")
pytest.importorskip("uvicorn", reason="the page has to be SERVED, not rendered in-process")
sync_api = pytest.importorskip("playwright.sync_api", reason="needs playwright + `playwright install chromium`")

from web_in_a_browser import chromium, seed_database, served


@pytest.fixture(scope="module")
def mate(tmp_path_factory):
    data = tmp_path_factory.mktemp("mate-tip")
    db = data / "leapmotor_mate.db"
    seed_database(db, "LVIN0000000000001")
    with served(data, db) as url:
        yield url


_TIP = """() => {
  const tip = document.getElementById('mate-tip');
  return {shown: !tip.classList.contains('hidden'), text: tip.textContent};
}"""


def test_a_data_tip_shows_on_hover_and_on_focus_and_goes_with_the_focus(mate):
    pw, browser = chromium(sync_api)
    try:
        page = browser.new_page(viewport={"width": 1180, "height": 900})
        assert page.goto(mate + "/settings").status == 200
        # any element anywhere on the page: the mechanism is the page's, not a view's
        page.evaluate("""() => {
          const b = document.createElement('button'); b.id = 'probe'; b.textContent = 'probe';
          b.setAttribute('data-tip', 'Poller running since: 27/09 12:09 (3h ago)');
          document.body.prepend(b);
        }""")
        assert page.evaluate(_TIP)["shown"] is False
        page.hover("#probe")
        assert page.evaluate(_TIP) == {"shown": True, "text": "Poller running since: 27/09 12:09 (3h ago)"}
        page.mouse.move(600, 800)
        assert page.evaluate(_TIP)["shown"] is False, "the pointer left it"
        page.focus("#probe")
        assert page.evaluate(_TIP)["shown"] is True, "keyboard focus shows it too"
        page.evaluate("document.getElementById('probe').blur()")
        assert page.evaluate(_TIP)["shown"] is False
    finally:
        browser.close()
        pw.stop()
