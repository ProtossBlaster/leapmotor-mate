"""The polling strip on the Settings page is 288 five-minute cells in a card about 300 px wide.
Laid out with a one-pixel gap between cells, the 287 gaps alone took the whole width and every
cell measured 0 px — the strip was there in the HTML and invisible on the screen. So this test
measures, in a real browser at a desktop and a phone width, what a cell is actually given.

Skips where it cannot run (no playwright, no Chromium), like the Commands page test.
"""
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi", reason="web/main.py needs fastapi (absent in the minimal CI env)")
pytest.importorskip("uvicorn", reason="the page has to be SERVED, not rendered in-process")
sync_api = pytest.importorskip("playwright.sync_api", reason="needs playwright + `playwright install chromium`")

from web_in_a_browser import chromium, seed_database, served

VIN = "LVIN0000000000001"


def _day_of_outcomes():
    """So the strip has every colour to show."""
    now = datetime.now(timezone.utc)
    return [("INSERT INTO poll_log (at, vehicle_id, kind, outcome, frame_age_s) VALUES (?, 1, 'poll', ?, ?)",
             ((now - timedelta(hours=hours_ago, minutes=1)).isoformat(), outcome, age))
            for hours_ago, outcome, age in ((23, "refused", None), (18, "failed", None), (12, "empty", None),
                                            (6, "answer", 900), (1, "answer", 12), (0, "answer", 12))]


@pytest.fixture(scope="module")
def mate(tmp_path_factory):
    data = tmp_path_factory.mktemp("mate-strip")
    db = data / "leapmotor_mate.db"
    seed_database(db, VIN, _day_of_outcomes())
    with served(data, db) as url:
        yield url


_MEASURE = """() => {
  const strip = document.querySelector('[data-diag-polling] .flex.h-4');
  const cells = Array.from(strip.children);
  const colours = new Set(cells.map(c => getComputedStyle(c).backgroundColor));
  return {strip: strip.getBoundingClientRect().width, cell: cells[0].getBoundingClientRect().width,
          cells: cells.length, colours: colours.size};
}"""


@pytest.mark.parametrize("width", [1180, 390])
def test_every_cell_of_the_strip_is_given_real_width(mate, width):
    pw, browser = chromium(sync_api)
    try:
        page = browser.new_page(viewport={"width": width, "height": 900})
        assert page.goto(mate + "/settings").status == 200
        # Since 4.5.1 the card fetches its own body when it is opened — Settings was building the
        # strip for every load of the page, open or not. So: open the cards, then wait for the swap.
        page.evaluate("document.querySelectorAll('details').forEach(d => d.setAttribute('open', ''))")
        page.wait_for_selector("[data-diag-polling] .flex.h-4", timeout=15000)
        m = page.evaluate(_MEASURE)
    finally:
        browser.close()
        pw.stop()
    assert m["cells"] == 288
    assert m["cell"] >= m["strip"] / 288 * 0.9, m
    assert m["cell"] >= 0.8, f"a cell must be visible on its own: {m}"
    assert m["colours"] >= 4, f"the day's outcomes must show as colours: {m}"
