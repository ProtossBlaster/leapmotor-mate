"""The day drawer's heading says how much battery the day's trips used.

Without a charge between the trips that is two readings, the first trip's start and the last one's
end, and the change between them: "84.4% → 51.6% (−32.8%)". A charge in between makes those two
readings meaningless, so the heading gives the trips' own change instead, "−45.0%", and what the charges
added, "⚡ +40.0%". Every change is summed from the figures as printed, so the heading adds up on screen. A
figure missing anywhere it is needed takes the whole battery field away: a partial sum, or a "−0%",
would read as the truth.

Rendered through both routes that draw the drawer — its own endpoint and the month view opened on a
day — because the heading is built in two places and must not differ between them.
"""
import re

import db as D
import db_reader
import pytest

pytest.importorskip("httpx", reason="Starlette's TestClient is built on httpx")
pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI env)")


def _install(tmp_path, monkeypatch, trips, charges=(), day="2026-07-04", zone="UTC"):
    """`trips`: (start, end, start_soc, end_soc) on `day`, times "HH:MM" UTC; `charges` alike."""
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LFZTEST0000000001','B10')")
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('timezone', ?)", (zone,))
    for i, (start, end, s0, s1) in enumerate(trips, 1):
        c.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc,"
                  " duration_min) VALUES (?,1,?,?,10.0,?,?,20)",
                  (i, f"{day}T{start}:00+00:00", f"{day}T{end}:00+00:00", s0, s1))
    for i, (start, end, s0, s1) in enumerate(charges, 1):
        c.execute("INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc, charge_type)"
                  " VALUES (?,1,?,?,?,?,'AC')",
                  (i, f"{day}T{start}:00+00:00", f"{day}T{end}:00+00:00", s0, s1))
    c.commit()
    pdb._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setattr(db_reader, "get_language", lambda: "en")


def _sql(stmt):
    pdb = D.Database(db_reader.DB_PATH)
    with pdb._conn as c:
        c.execute(stmt)
    pdb._conn.close()


def _drawer(year=2026, month=7, day=4):
    return _client().get("/api/trips/calendar/day", params={"year": year, "month": month, "day": day}).text


def _client():
    import main
    from starlette.testclient import TestClient
    return TestClient(main.app)


def _sums(html):
    """The heading's line of totals, without the trip rows under it (they carry arrows of their own)."""
    m = re.search(r'justify-end gap-x-3[^>]*>(.*?)</div>', html, re.DOTALL)
    assert m, "the day's heading has no totals line"
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).strip()


def _day(tmp_path, monkeypatch, trips, charges=()):
    _install(tmp_path, monkeypatch, trips, charges)
    return _sums(_drawer())


def test_a_day_without_a_charge_goes_from_the_first_reading_to_the_last(tmp_path, monkeypatch):
    sums = _day(tmp_path, monkeypatch, [("08:00", "08:30", 84.4, 70), ("12:00", "12:30", None, None),
                                        ("17:00", "17:40", 60, 51.6)])
    assert sums.startswith("84.4% → 51.6% (−32.8%)"), sums
    assert 'data-tip="Battery">84.4% → 51.6% (−32.8%)<' in _drawer()


def test_the_heading_writes_the_tenth_in_the_readers_language(tmp_path, monkeypatch):
    _install(tmp_path, monkeypatch, [("08:00", "08:30", 84.4, 70), ("17:00", "17:40", 60, 51.6)])
    monkeypatch.setattr(db_reader, "get_language", lambda: "pl")
    assert _sums(_drawer()).startswith("84,4% → 51,6% (−32,8%)")


def test_the_change_is_the_one_between_the_figures_as_printed(tmp_path, monkeypatch):
    """84.44 and 51.66 print as 84.4 and 51.7: the change beside them is −32.7, not the −32.8 they are apart."""
    sums = _day(tmp_path, monkeypatch, [("08:00", "08:30", 84.44, 70), ("17:00", "17:40", 60, 51.66)])
    assert sums.startswith("84.4% → 51.7% (−32.7%)"), sums


def test_a_charge_between_the_trips_sums_their_drops_and_what_it_added(tmp_path, monkeypatch):
    sums = _day(tmp_path, monkeypatch,
                [("08:00", "08:30", 84, 60), ("17:00", "17:40", 100, 79)],
                charges=[("09:00", "11:00", 60, 80), ("11:30", "12:00", 80, 100)])
    assert sums.startswith("−45.0% ⚡ +40.0%"), sums
    assert "→" not in sums
    html = _drawer()
    assert 'data-tip="Battery">−45.0%<' in html and 'data-tip="Energy Charged">⚡ +40.0%<' in html


@pytest.mark.parametrize("second_trip, shown", [
    pytest.param((80, 79), "+9.0%", id="a-range-extender-refilled-the-pack-while-driving"),
    pytest.param((80, 70), "0.0%", id="as-much-gained-as-used"),
    pytest.param((80, 62), "−8.0%", id="used"),
])
def test_the_trips_change_carries_one_sign(tmp_path, monkeypatch, second_trip, shown):
    sums = _day(tmp_path, monkeypatch, [("08:00", "08:30", 60, 70), ("17:00", "17:40", *second_trip)],
                charges=[("09:00", "11:00", 70, 80)])
    assert sums.startswith(f"{shown} ⚡ +10.0%"), sums


def test_the_trips_change_is_what_their_rows_add_up_to(tmp_path, monkeypatch):
    """84.44→80.66 and 90.44→86.66 print as 84.4→80.7 and 90.4→86.7: −7.4 on screen, though they fell 7.56."""
    sums = _day(tmp_path, monkeypatch, [("08:00", "08:30", 84.44, 80.66), ("17:00", "17:40", 90.44, 86.66)],
                charges=[("09:00", "11:00", 80.66, 90.44)])
    assert sums.startswith("−7.4% ⚡ +9.7%"), sums


# 25 Oct 2026 in Warsaw: 02:40 summer time (00:40 UTC) comes before 02:10 winter time (01:10 UTC).
@pytest.mark.parametrize("charges, shown", [
    pytest.param([], "84.0% → 52.0% (−32.0%)", id="without-a-charge"),
    pytest.param([("00:58", "01:05", 70, 90)], "−52.0% ⚡ +20.0%", id="with-a-charge-between"),
])
def test_the_hour_the_clocks_go_back_keeps_the_trips_in_order(tmp_path, monkeypatch, charges, shown):
    second_start = 90 if charges else 70
    _install(tmp_path, monkeypatch, [("00:40", "00:55", 84, 70), ("01:10", "01:30", second_start, 52)],
             charges=charges, day="2026-10-25", zone="Europe/Warsaw")
    sums = _sums(_drawer(2026, 10, 25))
    assert sums.startswith(shown), sums


def test_a_charge_after_the_last_trip_is_not_the_days(tmp_path, monkeypatch):
    sums = _day(tmp_path, monkeypatch,
                [("08:00", "08:30", 84, 60), ("17:00", "17:40", 60, 52)],
                charges=[("07:00", "07:50", 70, 84), ("18:00", "20:00", 52, 90)])
    assert sums.startswith("84.0% → 52.0%"), sums
    assert "⚡" not in sums


@pytest.mark.parametrize("trips, charges", [
    pytest.param([("08:00", "08:30", 84, 60), ("12:00", "12:30", None, None), ("17:00", "17:40", 100, 79)],
                 [("13:00", "15:00", 60, 100)], id="a-trip-without-soc-on-a-charging-day"),
    pytest.param([("08:00", "08:30", 84, 60), ("17:00", "17:40", 100, 79)],
                 [("13:00", "15:00", None, 100)], id="a-charge-without-soc"),
    pytest.param([("08:00", "08:30", None, None), ("17:00", "17:40", None, None)], [], id="no-soc-at-all"),
    pytest.param([("08:00", "08:30", None, 60), ("17:00", "17:40", 60, 52)], [], id="no-start-on-the-first-trip"),
    pytest.param([("08:00", "08:30", 84, 60), ("17:00", "17:40", 60, None)], [], id="no-end-on-the-last-trip"),
])
def test_a_missing_reading_takes_the_battery_field_away(tmp_path, monkeypatch, trips, charges):
    sums = _day(tmp_path, monkeypatch, trips, charges)
    assert "%" not in sums, sums
    assert re.match(r"\d+ km", sums), sums


def test_both_routes_print_the_same_heading(tmp_path, monkeypatch):
    _install(tmp_path, monkeypatch, [("08:00", "08:30", 84, 60), ("17:00", "17:40", 100, 79)],
             charges=[("09:00", "11:00", 60, 100)])
    client = _client()
    drawer = client.get("/api/trips/calendar/day", params={"year": 2026, "month": 7, "day": 4}).text
    month = client.get("/api/trips/calendar", params={"year": 2026, "month": 7, "open_day": 4}).text
    assert _sums(drawer).startswith("−45.0% ⚡ +40.0%"), _sums(drawer)
    assert _sums(month) == _sums(drawer)


def test_the_heading_says_how_long_the_day_was_driven(tmp_path, monkeypatch):
    sums = _day(tmp_path, monkeypatch, [("08:00", "08:30", 84, 70), ("12:00", "12:30", 70, 62),
                                        ("17:00", "17:40", 62, 52)])
    assert "30 km 1h 00m" in sums, sums
    assert 'data-tip="Drive Time">1h 00m<' in _drawer(), "the driving time does not say what it is"


def test_the_driving_time_is_what_the_rows_add_up_to(tmp_path, monkeypatch):
    """Three trips of 20.4 min print as 20 min each: 1h 00m on screen, though they drove 61.2."""
    _install(tmp_path, monkeypatch, [("08:00", "08:30", 84, 70), ("12:00", "12:30", 70, 62),
                                     ("17:00", "17:40", 62, 52)])
    _sql("UPDATE trips SET duration_min = 20.4")
    assert "30 km 1h 00m" in _sums(_drawer())


def test_a_trip_without_a_duration_takes_the_driving_time_away(tmp_path, monkeypatch):
    _install(tmp_path, monkeypatch, [("08:00", "08:30", 84, 70), ("17:00", "17:40", 62, 52)])
    _sql("UPDATE trips SET duration_min = NULL WHERE id = 2")
    sums = _sums(_drawer())
    assert re.search(r"20 km\s*$", sums), sums


def test_every_figure_in_the_heading_says_what_it_is(tmp_path, monkeypatch):
    """A bare "1h 54m" or "73 km" in a row of numbers does not say what it counts. Each one names
    itself in a data-tip, the app's tooltip that also opens on a tap; a `title` never shows on touch."""
    _install(tmp_path, monkeypatch, [("08:00", "08:30", 84, 60), ("17:00", "17:40", 100, 79)],
             charges=[("09:00", "11:00", 60, 100)])
    _sql("UPDATE trips SET efficiency_kwh_100km = 15.0, regen_kwh = 0.5")
    line = re.search(r'justify-end gap-x-3[^>]*>(.*?)</div>', _drawer(), re.DOTALL).group(1)
    spans = re.findall(r"<span[^>]*>", line)
    assert len(spans) >= 6, spans
    assert [s for s in spans if "data-tip=" not in s or "title=" in s] == []


def test_every_figure_in_the_month_strip_says_what_it_is(tmp_path, monkeypatch):
    """The strip over the calendar sums the month the way the heading sums a day, and names its bare
    figures the same way; "2 trips" carries its own noun and needs no tooltip."""
    _install(tmp_path, monkeypatch, [("08:00", "08:30", 84, 60), ("17:00", "17:40", 60, 52)])
    _sql("UPDATE trips SET efficiency_kwh_100km = 15.0")
    html = _client().get("/api/trips/calendar", params={"year": 2026, "month": 7}).text
    line = re.search(r'justify-center gap-4 text-sm mb-4[^>]*>(.*?)</div>', html, re.DOTALL).group(1)
    spans = [(tag, text.strip()) for tag, text in re.findall(r"(<span[^>]*>)([^<]*)", line)]
    figures = [(tag, text) for tag, text in spans if "trips" not in text]
    assert len(figures) >= 2, spans
    assert [text for tag, text in figures if "data-tip=" not in tag] == []


def test_a_trip_row_gives_its_battery_on_a_phone_too(tmp_path, monkeypatch):
    """The wide column with the battery bar is `hidden sm:flex`: a phone gets the figures on a line of
    their own, in the slot that says when there are none."""
    _install(tmp_path, monkeypatch, [("08:00", "08:30", 84.4, 51.6), ("17:00", "17:40", None, None)])
    phone = re.findall(r'<div class="sm:hidden text-slate-500 text-\[10px\]">([^<]*)</div>', _drawer())
    assert sorted(phone) == ["84→52%", "SoC unavailable"], phone
