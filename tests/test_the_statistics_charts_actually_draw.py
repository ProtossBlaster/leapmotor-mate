"""The Statistics charts, opened in a real browser — do they actually DRAW?

Beta #38 (@michapr): the "Consumption vs outside temperature" card came out blank. Not empty of
data — the caption under it printed the fitted trend, which the server computes only from eight
points or more — but blank on screen, with nothing in the console. The card had been added above
the line where the page loads `chart.umd.min.js`, so its inline script ran while `Chart` did not
exist yet and its own `typeof Chart === 'undefined'` guard returned in silence.

Every existing check on this page reads the rendered HTML, and the HTML was correct: the canvas, the
data and the script were all in it. What was wrong was the ORDER the browser executes them in, which
no assertion on a string can see. Its sibling, test_charts_load_their_library_first.py, pins that
order cheaply and runs everywhere; this one is the expensive proof that the pixels arrive.

Same costs, and the same limit, as test_the_overview_runs_in_a_browser_without_a_javascript_error.py:
fastapi + uvicorn + pytest-playwright + a Chromium, none of them in CI's minimal env, so this SKIPS
there and guards the laptop it runs on.
"""
import os
import pathlib
import socket
import sqlite3
import subprocess
import sys
import time
import types
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi", reason="web/main.py needs fastapi (absent in the minimal CI env)")
pytest.importorskip("uvicorn", reason="the page has to be SERVED, not rendered in-process")
sync_api = pytest.importorskip(
    "playwright.sync_api",
    reason="needs pytest-playwright + `playwright install chromium`",
)

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _seed(db_path: pathlib.Path) -> None:
    """Twelve finished trips carrying a temperature — the smallest database the card renders from.

    Twelve and not five: get_efficiency_vs_temp fits its trend line only from eight points up, and
    the trend is what proved, in @michapr's screenshot, that the server side was already fine. A
    seed below that threshold would draw the dots and still leave this test unable to tell the two
    halves of the card apart.

    Each trip needs all three of: `distance_km` >= 3 (shorter ones are held out on purpose —
    preconditioning spread over three kilometres reads as a cold penalty that isn't one), a
    non-NULL `efficiency_kwh_100km` (NULL is how finalize_trip marks a generator trip, which
    belongs to the petrol series), and at least one outside temperature. The temperatures are
    spread from -3 °C to 30 °C so the fit has something to fit, and the consumption steps 0.5 off a
    line either way, so the curve has an interval to shade.
    """
    import schema  # poller/ is on sys.path via tests/conftest.py

    now = datetime.now(timezone.utc)
    conn = sqlite3.connect(db_path)
    try:
        schema.ensure_schema(conn)
        conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1, ?, ?)",
                     ("TESTVIN0000000001", "C10"))
        for i in range(12):
            start = now - timedelta(days=12 - i, hours=2)
            end = start + timedelta(minutes=40)
            temp = -3.0 + i * 3.0
            conn.execute(
                "INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km, "
                "efficiency_kwh_100km, outside_temp_start_c, outside_temp_end_c) "
                "VALUES (1, ?, ?, ?, ?, ?, ?)",
                (start.isoformat(), end.isoformat(), 25.0 + i,
                 22.0 - i * 0.4 + (0.5 if i % 2 else -0.5), temp, temp + 1.0))
        conn.execute("INSERT INTO settings (key, value) VALUES ('setup_complete', '1')")
        conn.commit()
    finally:
        conn.close()


def _seed_petrol_only(db_path: pathlib.Path) -> None:
    """A range-extender whose every trip ran its generator: twelve petrol points, no electric one."""
    import schema

    now = datetime.now(timezone.utc)
    conn = sqlite3.connect(db_path)
    try:
        schema.ensure_schema(conn)
        conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1, ?, ?)",
                     ("TESTVIN0000000001", "C10"))
        for i in range(12):
            start = now - timedelta(days=12 - i, hours=2)
            temp = -3.0 + i * 3.0
            conn.execute(
                "INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km, efficiency_kwh_100km,"
                " outside_temp_start_c, outside_temp_end_c, fuel_start_pct, fuel_end_pct)"
                " VALUES (1, ?, ?, 60, NULL, ?, ?, 60, ?)",
                (start.isoformat(), (start + timedelta(minutes=50)).isoformat(), temp, temp + 1.0,
                 55.0 - (i % 3) * 0.5))
        conn.executemany("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                         [("setup_complete", "1"), ("is_reev", "1")])
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(scope="module")
def mate_url(tmp_path_factory):
    yield from _serve(tmp_path_factory, _seed)


@pytest.fixture(scope="module")
def petrol_only_url(tmp_path_factory):
    yield from _serve(tmp_path_factory, _seed_petrol_only)


def _serve(tmp_path_factory, seed):
    """A real Mate, serving a database of our own, on a port of its own."""
    data = tmp_path_factory.mktemp("mate-stats-browser")
    db = data / "leapmotor_mate.db"
    seed(db)

    port = _free_port()
    env = {**os.environ,
           "DB_PATH": str(db),
           "WEB_PORT": str(port),
           "PYTHONPATH": str(ROOT / "web"),
           "MATE_RESEARCH": "0"}
    for leak in ("MATE_AUTH_PASSWORD", "MATE_DEMO", "SUPERVISOR_TOKEN", "HASSIO_TOKEN"):
        env.pop(leak, None)

    log = data / "web.log"
    proc = subprocess.Popen([sys.executable, str(ROOT / "tests" / "no_internet.py"),
                             str(ROOT / "web" / "main.py")], env=env,
                            stdout=log.open("w"), stderr=subprocess.STDOUT, text=True)
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            if proc.poll() is not None:
                pytest.fail(f"the web process died before it served anything:\n{log.read_text()}")
            try:
                urllib.request.urlopen(url, timeout=1).read()
                break
            except urllib.error.HTTPError:
                break
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                time.sleep(0.2)
        else:
            pytest.fail(f"the web process never answered:\n{log.read_text()}")
        yield types.SimpleNamespace(url=url, log=log)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_the_temperature_chart_puts_its_points_on_the_canvas(mate_url):
    server = mate_url
    errors = []
    shot_dir = os.environ.get("MATE_SHOT_DIR", "")
    with sync_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1100, "height": 900})
        page.on("pageerror", lambda e: errors.append(str(e)))

        response = page.goto(f"{server.url}/statistics", wait_until="networkidle")
        page.wait_for_timeout(1200)
        status, html = (response.status if response else None), page.content()

        # Chart.js keeps a registry of the charts it built, keyed by canvas. Asking IT — rather
        # than looking at pixels — is what separates "the library never ran" from "it ran and the
        # series was empty", and those are two different defects.
        drawn = page.evaluate(
            """() => {
                 if (typeof Chart === 'undefined') return {lib: false};
                 const c = Chart.getChart('chart-efftemp');
                 if (!c) return {lib: true, chart: false};
                 return {lib: true, chart: true,
                         points: c.data.datasets[0].data.length,
                         datasets: c.data.datasets.length};
               }""")
        if shot_dir:
            page.screenshot(path=str(pathlib.Path(shot_dir) / "statistics-efftemp.png"))
        browser.close()

    assert status == 200, (f"Statistics did not render: HTTP {status}\n"
                           f"{server.log.read_text()[-3000:]}")
    assert 'id="chart-efftemp"' in html, f"this is not the Statistics page:\n{html[:500]}"
    assert errors == [], "Statistics threw in the browser:\n  " + "\n  ".join(errors)

    assert drawn["lib"], ("Chart.js was not loaded when the card's script ran — the canvas stays "
                          "blank and nothing is reported (beta #38)")
    assert drawn.get("chart"), "Chart.js was there, but no chart was built on #chart-efftemp"
    assert drawn["points"] == 12, f"the card drew {drawn['points']} of the 12 seeded trips"


def test_a_car_with_petrol_points_only_has_its_chart(petrol_only_url):
    """A range-extender that always ran its generator has no electric point; its petrol series is
    still the chart, and the page loads the library for it."""
    errors = []
    with sync_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1100, "height": 900})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{petrol_only_url.url}/statistics", wait_until="networkidle")
        page.wait_for_timeout(1200)
        drawn = page.evaluate(
            """() => {
                 if (typeof Chart === 'undefined') return {lib: false};
                 const c = Chart.getChart('chart-efftemp');
                 return {lib: true, chart: !!c,
                         series: c ? c.data.datasets.map(d => d.data.length) : []};
               }""")
        browser.close()
    assert errors == [], "Statistics threw in the browser:\n  " + "\n  ".join(errors)
    assert drawn["lib"] and drawn["chart"], f"no temperature chart for a petrol-only car: {drawn}"
    assert 12 in drawn["series"] and drawn["series"][0] == 0, drawn


def test_the_trend_is_drawn_as_the_cubic_over_the_trips_temperatures(mate_url):
    """D#498: the dashed line is the cubic, sampled across the range the trips were at and not past
    it, and the coefficients under the chart are the ones it was drawn from."""
    with sync_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1100, "height": 900})
        page.goto(f"{mate_url.url}/statistics", wait_until="networkidle")
        page.wait_for_timeout(1200)
        line = page.evaluate(
            """() => {
                 const c = Chart.getChart('chart-efftemp');
                 const d = c.data.datasets[1].data;
                 return {n: d.length, x0: d[0].x, x1: d[d.length - 1].x};
               }""")
        caption = page.inner_text("[data-efftemp-trend]")
        browser.close()
    assert line == {"n": 331, "x0": -2.5, "x1": 30.5}      # every 0.1 °C, the hover cursor's step
    assert "a·T³ + b·T² + c·T + d" in caption
    assert "a=" in caption and "d=" in caption and "r²=" in caption



def _hover(mate_url, temperature, on_dot):
    with sync_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1100, "height": 900})
        page.goto(f"{mate_url.url}/statistics", wait_until="networkidle")
        page.wait_for_timeout(1200)
        x, y = page.evaluate(
            """([t, onDot]) => {
                 const c = Chart.getChart('chart-efftemp');
                 const r = c.canvas.getBoundingClientRect();
                 const dot = c.getDatasetMeta(0).data.find(p => Math.abs(c.scales.x.getValueForPixel(p.x) - t) < 0.05);
                 return [r.left + c.scales.x.getPixelForValue(t),
                         r.top + (onDot ? dot.y : c.chartArea.top + 4)];
               }""", [temperature, on_dot])
        page.mouse.move(x, y)
        page.wait_for_timeout(300)
        box = page.evaluate(
            """() => {
                 const t = Chart.getChart('chart-efftemp').tooltip;
                 return {shown: t.opacity > 0, title: t.title,
                         body: (t.body || []).map(b => b.lines.join(' '))};
               }""")
        browser.close()
    return box


def test_off_the_dots_the_cursor_rides_the_curve(mate_url):
    """As on the trip charts, the box opens wherever the cursor is over the chart: off the dots it holds
    the cursor's temperature and what the curve gives there, ± its 95 % interval, and no trip."""
    box = _hover(mate_url, 1.4, on_dot=False)
    assert box["shown"], "no box off the dots: it opens only on a dot, unlike the trip charts"
    assert box["title"] == ["1.4 °C"]
    assert box["body"] == ["21.4 ± 0.7 kWh/100km"]


def test_on_a_dot_the_box_adds_its_trip(mate_url):
    box = _hover(mate_url, 0.5, on_dot=True)
    assert box["title"] == ["0.5 °C"]
    assert box["body"][0] == "21.5 ± 0.8 kWh/100km"
    assert len(box["body"]) == 2 and box["body"][1].startswith("22.1 kWh/100km · 26.0 km · ")


def test_the_band_hides_with_its_curve(mate_url):
    """The band belongs to its curve: a click on the curve's legend entry hides both, a second click
    brings both back. Read off the canvas, inside the band halfway between the curve and its edge."""
    with sync_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1100, "height": 900})
        page.goto(f"{mate_url.url}/statistics", wait_until="networkidle")
        page.wait_for_timeout(1200)
        shade = """() => {
            const c = Chart.getChart('chart-efftemp'), r = window.devicePixelRatio;
            const i = c.data.datasets.findIndex(d => d.isCurve), p = c.data.datasets[i].data[39];
            const x = c.scales.x.getPixelForValue(p.x), y = c.scales.y.getPixelForValue(p.y + p.u / 2);
            const px = c.ctx.getImageData(Math.round(x * r), Math.round((y - 2) * r), 1, Math.round(4 * r)).data;
            let min = 255;
            for (let k = 3; k < px.length; k += 4) min = Math.min(min, px[k]);
            return min;
        }"""
        legend = """() => {
            const c = Chart.getChart('chart-efftemp'), r = c.canvas.getBoundingClientRect();
            const i = c.legend.legendItems.findIndex(it => c.data.datasets[it.datasetIndex].isCurve);
            const b = c.legend.legendHitBoxes[i];
            return [r.left + b.left + b.width / 2, r.top + b.top + b.height / 2];
        }"""
        seen = [page.evaluate(shade)]
        for _ in range(2):
            page.mouse.click(*page.evaluate(legend))
            page.wait_for_timeout(1200)
            seen.append(page.evaluate(shade))
        browser.close()
    assert seen[0] > 0 and seen[1] == 0 and seen[2] == seen[0], seen


def test_the_axis_holds_the_whole_band(mate_url):
    """The band widens past the dots at the ends of the curve; the axis takes it in, so it is not cut."""
    with sync_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1100, "height": 900})
        page.goto(f"{mate_url.url}/statistics", wait_until="networkidle")
        page.wait_for_timeout(1200)
        fit = page.evaluate("""() => {
            const c = Chart.getChart('chart-efftemp'), d = c.data.datasets.find(d => d.isCurve).data;
            const dots = c.data.datasets[0].data.map(p => p.y);
            return {lo: Math.min(...d.map(p => p.y - p.u)), hi: Math.max(...d.map(p => p.y + p.u)),
                    dots: [Math.min(...dots), Math.max(...dots)], axis: [c.scales.y.min, c.scales.y.max]};
        }""")
        browser.close()
    assert fit["lo"] < fit["dots"][0] or fit["hi"] > fit["dots"][1], f"the band stays within the dots: {fit}"
    assert fit["axis"][0] <= fit["lo"] and fit["axis"][1] >= fit["hi"], fit
