"""The Battery health page reloads itself when idle, so a new charge or a park going on shows up unasked, and
the views picked on its two charts come back with it: they are kept in the address, like the month on Trips.
Skips where it cannot run (no playwright, no Chromium), like the other browser tests.

On the demo database, which has both charges to estimate the capacity from and parks to draw the drain of.
"""
import os
import subprocess
import sys

import pytest

pytest.importorskip("fastapi", reason="web/main.py needs fastapi (absent in the minimal CI env)")
pytest.importorskip("uvicorn", reason="the page has to be SERVED, not rendered in-process")
sync_api = pytest.importorskip("playwright.sync_api", reason="needs playwright + `playwright install chromium`")

from web_in_a_browser import ROOT, chromium, served

_BOTH_SECTIONS = ("window.settled = 0; document.addEventListener('htmx:afterSettle', e => {"
                  " if (e.target.matches('[hx-get^=\"api/battery-\"]')) window.settled++; })")


@pytest.fixture(scope="module")
def mate(tmp_path_factory):
    data = tmp_path_factory.mktemp("battery-views")
    db = data / "leapmotor_mate.db"
    # Its own process, as run.sh starts it: importing it here would put poller/ ahead of web/ on sys.path.
    subprocess.run([sys.executable, str(ROOT / "poller" / "seed_demo.py")], check=True, capture_output=True,
                   env={**os.environ, "DB_PATH": str(db)})
    with served(data, db) as url:
        yield url


@pytest.fixture
def browser():
    pw, b = chromium(sync_api)
    try:
        yield b
    finally:
        b.close()
        pw.stop()


def _drawn(page):
    page.wait_for_function("window.settled >= 2")
    page.wait_for_selector("#soh-chart .apexcharts-xaxis-label")
    page.wait_for_selector("#vampire-chart .apexcharts-yaxis-title")


def _open(browser, url):
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.add_init_script(_BOTH_SECTIONS)
    assert page.goto(url).status == 200
    _drawn(page)
    return page


def _views(page):
    """What the two charts show: the capacity's x axis, and the drain's unit and grouping, by the buttons lit."""
    return {
        "capacity_axis": page.locator("#soh-chart .apexcharts-xaxis-label").last.text_content(),
        "distance_lit": "bg-slate-700" in page.locator("#soh-x-dist").get_attribute("class"),
        "drain_unit": page.locator("#vampire-chart .apexcharts-yaxis-title").text_content(),
        "lit": page.eval_on_selector_all("#vampire-controls .vp-on", "bs => bs.map(b => b.id)"),
    }


def test_the_views_picked_come_back_after_a_reload(browser, mate):
    page = _open(browser, f"{mate}/battery")
    page.click("#soh-x-dist")
    page.click("#vp-abs")
    page.click("#vp-day")
    picked = _views(page)
    assert picked["capacity_axis"].endswith(" km") and picked["distance_lit"]
    assert picked["drain_unit"] == "%" and picked["lit"] == ["vp-abs", "vp-day"]
    page.reload()
    _drawn(page)
    assert _views(page) == picked

