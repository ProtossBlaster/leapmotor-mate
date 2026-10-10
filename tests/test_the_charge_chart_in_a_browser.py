"""The chart under a charge is one chart in bands — charging, battery, temperatures — with one
hover box across them, like the trip's. The battery band holds the SoC alone: the car's range
climbs with it through a charge, so its line would only repeat the SoC's. Its legend switches each line on and off, a band whose
lines are all off folds away, and the choice is remembered in the browser for every charge. A
line the session has no readings for is not offered, and a session without temperatures has no
third band.

Measured in a real browser, because the chart is drawn by ApexCharts at run time.
Skips where it cannot run (no playwright, no Chromium), like the other browser tests.
"""
import re
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi", reason="web/main.py needs fastapi (absent in the minimal CI env)")
pytest.importorskip("uvicorn", reason="the page has to be SERVED, not rendered in-process")
sync_api = pytest.importorskip("playwright.sync_api", reason="needs playwright + `playwright install chromium`")

from web_in_a_browser import chromium, seed_database, served

VIN = "LVIN0000000000001"
PLUGGED_IN = datetime(2026, 9, 29, 19, 0, tzinfo=timezone.utc)


def _two_charges():
    """Charge 1, an hour at home with every reading of the poll; charge 2, later the same evening, with
    the power and the SoC alone. The first poll of charge 1 has no weather reading yet."""
    charge = ("INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc, energy_added_kwh,"
              " location_type) VALUES (?,1,?,?,50,62,4.0,'HOME')")
    sample = ("INSERT INTO positions (vehicle_id, recorded_at, charging, charge_voltage_v, charge_current_a, soc,"
              " battery_min_temp, outside_temp, range_km, remaining_charge_min) VALUES (1,?,1,400,-10,?,?,?,?,?)")
    rows = [("INSERT INTO settings (key, value) VALUES ('timezone', 'UTC')", ())]
    for cid, start in ((1, PLUGGED_IN), (2, PLUGGED_IN + timedelta(hours=3))):
        rows.append((charge, (cid, start.isoformat(), (start + timedelta(hours=1)).isoformat())))
        for k in range(13):   # every five minutes
            readings = (20 + k // 2, None if k == 0 else 9.5 + k / 4, 200 + 5 * k, 65 - 5 * k) if cid == 1 \
                else (None, None, None, None)
            rows.append((sample, ((start + timedelta(minutes=5 * k)).isoformat(), 50 + k) + readings))
    return rows


@pytest.fixture(scope="module")
def mate(tmp_path_factory):
    data = tmp_path_factory.mktemp("mate-charge-chart")
    db = data / "leapmotor_mate.db"
    seed_database(db, VIN, _two_charges())
    with served(data, db) as url:
        yield url


_STATE = """(cid) => {
  const chart = document.getElementById('pc-' + cid)._c;
  const legend = {};
  document.querySelectorAll('#pcl-' + cid + ' [data-series]').forEach(b => {
    if (b.offsetParent !== null) legend[b.dataset.series] = [b.getAttribute('aria-pressed'), b.querySelector('[data-swatch]').textContent];
  });
  const names = chart ? chart.w.globals.seriesNames : [];
  return {legend, lines: names.filter(n => !n.startsWith('_')),
          bands: chart ? chart.w.config.yaxis[0].max : 0,
          clocks: Array.from(document.querySelectorAll('#pc-' + cid + ' .apexcharts-xaxis-label tspan')).map(t => t.textContent),
          units: Array.from(document.querySelectorAll('#pc-' + cid + ' .apexcharts-yaxis-label tspan')).map(t => t.textContent)
                   .filter(t => /[^0-9.-]/.test(t)),
          charts: document.querySelectorAll('#pc-' + cid + ' .apexcharts-canvas').length};
}"""


def _open(page, url, cid):
    """The Charges page on the charge's own day, the row the link meant opened, with its chart."""
    assert page.goto(f"{url}/charges?highlight={cid}").status == 200
    page.wait_for_selector(f"#charge-card-{cid}[open]")
    page.wait_for_function(f"() => document.getElementById('pc-{cid}') && document.getElementById('pc-{cid}')._c")
    return page.evaluate(_STATE, cid)


def _click(page, cid, series):
    page.click(f'#pcl-{cid} [data-series="{series}"]')
    return page.evaluate(_STATE, cid)


def test_the_legend_switches_a_line_folds_an_empty_band_and_remembers_it(mate):
    pw, browser = chromium(sync_api)
    try:
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        first = _open(page, mate, 1)
        assert list(first["legend"]) == ["power", "remaining", "soc", "batt", "outside"], \
            "a home charge with no wallbox mapped offers a wallbox line, or a reading is missing"
        assert all(v == ["true", "■"] for v in first["legend"].values())
        assert "range" not in first["legend"] and "Range" not in first["lines"], "the range repeats the SoC"
        assert first["lines"] == ["Power", "Time remaining", "SOC", "Battery temp", "Outside"], \
            "the hover box does not follow the legend's order"
        assert first["bands"] == 3 and first["charts"] == 1
        assert sorted(first["units"]) == sorted(["kW", "min", "%", "°C"]), \
            "a scale does not say its unit, or the two temperatures do not share one"
        assert first["clocks"] and all(re.fullmatch(r"\d\d:\d\d", c) for c in first["clocks"] if c), first["clocks"]
        page.hover("#pc-1", position={"x": 300, "y": 60})
        page.wait_for_function(
            "() => { var t = document.querySelector('#pc-1 .apexcharts-tooltip-title');"
            " return t && t.innerText.trim().length > 0; }")
        head = page.inner_text("#pc-1 .apexcharts-tooltip-title")
        assert re.fullmatch(r"\d\d:\d\d:\d\d \(\d+ min\)", head), head
        groups = page.eval_on_selector_all("#pc-1 .mate-tip-band",
                                           "gs => gs.map(g => Array.from(g.children).map(r => r.innerText.split(':')[0]))")
        assert groups == [["Power", "Time remaining"], ["SOC"], ["Battery temp", "Outside"]], groups

        off = _click(page, 1, "power")
        assert off["legend"]["power"] == ["false", "□"] and "Power" not in off["lines"] and off["bands"] == 3

        folded = _click(page, 1, "remaining")
        assert folded["lines"] == ["SOC", "Battery temp", "Outside"] and folded["bands"] == 2, \
            "a band with every line switched off still takes its height"
        assert "kW" not in folded["units"] and "min" not in folded["units"]

        other = _open(page, mate, 2)
        assert list(other["legend"]) == ["power", "soc"], "a session without temperatures offers their lines"
        assert other["legend"]["power"] == ["false", "□"] and other["lines"] == ["SOC"], \
            "the choice was not remembered for the next charge"
        assert other["bands"] == 1, "a session with only the power and the SoC has a temperatures band"

        back = _click(page, 2, "power")
        assert back["lines"] == ["Power", "SOC"] and back["bands"] == 2
        assert errors == []
    finally:
        browser.close()
        pw.stop()


def test_a_poll_without_a_reading_is_a_hole_not_a_zero(mate):
    """The first poll of the charge has no weather reading: the outside line starts at the second."""
    pw, browser = chromium(sync_api)
    try:
        page = browser.new_page()
        _open(page, mate, 1)
        drawn = page.evaluate("() => document.getElementById('pc-1')._c.w.globals.series")
        names = page.evaluate("() => document.getElementById('pc-1')._c.w.globals.seriesNames")
        outside = drawn[names.index("Outside")]
        assert outside[0] is None and outside[1] is not None, outside[:3]
    finally:
        browser.close()
        pw.stop()


def test_a_scale_two_lines_share_stays_while_either_shows(mate):
    """The temperatures' numbers and unit belong to both lines of the band: with the battery line
    switched off, the outside line keeps them, on the next opening too."""
    pw, browser = chromium(sync_api)
    try:
        page = browser.new_page()
        _open(page, mate, 1)
        off = _click(page, 1, "batt")
        assert "Battery temp" not in off["lines"] and "Outside" in off["lines"] and off["bands"] == 3
        assert "°C" in off["units"], "the outside line lost the scale it shares with the battery line"
        numbers = page.eval_on_selector_all(
            "#pc-1 .apexcharts-yaxis-label tspan", "ts => ts.map(t => t.textContent).filter(t => /^-?\\d+$/.test(t))")
        assert len(numbers) == 3 * 4, numbers   # three numbers for each of the four scales still shown
        again = _open(page, mate, 1)
        assert again["legend"]["batt"] == ["false", "□"] and "°C" in again["units"]
    finally:
        browser.close()
        pw.stop()


def test_two_charts_on_one_page_each_keep_their_own_choice(mate):
    """Two cards open on one day: a line switched off on one chart stays off after a line is switched
    off on the other, since each saves its own change into the choice the browser holds, not a copy
    of it taken when the chart was drawn."""
    pw, browser = chromium(sync_api)
    try:
        page = browser.new_page()
        _open(page, mate, 1)
        page.click('#charge-card-2 > summary')
        page.locator('#pchart-2').scroll_into_view_if_needed()    # the chart loads once it is seen
        page.wait_for_function("() => document.getElementById('pc-2') && document.getElementById('pc-2')._c")
        first = _click(page, 1, "power")
        second = _click(page, 2, "soc")
        assert "Power" not in first["lines"] and "SOC" in first["lines"]
        assert second["lines"] == ["Power"] and second["legend"]["power"] == ["true", "■"], \
            "the second chart took the first chart's choice as its own, against what its legend says"
        still = page.evaluate(_STATE, 1)
        assert still == first, "a click on the second chart redrew the first, or left its legend saying something else"
        again = _open(page, mate, 1)
        assert again["legend"]["power"] == ["false", "□"], "the second chart's save wiped the first chart's choice"
        assert again["legend"]["soc"] == ["false", "□"]
    finally:
        browser.close()
        pw.stop()


def test_a_browser_that_blocks_storage_still_switches_lines(mate):
    """With localStorage out of reach, the legend still works for the page in hand: a second switch
    does not undo the first, since the chart's own state is not what the storage holds."""
    pw, browser = chromium(sync_api)
    try:
        page = browser.new_page()
        page.add_init_script("Object.defineProperty(window, 'localStorage', { get: function () { throw new Error('blocked'); } });")
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        _open(page, mate, 1)
        _click(page, 1, "remaining")
        both = _click(page, 1, "soc")
        assert "Time remaining" not in both["lines"] and "SOC" not in both["lines"], both["lines"]
        assert both["legend"]["remaining"] == ["false", "□"] and both["legend"]["soc"] == ["false", "□"]
        assert errors == []
    finally:
        browser.close()
        pw.stop()
