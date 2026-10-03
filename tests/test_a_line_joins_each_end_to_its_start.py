"""A line joins each end on the list to its start, as in a graph of git history.

Left of the times run the tracks: a pair turns off its track to the dot of its upper row (╭, the
`n-` cell) and of its lower row (╰, `u-`), runs straight (`v-`) through every line between them,
day headings and hour separators included, and crosses (`h-`) the tracks nearer the dots on its way
to a dot. Each pair takes the lowest track free over its rows, so pairs apart share track 0 and
there are as many tracks as states open at once. A state still on runs to the top of the list; an
end whose start is before the days shown runs to the bottom, faded (`w-`), and names its start in a
chip. A start the word filter hid gets no line, only the chip.
"""
import re
from datetime import datetime, timedelta, timezone
from itertools import pairwise
from zoneinfo import ZoneInfo

import pytest
from events_fixture import Car, event_row, grouped, row_text, serve, web

ZONE = ZoneInfo("Europe/Warsaw")
DAY = datetime.now(ZONE).date() - timedelta(days=1)


def _local(hh, mm=0, day=DAY):
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=ZONE).astimezone(timezone.utc)


def _picture(**query):
    """Each line of the list as (what it is, its track cells left to right), and the track count."""
    ev = grouped(date_from=DAY.isoformat(), date_to=DAY.isoformat(), **query)
    return [(line["e"]["anchor"] if line["kind"] == "row" else line["kind"], line["cells"]) for line in ev["lines"]], ev["lanes"]


def test_a_tailgate_open_for_two_minutes_is_one_track(tmp_path, monkeypatch):
    car = Car(tmp_path)
    web(car, monkeypatch)
    event_row(car, "trunk", _local(13, 59))
    event_row(car, "trunk", _local(14, 1), state=0)
    lines, lanes = _picture()
    assert lanes == 1
    assert lines == [("day", [""]), ("hour", [""]), ("ev-signal-2", ["n-doors"]), ("hour", ["v-doors"]),
                     ("ev-signal-1", ["u-doors"])]


def test_two_states_at_once_take_two_tracks_and_cross(tmp_path, monkeypatch):
    car = Car(tmp_path)
    web(car, monkeypatch)
    event_row(car, "cable", _local(13))
    event_row(car, "trunk", _local(14))
    event_row(car, "trunk", _local(14, 30), state=0)
    event_row(car, "cable", _local(15), state=0)
    lines, lanes = _picture()
    assert lanes == 2
    assert [cells for what, cells in lines if what != "day"] == [
        ["", ""],                                  # 15:00
        ["", "n-charging"],                        # cable unplugged: the first pair takes track 0
        ["", "v-charging"],                        # 14:00
        ["n-doors", "v-charging h-doors"],         # the tailgate's track crosses the cable's to reach its dots
        ["u-doors", "v-charging h-doors"],
        ["", "v-charging"],                        # 13:00
        ["", "u-charging"],
    ]


def test_pairs_apart_share_the_first_track(tmp_path, monkeypatch):
    car = Car(tmp_path)
    web(car, monkeypatch)
    for hh in (10, 12, 16):
        event_row(car, "trunk", _local(hh))
        event_row(car, "trunk", _local(hh, 5), state=0)
    _, lanes = _picture()
    assert lanes == 1


def test_a_state_still_on_runs_to_the_top(tmp_path, monkeypatch):
    car = Car(tmp_path)
    web(car, monkeypatch)
    event_row(car, "cable", _local(10))
    lines, _ = _picture()
    assert lines == [("day", ["v-charging"]), ("hour", ["v-charging"]), ("ev-signal-1", ["u-charging"])]


def test_an_end_whose_start_is_before_the_days_shown_runs_faded_to_the_bottom(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    event_row(car, "cable", _local(18, 5, day=DAY - timedelta(days=2)))
    event_row(car, "trunk", _local(7))
    event_row(car, "trunk", _local(7, 2), state=0)
    event_row(car, "cable", _local(8), state=0)
    lines, lanes = _picture()
    assert lanes == 2
    assert lines == [("day", ["", ""]), ("hour", ["", ""]), ("ev-signal-4", ["", "n-charging"]),
                     ("hour", ["", "w-charging"]), ("ev-signal-3", ["n-doors", "w-charging h-doors"]),
                     ("ev-signal-2", ["u-doors", "w-charging h-doors"])]
    html = client.get(f"/events?date_from={DAY}&date_to={DAY}").text
    assert re.search(r'<span class="ev-chip ev-wrap">from \S+\xa0\S+\xa0\d{4} 18:05:00</span>', html)


def test_an_end_whose_start_is_listed_names_no_start(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    event_row(car, "trunk", _local(14, 19))
    event_row(car, "trunk", _local(14, 20), state=0)
    html = client.get(f"/events?date_from={DAY}&date_to={DAY}").text
    assert row_text(html, "ev-signal-2") == "Tailgate closed · 1 min"
    assert html.count('<i class="dot d-doors j"></i>') == 2, "both dots joined by the line"


def test_a_start_the_word_hid_gets_no_line(tmp_path, monkeypatch):
    """Ten tailgate openings apart and the word "closed": ten ends without their starts. Running
    each to the bottom would take ten tracks for nothing."""
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    for k in range(10):
        event_row(car, "trunk", _local(8 + k))
        event_row(car, "trunk", _local(8 + k, 2), state=0)
    lines, lanes = _picture(q="closed")
    assert lanes == 0 and len([w for w, _ in lines if w.startswith("ev-")]) == 10
    html = client.get(f"/events?date_from={DAY}&date_to={DAY}&q=closed").text
    assert html.count('class="ev-chip ev-wrap">from ') == 10 and html.count('class="dot d-doors"') == 10


def test_the_track_runs_unbroken_through_a_wrapped_row_and_a_days_heading(tmp_path, monkeypatch):
    pw = pytest.importorskip("playwright.sync_api")
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    event_row(car, "cable", _local(21, day=DAY - timedelta(days=1)), soc=40.0)
    event_row(car, "climate", _local(7, 40), inside_temp=17.0, climate_target_temp=21.0, outside_temp=8.0)
    event_row(car, "climate", _local(7, 55), state=0, inside_temp=21.5)
    event_row(car, "cable", _local(9), state=0, soc=80.0)
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 800})
        page.route("**/*", serve(client))
        page.goto(f"http://mate.test/events?date_from={DAY - timedelta(days=1)}&date_to={DAY}")
        boxes = page.evaluate("""() => [...document.querySelectorAll('.ev-list > div')].map(line => {
            const track0 = [...line.querySelectorAll('.ev-l > i:not(.dot)')].pop();
            const cell = track0.getBoundingClientRect(), box = line.getBoundingClientRect();
            return [line.className, box.top, box.bottom, cell.top, cell.bottom, getComputedStyle(track0).backgroundImage];
        })""")
        browser.close()
    assert [b[0] for b in boxes] == ["ev-day", "event-hour", "event-row", "event-hour", "event-row", "event-row",
                                     "ev-day", "event-hour", "event-row"]
    climate_on = boxes[5]
    assert climate_on[2] - climate_on[1] > 30, "the climate start wraps onto a second line at 390 px"
    for (_, top, bottom, ctop, cbottom, _), nxt in pairwise(boxes):
        assert (ctop, cbottom) == (top, bottom), "a cell is as tall as its line"
        assert abs(nxt[3] - cbottom) < 0.5, "and meets the next line's cell"
    assert all("linear-gradient" in b[5] for b in boxes[3:8]), "the cable's track is drawn through them all"


def test_a_lit_pair_is_one_block_across_the_hour_between_its_rows(tmp_path, monkeypatch):
    pw = pytest.importorskip("playwright.sync_api")
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    event_row(car, "trunk", _local(13, 59))
    event_row(car, "trunk", _local(14, 1), state=0)
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.route("**/*", serve(client))
        page.goto(f"http://mate.test/events?date_from={DAY}&date_to={DAY}")
        page.locator("#ev-signal-2 .dot").click()
        lines = page.evaluate("""() => [...document.querySelectorAll('.ev-list > div')].slice(2).map(l => {
            const s = getComputedStyle(l);
            return [l.id || l.className, l.classList.contains('ev-pair'), s.borderTopLeftRadius, s.borderBottomLeftRadius]; })""")
        browser.close()
    assert lines == [["ev-signal-2", True, "6px", "0px"], ["event-hour ev-pair", True, "0px", "0px"],
                     ["ev-signal-1", True, "0px", "6px"]], "one block, rounded at its ends"


def test_a_lit_pair_thickens_its_own_line_not_the_one_it_crosses(tmp_path, monkeypatch):
    """The tailgate's line reaches its dots across the cable's track: the crossing cell holds both
    lines, and only the tailgate's horizontal is drawn thicker there."""
    pw = pytest.importorskip("playwright.sync_api")
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    event_row(car, "cable", _local(13))
    event_row(car, "trunk", _local(14))
    event_row(car, "trunk", _local(14, 30), state=0)
    event_row(car, "cable", _local(15), state=0)
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.route("**/*", serve(client))
        page.goto(f"http://mate.test/events?date_from={DAY}&date_to={DAY}")
        sizes = """id => [...document.querySelectorAll('#' + id + ' .ev-l > i:not(.dot)')].map(c =>
            [c.className.replace(/ ?sel\\w*/g, ''), getComputedStyle(c).backgroundSize])"""
        assert page.evaluate(sizes, "ev-signal-3") == [["n-doors", "2px 100%, 100% 2px"],
                                                       ["v-charging h-doors", "2px 100%, 100% 2px"]]
        page.locator("#ev-signal-3 .dot").click()
        assert page.evaluate(sizes, "ev-signal-3") == [["n-doors", "4px 100%, 100% 2px"],
                                                       ["v-charging h-doors", "2px 100%, 100% 4px"]], \
            "the cable's vertical stays thin, the tailgate's horizontal is thick"
        assert page.evaluate(sizes, "ev-signal-4")[1][1] == "2px 100%, 100% 2px", "the cable's own row is not lit"
        browser.close()
