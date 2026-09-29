"""Tapping "LeapMotor Mate" in the top bar goes to the home page.

@matttiaromano, #342: on a phone the top bar is all there is — the menu is behind the hamburger —
so returning to Overview from anywhere takes two taps on the one control every other app makes a
one-tap shortcut. The logo looked like that shortcut and did nothing.

The link is `href="."`, relative like every other path in `base.html`: under the Home Assistant
ingress the page is served below a prefix that `<base href>` supplies, and an absolute "/" would
leave Mate for the Home Assistant root.

🔑 Only the mark and the name are inside the link. The version, DEMO and beta chips sit beside
them and stay out: `version_badge` renders an anchor of its own when an update is waiting, and an
anchor inside an anchor is closed by the parser at the inner one — the rest of the bar would fall
outside both links.
"""
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASE = ROOT / "web" / "templates" / "base.html"


def _logo_anchors(html: str):
    """Every anchor that wraps the Mate mark, with its href."""
    return [(m.group(1), m.group(0)) for m in
            re.finditer(r'<a\s[^>]*href="([^"]*)"[^>]*>(?:(?!</a>).)*?mate-icon\.svg'
                        r'(?:(?!<a\s).)*?</a>', html, re.S)]


def test_both_bars_put_the_logo_in_a_link_home():
    html = BASE.read_text()
    marks = html.count('<img src="static/mate-icon.svg"')
    assert marks == 2, f"base.html carries {marks} Mate marks, not the mobile bar + the sidebar"
    anchors = _logo_anchors(html)
    assert len(anchors) == 2, f"only {len(anchors)} of the 2 Mate marks is a link home"
    for href, _ in anchors:
        assert href == ".", f'the logo links to {href!r}, not to "." — ingress would break it'


def test_the_version_chip_stays_outside_the_link():
    """An <a> inside an <a> is not nestable: the parser closes the outer one at the inner tag."""
    for _, anchor in _logo_anchors(BASE.read_text()):
        assert "version_badge" not in anchor and "app_badge" not in anchor, \
            "the version chip is inside the logo link, and it renders an anchor of its own"


def test_the_served_page_offers_the_link(tmp_path, monkeypatch):
    """The template is not the page: the bar is rendered by every route through base.html."""
    pytest.importorskip("httpx", reason="Starlette's TestClient is built on httpx")
    pytest.importorskip("fastapi")
    import db as D
    import db_reader
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    pdb._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LVIN0000000000001','C10')")
    for key, value in (("timezone", "UTC"), ("setup_complete", "1"), ("language", "en")):
        pdb._conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
    pdb._conn.commit()
    pdb._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    import main
    from starlette.testclient import TestClient
    html = TestClient(main.app).get("/").text
    assert html.count('<img src="static/mate-icon.svg"') == 2
    assert len(_logo_anchors(html)) == 2, "the served page has no way home from the logo"


# ── and in a phone-shaped browser, which is where it was reported ─────────────

def test_tapping_the_logo_on_a_phone_lands_on_the_overview(tmp_path_factory):
    """The HTML above says the anchor is there; only a browser says the tap arrives. At 390 px the
    sidebar is off screen and the mobile bar is the one on show, which is the case reported."""
    pytest.importorskip("fastapi", reason="web/main.py needs fastapi (absent in the minimal CI env)")
    pytest.importorskip("uvicorn", reason="the page has to be SERVED, not rendered in-process")
    sync_api = pytest.importorskip("playwright.sync_api",
                                   reason="needs playwright + `playwright install chromium`")
    from web_in_a_browser import chromium, seed_database, served

    data = tmp_path_factory.mktemp("mate-logo")
    db = data / ("leapmotor_mate" + ".db")
    seed_database(db, "LVIN0000000000001")
    with served(data, db) as url:
        pw, browser = chromium(sync_api)
        try:
            page = browser.new_page(viewport={"width": 390, "height": 780})
            assert page.goto(url + "/charges").status == 200
            logo = page.locator("header a[href='.']").first
            assert logo.is_visible(), "the phone bar's logo is not on screen at 390 px"
            logo.click()
            page.wait_for_load_state("domcontentloaded")
            landed = page.url
        finally:
            browser.close()
            pw.stop()
    assert landed.rstrip("/") == url.rstrip("/"), f"the logo went to {landed}, not to the overview"
