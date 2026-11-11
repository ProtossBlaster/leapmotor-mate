"""The Charges page reloads its history when the car ends its charge session, not when the charging flag drops.

The live panel polls every 30 s, and the page reloaded once the panel's charging flag went from on to off.
That was the end of the session while the flag stayed on for the whole of it (#307). The flag now goes off
once the battery is at the car's limit with nothing going in, while the car holds the session open some
minutes more, and the charge is in the history only when the session closes. So the panel carries the open
session, and the page reloads once that session is no longer the one in progress: it closed, or the next one
began between two polls. The reload takes nothing from the reader: it waits, as the idle auto-refresh does, while
a field is in use or the merge dialog is open, and for as long as a form holds edits that were not sent: a past
charge typed in and left, also with its form folded away, or a note that is not saved (its save failed, or it was
changed while the save was on its way). A saved or cancelled note, a figure of the row
changed and cancelled, and a search hold nothing.

Measured in a real browser, like the other live-panel tests; skips where it cannot run.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi", reason="web/main.py needs fastapi (absent in the minimal CI env)")
pytest.importorskip("uvicorn", reason="the page has to be SERVED, not rendered in-process")
sync_api = pytest.importorskip("playwright.sync_api", reason="needs playwright + `playwright install chromium`")

from web_in_a_browser import chromium, seed_database, served

VIN = "LVIN0000000000001"
PLUGGED_IN = datetime(2026, 10, 11, 6, 13, tzinfo=timezone.utc)
YESTERDAY = PLUGGED_IN - timedelta(days=1)
READING = ("INSERT INTO positions (vehicle_id, recorded_at, plug_connected, charging, charge_voltage_v,"
           " charge_current_a, soc) VALUES (1, ?, 1, ?, 430, ?, ?)")
# A poll of the panel, as its timer makes it; resolves once the answer is swapped in.
_POLL = """() => new Promise(done => {
  const panel = document.getElementById('charging-live-panel');
  panel.addEventListener('htmx:afterSwap', () => done(), { once: true });
  htmx.ajax('GET', 'api/charging-live', { source: panel });
})"""
_PANEL = "() => document.querySelector('#charging-live-panel').innerText"
_ENDED = ("UPDATE charges SET ended_at=?, end_soc=100, energy_added_kwh=15 WHERE id=9",
          ((PLUGGED_IN + timedelta(minutes=51)).isoformat(),))
_NEXT = ("INSERT INTO charges (id, vehicle_id, started_at, start_soc) VALUES (10, 1, ?, 99.0)",
         ((PLUGGED_IN + timedelta(minutes=52)).isoformat(),))
# What a reader can be in the middle of: adding a past charge by hand, or the merge dialog, open as its button opens it.
_BUSY = {"a field in use": ("() => { document.getElementById('manual-charge-form').classList.remove('hidden');"
                            " document.querySelector('#manual-charge-form [name=energy]').focus({ preventScroll: true }) }",
                            "() => document.activeElement.blur()"),
         "the merge dialog open": ("() => { document.getElementById('charge-merge-modal').style.display = 'flex' }",
                                   "() => { document.getElementById('charge-merge-modal').style.display = 'none' }")}

def _a_charge_in_progress():
    rows = [("INSERT INTO settings (key, value) VALUES ('timezone', 'UTC')", ()),
            ("INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc, energy_added_kwh, location_type)"
             " VALUES (8, 1, ?, ?, 40, 80, 20, 'PUBLIC')", (YESTERDAY.isoformat(), (YESTERDAY + timedelta(hours=2)).isoformat())),
            ("INSERT INTO charges (id, vehicle_id, started_at, start_soc) VALUES (9, 1, ?, 77.1)",
             (PLUGGED_IN.isoformat(),))]
    for k in range(6):
        rows.append((READING, ((PLUGGED_IN + timedelta(seconds=30 * k)).isoformat(), 1, -7, 77.1 + 0.1 * k)))
    return rows


@pytest.fixture
def mate(tmp_path_factory):
    data = tmp_path_factory.mktemp("mate-session-reload")
    db = data / "leapmotor_mate.db"
    seed_database(db, VIN, _a_charge_in_progress())
    with served(data, db) as url:
        yield url, db


def _write(db, sql, params):
    con = sqlite3.connect(db)
    con.execute(sql, params)
    con.commit()
    con.close()


def test_the_page_reloads_once_the_session_is_closed_and_not_when_the_battery_is_full(mate):
    url, db = mate
    pw, browser = chromium(sync_api)
    try:
        page = browser.new_page()
        page.clock.install()            # the reload waits for 20 s without an interaction
        assert page.goto(f"{url}/charges").status == 200
        assert "1 sessions recorded" in page.inner_text("body")
        page.evaluate("() => { window.stays = 1 }")

        # The battery reaches 100 % with nothing going in; the car holds its session open.
        _write(db, READING, ((PLUGGED_IN + timedelta(minutes=40)).isoformat(), 0, 1.1, 100.0))
        page.evaluate(_POLL)
        assert "Fully charged" in page.evaluate(_PANEL)
        page.clock.fast_forward("00:21")
        page.wait_for_timeout(300)
        assert page.evaluate("() => window.stays") == 1, "the page reloaded before the charge was in the history"

        # The car ends its session: the charge is filed, and the page shows it.
        _write(db, *_ENDED)
        with page.expect_event("load"):
            page.evaluate(_POLL)
            page.clock.fast_forward("00:21")
        assert page.evaluate("() => window.stays") is None
        assert "2 sessions recorded" in page.inner_text("body")
    finally:
        browser.close()
        pw.stop()


def test_the_page_reloads_when_the_next_session_began_between_two_polls(mate):
    url, db = mate
    pw, browser = chromium(sync_api)
    try:
        page = browser.new_page()
        page.clock.install()
        assert page.goto(f"{url}/charges").status == 200
        _write(db, *_ENDED)
        _write(db, *_NEXT)
        with page.expect_event("load"):
            page.evaluate(_POLL)
            page.clock.fast_forward("00:21")
        assert "2 sessions recorded" in page.inner_text("body"), "the charge that ended is not in the history"
    finally:
        browser.close()
        pw.stop()


@pytest.mark.parametrize("busy", _BUSY)
def test_the_reload_waits_for_a_reader_in_the_middle_of_something(mate, busy):
    url, db = mate
    begin, leave = _BUSY[busy]
    pw, browser = chromium(sync_api)
    try:
        page = browser.new_page()
        page.clock.install()
        assert page.goto(f"{url}/charges").status == 200
        page.evaluate("() => { window.stays = 1 }")
        page.evaluate(begin)
        _write(db, *_ENDED)
        page.evaluate(_POLL)
        page.clock.fast_forward("00:21")
        page.wait_for_timeout(1500)
        assert page.evaluate("() => window.stays") == 1, f"the end of the charge took the page from under {busy}"
        with page.expect_event("load"):
            page.evaluate(leave)
            page.clock.fast_forward("00:21")
        assert "2 sessions recorded" in page.inner_text("body")
    finally:
        browser.close()
        pw.stop()


@pytest.fixture
def charges(mate):
    """The Charges page with the charge in progress, the day of the finished charge open, and what ends the charge."""
    url, db = mate
    pw, browser = chromium(sync_api)
    try:
        page = browser.new_page()
        page.clock.install()
        assert page.goto(f"{url}/charges?month={YESTERDAY:%Y-%m}").status == 200
        page.wait_for_load_state("networkidle")       # the calendar draws itself again once loaded
        page.locator(f'.cal-day[hx-get$="&day={YESTERDAY.day}"]').first.click()
        page.wait_for_selector("#charges-day-drawer details.charge-card")
        page.evaluate("() => { document.querySelector('#charges-day-drawer details.charge-card').open = true; window.stays = 1 }")

        def ends():
            _write(db, *_ENDED)
            page.evaluate(_POLL)
        yield page, ends
    finally:
        browser.close()
        pw.stop()


def _idle(page):
    page.clock.fast_forward("00:21")


def _stays(page):
    _idle(page)
    page.wait_for_timeout(1500)
    return page.evaluate("() => window.stays") == 1


def _reloads(page):
    with page.expect_event("load"):
        _idle(page)
    return True


def _left(page):
    page.evaluate("() => document.activeElement.blur()")


def _a_note(page, text):
    page.click("#cnote-edit-8")
    page.fill("#cnote-8", text)
    _left(page)


@pytest.mark.parametrize("folded", [False, True], ids=["left open", "its form folded away"])
def test_a_past_charge_typed_in_and_left_holds_the_reload(charges, folded):
    page, ends = charges
    page.click("text=Add a past charge")
    page.fill("#manual-charge-form [name=energy]", "12.34")
    _left(page)
    if folded:
        page.click("text=Add a past charge")
    ends()
    assert _stays(page), "the end of the charge took a past charge that was not added yet"
    assert page.input_value("#manual-charge-form [name=energy]") == "12.34"


def test_a_note_that_is_not_saved_holds_the_reload_and_a_saved_one_lets_it_go(charges):
    page, ends = charges
    _a_note(page, "in the shade")
    ends()
    assert _stays(page), "the end of the charge took a note that was not saved"
    assert page.input_value("#cnote-8") == "in the shade"
    with page.expect_response("**/api/charges/8/note"):
        page.click("#cnote-form-8 button[type=submit]")
    _left(page)
    page.clock.fast_forward("00:10")          # the panel's own poll falls here; it and the scroll it sets off are activity
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(200)
    assert _reloads(page)
    assert page.text_content("#cnote-line-8 span") == "in the shade"


def test_a_cancelled_note_holds_nothing(charges):
    page, ends = charges
    _a_note(page, "in the shade")
    page.click("#cnote-form-8 button[type=button]")
    ends()
    assert _reloads(page)


@pytest.mark.parametrize("box, field", [("cm-form-8", "cost"), ("gk-form-8", "gross_kwh")],
                         ids=["the cost", "the energy delivered"])
def test_a_figure_of_the_row_changed_and_cancelled_holds_nothing(charges, box, field):
    page, ends = charges
    page.evaluate("id => document.getElementById(id).classList.remove('hidden')", box)
    drawn = page.input_value(f"#{box} [name={field}]")
    page.fill(f"#{box} [name={field}]", "7.5")
    page.click(f"#{box} button[type=button]")
    assert page.input_value(f"#{box} [name={field}]") == drawn, "Cancel left the figure that was typed"
    ends()
    assert _reloads(page)


def test_a_note_whose_save_failed_holds_the_reload(charges):
    page, ends = charges
    page.route("**/api/charges/8/note", lambda route: route.fulfill(status=500, body="no"))
    _a_note(page, "in the shade")
    with page.expect_response("**/api/charges/8/note"):
        page.click("#cnote-form-8 button[type=submit]")
    _left(page)
    ends()
    assert _stays(page), "a save that failed let the page reload over the note"
    assert page.input_value("#cnote-8") == "in the shade"


def test_a_note_changed_while_its_save_is_on_its_way_holds_the_reload(charges):
    page, ends = charges
    held = []
    page.route("**/api/charges/8/note", lambda route: held.append(route))
    _a_note(page, "in the shade")
    page.click("#cnote-form-8 button[type=submit]")
    page.wait_for_function("() => document.querySelector('#cnote-form-8.htmx-request')")
    page.fill("#cnote-8", "in the shade, slow")
    _left(page)
    with page.expect_response("**/api/charges/8/note"):
        held[0].continue_()
    ends()
    assert _stays(page), "the answer to the first save let the page reload over the text typed since"
    assert page.input_value("#cnote-8") == "in the shade, slow"


def test_a_search_holds_nothing(charges):
    page, ends = charges
    with page.expect_response("**/api/charges/search*"):
        page.locator("#charges-search-q").press_sequentially("shade")
        page.clock.fast_forward("00:01")          # the search waits for a pause in the typing
    _left(page)
    ends()
    assert _reloads(page)
