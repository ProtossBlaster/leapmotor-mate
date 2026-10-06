"""The web app served for real and driven by a real browser — for what rendered HTML cannot show:
what a cell is given on screen, whether a tooltip actually appears. Shared by the browser tests;
each skips where it cannot run (no playwright, no Chromium), like the Commands page test.
"""
import os
import pathlib
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def seed_database(db_path, vin, rows=()):
    """A schema with one car, setup complete, and `rows` of `(sql, params)` on top."""
    import schema
    conn = sqlite3.connect(db_path)
    try:
        schema.ensure_schema(conn)
        conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1, ?, 'B10')", (vin,))
        conn.execute("INSERT INTO settings (key, value) VALUES ('setup_complete', '1')")
        for sql, params in rows:
            conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


# A trip and a charge for `seed_database`, each taking (id, started_at, ended_at).
TRIP_SQL = ("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, duration_min, start_soc, end_soc)"
            " VALUES (?,1,?,?,12,30,80,70)")
CHARGE_SQL = ("INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc, energy_added_kwh)"
              " VALUES (?,1,?,?,40,60,9)")


# Counts the calendar block's settled swaps in window.settled; the page's other blocks load on their own.
COUNT_CALENDAR_SWAPS = ("window.settled = 0; document.addEventListener('htmx:afterSettle', e => {"
                        " if (e.target.closest('[id$=-calendar-month-wrap], #fuel-calendar-wrap')) window.settled++; })")


def swapped(page, act):
    """Does `act` and waits for the calendar block's next settled swap."""
    n = page.evaluate("window.settled")
    act()
    page.wait_for_function(f"window.settled >= {n + 1}")


def ringed(page):
    """The days ringed on the calendar, by number."""
    return [int(d) for d in page.eval_on_selector_all(
        ".cal-day[data-selected]", "cs => cs.map(c => /day=(\\d+)/.exec(c.getAttribute('hx-get'))[1])")]


# How many ports to try before giving up. A port is chosen by binding to 0 and letting go, so
# between the choice and the child binding it anything else on the machine can take it — and in a
# full suite run something does: twice in six runs the browser tests died on
# `[Errno 48] ... address already in use`, with nothing wrong in the test that lost the race.
# → tests/test_a_browser_test_does_not_lose_its_port_to_another.py
_PORT_ATTEMPTS = 5


def _spawn(data_dir, db_path):
    """The web process on a port nobody else had taken by the time it bound one.

    Returns (proc, url, log). Raises the last log if every attempt lost the race."""
    env = {**os.environ, "DB_PATH": str(db_path), "PYTHONPATH": str(ROOT / "web"),
           "MATE_RESEARCH": "0"}
    for leak in ("MATE_AUTH_PASSWORD", "MATE_DEMO", "SUPERVISOR_TOKEN", "HASSIO_TOKEN"):
        env.pop(leak, None)
    log = data_dir / "web.log"
    for attempt in range(_PORT_ATTEMPTS):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        proc = subprocess.Popen([sys.executable, str(ROOT / "tests" / "no_internet.py"),
                                 str(ROOT / "web" / "main.py")],
                                env={**env, "WEB_PORT": str(port)},
                                stdout=log.open("w"), stderr=subprocess.STDOUT, text=True)
        url = f"http://127.0.0.1:{port}"
        deadline = time.time() + 30
        while time.time() < deadline:
            if proc.poll() is not None:
                break                       # died: the log below says whether it was the port
            try:
                urllib.request.urlopen(url, timeout=1).read()
                return proc, url, log
            except urllib.error.HTTPError:
                return proc, url, log       # answering, just not with a 200
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                time.sleep(0.2)
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        text = log.read_text()
        if "address already in use" not in text:
            pytest.fail(f"the web process did not serve anything:\n{text}")
        # Someone else had this port. Take another one.
    pytest.fail(f"lost the port race {_PORT_ATTEMPTS} times running:\n{log.read_text()}")


@contextmanager
def served(data_dir, db_path):
    """web/main.py as its own process on a free port; yields the base URL."""
    proc, url, _log = _spawn(data_dir, db_path)
    try:
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def chromium(sync_api):
    """A browser, or a skip where playwright has none installed."""
    pw = sync_api.sync_playwright().start()
    try:
        return pw, pw.chromium.launch()
    except Exception as exc:  # noqa: BLE001
        pw.stop()
        pytest.skip(f"no Chromium for playwright here: {exc}")
