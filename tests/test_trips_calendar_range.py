"""The Trips calendar opens a range of days: one heading with the range's totals, then each day with trips
under its own heading and its own trips, newest first. Rendered through both routes that draw the drawer —
its own endpoint and the month view opened on the range — because both must print the same thing.

3 July: two trips, 90 → 80 → 72 %. 4 July: none. 5 July: a charge 72 → 95 %, then a trip 95 → 85 %.
6 July: a trip outside the range.
"""
import re

import db as D
import db_reader
import pytest

pytest.importorskip("httpx", reason="Starlette's TestClient is built on httpx")
pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI env)")

from test_a_days_heading_sums_up_its_trips import _client

_TRIPS = [(1, "03T08:00", "03T08:30", 90, 80, 12.4), (2, "03T17:00", "03T17:30", 80, 72, 7.3),
          (3, "05T09:00", "05T09:30", 95, 85, 9.1), (4, "06T09:00", "06T09:30", 85, 80, 5.0)]


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LFZTEST0000000001','B10')")
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('timezone', 'UTC')")
    for i, start, end, s0, s1, km in _TRIPS:
        c.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc,"
                  " duration_min) VALUES (?,1,?,?,?,?,?,30)",
                  (i, f"2026-07-{start}:00+00:00", f"2026-07-{end}:00+00:00", km, s0, s1))
    c.execute("INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc, charge_type)"
              " VALUES (1,1,'2026-07-05T06:00:00+00:00','2026-07-05T08:00:00+00:00',72,95,'AC')")
    c.commit()
    c.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setattr(db_reader, "get_language", lambda: "en")
    return _client()


def _drawer(client, **params):
    return client.get("/api/trips/calendar/day", params={"year": 2026, "month": 7, **params}).text


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _sums(html):
    """Every line of totals in the drawer, in page order: the range's first, then each day's."""
    return [_text(m) for m in re.findall(r'justify-end gap-x-3[^>]*>(.*?)</div>', html, re.DOTALL)]


def test_the_range_has_one_heading_then_a_heading_per_day_newest_first(client):
    html = _drawer(client, day=3, to_day=5)
    heading = _text(html.split('<div class="space-y-4">', 1)[0])
    assert heading.startswith("03 – 05 Jul 2026 3 trips"), heading
    assert re.findall(r'data-cal-day="(\d+)"', html) == ["5", "3"]       # 4 July drove nothing
    assert "06 Jul" not in html


def test_the_range_adds_up_its_days(client):
    whole, fifth, third = _sums(_drawer(client, day=3, to_day=5))
    assert "28.8 km" in whole and "9.1 km" in fifth and "19.7 km" in third
    assert whole.endswith("1h 30m") and fifth.endswith("30 min") and third.endswith("1h 00m")


def test_a_charge_between_the_days_splits_the_battery(client):
    """First reading → last would say 90 → 85 %; the charge on the 5th makes that meaningless."""
    whole, fifth, third = _sums(_drawer(client, day=3, to_day=5))
    assert whole.startswith("−28.0% ⚡ +23.0%"), whole
    assert fifth.startswith("95.0% → 85.0% (−10.0%)"), fifth
    assert third.startswith("90.0% → 72.0% (−18.0%)"), third


def test_a_range_has_no_merge_button(client):
    assert "merge=1" not in _drawer(client, day=3, to_day=5)
    assert "merge=1" in _drawer(client, day=3)


def test_a_range_of_one_day_is_that_day(client):
    assert _drawer(client, day=3, to_day=3) == _drawer(client, day=3)


def test_the_days_can_come_in_either_order(client):
    assert _drawer(client, day=5, to_day=3) == _drawer(client, day=3, to_day=5)


def test_a_range_past_the_month_ends_with_the_month(client):
    """June has 30 days: the 31st is its last, from either end. A day no month has is refused, as is a day
    before the 1st."""
    assert _drawer(client, month=6, day=3, to_day=31) == _drawer(client, month=6, day=3, to_day=30)
    assert _drawer(client, month=6, day=31, to_day=3) == _drawer(client, month=6, day=3, to_day=30)
    for bad in ({"day": 0}, {"day": 32}, {"day": 3, "to_day": 32}, {"day": 3, "to_day": -1}):
        assert client.get("/api/trips/calendar/day", params={"year": 2026, "month": 7, **bad}).status_code == 422


def test_the_month_view_opened_on_the_range_draws_the_same_drawer(client):
    month = client.get("/api/trips/calendar", params={"year": 2026, "month": 7, "open_day": 3, "open_to": 5}).text
    drawer = month.split('id="trips-day-drawer"', 1)[1]
    assert _text(_drawer(client, day=3, to_day=5)) in _text(drawer)
    ringed = re.findall(r'<button type="button" data-selected data-day="(\d+)"', month)
    assert ringed == ["3", "5"], ringed
