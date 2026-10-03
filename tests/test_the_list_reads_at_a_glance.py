"""The Events list reads at a glance: days, hours, groups, places, times to the second, and an end
that leads to its start.

One card holds the list: a heading per day and, weaker, a separator naming each hour before its
first row. A row's dot wears its group's colour, the colour of the group's pill; a row at one of
the owner's charging places names it. The time is the car's, to the second, and its tooltip names
it beside the time Mate wrote the row down (with the date only when that is another day); a state
shorter than a minute lasts "35s". An end whose start is not on the list names it in a chip, with
its day in the words the language puts before a date. On a phone a row's short parts stay whole.
A click on a line or a dot lights that pair, the line and its two rows, and moves nothing: the
reader scrolls.
"""
import pathlib
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import db_reader
import pytest
from events_fixture import Car, event_row, row_text, serve, web

ZONE = ZoneInfo("Europe/Warsaw")
DAY = datetime.now(ZONE).date() - timedelta(days=1)
HOME = (52.2297, 21.0122)


def _local(hh, mm=0):
    return datetime(DAY.year, DAY.month, DAY.day, hh, mm, tzinfo=ZONE).astimezone(timezone.utc)


def _day_page(client, query=""):
    return client.get(f"/events?date_from={DAY}&date_to={DAY}{query}").text


def test_each_hour_is_named_once_before_its_first_row(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    for hh, mm in ((14, 5), (14, 40), (13, 10)):
        event_row(car, "trunk", _local(hh, mm))
        event_row(car, "trunk", _local(hh, mm + 1), state=0)
    html = _day_page(client)
    assert re.findall(r'class="event-hour"><span class="ev-l">.*?</span><span>([^<]+)</span>', html) == ["14:00", "13:00"]
    assert html.index(">14:00<") < html.index('id="ev-signal-1"') < html.index(">13:00<") < html.index('id="ev-signal-5"')


def test_one_card_holds_the_days_and_hours_in_order(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    before = DAY - timedelta(days=1)
    event_row(car, "trunk", _local(9))
    event_row(car, "trunk", _local(9, 1), state=0)
    event_row(car, "unlocked", datetime(before.year, before.month, before.day, 22, tzinfo=ZONE).astimezone(timezone.utc))
    event_row(car, "unlocked", datetime(before.year, before.month, before.day, 22, 5, tzinfo=ZONE).astimezone(timezone.utc),
              state=0)
    html = client.get(f"/events?date_from={before}&date_to={DAY}").text
    assert html.count('class="card p-0 overflow-hidden ev-list"') == 1
    lines = re.findall(r'<div (?:id="(ev-[\w-]+)" class="event-row"|class="(ev-day|event-hour)"><span class="ev-l">.*?</span><span>([^<]+))',
                       html)
    assert [a or text for a, _, text in lines] == [
        lines[0][2], "09:00", "ev-signal-2", "ev-signal-1", lines[4][2], "22:00", "ev-signal-4", "ev-signal-3"]
    assert lines[0][2].endswith(DAY.strftime("%Y")) and lines[0][1] == "ev-day" and lines[4][1] == "ev-day"


def test_a_row_carries_its_groups_colour_and_its_place(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    car.db._conn.execute("INSERT INTO charging_places (vehicle_id, name, latitude, longitude, radius_m, rate)"
                         " VALUES (?, 'Dom', ?, ?, 100, 0.6)", (car.vid, *HOME))
    car.db._conn.commit()
    event_row(car, "cable", _local(18), latitude=HOME[0] + 0.0004, longitude=HOME[1])      # ~45 m away
    event_row(car, "trunk", _local(19), latitude=HOME[0] + 0.01, longitude=HOME[1])        # ~1.1 km away
    html = _day_page(client)
    color = db_reader.EVENT_GROUP_COLORS["charging"]
    assert re.search(r'<div id="ev-signal-1" class="event-row"[^>]*>.*?class="dot d-charging[ "]', html)
    assert f".n-charging, .u-charging, .d-charging {{ --k: {color}; }}" in html
    assert row_text(html, "ev-signal-1") == "Cable connected · Dom"
    assert "Dom" not in row_text(html, "ev-signal-2")
    assert re.search(rf'value="charging".*?inset 3px 0 0 {color}', html, re.DOTALL), "the pill wears the same colour"


def test_the_time_is_the_cars_to_the_second_and_its_tooltip_names_both(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    frame = _local(14, 19) + timedelta(seconds=7)
    event_row(car, "trunk", frame + timedelta(seconds=2), frame_ts=int(frame.timestamp() * 1000))
    event_row(car, "trunk", _local(14, 30), state=0)                       # no car clock on this frame: Mate's
    car.db._conn.execute("INSERT INTO trips (vehicle_id, started_at, ended_at) VALUES (?, ?, ?)",
                         (car.vid, _local(15).isoformat(), _local(15, 20).isoformat()))
    car.db._conn.commit()
    late = _local(23, 59) + timedelta(seconds=50)
    event_row(car, "cable", late + timedelta(hours=2), frame_ts=int(late.timestamp() * 1000))    # written the next day
    html = _day_page(client)
    assert '<time title="Car event: 14:19:07 · Recorded by Mate: 14:19:09">14:19:07</time>' in html
    assert '<time title="Car event: 14:30:00 · Recorded by Mate: 14:30:00">14:30:00</time>' in html
    assert '<time title="Recorded by Mate: 15:00:00">15:00:00</time>' in html, "a trip has one clock"
    next_day = db_reader.i18n.fmt_day_month_year("en", DAY + timedelta(days=1))
    assert f'<time title="Car event: 23:59:50 · Recorded by Mate: {next_day} 01:59:50">23:59:50</time>' in html, \
        "Mate's clock names its day where it wrote on another"


@pytest.mark.parametrize("lang", ["en", "pl"])
def test_a_state_shorter_than_a_minute_lasts_seconds(tmp_path, monkeypatch, lang):
    car = Car(tmp_path)
    client = web(car, monkeypatch, lang=lang)
    event_row(car, "trunk", _local(14, 19))
    event_row(car, "trunk", _local(14, 19) + timedelta(seconds=35), state=0)
    event_row(car, "unlocked", _local(16))
    event_row(car, "unlocked", _local(16) + timedelta(seconds=90), state=0)
    html = _day_page(client)
    assert row_text(html, "ev-signal-2").endswith(" · 35s")
    assert row_text(html, "ev-signal-4").endswith(" · 2 min")


@pytest.mark.parametrize("lang, chip", [("en", "from {day} 08:00:00"), ("es", "desde el {day} a las 08:00:00")])
def test_a_start_off_the_list_is_named_in_a_chip(tmp_path, monkeypatch, lang, chip):
    car = Car(tmp_path)
    client = web(car, monkeypatch, lang=lang)
    event_row(car, "cable", _local(8) - timedelta(days=2))
    event_row(car, "cable", _local(8) + timedelta(seconds=5), state=0)
    event_row(car, "trunk", _local(9))
    event_row(car, "trunk", _local(9, 3), state=0)
    html = _day_page(client)
    began = db_reader.i18n.fmt_day_month_year(lang, DAY - timedelta(days=2)).replace(" ", "\xa0")   # a date stays whole
    assert f'<span class="ev-chip ev-wrap">{chip.format(day=began)}</span>' in html
    assert row_text(html, "ev-signal-4").endswith(" · 3 min"), "a start on the list is joined by a line, not named"


def test_a_start_the_word_hides_is_named_in_a_chip(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    event_row(car, "trunk", _local(14, 19))
    event_row(car, "trunk", _local(14, 20), state=0)
    html = client.get(f"/events?f=1&group=doors&date_from={DAY}&date_to={DAY}").text
    assert "#ev-signal-1" not in html, "nothing on a row leads away from where the reader is"
    html = client.get(f"/events?f=1&group=doors&date_from={DAY}&date_to={DAY}&q=closed").text
    assert '<span class="ev-chip ev-wrap">from 14:19:00</span>' in html


@pytest.mark.parametrize("lang", ["pl", "pt-PT", "es"])
def test_a_rows_parts_stay_whole_and_inside_the_card_on_a_phone(tmp_path, monkeypatch, lang):
    """A short part ("na postoju", "Kabina 14 → 20,5 °C", a figure) never breaks in two; a place, a
    command, the cable's wait or a start on another day ("desde el 01 oct 2026 a las 08:00:00") may
    wrap, a duration, a date and a time staying whole; nothing runs past the card."""
    pw = pytest.importorskip("playwright.sync_api")
    car = Car(tmp_path)
    client = web(car, monkeypatch, lang=lang)
    event_row(car, "cable", _local(1))
    car.db._conn.execute("INSERT INTO charges (vehicle_id, started_at, ended_at, energy_added_kwh) VALUES (?, ?, ?, 29.9)",
                         (car.vid, _local(7, 41).isoformat(), _local(8, 20).isoformat()))
    car.db._conn.commit()
    event_row(car, "cable", _local(8, 26), state=0, soc=89.9)
    event_row(car, "climate", _local(9, 53), climate_target_temp=21.0, outside_temp=11.0, inside_temp=14.0)
    event_row(car, "climate", _local(10, 2), state=0, inside_temp=20.5)
    event_row(car, "trunk", _local(8) - timedelta(days=2))
    event_row(car, "trunk", _local(11), state=0)
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 800})
        page.route("**/*", serve(client))
        page.goto(f"http://mate.test/events?date_from={DAY}&date_to={DAY}")
        parts = page.evaluate("""() => [...document.querySelectorAll('.ev-txt > *, .ev-wait > span')].map(p => {
            const card = p.closest('.ev-list').getBoundingClientRect();
            const whole = p.tagName === 'STRONG' || p.classList.contains('ev-wrap')
                || new Set([...p.getClientRects()].map(r => Math.round(r.bottom))).size === 1;
            return [p.textContent.trim(), whole, p.getBoundingClientRect().right <= card.right + 0.5]; })""")
        assert len(parts) > 10 and [p for p in parts if not (p[1] and p[2])] == []
        browser.close()


def _lit_pair(page):
    return page.locator(".event-row.ev-pair").evaluate_all("rs => rs.map(r => r.id)")


def test_a_click_on_a_line_lights_its_pair_and_moves_nothing(tmp_path, monkeypatch):
    pw = pytest.importorskip("playwright.sync_api")
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    event_row(car, "trunk", _local(8))
    for minutes in range(1, 58, 2):                       # enough rows that the tailgate's line is long
        event_row(car, "unlocked", _local(9, minutes))
        event_row(car, "unlocked", _local(9, minutes + 1), state=0)
    event_row(car, "trunk", _local(12), state=0)
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 700})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("**/*", serve(client))
        page.goto(f"http://mate.test/events?date_from={DAY}&date_to={DAY}")
        # Lay the whole list out before measuring a pixel. A line the reader has not reached is
        # skipped (`content-visibility: auto`) and stands in at `contain-intrinsic-size` until it is
        # drawn once; the kinds below are declared at their true heights, but a row whose chip wraps
        # on a phone is 52 and not 32, and a line resolving mid-test would move everything under it
        # on the renderer's schedule, not the click's. That is what this test is NOT about.
        page.add_style_tag(content=".ev-list > div { content-visibility: visible !important; }")
        url = page.url
        middle = page.locator("#ev-signal-30")
        middle.scroll_into_view_if_needed()
        y = middle.evaluate("r => r.getBoundingClientRect().top")
        middle.locator(".ev-l > i:not(.dot)").last.click()             # the tailgate's track, passing by
        assert _lit_pair(page) == ["ev-signal-60", "ev-signal-1"]
        assert page.locator("#ev-signal-60 .dot.sel, #ev-signal-1 .dot.sel").count() == 2
        assert middle.evaluate("r => r.getBoundingClientRect().top") == y and page.url == url, "nothing moved"
        middle.locator(".ev-l > i:not(.dot)").last.click()
        assert _lit_pair(page) == [] and page.locator(".ev-l .sel").count() == 0, "again puts it out"
        page.locator("#ev-signal-31 .dot").click()                      # an unlock's own end
        assert _lit_pair(page) == ["ev-signal-31", "ev-signal-30"]
        assert errors == []
        browser.close()


def test_a_line_the_reader_has_not_reached_declares_the_height_it_will_have(tmp_path, monkeypatch):
    """`content-visibility: auto` lets a long range lay out only what is seen, and each skipped line
    stands in at `contain-intrinsic-size` until it is drawn once. That size has to be the one the
    line really takes, per kind, or the list changes height under the reader as the renderer catches
    up — and the scroll anchoring that compensates is the browser's business, on nobody's schedule.

    One 32px for every kind is what made the pair-lighting test fail one CI run in four: each heading
    coming into view replaced 32 with its true 16 or 39, and the measured row drifted 25px. So this
    compares what the stylesheet PROMISES with what the browser MEASURES, kind by kind, which is the
    only way a padding changed in six months' time cannot quietly make the promise a lie."""
    pw = pytest.importorskip("playwright.sync_api")
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    event_row(car, "trunk", _local(8))                        # a day heading and three hours
    for minutes in range(1, 20, 2):
        event_row(car, "unlocked", _local(9, minutes))
        event_row(car, "unlocked", _local(9, minutes + 1), state=0)
    event_row(car, "cable", _local(10, 5), soc=42.0)
    event_row(car, "cable", _local(11, 30), state=0, soc=89.9)
    event_row(car, "trunk", _local(12), state=0)
    declared = dict(re.findall(r"\.ev-list > \.?([\w-]+) \{[^}]*contain-intrinsic-size: auto (\d+)px",
                               pathlib.Path("web/templates/events.html").read_text()))
    assert set(declared) == {"div", "event-hour", "ev-day"}, declared
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        for width in (390, 1280):
            page = browser.new_page(viewport={"width": width, "height": 800})
            page.route("**/*", serve(client))
            page.goto(f"http://mate.test/events?date_from={DAY}&date_to={DAY}")
            page.add_style_tag(content=".ev-list > div { content-visibility: visible !important; }")
            drawn = page.evaluate("""() => {
                const out = {};
                document.querySelectorAll('#events-list .ev-list > *').forEach(el => {
                    const kind = el.classList.contains('ev-day') ? 'ev-day'
                               : el.classList.contains('event-hour') ? 'event-hour' : 'div';
                    (out[kind] = out[kind] || []).push(el.getBoundingClientRect().height);
                });
                return out;
            }""")
            assert set(drawn) == set(declared), (width, sorted(drawn))
            for kind, heights in drawn.items():
                common = max(set(heights), key=heights.count)      # the height that kind usually is
                assert common == float(declared[kind]), (
                    f"at {width}px a {kind} line is {common}px, and the stylesheet tells the browser "
                    f"to hold {declared[kind]}px for it until it is drawn"
                )
            page.close()
        browser.close()
