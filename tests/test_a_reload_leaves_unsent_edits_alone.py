"""A page that reloads itself leaves edits the reader has not sent alone. A field in use and input in the last
20 s already hold the reload; a field the reader has left did not, so a note typed and not yet saved was gone with
the next reload. A form that saves now holds it for as long as one of its fields differs from what it was drawn
with or last saved: leaving the field does not release it, saving does, and a save answers for what was sent,
not for what was typed while it was on its way.

Measured in a real browser on a trip's page, which reloads itself every 30 s; skips where it cannot run.
"""
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi", reason="web/main.py needs fastapi (absent in the minimal CI env)")
pytest.importorskip("uvicorn", reason="the page has to be SERVED, not rendered in-process")
sync_api = pytest.importorskip("playwright.sync_api", reason="needs playwright + `playwright install chromium`")

from web_in_a_browser import chromium, seed_database, served

VIN = "LVIN0000000000001"
STARTED = datetime(2026, 10, 2, 6, 13, tzinfo=timezone.utc)
NOTE = "textarea[name=note]"


def _a_trip():
    return [("INSERT INTO settings (key, value) VALUES ('timezone', 'UTC')", ()),
            ("INSERT INTO trips (id, vehicle_id, started_at, ended_at, start_soc, end_soc, distance_km, duration_min)"
             " VALUES (6, 1, ?, ?, 90, 88.3, 9, 30)", (STARTED.isoformat(), (STARTED + timedelta(minutes=30)).isoformat()))]


@pytest.fixture
def page(tmp_path_factory):
    data = tmp_path_factory.mktemp("mate-unsent-edits")
    db = data / "leapmotor_mate.db"
    seed_database(db, VIN, _a_trip())
    with served(data, db) as url:
        pw, browser = chromium(sync_api)
        try:
            page = browser.new_page()
            page.clock.install()            # the page reloads itself after 30 s, 20 s after the last input
            assert page.goto(f"{url}/trips/6").status == 200
            page.wait_for_load_state("networkidle")
            page.evaluate("window.__same = true")
            yield page
        finally:
            browser.close()
            pw.stop()


def _typed_and_left(page, text):
    page.fill(NOTE, text)
    page.evaluate("document.activeElement.blur()")


def _stays(page):
    page.clock.fast_forward("00:31")
    page.wait_for_timeout(1000)
    return page.evaluate("window.__same === true")


def test_a_note_typed_and_left_unsaved_is_still_there(page):
    _typed_and_left(page, "winter tyres on")
    assert _stays(page), "the page reloaded over a note that was not saved"
    assert page.input_value(NOTE) == "winter tyres on"


def test_the_page_reloads_again_once_the_note_is_saved(page):
    _typed_and_left(page, "winter tyres on")
    with page.expect_response("**/api/trips/6/note"):
        page.click("button[type=submit]:near(#trip-note-msg)")
    page.evaluate("document.activeElement.blur()")
    with page.expect_event("load"):
        page.clock.fast_forward("00:31")
    assert page.input_value(NOTE) == "winter tyres on", "the saved note is not the one the page shows"


def test_a_note_changed_while_its_save_is_on_its_way_is_still_unsent(page):
    held = []
    page.route("**/api/trips/6/note", lambda route: held.append(route))
    _typed_and_left(page, "winter tyres on")
    page.click("button[type=submit]:near(#trip-note-msg)")
    page.wait_for_function("document.querySelector('.htmx-request')")
    _typed_and_left(page, "winter tyres on, roof box")
    with page.expect_response("**/api/trips/6/note"):
        held[0].continue_()
    assert _stays(page), "the answer to the first save let the page reload over the text typed since"
    assert page.input_value(NOTE) == "winter tyres on, roof box"


def test_a_field_that_holds_its_drawn_value_in_its_own_form_is_not_an_edit(page):
    """A slider drawn with 10.0 holds 10: the same value, so the page goes on reloading; a moved slider is an edit."""
    page.evaluate("""() => document.querySelector('main').insertAdjacentHTML('beforeend',
        '<form hx-post="api/nowhere"><input type="range" name="amps" min="6" max="16" step="1" value="10.0"></form>')""")
    assert page.evaluate("lmUnsentEdits()") is False
    page.evaluate("document.querySelector('[name=amps]').value = 12")
    assert page.evaluate("lmUnsentEdits()") is True


_FORM = """([fields]) => { const old = document.getElementById('probe'); if (old) old.remove();
  document.querySelector('main').insertAdjacentHTML('beforeend', '<form id="probe" hx-post="api/nowhere">' + fields + '</form>') }"""


@pytest.mark.parametrize("fields", ['<select name="a"></select>',
                                    '<select name="a" multiple><option value="x">x<option value="y">y</select>',
                                    '<select name="a"><option value="x">x<option value="y">y</select>',
                                    '<input name="a" value="1" disabled>'],
                         ids=["a list without options", "a list of several choices", "a list with none marked", "a field that is off"])
def test_a_form_nobody_touched_holds_nothing(page, fields):
    page.evaluate(_FORM, [fields])
    page.evaluate("() => { const off = document.querySelector('#probe [disabled]'); if (off) off.value = '2' }")   # a script's doing
    assert page.evaluate("lmUnsentEdits()") is False


@pytest.mark.parametrize("fields, edit, sent", [
    ('<input type="checkbox" name="a" value="1" checked>', "f.a.checked = false", "{}"),
    ('<input name="a" value=""><input name="a" value="">', "f.a[0].value = 'p'; f.a[1].value = 'q'", "{a: ['p', 'q']}"),
    ('<select name="a" multiple><option value="x">x<option value="y">y</select>', "f.a.options[1].selected = true", "{a: 'y'}"),
    ('<select name="a" multiple><option value="x" selected>x<option value="y">y</select>', "f.a.options[0].selected = false", "{}"),
], ids=["a box unticked", "two fields of one name", "a list of several choices", "a list with its last choice taken off"])
def test_what_a_form_sent_is_no_longer_unsent(page, fields, edit, sent):
    page.evaluate(_FORM, [fields])
    page.evaluate("() => { const f = document.getElementById('probe'); %s }" % edit)
    assert page.evaluate("lmUnsentEdits()") is True
    page.evaluate("() => lmSaved(document.getElementById('probe'), %s)" % sent)
    assert page.evaluate("lmUnsentEdits()") is False
