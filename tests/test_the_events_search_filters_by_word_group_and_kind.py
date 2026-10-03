"""The Events search: a word, the range buttons, the group pills, a kind, dates — and the marker
that tells an empty selection from no filter.

The word matches the row's label in the reader's language, a command's action and outcome, a
row's place, and the note of a trip or a charge (where the address of a trip is). The range counts
back from today, three days unless asked, months to the same day of an earlier month; typed dates
win over it. A pill is a group of kinds, repeated in the URL; a kind narrows further. An empty
date field, or one no clock can hold, is no filter and never a 422 (#175). An unchecked checkbox sends nothing, so `f=1` marks
a submitted form: with it an empty `group` means nothing selected, without it the page shows
everything. The reset returns to the defaults. The address follows the filters, the marker too, in
place: Back leaves the page rather than restoring htmx's copy of it.
"""
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import db_reader
import pytest
from events_fixture import VIN, Car, event_row, serve, web

ZONE = ZoneInfo("Europe/Warsaw")
# Yesterday 20:00 local: every session seeded a few hours before it stays on one local day, at any hour the suite runs.
NOW = datetime.combine(datetime.now(ZONE).date() - timedelta(days=1), datetime.min.time().replace(hour=20),
                       ZONE).astimezone(timezone.utc)


def _seed(car):
    t0 = NOW - timedelta(hours=4)
    event_row(car, "unlocked", t0)
    event_row(car, "unlocked", t0 + timedelta(minutes=5), state=0)
    event_row(car, "trunk", t0 + timedelta(minutes=10))
    event_row(car, "trunk", t0 + timedelta(minutes=12), state=0)
    event_row(car, "climate", t0 - timedelta(days=3))
    event_row(car, "climate", t0 - timedelta(days=3) + timedelta(minutes=9), state=0)
    car.db._conn.execute(
        "INSERT INTO charges (vehicle_id, started_at, ended_at, energy_added_kwh, charging_place_name)"
        " VALUES (?, ?, ?, 7.0, 'Lidl Praga')",
        (car.vid, (t0 + timedelta(minutes=30)).isoformat(), (t0 + timedelta(minutes=70)).isoformat()))
    car.db._conn.commit()
    db_reader.log_command("ac_on", "timeout_car", None, vin=VIN)


def _kinds(html):
    return re.findall(r'data-kind="(\w+)"', html)


def test_a_word_matches_the_label_the_command_and_the_place(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch, lang="pl")
    _seed(car)
    assert _kinds(client.get("/api/events/search?q=odblok").text) == ["unlocked"]
    assert _kinds(client.get("/api/events/search?q=brak odpowiedzi").text) == ["command"]
    assert _kinds(client.get("/api/events/search?q=włącz klimat").text) == ["command"]
    assert _kinds(client.get("/api/events/search?q=lidl").text) == ["charge", "charge"]
    assert _kinds(client.get("/api/events/search?q=zamknięta").text) == ["trunk"], "the end's own label"


def test_the_pills_select_groups_and_a_kind_narrows_them(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    _seed(car)
    assert sorted(_kinds(client.get("/api/events/search?f=1&group=security&group=doors").text)) == ["trunk"] * 2 + ["unlocked"] * 2
    assert _kinds(client.get("/api/events/search?f=1&group=security&group=doors&kind=trunk").text) == ["trunk"] * 2
    assert _kinds(client.get("/api/events/search?f=1&group=commands").text) == ["command"]


def test_a_date_range_and_an_empty_date_field(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    _seed(car)
    day = (NOW - timedelta(days=3, hours=4)).astimezone(ZONE).date().isoformat()
    r = client.get(f"/api/events/search?date_from={day}&date_to={day}")
    assert r.status_code == 200 and _kinds(r.text) == ["climate"] * 2
    r = client.get("/api/events/search?date_from=&date_to=&q=")
    assert r.status_code == 200 and len(_kinds(r.text)) == 7, "the default three days, without the climate four days back"
    for odd in ("date_from=0001-01-01", "date_to=9999-12-31", "part=x"):        # no 500, no 422: no filter
        r = client.get(f"/api/events/search?{odd}")
        assert r.status_code == 200 and len(_kinds(r.text)) == 7, odd
    r = client.get(f"/api/events/search?range=7d&date_from={day}&date_to=")
    assert _kinds(r.text) == ["command", "charge", "charge", "trunk", "trunk", "unlocked", "unlocked", "climate", "climate"], \
        "from the typed day to today, whatever the range"


def test_nothing_selected_is_not_the_same_as_no_filter(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    _seed(car)
    nothing = client.get("/api/events/search?f=1")
    assert _kinds(nothing.text) == [] and "No event matches your filters" in nothing.text
    assert nothing.headers["hx-replace-url"] == "events?f=1"
    everything = client.get("/api/events/search")
    assert len(_kinds(everything.text)) == 7
    assert everything.headers["hx-replace-url"] == "events"


def test_the_page_renders_the_filters_it_was_opened_with(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    _seed(car)
    html = client.get("/events?f=1&group=doors&q=tail").text
    assert _kinds(html) == ["trunk"] * 2
    assert 'name="group" value="doors" class="peer sr-only" checked' in html
    assert 'name="group" value="security" class="peer sr-only" >' in html
    assert 'name="q" value="tail"' in html
    html = client.get("/events").text
    assert len(re.findall(r'name="group" value="\w+" class="peer sr-only" checked', html)) == len(db_reader.EVENT_GROUPS), \
        "every pill on by default"


def _checked_range(html):
    return re.findall(r'name="range" value="(\w+)" class="peer sr-only" checked', html)


def test_the_range_buttons_count_back_from_today(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    _seed(car)
    assert _checked_range(client.get("/events").text) == ["3d"]
    assert len(_kinds(client.get("/api/events/search?range=7d").text)) == 9
    html = client.get("/events?range=30d").text
    assert _checked_range(html) == ["30d"] and len(_kinds(html)) == 9
    html = client.get("/events?range=5y").text
    assert _checked_range(html) == ["3d"] and len(_kinds(html)) == 7, "an unknown range is the default"
    day = (NOW - timedelta(days=3, hours=4)).astimezone(ZONE).date().isoformat()
    html = client.get(f"/events?range=7d&date_from={day}&date_to={day}").text
    assert _checked_range(html) == [] and _kinds(html) == ["climate"] * 2, "typed dates win and no button is lit"
    today = datetime.now(ZONE).date()
    for rng, first in (("3d", today - timedelta(days=2)), ("7d", today - timedelta(days=6)),
                       ("30d", today - timedelta(days=29)), ("3m", db_reader._months_back(today, 3)),
                       ("6m", db_reader._months_back(today, 6)), ("12m", db_reader._months_back(today, 12)),
                       ("all", None)):
        assert db_reader.EventFilter.from_query(range=rng).days(ZONE) == (first, today), rng


def test_a_month_back_from_a_long_month_is_the_shorter_months_last_day():
    assert db_reader._months_back(date(2026, 5, 31), 3) == date(2026, 2, 28)
    assert db_reader._months_back(date(2028, 5, 31), 3) == date(2028, 2, 29)
    assert db_reader._months_back(date(2026, 1, 15), 12) == date(2025, 1, 15)
    assert db_reader._months_back(date(2026, 3, 31), 6) == date(2025, 9, 30)


def test_all_shows_every_row_at_once(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    first = NOW - timedelta(days=40)
    pairs = [(("unlocked", "trunk")[k % 2], first + timedelta(days=d, minutes=30 * k)) for d in range(40) for k in range(12)]
    car.db._conn.executemany("INSERT INTO events (vehicle_id, kind, at, state) VALUES (?, ?, ?, ?)",
                             [(car.vid, kind, (at + timedelta(minutes=10 * (1 - state))).isoformat(), state)
                              for kind, at in pairs for state in (1, 0)])
    car.db._conn.commit()
    html = client.get("/events?range=all").text
    assert len(_kinds(html)) == 960 and "🔍 960 " in html


def _trip(car, note):
    car.db._conn.execute("INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km, note) VALUES (?, ?, ?, 9.0, ?)",
                         (car.vid, (NOW - timedelta(hours=7)).isoformat(), (NOW - timedelta(hours=6, minutes=40)).isoformat(),
                          note))
    car.db._conn.commit()


def test_a_word_finds_a_trip_by_its_address_and_a_charge_by_its_note(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    _seed(car)
    _trip(car, "5, Biedronki, Zielonka, gmina Białe Błota · 10:40 → 11:00")
    car.db._conn.execute("UPDATE charges SET note = 'under the shelter'")
    car.db._conn.commit()
    assert _kinds(client.get("/api/events/search?q=białe błota").text) == ["trip", "trip"], "both rows of the trip"
    assert _kinds(client.get("/api/events/search?q=shelter").text) == ["charge", "charge"]


def test_the_reset_brings_back_the_defaults(tmp_path, monkeypatch):
    pw = __import__("pytest").importorskip("playwright.sync_api")
    from events_fixture import serve
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    _seed(car)
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 800})
        page.route("**/*", serve(client))
        page.goto("http://mate.test/events?f=1&group=doors&q=tail&range=30d")
        reset = page.locator("#events-list button", has_text="Reset filters")             # as on Trips and Charges
        assert reset.count() == 1
        reset.click()
        page.wait_for_url("http://mate.test/events")
        page.wait_for_function("() => document.querySelectorAll('.event-row').length === 7")
        assert page.input_value("#events-search-q") == ""
        assert page.locator('input[name=group]:checked').count() == len(db_reader.EVENT_GROUPS)
        assert page.locator('input[name=range]:checked').get_attribute("value") == "3d"
        browser.close()


def test_back_after_a_search_leaves_the_page_whole(tmp_path, monkeypatch):
    """A copy of the page restored by htmx would run its scripts a second time: Mate's menu then no
    longer opened on a phone."""
    pw = pytest.importorskip("playwright.sync_api")
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    _seed(car)
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 800})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("**/*", serve(client))
        page.goto("http://mate.test/trips")
        page.goto("http://mate.test/events")
        page.locator("#events-search-q").press_sequentially("closed")
        page.wait_for_url("**q=closed*")
        page.go_back()
        page.wait_for_url("**/trips")
        assert errors == []
        browser.close()
