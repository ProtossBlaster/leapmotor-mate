"""The chart under a trip's map is one chart in bands — driving, battery, altitude — with one
cursor line and one hover box across them. Its legend switches each line on and off, a band whose
lines are all off folds away, and the choice is remembered in the browser for every trip.

A line switched off leaves an empty square in the legend. Measured in a real browser, because the
chart is drawn by ApexCharts at run time.
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
START = datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc)


def _two_trips():
    """Two moving ten-minute drives with an elevation profile; the first one's points also keep the
    battery power, coldest-cell temperature and range of their poll. Elevation lookups off, so the
    served app never reaches for the network."""
    trip = ("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, duration_min,"
            " start_soc, end_soc) VALUES (?,1,?,?,12,10,80,77)")
    point = ("INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude, speed_kmh, soc,"
             " elevation_m) VALUES (?,?,?,9.0,?,?,?)")
    reading = ("UPDATE trip_positions SET power_kw = ?, battery_temp_c = ?, range_km = ?"
               " WHERE trip_id = 1 AND recorded_at = ?")
    rows = [("INSERT INTO settings (key, value) VALUES ('elevation_enabled', '0')", ())]
    for tid, start in ((1, START), (2, START + timedelta(hours=2))):
        rows.append((trip, (tid, start.isoformat(), (start + timedelta(minutes=10)).isoformat())))
        rows += [(point, (tid, (start + timedelta(minutes=k)).isoformat(), 45 + k * 0.01, 50 + k * 3,
                          80 - k * 0.3, 100 + k * 5)) for k in range(10)]
    rows += [(reading, (60 - k * 16, 18 + k // 4, 300 - k, (START + timedelta(minutes=k)).isoformat()))
             for k in range(10)]
    return rows


def _a_trip_whose_scales_could_step_by_halves():
    """Trip 3: a SoC from 69% to 60.45% and a battery from 15 to 24 °C, spans a step of 2.5 would cover."""
    start = START + timedelta(hours=4)
    rows = [("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, duration_min, start_soc,"
             " end_soc) VALUES (3,1,?,?,12,10,69,60.45)", (start.isoformat(), (start + timedelta(minutes=10)).isoformat()))]
    rows += [("INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude, speed_kmh, soc, elevation_m,"
              " power_kw, battery_temp_c, range_km) VALUES (3,?,?,9.0,?,?,?,?,?,?)",
              ((start + timedelta(minutes=k)).isoformat(), 45 + k * 0.01, 50 + k * 3, 69 - k * 0.95, 100 + k * 5,
               60 - k * 16, 15 + k, 300 - k)) for k in range(10)]
    return rows


AUTUMN = datetime(2026, 10, 25, 0, 50, tzinfo=timezone.utc)   # 02:50 summer time in Warsaw; at 01:00 UTC it is 02:00 again


def _a_trip_across_the_autumn_clock_change():
    """Trip 4, one reading a minute from 02:50 summer time to 02:10 winter time, with Mate on Warsaw."""
    rows = [("INSERT INTO settings (key, value) VALUES ('timezone', 'Europe/Warsaw')", ()),
            ("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, duration_min, start_soc,"
             " end_soc) VALUES (4,1,?,?,12,20,80,76)", (AUTUMN.isoformat(), (AUTUMN + timedelta(minutes=20)).isoformat()))]
    rows += [("INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude, speed_kmh, soc)"
              " VALUES (4,?,?,9.0,?,?)", ((AUTUMN + timedelta(minutes=k)).isoformat(), 45 + k * 0.01, 50 + k,
                                          80 - k * 0.2)) for k in range(21)]
    return rows


@pytest.fixture(scope="module")
def mate(tmp_path_factory):
    data = tmp_path_factory.mktemp("mate-trip-chart")
    db = data / "leapmotor_mate.db"
    seed_database(db, VIN, _two_trips() + _a_trip_whose_scales_could_step_by_halves()
                  + _a_trip_across_the_autumn_clock_change())
    with served(data, db) as url:
        yield url


_STATE = """() => {
  const chart = document.getElementById('trip-profile')._c;
  const legend = {};
  document.querySelectorAll('#trip-profile-legend [data-series]').forEach(b => {
    if (b.offsetParent !== null) legend[b.dataset.series] = [b.getAttribute('aria-pressed'), b.querySelector('[data-swatch]').textContent];
  });
  const names = chart ? chart.w.globals.seriesNames : [];
  return {legend, lines: names.filter(n => !n.startsWith('_') && !n.endsWith('__dash')),
          bands: chart ? chart.w.config.yaxis[0].max : 0,
          minutes: Array.from(document.querySelectorAll('#trip-profile .apexcharts-xaxis-label tspan')).map(t => t.textContent),
          units: Array.from(document.querySelectorAll('#trip-profile .apexcharts-yaxis-label tspan')).map(t => t.textContent)
                   .filter(t => /[^0-9.-]/.test(t)),
          charts: document.querySelectorAll('#trip-profile .apexcharts-canvas').length,
          tooltips: document.querySelectorAll('#trip-profile .apexcharts-tooltip').length};
}"""


def _open(page, url):
    assert page.goto(url).status == 200
    page.wait_for_function("() => document.getElementById('trip-profile') && document.getElementById('trip-profile')._c")
    return page.evaluate(_STATE)


def _click(page, series):
    page.click(f'#trip-profile-legend [data-series="{series}"]')
    return page.evaluate(_STATE)


def test_the_legend_switches_a_line_folds_an_empty_band_and_remembers_it(mate):
    pw, browser = chromium(sync_api)
    try:
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        first = _open(page, mate + "/trips/1")
        assert list(first["legend"]) == ["speed", "power", "soc", "range", "elev", "batt"]
        assert all(v == ["true", "■"] for v in first["legend"].values())
        assert first["lines"] == ["Speed", "Power", "SOC", "Range", "Altitude", "Battery temp"], \
            "the hover box does not follow the legend's order"
        assert first["bands"] == 3
        assert (first["charts"], first["tooltips"]) == (1, 1), "the bands are not one chart with one hover box"
        assert sorted(first["units"]) == sorted(["%", "km", "km/h", "kW", "m", "°C"]), "a scale does not say its unit"
        assert first["minutes"] and all(m.endswith(" min") for m in first["minutes"]), first["minutes"]
        page.hover("#trip-profile", position={"x": 300, "y": 60})
        # ApexCharts fills the hover box on ITS tick, not inside hover(): reading straight after saw
        # an empty title in 8 of 25 runs. Wait for the title to have something in it. The third test
        # below already tolerates the empty read (heads.discard("")); here it is the assertion.
        page.wait_for_function(
            "() => { var t = document.querySelector('#trip-profile .apexcharts-tooltip-title');"
            " return t && t.innerText.trim().length > 0; }")
        head = page.inner_text("#trip-profile .apexcharts-tooltip-title")
        assert re.fullmatch(r"\d\d:\d\d:\d\d \(\d+ min\)", head), head
        groups = page.eval_on_selector_all("#trip-profile .mate-tip-band",
                                           "gs => gs.map(g => Array.from(g.children).map(r => r.innerText.split(':')[0]))")
        assert groups == [["Speed", "Power"], ["SOC", "Range"], ["Altitude", "Battery temp"]], groups

        off = _click(page, "speed")
        assert off["legend"]["speed"] == ["false", "□"] and "Speed" not in off["lines"]
        assert off["bands"] == 3

        folded = _click(page, "power")
        assert folded["lines"] == ["SOC", "Range", "Altitude", "Battery temp"] and folded["bands"] == 2, \
            "a band with every line switched off still takes its height"
        assert "km/h" not in folded["units"] and "kW" not in folded["units"]

        other = _open(page, mate + "/trips/2")
        assert other["legend"]["speed"] == ["false", "□"] and other["lines"] == ["SOC", "Altitude"], \
            "the choice was not remembered for the next trip"
        assert "power" not in other["legend"], "a trip without power readings offers a Power line"

        back = _click(page, "speed")
        assert back["lines"] == ["Speed", "SOC", "Altitude"] and back["bands"] == 3

        again = _open(page, mate + "/trips/1")
        assert again["legend"]["power"] == ["false", "□"], "Power, switched off, did not stay off"
        assert again["lines"] == ["Speed", "SOC", "Range", "Altitude", "Battery temp"]
        assert errors == []
    finally:
        browser.close()
        pw.stop()


def test_every_number_on_a_scale_is_the_value_of_its_line(mate):
    """A band's three numbers are its gridlines' values, so they climb in equal steps. A step of 2.5
    printed without its decimals would read 63, 65, 68 for lines at 62.5, 65 and 67.5."""
    pw, browser = chromium(sync_api)
    try:
        page = browser.new_page()
        _open(page, mate + "/trips/3")
        axes = page.eval_on_selector_all(
            "#trip-profile .apexcharts-yaxis",
            "axes => axes.map(a => Array.from(a.querySelectorAll('.apexcharts-yaxis-label tspan')).map(t => t.textContent))")
        bands = []
        for texts in axes:
            numbers = [float(t) for t in texts if re.fullmatch(r"-?\d+(\.\d+)?", t)]
            bands += [numbers[i:i + 3] for i in range(0, len(numbers), 3)]
        assert len(bands) == 6, bands
        for band in bands:
            assert band[1] - band[0] == band[2] - band[1], f"the numbers {band} do not match evenly spaced lines"
    finally:
        browser.close()
        pw.stop()


def test_the_clock_in_the_hover_box_follows_the_clock_change(mate):
    """Every reading's time of day is its own in Mate's zone: after the clocks go back at 03:00 the
    reading at 01:04 UTC is 02:04, not 03:04 at the offset the drive started with."""
    from zoneinfo import ZoneInfo

    pw, browser = chromium(sync_api)
    try:
        page = browser.new_page()
        _open(page, mate + "/trips/4")
        width = page.eval_on_selector("#trip-profile", "e => e.getBoundingClientRect().width")
        heads = set()
        for x in range(40, int(width) - 40, 12):
            page.hover("#trip-profile", position={"x": x, "y": 60})
            heads.add(page.inner_text("#trip-profile .apexcharts-tooltip-title"))
        heads.discard("")   # the pointer outside the plot shows no hover box
        warsaw = ZoneInfo("Europe/Warsaw")
        offsets = set()
        for head in heads:
            clock, minute = re.fullmatch(r"(\d\d:\d\d:\d\d) \((\d+) min\)", head).groups()
            at = (AUTUMN + timedelta(minutes=int(minute))).astimezone(warsaw)
            assert clock == at.strftime("%H:%M:%S"), f"{head} should read {at:%H:%M:%S}"
            offsets.add(at.utcoffset())
        assert len(offsets) == 2, f"the hover never reached both sides of the change: {sorted(heads)}"
    finally:
        browser.close()
        pw.stop()
