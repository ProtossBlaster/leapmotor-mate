"""A charge row in a real browser: on a phone and on a computer nothing sticks out of it and the curve beside it is
a picture the browser drew; a click on the row opens the body under it and asks for the chart once, however often
the row is opened and closed; the editors the row keeps (the type menu, 🆓, 🧭, ✏️, the place picker, the station's
link) and a tooltip's mark (🛣) do their own work and leave the row closed, a word typed in their fields or a
click on their text included; Expand all, there while a row is closed, opens every row of the day, and Collapse
all, there while one is open, closes them; a link to a charge opens its row; a note is written in the opened
row's foot and read there, and a note changed while its save is on its way stays open and unsaved; a merge or a
split leaves the row of the charge open; an opened card's foot with every action fits a phone in four languages,
and a merged, reconstructed charge's times keep to their column. Measured here, because only a browser says what
a <summary> does with a click on a button inside it.
Skips where it cannot run (no playwright, no Chromium), like the other browser tests.
"""
import sqlite3

import pytest

pytest.importorskip("fastapi", reason="web/main.py needs fastapi (absent in the minimal CI env)")
pytest.importorskip("uvicorn", reason="the page has to be SERVED, not rendered in-process")
sync_api = pytest.importorskip("playwright.sync_api", reason="needs playwright + `playwright install chromium`")

from web_in_a_browser import COUNT_CALENDAR_SWAPS, chromium, seed_database, served

_CHARGE = ("INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc, energy_added_kwh, cost,"
           " duration_min, max_power_kw, latitude, longitude, location_type, location_name, location_url, note)"
           " VALUES (?, 1, ?, ?, 40, 60, 9, 2.5, 60, 7.2, 45.08, 7.69, 'AC', ?, ?, ?)")
_OFFICE = ("INSERT INTO charging_places (id, vehicle_id, name, latitude, longitude, radius_m, rate, enabled)"
           " VALUES (1, 1, 'Office', 45.2, 7.8, 100, 0.2, 1)")
_LONG_NOTE = "Cable left in the port overnight, the wallbox stopped at ninety per cent as scheduled, nothing else to say"


@pytest.fixture(scope="module")
def mate_db(tmp_path_factory):
    db = tmp_path_factory.mktemp("charge-row") / "leapmotor_mate.db"
    seed_database(db, "LVIN0000000000001", [
        ("INSERT INTO settings (key, value) VALUES ('timezone', 'UTC')", ()), (_OFFICE, ()),
        (_CHARGE, (1, "2026-07-03T08:00:00+00:00", "2026-07-03T09:00:00+00:00", None, None, None)),
        (_CHARGE, (2, "2026-07-03T17:00:00+00:00", "2026-07-03T18:00:00+00:00",
                   "Ionity Area di Servizio Rubicone Est", "https://example.invalid/station/1", _LONG_NOTE)),
        ("UPDATE charges SET odometer_km = 1000 + 180 * (id - 1) WHERE id IN (1, 2)", ()),   # 🛣 180 km on 2
        # 4 July: a charge, then a typed-in one merged with its continuation, close enough to offer one more
        # merge: the card with every action its foot can carry.
        (_CHARGE, (3, "2026-07-04T08:00:00+00:00", "2026-07-04T09:00:00+00:00", None, None, None)),
        (_CHARGE, (4, "2026-07-04T09:10:00+00:00", "2026-07-04T10:00:00+00:00", None, None, None)),
        (_CHARGE, (5, "2026-07-04T10:05:00+00:00", "2026-07-04T10:30:00+00:00", None, None, None)),
        ("UPDATE charges SET manual_entry = 1 WHERE id = 4", ()),
        ("UPDATE charges SET merged_into_id = 4 WHERE id = 5", ()),
        # 5 July: a reconstructed charge merged with its continuation, the most a row puts by its times.
        (_CHARGE, (6, "2026-07-05T21:00:00+00:00", "2026-07-05T22:00:00+00:00", None, None, None)),
        (_CHARGE, (7, "2026-07-05T22:05:00+00:00", "2026-07-05T23:00:00+00:00", None, None, None)),
        ("UPDATE charges SET reconstructed = 1 WHERE id = 6", ()),
        ("UPDATE charges SET start_soc = 60, end_soc = 70 WHERE id = 7", ()),
        ("UPDATE charges SET merged_into_id = 6 WHERE id = 7", ())])
    return db


@pytest.fixture(scope="module")
def mate(mate_db):
    with served(mate_db.parent, mate_db) as url:
        yield url


@pytest.fixture
def charge_1_kept(mate_db):
    """Charge 1 put back as seeded after the test: the editors change it on the server the whole file shares."""
    conn = sqlite3.connect(mate_db)
    cur = conn.execute("SELECT * FROM charges WHERE id = 1")
    cols, row = [d[0] for d in cur.description], cur.fetchone()
    conn.close()
    yield
    conn = sqlite3.connect(mate_db)
    conn.execute(f"UPDATE charges SET {', '.join(c + ' = ?' for c in cols)} WHERE id = 1", row)
    conn.commit()
    conn.close()


@pytest.fixture
def browser():
    pw, b = chromium(sync_api)
    try:
        yield b
    finally:
        b.close()
        pw.stop()


def _open(browser, url, width=1280, highlight=1):
    page = browser.new_page(viewport={"width": width, "height": 900})
    page.add_init_script(COUNT_CALENDAR_SWAPS)
    page.charts = []
    page.on("request", lambda r: "/power-chart" in r.url and page.charts.append(r.url))
    assert page.goto(f"{url}/charges?highlight={highlight}").status == 200
    # The calendar block draws itself again on load: a row touched before that swap is replaced.
    page.wait_for_function("window.settled >= 1")
    page.wait_for_load_state("networkidle")
    page.locator(f"#charge-card-{highlight} > summary").wait_for()
    # The link's row is scrolled to smoothly, inside the page's own scrolling column rather than the window;
    # a click sent while it still moves lands elsewhere.
    page.wait_for_function("""cid => new Promise(r => { const c = document.getElementById('charge-card-' + cid);
      const y = c.getBoundingClientRect().top; setTimeout(() => r(c.getBoundingClientRect().top === y), 200); })""",
                           arg=highlight)
    return page


def _is_open(page, cid):
    return page.locator(f"#charge-card-{cid}").evaluate("d => d.open")


_FITS = """() => [...document.querySelectorAll('details.charge-card > summary')].map(row => {
  const r = row.getBoundingClientRect();
  const out = [...row.querySelectorAll('*')].filter(e => e.offsetParent !== null)
    .map(e => e.getBoundingClientRect()).filter(b => b.width > 0 && (b.left < r.left - 0.5 || b.right > r.right + 0.5));
  return {scrolls: row.scrollWidth > row.clientWidth, out: out.length,
          locWidth: row.querySelector('.charge-location').getBoundingClientRect().width / r.width}; })"""


@pytest.mark.parametrize("width", [390, 1280])
def test_nothing_sticks_out_of_a_closed_row(browser, mate, width):
    page = _open(browser, mate, width)
    for cid in (1, 2):
        page.locator(f"#charge-card-{cid}").evaluate("d => { d.open = false; }")
    rows = page.evaluate(_FITS)
    assert len(rows) == 2
    for row in rows:
        assert not row["scrolls"] and row["out"] == 0, row
    drawn = page.eval_on_selector_all("img.charge-thumb", "imgs => imgs.map(i => i.complete && i.naturalWidth > 0)")
    assert drawn == [True, True], ("each row's curve is a picture the browser could draw", drawn)
    if width == 390:
        assert all(row["locWidth"] > 0.9 for row in rows), ("on a phone the 📍 line has the whole row", rows)
        tops = page.eval_on_selector_all("details.charge-card > summary .sm\\:hidden", """els => els.map(e =>
          [...e.querySelectorAll('span.whitespace-nowrap')].map(s => Math.round(s.getBoundingClientRect().top)))""")
        assert tops and all(len(set(t)) == 1 for t in tops), ("the battery's change stays beside its figures", tops)
    else:
        assert all(row["locWidth"] < 0.8 for row in rows), ("on a computer it stays in the middle column", rows)


_BESIDE_KWH = """() => [...document.querySelectorAll('details.charge-card > summary')].map(row => {
  const k = row.querySelector('.charge-kwh').getBoundingClientRect();
  return [...row.querySelectorAll('.charge-head *')].filter(e => e.offsetParent !== null && !e.children.length)
    .map(e => e.getBoundingClientRect())
    .filter(b => b.width > 0 && b.right > k.left + 0.5 && b.left < k.right && b.bottom > k.top && b.top < k.bottom).length; })"""


@pytest.mark.parametrize("lang", ["de", "pt-PT"])
@pytest.mark.parametrize("width", [360, 390])
def test_the_times_keep_out_of_the_kwh_on_a_phone(browser, mate, width, lang):
    """A reconstructed charge merged with its continuation: its times, 🔗 2 and ✨ wrap in their own column,
    in a long language on a narrow phone, and nothing leaves the row."""
    page = browser.new_page(viewport={"width": width, "height": 900})
    page.request.post(f"{mate}/api/settings/language", form={"language": lang})
    try:
        phone = _open(browser, mate, width=width, highlight=6)
        phone.locator("#charge-card-6").evaluate("d => { d.open = false; }")
        assert phone.evaluate(_BESIDE_KWH) == [0], "the times run into the kWh"
        assert [(r["scrolls"], r["out"]) for r in phone.evaluate(_FITS)] == [(False, 0)]
    finally:
        page.request.post(f"{mate}/api/settings/language", form={"language": "en"})


def test_a_split_and_a_merge_leave_the_charges_row_open(browser, mate):
    """Either draws the calendar again with every row closed; the row of the charge it leaves opens, with the
    way back in it. 6 and 7 are split, then joined again as they were."""
    page = _open(browser, mate, highlight=6)
    page.locator('#charge-card-6 button[hx-post*="unmerge?parent=6"]').click()
    page.locator("#mate-confirm-yes").click()
    page.locator("#charge-card-7").wait_for()
    page.wait_for_function("() => document.getElementById('charge-card-6').open")
    page.locator("#charge-card-7").evaluate("d => { d.open = true; }")
    page.locator("#charge-card-7 button[hx-get*='merge-preview']").click()
    page.locator("#charge-merge-modal button[hx-post*='charges/merge']").click()
    page.locator("#charge-card-7").wait_for(state="detached")
    page.wait_for_function("() => document.getElementById('charge-card-6').open")
    assert page.locator('#charge-card-6 button[hx-post*="unmerge?parent=6"]').is_visible()


def test_a_click_on_the_row_opens_it_and_asks_for_the_chart_once(browser, mate):
    page = _open(browser, mate, highlight=2)
    asked = lambda: [u for u in page.charts if u.endswith("/api/charge/1/power-chart")]
    assert not _is_open(page, 1) and asked() == []
    page.locator("#charge-card-1 > summary").click()
    assert _is_open(page, 1)
    page.locator("#pchart-1").scroll_into_view_if_needed()      # the chart is asked for once it is in view
    page.wait_for_function("() => document.querySelector('#pchart-1').innerText.trim() !== '…'")
    assert len(asked()) == 1
    page.locator("#charge-card-1 > summary").click()
    assert not _is_open(page, 1)
    page.locator("#charge-card-1 > summary").click()
    page.wait_for_timeout(300)
    assert _is_open(page, 1) and len(asked()) == 1


def test_a_link_to_a_charge_opens_its_row(browser, mate):
    page = _open(browser, mate, highlight=2)
    assert _is_open(page, 2) and not _is_open(page, 1)
    assert "inset" in page.locator("#charge-card-2").evaluate("d => d.style.boxShadow")


def test_the_editors_in_the_row_keep_their_own_click(browser, mate, charge_1_kept):
    page = _open(browser, mate, highlight=2)
    page.locator("#charge-card-2").evaluate("d => { d.open = false; }")

    road = page.locator("#charge-card-2 [data-tip]", has_text="🛣 180 km")
    road.click()                                                              # the tooltip's, not the row's
    assert not _is_open(page, 2)
    road.focus()
    for key in ("Enter", "Space"):                                            # Chromium sends them to the <summary>
        road.press(key)
        assert not _is_open(page, 2), key

    page.locator("#loc-1 button[hx-post$='/address']").click()               # 🧭 asks and, with no network, says so
    page.locator("#loc-1 [data-place-lookup-said]").wait_for()
    assert not _is_open(page, 1)

    page.locator("#loc-1 button", has_text="✏️").click()
    assert page.locator("#loc-manual-1").is_visible() and not _is_open(page, 1)
    page.locator("#loc-manual-1 input").press_sequentially("Colonnina Lingotto")   # key by key: a released Space
    assert not _is_open(page, 1)                                                     # is a summary's own key
    page.locator("#loc-manual-1 input").press("Enter")
    page.wait_for_function("() => document.querySelector('#loc-1').innerText.includes('Colonnina Lingotto')")
    assert not _is_open(page, 1)

    page.locator("#place-1 button").click()                                   # the place picker, a <select> in the row
    page.locator("#place-1 select").select_option("1")
    page.locator("#place-1 button[type=submit]").click()
    page.locator("#place-1 select").wait_for(state="detached")
    assert "Office ·" in page.locator("#place-1").inner_text() and not _is_open(page, 1)

    page.locator("#cm-1 > button").click()                                    # the price's ✎ box and its words
    page.locator("#cm-form-1 > div").click()
    assert page.locator("#cm-form-1").is_visible() and not _is_open(page, 1)
    page.locator("#cm-form-1 button", has_text="Cancel").click()
    page.locator('#charge-type-1 > div > button[onclick*="ct-menu-1"]').click()   # the type badge opens its menu
    assert page.locator("#ct-menu-1").is_visible() and not _is_open(page, 1)
    page.locator('#ct-menu-1 form[hx-post$="/cost"] span').click()            # the menu's ✎ Manual, plain text
    assert page.locator("#ct-menu-1").is_visible() and not _is_open(page, 1)
    page.locator('#ct-menu-1 form[hx-post$="/type"] input[value="HOME"] + button').click()
    page.locator("#charge-type-1 form[hx-post$='/free']").wait_for()         # a home charge offers 🆓
    assert "Home" in page.locator('#charge-type-1 > div > button[onclick*="ct-menu-1"]').inner_text() and not _is_open(page, 1)

    page.locator("#charge-type-1 form[hx-post$='/free'] button").click()
    page.wait_for_function("() => document.querySelector(\"#charge-type-1 form[hx-post$='/free'] button\").innerText.includes('✓')")
    assert not _is_open(page, 1)

    with page.expect_popup():                                                 # the station's link opens its page
        page.locator("#loc-2 a[target=_blank]").click()
    assert not _is_open(page, 2)


def test_expand_all_while_a_row_is_closed_collapse_all_while_one_is_open(browser, mate):
    page = _open(browser, mate, highlight=2)
    expand = page.locator("#charges-day-drawer [data-charges-all=open]")
    collapse = page.locator("#charges-day-drawer [data-charges-all=close]")
    shown = lambda: (expand.is_visible(), collapse.is_visible())
    page.wait_for_function("() => !document.querySelector('#charges-day-drawer [data-charges-all=close]').hidden")
    assert _is_open(page, 2) and not _is_open(page, 1) and shown() == (True, True), "the link's row came open"
    expand.click()
    assert _is_open(page, 1) and _is_open(page, 2) and shown() == (False, True)
    collapse.click()
    assert not _is_open(page, 1) and not _is_open(page, 2) and shown() == (True, False)
    # A row opened by hand, clicked on its padding (the middle of a row may be one of its buttons): Collapse
    # all comes beside Expand all after the browser's own toggle event, so it is awaited.
    page.locator("#charge-card-1 > summary").click(position={"x": 6, "y": 6})
    collapse.wait_for()
    assert _is_open(page, 1) and shown() == (True, True)
    page.locator("#charge-card-2 > summary").click(position={"x": 6, "y": 6})   # the last one: nothing left to open
    expand.wait_for(state="hidden")
    assert shown() == (False, True)
    page.locator("#charge-card-1 > summary").click(position={"x": 6, "y": 6})
    page.locator("#charge-card-2 > summary").click(position={"x": 6, "y": 6})   # all closed by hand
    collapse.wait_for(state="hidden")
    assert shown() == (True, False)


def test_a_note_is_written_and_read_in_the_opened_row(browser, mate):
    page = _open(browser, mate)                                              # the link opens charge 1's row
    assert _is_open(page, 1)
    line, button, form = page.locator("#cnote-line-1"), page.locator("#cnote-edit-1"), page.locator("#cnote-form-1")
    assert line.is_hidden() and button.inner_text() == "📝 Add a note" and form.is_hidden()
    button.click()
    assert form.is_visible() and _is_open(page, 1)
    page.locator("#cnote-1").fill("Shaded spot")
    page.locator("#cnote-form-1 button[type=submit]").click()
    page.wait_for_function("() => document.querySelector('#cnote-line-1').innerText === '📝 Shaded spot'")
    assert line.is_visible() and button.inner_text() == "✏️" and form.is_hidden(), "the field closes on a save"
    button.click()
    page.locator("#cnote-1").fill("Sunny spot, typed by mistake")
    page.locator("#cnote-form-1 button", has_text="Cancel").click()          # Cancel: nothing saved, the field closes
    assert form.is_hidden() and line.inner_text() == "📝 Shaded spot"
    button.click()
    assert page.locator("#cnote-1").input_value() == "Shaded spot", "the field holds the saved note again"
    page.locator("#cnote-1").fill("")
    page.locator("#cnote-form-1 button[type=submit]").click()
    page.wait_for_function("() => document.querySelector('#cnote-line-1').hidden")
    assert button.inner_text() == "📝 Add a note" and form.is_hidden()


def test_a_note_changed_while_its_save_is_on_its_way_stays_open_and_unsaved(browser, mate):
    page = _open(browser, mate)
    held = []
    page.route("**/api/charges/1/note", lambda route: held.append(route))
    try:
        page.locator("#cnote-edit-1").click()
        page.locator("#cnote-1").fill("Submitted note")
        page.locator("#cnote-form-1 button[type=submit]").click()
        for _ in range(100):                                                   # the save, held on its way
            if held:
                break
            page.wait_for_timeout(20)
        page.locator("#cnote-1").fill("Later unsaved edit")
        held[0].continue_()
        page.wait_for_function("() => document.querySelector('#cnote-line-1').innerText === '📝 Submitted note'")
        assert page.locator("#cnote-form-1").is_visible(), "the field with unsaved text stays open"
        assert page.locator("#cnote-1").input_value() == "Later unsaved edit"
        again = _open(browser, mate)
        assert again.locator("#cnote-line-1").inner_text() == "📝 Submitted note", "what was saved is what was sent"
    finally:
        page.request.post(f"{mate}/api/charges/1/note", form={"note": ""})


_FOOT_FITS = """id => {
  const body = document.querySelector(`#charge-card-${id} .charge-body`), r = body.getBoundingClientRect();
  // The note line cuts its text with an ellipsis: the line is measured, not the text inside it.
  return [...body.querySelectorAll('*')].filter(e => e.offsetParent !== null && !e.closest('.charge-note-line > *'))
    .map(e => [e.tagName, e.textContent.trim().slice(0, 30), e.getBoundingClientRect()])
    .filter(([, , b]) => b.width > 0 && (b.left < r.left - 0.5 || b.right > r.right + 0.5))
    .map(([tag, text, b]) => `${tag} ${text} ${Math.round(b.left)}..${Math.round(b.right)} of ${Math.round(r.right)}`); }"""


@pytest.mark.parametrize("lang", ["en", "de", "pl", "pt-PT"])
def test_an_opened_cards_foot_with_every_action_fits_a_phone(browser, mate, lang):
    """A typed-in, merged charge that can merge once more carries all four actions; on a phone in a long
    language they wrap under the note, with a note and without one, and nothing leaves the card."""
    page = browser.new_page(viewport={"width": 390, "height": 900})
    page.request.post(f"{mate}/api/settings/language", form={"language": lang})
    try:
        for note in ("", _LONG_NOTE):
            page.request.post(f"{mate}/api/charges/4/note", form={"note": note})
            phone = _open(browser, mate, width=390, highlight=4)
            assert _is_open(phone, 4)
            assert phone.locator("#charge-card-4 .charge-actions > button").count() == 4, "merge, split, edit, delete"
            assert phone.evaluate(_FOOT_FITS, 4) == [], (lang, bool(note))
    finally:
        page.request.post(f"{mate}/api/charges/4/note", form={"note": ""})
        page.request.post(f"{mate}/api/settings/language", form={"language": "en"})
