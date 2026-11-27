"""A form the server refuses says WHY, in the form — run in a real browser.

#501 (@matttiaromano, 27/11): moving the end of a recovered charge from 03:00 AM to 03:01 PM widened
it, and 5.4.0 refuses that on purpose. The server answered 400 with the reason, «The times must stay
inside the span the charge has now.», and the page showed «This part did not load. (400) Retry»
instead: htmx swaps nothing on a 4xx, so every refusal the routes write as a red line for the form's
own message slot (thirty of them) never reached it, and since #240 the generic strip took its place
— with a Retry that only sends the same refused values again.

A 400 that carries HTML is the server's answer for that slot: it goes there, and no strip. It stays
a failure for the form, so a form that reloads the page on success does not reload on it.

The second test is the typed-in charge's own form (#188), which answers its refusals the same way.

It needs fastapi, uvicorn, playwright and a Chromium, none of them in CI's minimal env, so it
skips there and guards the laptop it runs on.
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

import pytest

pytest.importorskip("fastapi", reason="web/main.py needs fastapi (absent in the minimal CI env)")
pytest.importorskip("uvicorn", reason="the page has to be SERVED, not rendered in-process")
sync_api = pytest.importorskip(
    "playwright.sync_api",
    reason="needs playwright + `playwright install chromium`",
)

ROOT = pathlib.Path(__file__).resolve().parent.parent
RECOVERED, TYPED = 1, 2


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _seed(db_path: pathlib.Path) -> None:
    """A T03 with one recovered charge (22:00 → 03:00, as on #501) and one typed in by hand."""
    import schema  # poller/ is on sys.path via tests/conftest.py

    conn = sqlite3.connect(db_path)
    try:
        schema.ensure_schema(conn)
        conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1, 'LVIN0000000000001', 'T03')")
        conn.execute(
            "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc, energy_added_kwh,"
            " duration_min, charge_type, location_type, reconstructed) VALUES (?, 1, '2026-11-20T22:00:00+00:00',"
            " '2026-11-21T03:00:00+00:00', 41, 76, 12.6, 300.0, 'AC', 'HOME', 1)", (RECOVERED,))
        conn.execute(
            "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc, energy_added_kwh,"
            " duration_min, charge_type, location_type, manual_entry) VALUES (?, 1, '2026-11-10T08:00:00+00:00',"
            " '2026-11-10T10:00:00+00:00', 30, 60, 10.0, 120.0, 'AC', 'HOME', 1)", (TYPED,))
        for key, value in (("setup_complete", "1"), ("language", "en"), ("timezone", "UTC")):
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(scope="module")
def mate(tmp_path_factory):
    """A real Mate, serving a database of our own, on a port of its own."""
    data = tmp_path_factory.mktemp("mate-refusal")
    db = data / "leapmotor_mate.db"
    _seed(db)

    port = _free_port()
    env = {**os.environ,
           "DB_PATH": str(db),
           "WEB_PORT": str(port),
           "PYTHONPATH": str(ROOT / "web"),
           "MATE_RESEARCH": "0"}
    for leak in ("MATE_AUTH_PASSWORD", "MATE_DEMO", "SUPERVISOR_TOKEN", "HASSIO_TOKEN",
                 "LEAPMOTOR_USER", "LEAPMOTOR_PASSWORD", "LEAPMOTOR_PIN"):
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
        yield types.SimpleNamespace(url=url, db=db, log=log)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as pw:
        chromium = pw.chromium.launch()
        yield chromium
        chromium.close()


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    pg = context.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.loads = 0
    pg.on("load", lambda: setattr(pg, "loads", pg.loads + 1))
    yield pg
    context.close()


def _open(page, mate, cid, button):
    page.goto(f"{mate.url}/charges?highlight={cid}", wait_until="networkidle")
    card = page.locator(f"#charge-card-{cid}")
    card.wait_for()
    if not card.evaluate("e => e.open"):
        card.locator("summary").click()
    card.get_by_text(button).first.click()
    return card


def _times(mate, cid):
    conn = sqlite3.connect(mate.db)
    try:
        return conn.execute("SELECT started_at, ended_at, energy_added_kwh FROM charges WHERE id=?", (cid,)).fetchone()
    finally:
        conn.close()


def test_a_widened_recovered_charge_says_why_it_was_refused(page, mate):
    before = _times(mate, RECOVERED)
    card = _open(page, mate, RECOVERED, "Correct the times")
    card.locator("input[name=end_time]").fill("15:01")          # 03:00 AM → 03:01 PM, as on #501
    loads = page.loads
    with page.expect_response(lambda r: r.url.endswith(f"/api/charges/{RECOVERED}/times")) as answer:
        card.locator("form[hx-post$='/times'] button[type=submit]").click()
    assert answer.value.status == 400
    msg = page.locator(f"#ctimes-msg-{RECOVERED}")
    msg.get_by_text("The times must stay inside the span the charge has now.").wait_for(timeout=5000)
    page.wait_for_timeout(1500)                                 # past the 900 ms a success would reload after
    assert page.locator(".lm-load-error").count() == 0
    assert page.loads == loads                                  # a refusal is not a save: no reload
    assert _times(mate, RECOVERED) == before
    assert page.errors == []


def test_a_typed_charge_refused_says_why(page, mate):
    before = _times(mate, TYPED)
    card = _open(page, mate, TYPED, "Edit")
    card.locator(f"#cedit-{TYPED} input[name=energy]").fill("0")
    loads = page.loads
    with page.expect_response(lambda r: r.url.endswith(f"/api/charges/{TYPED}/edit")) as answer:
        card.locator(f"#cedit-{TYPED} button[type=submit]").click()
    assert answer.value.status == 400
    page.locator(f"#cedit-msg-{TYPED}").get_by_text("Date and energy are required").wait_for(timeout=5000)
    page.wait_for_timeout(1500)
    assert page.locator(".lm-load-error").count() == 0
    assert page.loads == loads
    assert _times(mate, TYPED) == before
    assert page.errors == []
