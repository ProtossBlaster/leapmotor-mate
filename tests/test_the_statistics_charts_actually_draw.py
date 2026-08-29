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
    spread from -3 °C to 30 °C so the fit has something to fit.
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
                 22.0 - i * 0.4, temp, temp + 1.0))
        conn.execute("INSERT INTO settings (key, value) VALUES ('setup_complete', '1')")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(scope="module")
def mate_url(tmp_path_factory):
    """A real Mate, serving a database of our own, on a port of its own."""
    data = tmp_path_factory.mktemp("mate-stats-browser")
    db = data / "leapmotor_mate.db"
    _seed(db)

    port = _free_port()
    env = {**os.environ,
           "DB_PATH": str(db),
           "WEB_PORT": str(port),
           "PYTHONPATH": str(ROOT / "web"),
           "MATE_RESEARCH": "0"}
    for leak in ("MATE_AUTH_PASSWORD", "MATE_DEMO", "SUPERVISOR_TOKEN", "HASSIO_TOKEN"):
        env.pop(leak, None)

    log = data / "web.log"
    proc = subprocess.Popen([sys.executable, str(ROOT / "web" / "main.py")], env=env,
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
