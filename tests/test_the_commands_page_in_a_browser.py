"""The Commands page's own script, run in a real browser.

Every other test reads the HTML a TestClient returns. Most of what the Commands page does happens
after that, in its script: commands sent from a tile, the grid refetched under them, sliders that
move and snap. None of it is in the string, so this file serves a real Mate and drives the page.
Commands are caught at the network and answered here; nothing leaves for the car.

It needs fastapi, uvicorn, playwright and a Chromium, none of them in CI's minimal env, so it
skips there and guards the laptop it runs on.
"""
import json
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
    reason="needs playwright + `playwright install chromium`",
)

ROOT = pathlib.Path(__file__).resolve().parent.parent
VIN = "LVIN0000000000001"
MIRROR = 'form[hx-post="api/command/mirror_heat_on"] button'
STEERING = 'form[hx-post="api/command/steering_heat_on"] button'
DONE = '<span data-ok="1" style="color:#22c55e">✓ Done</span>'      # what run_command answers on success
REFUSED = '<span data-warn="1" style="color:#fbbf24">⏳ Not sent — retry in 4s</span>'


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _seed(db_path: pathlib.Path) -> None:
    """A set-up B10, parked, with a position — enough for the Commands page to draw its comfort tiles."""
    import schema  # poller/ is on sys.path via tests/conftest.py

    conn = sqlite3.connect(db_path)
    try:
        schema.ensure_schema(conn)
        conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1, ?, 'B10')", (VIN,))
        conn.execute(
            "INSERT INTO positions (vehicle_id, recorded_at, latitude, longitude, soc, "
            "odometer_km, range_km, gear, speed_kmh, charging) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (datetime.now(timezone.utc).isoformat(), 45.4642, 9.1900, 62.0, 12345.0, 280.0, "P", 0.0),
        )
        conn.execute("INSERT INTO settings (key, value) VALUES ('setup_complete', '1')")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(scope="module")
def mate(tmp_path_factory):
    """A real Mate, serving a database of our own, on a port of its own."""
    data = tmp_path_factory.mktemp("mate-steering")
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
        yield types.SimpleNamespace(url=url, db=db, log=log)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def _report(db, raw):
    """The poller's side: write what the car reports on 1816, as it does after every poll."""
    conn = sqlite3.connect(db)
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                 (f"comfort_state_{VIN.lower()}", json.dumps({"steering_heat": raw})))
    conn.commit()
    conn.close()


@pytest.fixture
def commands(mate):
    """Open the Commands page with the wheel reporting `raw` on 1816. Returns (page, sent commands)."""
    with sync_api.sync_playwright() as pw:
        browser = pw.chromium.launch()

        def open_with(raw, fake_clock=False, answer=DONE):
            _report(mate.db, raw)
            page = browser.new_page()
            if fake_clock:
                page.clock.install()
            errors, sent, held = [], [], []

            def command(route):
                sent.append(route.request.url.rsplit("/", 1)[1])
                if page.hold:                 # the cloud is still working on it
                    held.append(route)
                else:
                    route.fulfill(status=200, content_type="text/html", body=answer)
            page.hold, page.held = False, held
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.route("**/api/command/**", command)
            response = page.goto(mate.url + "/commands")
            assert response.status == 200, mate.log.read_text()[-3000:]
            page.wait_for_selector("#cmd-grid")
            # How many grid refetches are out (the page's clock can be fake, the network never is: _grid_back),
            # how many came back with the grid, and how many command answers the page heard.
            page.evaluate("""() => {
                window.gridOut = window.gridsIn = window.cmdAnswers = 0;
                const path = e => (e.detail.pathInfo || {}).requestPath || '';
                const grid = e => path(e).indexOf('cmd-grid') !== -1, cmd = e => path(e).indexOf('api/command/') !== -1;
                document.addEventListener('htmx:beforeRequest', e => { if (grid(e)) window.gridOut++; });
                document.addEventListener('htmx:afterRequest', e => {
                    if (grid(e)) { window.gridOut--; if (e.detail.successful) window.gridsIn++; }
                    if (cmd(e)) window.cmdAnswers++;
                });
            }""")
            page.errors = errors
            return page, sent

        yield open_with
        browser.close()


def _grid_back(page):
    """The page's clock is fake, the network is not: wait, in real time, for every refetch to be in."""
    deadline = time.time() + 10
    while page.evaluate("window.gridOut") and time.time() < deadline:
        page.wait_for_timeout(50)
    assert page.evaluate("window.gridOut") == 0, "a grid refetch never came back"


def _tick(page, ms):
    """Advance the page's clock, let the refetches it fired come back, then let htmx settle them."""
    page.clock.run_for(ms)
    _grid_back(page)
    page.clock.run_for(100)


def _status(page, anchor):
    """The line under a comfort tile's control, where the command's progress is told."""
    return page.eval_on_selector(anchor, "el => el.closest('.w-full.mt-auto').textContent.replace(/\\s+/g, ' ')")


def test_a_refetch_asked_for_while_another_is_out_does_not_stop_the_grid(commands):
    """Two refetches overlap whenever one is asked for before the last is back. htmx queued the second
    with the #cmd-grid the first swapped out of the page; the swap into that detached element threw,
    and htmx never released the source again: no refetch ever after, until a reload."""
    page, _ = commands(0)
    held = []
    page.route("**/api/cmd-grid", lambda route: held.append(route))
    page.evaluate("refreshCmdGrid()")
    page.wait_for_timeout(300)
    page.evaluate("refreshCmdGrid()")              # asked for while the first is still out
    page.wait_for_timeout(300)
    page.unroute("**/api/cmd-grid")
    for route in held:
        route.continue_()
    _grid_back(page)
    page.evaluate("refreshCmdGrid()")              # and the grid still refreshes after it
    _grid_back(page)
    assert page.errors == []


def test_a_busy_tile_keeps_saying_so_through_a_refetch(commands):
    """htmx settles a swap 20 ms later by putting an id'd element's fresh attributes back, the busy
    look of #spin- among them. Re-applied only on the page's next 300 ms tick, the "Command in
    progress" blinked off at every refetch."""
    page, sent = commands(0, fake_clock=True)
    page.evaluate("""() => {
        window.gaps = 0;
        new MutationObserver(() => {
            const sp = document.getElementById('spin-mirror_heat_left');
            if ((sending || isBusy()) && sp && sp.classList.contains('htmx-indicator')) window.gaps++;
        }).observe(document.body, {subtree: true, childList: true, attributes: true});
    }""")
    page.click(MIRROR)
    page.wait_for_timeout(300)
    assert sent == ["mirror_heat_on"]
    _tick(page, 1_600)                   # the 1.5 s refetch
    _tick(page, 3_500)                   # the 5 s one
    assert page.evaluate("isBusy()"), "the busy floor should still be on"
    assert page.evaluate("window.gaps") == 0


@pytest.mark.parametrize("anchor", [STEERING, MIRROR], ids=["steering toggle", "mirror toggle"])
def test_a_comfort_tile_says_one_thing_while_its_command_completes(commands, anchor):
    """A comfort tile answers into its own #tg- span, not #result-, so its "✓ Done" was not dropped:
    it stood beside "Command in progress", vanished with the 1.5 s refetch, and the tile said three
    things before it settled."""
    page, sent = commands(0, fake_clock=True)
    page.click(anchor)
    page.wait_for_timeout(300)
    assert sent, "the command never left"
    status = _status(page, anchor)
    assert "Command in progress" in status and "✓ Done" not in status, status

    _tick(page, 1_600)                   # the first refetch
    status = _status(page, anchor)
    assert "Command in progress" in status and "✓ Done" not in status, status

    _tick(page, 10_000)                  # the floor is over
    assert "Command in progress" not in _status(page, anchor)


def test_a_refetch_does_not_land_while_a_command_is_out(commands):
    """htmx reports the end of a request on the tile that sent it. A refetch landing meanwhile replaced the
    grid, tile included, and the answer reached a tile no longer in the page: "Command in progress" stayed
    for the watchdog's 60 s, no refetch followed the answer, and the car's state sat stale under it."""
    page, sent = commands(0, fake_clock=True)
    page.click(MIRROR)                   # taken: refetches follow at 1.5, 5, 9, 15, 23 and 31 s
    page.wait_for_timeout(300)
    _tick(page, 12_000)                  # past the busy floor
    page.hold = True
    page.click(MIRROR)                   # the cloud slow on this one
    page.wait_for_timeout(300)
    assert sent == ["mirror_heat_on", "mirror_heat_on"]
    _tick(page, 4_000)                   # the first command's 15 s refetch comes round
    page.hold = False
    page.held[0].fulfill(status=200, content_type="text/html", body=DONE)
    page.wait_for_timeout(500)
    assert (page.evaluate("sending"), page.evaluate("window.cmdAnswers")) == (False, 2), "the page never heard the answer"
    _tick(page, 11_000)                  # the floor after the answer is over; the wait was not 60 s
    assert "Command in progress" not in _status(page, MIRROR)


def test_a_refetch_already_out_when_a_command_leaves_does_not_land_either(commands):
    """The refetch may be out before the command leaves; its swap would land during the request the same
    way. The page aborts it, and refetches again once the answer is in."""
    page, _ = commands(0, fake_clock=True)
    held = []
    page.route("**/api/cmd-grid", lambda route: held.append(route))
    page.evaluate("refreshCmdGrid()")
    page.wait_for_timeout(300)
    page.hold = True
    page.click(MIRROR)                   # while the refetch is out
    page.wait_for_timeout(300)
    page.unroute("**/api/cmd-grid")
    for route in held:
        try:
            route.continue_()            # too late: the page has let go of it
        except sync_api.Error:
            pass
    page.wait_for_timeout(300)
    page.hold = False
    page.held[0].fulfill(status=200, content_type="text/html", body=DONE)
    page.wait_for_timeout(500)
    assert (page.evaluate("sending"), page.evaluate("window.cmdAnswers")) == (False, 1), "the page never heard the answer"
    assert page.errors == []             # the aborted refetch's promise rejected, and that was handled
    _tick(page, 2_000)                   # and the grid is refetched after the answer
    assert page.evaluate("window.gridsIn") >= 1


def test_a_comfort_tile_shows_a_refusal_as_a_refusal(commands):
    """A refusal (data-warn) was looked for in #result-<command>, which a comfort tile does not have: the
    tile said "Command in progress" for a command the cloud refused, and the refetch that followed wiped
    the notice after 1.5 s."""
    page, sent = commands(0, fake_clock=True, answer=REFUSED)
    page.click(MIRROR)
    page.wait_for_timeout(300)
    assert sent == ["mirror_heat_on"]
    status = _status(page, MIRROR)
    assert "Not sent" in status and "Command in progress" not in status, status
    _tick(page, 2_000)                   # no refetch follows a refusal: the notice is still there
    assert "Not sent" in _status(page, MIRROR)
    _tick(page, 5_000)                   # and it clears on its own
    assert "Not sent" not in _status(page, MIRROR)


def test_the_page_does_not_reload_itself_while_a_slow_command_is_completing(commands):
    """The layout reloads an idle page every 30 s, and a command the cloud is slow to answer makes the
    page look idle: no interaction for 20 s, and the refetch after the answer takes the focus off the
    control pressed. The reload would drop the busy state, so the page opts out of it."""
    page, _ = commands(0, fake_clock=True)
    page.clock.pause_at(page.evaluate("Date.now()") / 1000)   # seconds, not ms; from here on time moves only when ticked
    page.evaluate("window.samePage = true")
    page.hold = True                     # the cloud is still on it
    page.click(MIRROR)
    page.wait_for_timeout(300)
    _tick(page, 22_000)                  # longer than an interaction counts for
    page.hold = False
    page.held[0].fulfill(status=200, content_type="text/html", body=DONE)
    page.wait_for_timeout(300)
    for ms in (2_000, 3_500, 2_600):     # the refetches after the answer (+1.5 s, +5 s) rebuild the grid one
        _tick(page, ms)                  # at a time, and none is out when the layout's 30 s come round
    page.wait_for_timeout(300)
    assert page.evaluate("window.samePage") is True, "the page reloaded"
    assert "Command in progress" in _status(page, MIRROR)
