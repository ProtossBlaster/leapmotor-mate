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


@contextmanager
def served(data_dir, db_path):
    """web/main.py as its own process on a free port; yields the base URL."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**os.environ, "DB_PATH": str(db_path), "WEB_PORT": str(port), "PYTHONPATH": str(ROOT / "web"),
           "MATE_RESEARCH": "0"}
    for leak in ("MATE_AUTH_PASSWORD", "MATE_DEMO", "SUPERVISOR_TOKEN", "HASSIO_TOKEN"):
        env.pop(leak, None)
    log = data_dir / "web.log"
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
