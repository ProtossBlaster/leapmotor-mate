"""The Commands page's own script, run in a real browser.

Every other test reads the HTML a TestClient returns. Most of what the Commands page does happens
after that, in its script: commands sent from a tile, the grid refetched under them, sliders that
move and snap. None of it is in the string, so this file serves a real Mate and drives the page.
Commands are caught at the network and answered here; nothing leaves for the car.

The steering heat slider, Off | 1 | 2, may show level I but never ask for it: the remote command
only switches the wheel off or on at II (test_steering_heat_shows_its_level.py has the signal and
the command). The tests move the thumb the way a finger does, one `input` per position crossed,
and the way a keyboard does, arrow keys, which also commit the change and send it.

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
VIN_B = "LVIN0000000000002"          # the account's other car
MIRROR = 'form[hx-post="api/command/mirror_heat_on"] button'
SLIDER = 'input[hx-post="api/command/steering_heat_on"]'
ACCEPTED = '<span data-warn="1" data-accepted="1">Cloud accepted; vehicle unconfirmed</span>'
DONE = '<span data-ok="1" style="color:#22c55e">✓ Done</span>'      # what run_command answers on success
REFUSED = '<span data-warn="1" style="color:#fbbf24">⏳ Not sent — retry in 4s</span>'


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _seed(db_path: pathlib.Path) -> None:
    """Two set-up B10s, parked, with a position — enough for the Commands page to draw their comfort tiles."""
    import schema  # poller/ is on sys.path via tests/conftest.py

    conn = sqlite3.connect(db_path)
    try:
        schema.ensure_schema(conn)
        for vid, vin in ((1, VIN), (2, VIN_B)):
            conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (?, ?, 'B10')", (vid, vin))
            conn.execute(
                "INSERT INTO positions (vehicle_id, recorded_at, latitude, longitude, soc, "
                "odometer_km, range_km, gear, speed_kmh, charging) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                (vid, datetime.now(timezone.utc).isoformat(), 45.4642, 9.1900, 62.0, 12345.0, 280.0, "P", 0.0),
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
    for leak in ("MATE_AUTH_PASSWORD", "MATE_DEMO", "SUPERVISOR_TOKEN", "HASSIO_TOKEN",
                 "LEAPMOTOR_USER", "LEAPMOTOR_PASSWORD", "LEAPMOTOR_PIN"):
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


def _setting(db, key, value):
    conn = sqlite3.connect(db)
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()


def _report(db, raw, vin=VIN):
    """The poller's side: write what the car reports on 1816, as it does after every poll."""
    _setting(db, f"comfort_state_{vin.lower()}", json.dumps({"steering_heat": raw}))


@pytest.fixture
def choose_car(mate):
    """Pick the car the whole interface follows, as the picker in another tab does; back to the first after."""
    yield lambda vin: _setting(mate.db, "active_vehicle_vin", vin)
    _setting(mate.db, "active_vehicle_vin", VIN)


@pytest.fixture
def commands(mate):
    """Open the Commands page with the wheel reporting `raw` on 1816. Returns (page, sent commands)."""
    # Explicit synthetic owner snapshots satisfy the independent client's rights gate.
    # All command requests remain intercepted below; no cloud calls are made.
    from cloud_access_fixture import ABILITIES
    from ui_command_access import account_hash, snapshot_key
    # This is an invented account name, not a secret. Keep it plaintext so the
    # subprocess can read it: the parent test runner uses a DIFFERENT secret.key.
    user = 'synthetic-layout-account'
    _setting(mate.db, 'leapmotor_user', user)
    for vin in (VIN, VIN_B):
        _setting(mate.db, snapshot_key(vin), json.dumps({
            'account': account_hash(user), 'at': time.time(), 'shared': False,
            'vehicle': {'vin': vin, 'carType': 'B10', 'abilities': ABILITIES}}))
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
            # Fail promptly at the authorization seam instead of timing out on
            # every later click when a fixture accidentally hides the controls.
            assert page.locator(SLIDER).is_visible(), mate.log.read_text()[-3000:]
            assert page.locator(MIRROR).is_visible(), mate.log.read_text()[-3000:]
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


def _level(page):
    """What the slider shows: its value and the readout under it."""
    return page.eval_on_selector(SLIDER, "el => el.value"), page.text_content("#sm-steering_heat")


def _drag(page, *positions):
    """Move the thumb through `positions` the way a finger does: one `input` per position, no `change`."""
    for p in positions:
        page.eval_on_selector(SLIDER, "(el, p) => { el.value = p; "
                              "el.dispatchEvent(new Event('input', {bubbles: true})); }", p)
    return page.eval_on_selector(SLIDER, "el => el.value")


def _drag_by_mouse(page, to, steps):
    """A real pointer drag from the thumb to `to` (0..1 of the track) in `steps` moves, then a release.
    Returns the values the page showed along the way: the browser fires input wherever its reading
    differs from the value, many times over one spot when the page keeps changing the value under it."""
    page.locator(SLIDER).scroll_into_view_if_needed()
    box = page.locator(SLIDER).bounding_box()
    y = box["y"] + box["height"] / 2

    def x(frac):                                                 # thumb centre at 0 and 1 of the track
        return box["x"] + 2 + (box["width"] - 4) * frac

    start = int(page.eval_on_selector(SLIDER, "el => el.value")) / 2
    page.evaluate("s => { window.shown = []; document.querySelector(s)"
                  ".addEventListener('input', e => window.shown.push(e.target.value)); }", SLIDER)
    page.mouse.move(x(start), y)
    page.mouse.down()
    for i in range(1, steps + 1):
        page.mouse.move(x(start + (to - start) * i / steps), y)
    page.mouse.up()
    page.wait_for_timeout(300)
    return page.evaluate("window.shown")


def _ask_for_two(page, sent):
    page.focus(SLIDER)
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(300)
    assert sent == ["steering_heat_on"]


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


@pytest.mark.parametrize("anchor, press", [
    (SLIDER, lambda page: (page.focus(SLIDER), page.keyboard.press("ArrowRight"))),
    (MIRROR, lambda page: page.click(MIRROR)),
], ids=["steering slider", "mirror toggle"])
def test_a_comfort_tile_says_one_thing_while_its_command_completes(commands, anchor, press):
    """A comfort tile answers into its own #tg- span, not #result-, so its "✓ Done" was not dropped:
    it stood beside "Command in progress", vanished with the 1.5 s refetch, and the tile said three
    things before it settled."""
    page, sent = commands(1, fake_clock=True)
    press(page)
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


def test_a_command_whose_answer_never_comes_lets_the_page_go(commands):
    """A request the browser neither answers nor fails held the request lock past the watchdog, and the grid
    was never refetched again. The page gives up on it, says the outcome is unknown, does not resend, and
    re-reads the car's state."""
    page, sent = commands(0, fake_clock=True)
    page.hold = True
    page.click(MIRROR)
    page.wait_for_timeout(300)
    _tick(page, 181_000)                 # past the watchdog, past the page's own limit
    page.wait_for_timeout(300)
    assert sent == ["mirror_heat_on"]    # not resent
    assert (page.evaluate("sending"), page.evaluate("window.gridsIn")) == (False, 0)
    status = _status(page, MIRROR)
    assert "No answer from Mate" in status and "Command in progress" not in status, status
    assert page.errors == []
    _tick(page, 7_000)                   # the notice clears, and the grid is re-read
    assert "No answer from Mate" not in _status(page, MIRROR)
    assert page.evaluate("window.gridsIn") >= 1


def test_a_retry_the_page_gives_up_on_raises_no_error(commands):
    """The layout's "Try again" replays a failed request through htmx.ajax, whose promise rejects when the
    page gives up on the replay: an unhandled rejection, reported as a page error, for an outcome the tile
    already shows."""
    page, sent = commands(0, fake_clock=True)
    page.hold = True
    page.click(MIRROR)
    page.wait_for_timeout(300)
    page.hold = False
    page.held[0].fulfill(status=500, content_type="text/html", body="boom")
    page.wait_for_timeout(300)
    page.hold = True
    page.click(".lm-load-error button")
    page.wait_for_timeout(300)
    assert sent == ["mirror_heat_on", "mirror_heat_on"]
    _tick(page, 181_000)                 # the page gives up on the replay
    page.wait_for_timeout(300)
    assert "No answer from Mate" in _status(page, MIRROR)
    assert page.errors == []
    _tick(page, 7_000)
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


def test_from_off_the_thumb_goes_straight_to_two(commands):
    page, _ = commands(0)
    assert _drag(page, 1) == "2"
    assert _level(page) == ("2", "Lv 2")


def test_from_two_the_thumb_goes_straight_to_off(commands):
    page, _ = commands(3)
    assert _drag(page, 1) == "0"
    assert _level(page) == ("0", "Off")


def test_level_one_the_car_reports_is_shown(commands):
    page, _ = commands(1)
    assert _level(page) == ("1", "Lv 1")


def test_once_the_thumb_has_left_one_it_cannot_come_back(commands):
    page, _ = commands(1)
    assert _drag(page, 2, 1) == "0"          # right to II, then back over 1: on to Off
    page, _ = commands(1)
    assert _drag(page, 0, 1) == "2"          # left to Off, then back over 1: on to II
    assert page.errors == []


@pytest.mark.parametrize("steps", [20, 21])
def test_a_finger_that_stops_over_one_still_ends_where_it_was_heading(commands, steps):
    """The browser reads 1 under a finger held over the middle and fires input again after every snap.
    A thumb that took each snap as the start of a new move would flip 2, 0, 2, 0 under the finger and
    end wherever the count of moves left it."""
    page, sent = commands(0)
    shown = _drag_by_mouse(page, 0.5, steps)
    assert "0" not in shown[shown.index("2"):], shown
    assert (_level(page), sent) == (("2", "Lv 2"), ["steering_heat_on"])

    page, sent = commands(3)
    shown = _drag_by_mouse(page, 0.5, steps)
    assert "2" not in shown[shown.index("0"):], shown
    assert (_level(page), sent) == (("0", "Off"), ["steering_heat_off"])


def test_the_keyboard_sends_the_existing_commands(commands):
    page, sent = commands(0)
    page.focus(SLIDER)
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(500)
    assert (_level(page), sent) == (("2", "Lv 2"), ["steering_heat_on"])

    page, sent = commands(1)
    page.focus(SLIDER)
    page.keyboard.press("ArrowLeft")
    page.wait_for_timeout(500)
    assert sent == ["steering_heat_off"]
    assert page.errors == []


@pytest.mark.parametrize("status, answer", [(200, DONE), (500, "boom")], ids=["taken", "server error"])
def test_the_keys_cannot_move_the_thumb_while_its_command_is_still_out(commands, status, answer):
    """htmx queues a second change behind the request out, and the page's request lock
    (htmx:beforeRequest) never sees a queued one. The slider refuses the move itself, and a change
    while its own request is out is dropped, not queued: after an error there is no busy floor to
    refuse the repeat."""
    page, sent = commands(0)
    page.hold = True
    page.focus(SLIDER)
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(300)
    assert sent == ["steering_heat_on"] and len(page.held) == 1
    page.keyboard.press("ArrowLeft")
    page.wait_for_timeout(300)
    assert _level(page) == ("2", "Lv 2")

    page.hold = False
    page.held[0].fulfill(status=status, content_type="text/html", body=answer)
    page.wait_for_timeout(500)
    assert sent == ["steering_heat_on"], sent    # nothing was queued behind it
    assert page.errors == []


def test_the_keys_cannot_move_the_thumb_while_a_command_is_in_progress(commands):
    """The busy look stops the pointer, not the keys. A refused move goes back to what the tile stood
    on: the level held for the command in progress, or the car's own when the command was another
    tile's."""
    page, sent = commands(0)
    _ask_for_two(page, sent)
    page.keyboard.press("ArrowLeft")
    page.wait_for_timeout(300)
    assert (_level(page), sent) == (("2", "Lv 2"), ["steering_heat_on"])

    page, sent = commands(0)
    page.click(MIRROR)
    page.wait_for_timeout(300)
    page.focus(SLIDER)
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(300)
    assert (_level(page), sent) == (("0", "Off"), ["mirror_heat_on"])
    assert page.errors == []


@pytest.mark.parametrize("answer", [DONE, ACCEPTED], ids=["done", "accepted-unconfirmed"])
def test_the_thumb_stays_on_the_level_asked_for_until_the_car_reports_it(commands, mate, answer):
    """"Done" is the cloud taking the command, not the car acting on it: the refetch 1.5 s later still
    drew the car's old level, and the thumb went back, then forward again."""
    page, sent = commands(1, fake_clock=True, answer=answer)
    _ask_for_two(page, sent)
    _tick(page, 2_000)                   # the first refetch: the car has not answered yet
    assert _level(page) == ("2", "Lv 2")

    _report(mate.db, 3)                  # it answers
    _tick(page, 4_000)
    assert _level(page) == ("2", "Lv 2")
    assert "(turned on remotely)" in page.content()

    _report(mate.db, 1)                  # and is then turned down in the car: the hold is over
    _tick(page, 31_000)
    assert _level(page) == ("1", "Lv 1")


def test_a_late_answer_starts_the_wait_for_the_car(commands, mate):
    """The cloud can answer after the page's 60 s lock, and after the two minutes a hold lasts. The page
    must still hear the answer — no refetch may replace the slider meanwhile, and the periodic refresh
    runs again once the slider has lost focus — and the wait for the car starts at the answer."""
    page, sent = commands(0, fake_clock=True)
    page.hold = True
    _ask_for_two(page, sent)
    page.evaluate("document.activeElement.blur()")
    for _ in range(5):
        _tick(page, 31_000)              # 155 s: the lock let go at 60 s, the hold ran out at 120 s
    page.hold = False
    page.held[0].fulfill(status=200, content_type="text/html", body=DONE)
    page.wait_for_timeout(500)
    assert page.evaluate("window.cmdAnswers") == 1, "the page never heard the answer"
    _tick(page, 6_000)                   # the refetches after the answer, the car still off
    assert _level(page) == ("2", "Lv 2")

    _report(mate.db, 3)                  # the car acts on it
    _tick(page, 10_000)
    assert "(turned on remotely)" in page.content()
    _report(mate.db, 0)                  # and is turned off in the car: the hold is over
    _tick(page, 31_000)
    assert _level(page) == ("0", "Off")


def test_a_car_that_never_answers_gets_its_own_state_back(commands, mate):
    page, sent = commands(0, fake_clock=True)
    _ask_for_two(page, sent)
    _tick(page, 2_000)
    assert _level(page) == ("2", "Lv 2")
    _tick(page, 125_000)                 # past the hold, the car still off
    _tick(page, 31_000)
    assert _level(page) == ("0", "Off")


def test_the_tile_catches_up_with_a_car_that_answers_late(commands, mate):
    """The car's answer can reach Mate long after the last post-command refetch (31 s); only the 30 s
    refresh brings it in, and that one stands down while an input has focus. A slider with an id kept
    the focus htmx handed back after every swap, and the tile sat on "Lv 1" beside a car on II."""
    page, sent = commands(1, fake_clock=True)
    _ask_for_two(page, sent)
    _tick(page, 40_000)                  # every post-command refetch, the car still on I
    assert "(turned on remotely)" not in page.content()

    _report(mate.db, 3)                  # the car's answer arrives late
    _tick(page, 31_000)                  # one 30 s refresh
    assert _level(page) == ("2", "Lv 2")
    assert "(turned on remotely)" in page.content()   # drawn by the server only: a refresh brought it


@pytest.mark.parametrize("status, answer", [(500, "boom"), (200, REFUSED)], ids=["server error", "refused"])
def test_a_command_the_cloud_did_not_take_puts_the_thumb_back(commands, status, answer):
    """The answer itself puts the thumb back: no refetch follows a failed request, and the 30 s
    refresh waits while the slider has focus."""
    page, sent = commands(2, fake_clock=True)
    page.hold = True
    page.focus(SLIDER)
    page.keyboard.press("ArrowLeft")
    page.wait_for_timeout(300)
    assert sent == ["steering_heat_off"] and _level(page) == ("0", "Off")
    page.hold = False
    page.held[0].fulfill(status=status, content_type="text/html", body=answer)
    page.wait_for_timeout(500)          # the clock is fake: no refetch can have run
    assert _level(page) == ("2", "Lv 2")


def test_a_retry_after_an_error_sends_the_command_the_gesture_asked_for(commands):
    """The "Try again" the layout offers replays the request from the slider, whose thumb is back on
    the car's level by then: the endpoint must follow the thumb only when a move of the thumb sends
    it."""
    page, sent = commands(0)
    page.hold = True
    page.focus(SLIDER)
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(300)
    page.hold = False
    page.held[0].fulfill(status=500, content_type="text/html", body="boom")
    page.wait_for_timeout(500)
    assert _level(page) == ("0", "Off")
    page.hold = True
    page.click(".lm-load-error button")
    page.wait_for_timeout(300)
    assert sent == ["steering_heat_on", "steering_heat_on"]
    assert _level(page) == ("2", "Lv 2")
    assert "Command in progress" in _status(page, SLIDER)
    page.hold = False
    page.held[1].fulfill(status=200, content_type="text/html", body=DONE)


def test_a_new_command_takes_over_from_the_level_held_for_the_last_one(commands, mate):
    """Off was taken and held while the car had not answered; then 2 is asked for. The hold names 2
    from the moment the command leaves, and the refetches after its answer draw 2, not the Off held
    for the last one."""
    page, sent = commands(3, fake_clock=True)
    page.focus(SLIDER)
    page.keyboard.press("ArrowLeft")
    page.wait_for_timeout(300)
    _tick(page, 12_000)                  # past the busy floor; the car still reports II
    assert _level(page) == ("0", "Off")

    page.hold = True
    page.focus(SLIDER)
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(300)
    assert sent == ["steering_heat_off", "steering_heat_on"]
    assert _level(page) == ("2", "Lv 2")
    page.hold = False
    page.held[0].fulfill(status=200, content_type="text/html", body=DONE)
    page.wait_for_timeout(300)
    _tick(page, 6_000)                   # the refetches after the answer, the car still on II
    assert _level(page) == ("2", "Lv 2")


def test_the_level_held_for_one_car_is_not_put_on_the_other(commands, mate, choose_car):
    """The picker moves the whole interface to the other car, and a page left open on the first gets
    that car's grid at its next refetch. The level held for the first car's command was put on the
    slider of the second, which has its own state."""
    page, sent = commands(0, fake_clock=True)
    _report(mate.db, 0, VIN_B)
    _ask_for_two(page, sent)
    choose_car(VIN_B)                    # in another tab
    _tick(page, 2_000)                   # the first refetch draws the other car
    assert _level(page) == ("0", "Off")


def test_the_old_level_a_refetch_still_draws_is_not_the_car_answering(commands, mate):
    """2 → off → on: the grid draws the 2 the car had until it has acted on the off, so a refetch after
    the on met the level asked for and let the hold go. Then the off landed, and the thumb stood on
    Off with an on out for the car. A level the grid already showed when the command left must first
    give way before it counts as the answer."""
    page, sent = commands(3, fake_clock=True)
    page.focus(SLIDER)
    page.keyboard.press("ArrowLeft")
    page.wait_for_timeout(300)
    _tick(page, 12_000)                  # past the busy floor; the car still reports II
    page.focus(SLIDER)
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(300)
    assert sent == ["steering_heat_off", "steering_heat_on"]
    _tick(page, 6_000)                   # refetches land, the grid still on the old 2
    _report(mate.db, 0)                  # the car has acted on the off
    _tick(page, 5_000)
    assert _level(page) == ("2", "Lv 2")

    _report(mate.db, 3)                  # and on the on
    _tick(page, 10_000)
    assert _level(page) == ("2", "Lv 2")
    assert "(turned on remotely)" in page.content()


def test_the_thumb_cannot_move_while_its_request_is_still_out_after_the_lock_let_go(commands):
    """The page lets go of a request after 60 s, since a tile a refetch swapped out never reports its
    end. The slider's own request may still be out then: its change was dropped, not sent, and the
    thumb stood on Off for a command that never left."""
    page, sent = commands(0, fake_clock=True)
    page.hold = True
    _ask_for_two(page, sent)
    _tick(page, 61_000)
    page.keyboard.press("ArrowLeft")
    page.wait_for_timeout(300)
    assert (_level(page), sent) == (("2", "Lv 2"), ["steering_heat_on"])
    page.hold = False
    page.held[0].fulfill(status=200, content_type="text/html", body=DONE)


def test_the_slider_is_named_for_a_screen_reader(commands):
    """The button it replaces was announced as "Turn On"; a slider with no name is announced as "0"."""
    page, _ = commands(0)
    assert page.get_by_role("slider", name="Steering heat").count() == 1
