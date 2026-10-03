"""A long range is sent in parts, the next one loaded as the end of the list comes into view.

The whole range is composed and its tracks laid out first, so a part is a slice of the same lines:
the parts put together are the list, the count is the whole range's from the first part, and the
line that loads the next part carries the tracks running across the cut. A part after the first is
only its lines, and neither it nor the links in it put the part into the page's URL. A part asked
for after the list changed (a row written meanwhile, a state that ended) would not join the parts on
the page, so the list is sent again whole instead.
"""
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import db_reader
import pytest
from events_fixture import Car, event_row, grouped, serve, web

ZONE = ZoneInfo("Europe/Warsaw")
DAY = datetime.now(ZONE).date() - timedelta(days=1)


def _local(hh, mm=0):
    return datetime(DAY.year, DAY.month, DAY.day, hh, mm, tzinfo=ZONE).astimezone(timezone.utc)


def _seed(car, pairs=10):
    """A cable plugged in all day and tailgate openings apart under it: one track crosses every cut."""
    event_row(car, "cable", _local(6))
    for k in range(pairs):
        event_row(car, "trunk", _local(7, 0) + timedelta(minutes=50 * k))
        event_row(car, "trunk", _local(7, 2) + timedelta(minutes=50 * k), state=0)
    event_row(car, "cable", _local(23), state=0)


def _query():
    return {"date_from": DAY.isoformat(), "date_to": DAY.isoformat()}


def _shape(line):
    return line["kind"], line["e"]["anchor"] if line["kind"] == "row" else line["label"], line["cells"]


def test_the_parts_put_together_are_the_list(tmp_path, monkeypatch):
    car = Car(tmp_path)
    web(car, monkeypatch)
    _seed(car)
    whole = grouped(**_query())
    monkeypatch.setattr(db_reader, "EVENTS_PART_ROWS", 8)
    parts = [grouped(**_query(), part=k) for k in range(4)]
    assert [sum(line["kind"] == "row" for line in p["lines"]) for p in parts] == [8, 8, 6, 0]
    assert [_shape(line) for p in parts for line in p["lines"]] == [_shape(line) for line in whole["lines"]]
    assert {p["count"] for p in parts} == {whole["count"]} == {22} and {p["lanes"] for p in parts} == {2}
    assert parts[2]["more"] is None and whole["more"] is None
    assert parts[0]["more"] == parts[1]["more"] == ["v-doors", "v-charging"], \
        "each cut falls inside a tailgate's pair and inside the cable's"


def test_a_later_part_is_only_its_lines_and_keeps_the_url(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    _seed(car)
    monkeypatch.setattr(db_reader, "EVENTS_PART_ROWS", 4)
    query = f"f=1&group=doors&group=charging&date_from={DAY}&date_to={DAY}"
    first = client.get(f"/api/events/search?{query}")
    assert first.headers["hx-replace-url"] == f"events?{query}"
    v = re.search(r'hx-get="api/events/search\?[^"]*&amp;v=(\w+)&amp;part=1" hx-trigger="intersect once"', first.text).group(1)
    assert f'hx-get="api/events/search?{query.replace("&", "&amp;")}&amp;v={v}&amp;part=1"' in first.text
    second = client.get(f"/api/events/search?{query}&v={v}&part=1")
    assert "hx-replace-url" not in second.headers
    assert "🔍" not in second.text and second.text.count('class="event-row"') == 4
    assert "part=1" not in second.text and f"{query.replace('&', '&amp;')}&amp;v={v}&amp;part=2" in second.text
    page = client.get(f"/events?{query}&part=1").text
    assert page.count('class="event-row"') == 4 and "🔍 22 " in page, "the page itself always opens on the first part"


def test_scrolling_to_the_end_loads_the_rest(tmp_path, monkeypatch):
    pw = pytest.importorskip("playwright.sync_api")
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    _seed(car, pairs=14)
    monkeypatch.setattr(db_reader, "EVENTS_PART_ROWS", 10)
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 700})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("**/*", serve(client))
        page.goto(f"http://mate.test/events?date_from={DAY}&date_to={DAY}")
        url = page.url
        assert page.locator(".event-row").count() == 10
        for rows in (20, 30):
            page.locator(".ev-more").scroll_into_view_if_needed()
            page.wait_for_function(f"() => document.querySelectorAll('.event-row').length === {rows}")
        assert page.locator(".ev-more").count() == 0 and page.url == url
        gaps = page.evaluate("""() => [...document.querySelectorAll('.ev-list > div')].map(line =>
            [...line.querySelectorAll('.ev-l > i:not(.dot)')].pop().getBoundingClientRect())
            .map((c, k, all) => k ? Math.abs(c.top - all[k - 1].bottom) : 0)""")
        assert max(gaps) < 0.5, "the tracks run on across the parts"
        assert errors == []
        browser.close()


def test_a_part_after_the_list_changed_sends_the_list_again(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    _seed(car)
    monkeypatch.setattr(db_reader, "EVENTS_PART_ROWS", 4)
    query = f"date_from={DAY}&date_to={DAY}"
    v = re.search(r'&amp;v=(\w+)&amp;part=1"', client.get(f"/api/events/search?{query}").text).group(1)
    event_row(car, "trunk", _local(23, 30))                       # written meanwhile, at the top
    again = client.get(f"/api/events/search?{query}&v={v}&part=1")
    assert again.headers["hx-retarget"] == "#events-list" and again.headers["hx-reswap"] == "innerHTML"
    assert "hx-replace-url" not in again.headers and "🔍 23 " in again.text
    assert again.text.count('class="event-row"') == 4 and 'id="ev-signal-23"' in again.text, "its first part, anew"
    fresh = re.search(r'&amp;v=(\w+)&amp;part=1"', again.text).group(1)
    assert fresh != v and "hx-retarget" not in client.get(f"/api/events/search?{query}&v={fresh}&part=1").headers


def test_rows_written_while_reading_leave_no_row_twice_and_no_gap(tmp_path, monkeypatch):
    pw = pytest.importorskip("playwright.sync_api")
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    _seed(car, pairs=14)
    event_row(car, "climate", _local(5))                          # still on when the page opens
    monkeypatch.setattr(db_reader, "EVENTS_PART_ROWS", 10)
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 700})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("**/*", serve(client))
        page.goto(f"http://mate.test/events?date_from={DAY}&date_to={DAY}")
        assert page.locator(".event-row").count() == 10
        event_row(car, "climate", _local(23, 30), state=0)        # the state ends, a row at the top
        event_row(car, "trunk", _local(7, 25))                    # and a pair in the middle, across a cut
        event_row(car, "trunk", _local(7, 26), state=0)
        page.wait_for_function("""() => { const more = document.querySelector('.ev-more');
            if (more) more.scrollIntoView(); return !more && document.querySelectorAll('.event-row').length === 34; }""")
        ids = page.locator(".event-row").evaluate_all("rs => rs.map(r => r.id)")
        assert len(ids) == len(set(ids)) == 34 and ids[0] == "ev-signal-32"
        gaps = page.evaluate("""() => [...document.querySelectorAll('.ev-list > div')].map(line =>
            [...line.querySelectorAll('.ev-l > i:not(.dot)')].pop().getBoundingClientRect())
            .map((c, k, all) => k ? Math.abs(c.top - all[k - 1].bottom) : 0)""")
        assert max(gaps) < 0.5
        cells = page.locator(".ev-list > div").evaluate_all("ls => ls.map(l => [...l.querySelectorAll('.ev-l > i:not(.dot)')].map(c => c.className))")
        now = [line["cells"] for k in range(4) for line in grouped(**_query(), part=k)["lines"]]
        assert cells == now, "the tracks of the list as it is now"
        assert errors == []
        browser.close()


def test_a_pair_lit_across_the_cut_is_lit_in_the_parts_loaded_after(tmp_path, monkeypatch):
    pw = pytest.importorskip("playwright.sync_api")
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    _seed(car, pairs=14)
    monkeypatch.setattr(db_reader, "EVENTS_PART_ROWS", 10)
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 700})
        page.route("**/*", serve(client))
        page.goto(f"http://mate.test/events?date_from={DAY}&date_to={DAY}")
        page.locator("#ev-signal-30 .dot").click()                    # the cable's end; its start two parts on
        page.wait_for_function("""() => { const more = document.querySelector('.ev-more');
            if (more) more.scrollIntoView(); return !more; }""")
        page.locator("#ev-signal-1.ev-pair").wait_for(timeout=3000)               # once the last part settled
        assert page.locator(".event-row.ev-pair").evaluate_all("rs => rs.map(r => r.id)") == ["ev-signal-30", "ev-signal-1"]
        gaps = page.locator(".ev-list > div").evaluate_all("""ls => ls.slice(ls.findIndex(l => l.id === 'ev-signal-30'),
            ls.findIndex(l => l.id === 'ev-signal-1') + 1).filter(l => !l.querySelector('.ev-l .sel')).length""")
        assert gaps == 0, "its line lit all the way"
        browser.close()
