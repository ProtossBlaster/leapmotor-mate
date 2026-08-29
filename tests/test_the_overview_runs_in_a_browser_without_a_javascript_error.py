"""The Overview, opened in a real browser — does its JavaScript actually run?

The suite has 312 files and not one of them opens a page. `TestClient` returns the HTML and every
existing check reads that string, which means a page can be served 200 OK, look complete to every
test we have, and still be dead in the hand: the Overview carries ~120 lines of inline script plus
Leaflet, and a single uncaught exception in there stops the command buttons, the 30 s refresh and
the map without changing one byte of the HTML we assert on.

This is the first test that runs the page instead of reading it. It asserts the one thing a string
comparison can never see — the browser raised no uncaught error — and nothing else. Deliberately
`pageerror` and not console errors: a console error is also what a blocked map tile logs, so it
would go red on a slow network rather than on a defect. An uncaught exception is never anything
but our bug.

Costs: fastapi + uvicorn + pytest-playwright + a Chromium. None of them are in CI's minimal env
(.github/workflows/ci.yml installs pytest, cryptography, jinja2, leapmotor-api, paho-mqtt), so
today this file SKIPS there and guards only the laptop it runs on — the same half-measure
test_access_card_markup.py's docstring warns about. Adding them to the CI step is what turns it
into a real guard; that is a separate, deliberate decision, not something this file assumes.
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
from datetime import datetime, timezone

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
    """The smallest database that reaches the code under test.

    A latitude and a longitude are not decoration here: they are what puts the Overview down the
    `{% if status.latitude and status.longitude %}` branch, and that branch is where the Leaflet
    script lives. Seed a car with no fix and this test would pass over a page that never ran the
    half we care about.

    `speed_kmh` is written explicitly, and 0.0 rather than left out, because leaving it out is a
    500: main._driving does `pos.get("speed_kmh", 0) > 1`, and a default only answers for a key
    that is ABSENT — a column that is there and NULL comes back as None, and None > 1 raises. A
    parked car (gear 'P') takes that branch every time. Silvio's own 276.672 rows have no NULL
    speed, so this is a trap rather than a live fault, but the seed must not walk into it: a
    fixture that 500s here would hand every later browser test an error page to pass over.
    """
    import schema  # poller/ is on sys.path via tests/conftest.py

    # Timezone-AWARE, exactly as poller/db._now_iso writes it. db_reader.get_latest_status derives
    # `last_seen_s` by subtracting this from an aware "now" inside a try/except; a naive stamp
    # raises there, the key is never set, and the status card then dies on an undefined attribute —
    # a 500, from nothing but a timestamp written the wrong shape.
    recorded_at = datetime.now(timezone.utc).isoformat()

    conn = sqlite3.connect(db_path)
    try:
        schema.ensure_schema(conn)
        conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1, ?, ?)",
                     ("TESTVIN0000000001", "C10"))
        conn.execute(
            "INSERT INTO positions (vehicle_id, recorded_at, latitude, longitude, soc, "
            "odometer_km, range_km, gear, speed_kmh, charging) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (recorded_at, 45.4642, 9.1900, 62.0, 12345.0, 280.0, "P", 0.0),
        )
        conn.execute("INSERT INTO settings (key, value) VALUES ('setup_complete', '1')")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(scope="module")
def mate_url(tmp_path_factory):
    """A real Mate, serving a database of our own, on a port of its own."""
    data = tmp_path_factory.mktemp("mate-browser")
    db = data / "leapmotor_mate.db"
    _seed(db)

    port = _free_port()
    env = {**os.environ,
           "DB_PATH": str(db),
           "WEB_PORT": str(port),
           "PYTHONPATH": str(ROOT / "web"),
           "MATE_RESEARCH": "0"}
    # Never let the developer's own shell decide whether this test sees a login page.
    for leak in ("MATE_AUTH_PASSWORD", "MATE_DEMO", "SUPERVISOR_TOKEN", "HASSIO_TOKEN"):
        env.pop(leak, None)

    # To a file, not a pipe: when the server is the thing that went wrong, its own output is the
    # only account of why, and a pipe we are not draining is exactly where it gets lost.
    log = data / "web.log"
    proc = subprocess.Popen([sys.executable, str(ROOT / "web" / "main.py")], env=env,
                            stdout=log.open("w"), stderr=subprocess.STDOUT, text=True)
    url = f"http://127.0.0.1:{port}"
    try:
        # Readiness is "the socket answers", NOT /healthz: that probe reports on the POLLER, and we
        # start the web alone, so on a set-up database it correctly answers 503 for ever. Using it
        # here would have made this fixture wait 30 s and then blame the page.
        deadline = time.time() + 30
        while time.time() < deadline:
            if proc.poll() is not None:
                pytest.fail(f"the web process died before it served anything:\n{log.read_text()}")
            try:
                urllib.request.urlopen(url, timeout=1).read()
                break
            except urllib.error.HTTPError:
                break        # it answered, and any answer means the socket is up
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


def test_the_overview_raises_no_uncaught_javascript_error(mate_url):
    server = mate_url
    errors = []
    with sync_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        # Keep the test off the internet: the map asks openstreetmap.org for tiles, and a test
        # that needs a network is a test that goes red for reasons that are not ours. Aborting
        # them is also the harsher case — Leaflet gets failures back, not tiles.
        page.route("**://*.tile.openstreetmap.org/**", lambda route: route.abort())

        response = page.goto(server.url, wait_until="networkidle")
        # The inline script's own clock (setInterval(tick, 300)) plus the two hx-trigger="load"
        # blocks: a throw inside either lands after the page is "loaded", so waiting for the
        # document is not enough.
        page.wait_for_timeout(1500)
        status, html = (response.status if response else None), page.content()
        browser.close()

    # Prove we were looking at the Overview before believing anything about its JavaScript. The
    # first draft of this test did not, and it went green over a 500 error page: 172 bytes with no
    # script in them at all, so of course nothing threw. A test that cannot tell "the page is
    # clean" from "there is no page" measures nothing. `hero-card` is the Overview's own id — a
    # redirect to /setup or /login carries a 200 too.
    assert status == 200, (f"the Overview did not render: HTTP {status}\n"
                           f"{server.log.read_text()[-3000:]}")
    assert 'id="hero-card"' in html, f"this is not the Overview:\n{html[:500]}"

    assert errors == [], "the Overview threw in the browser:\n  " + "\n  ".join(errors)
