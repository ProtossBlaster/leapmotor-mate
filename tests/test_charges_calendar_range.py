"""The Charges calendar opens a range of days, as the Trips one does: one heading with the range's totals,
then each day with charges under its own heading and its own cards, newest first. Rendered through both
routes that draw the drawer — its own endpoint and the month view opened on the range — because both must
print the same thing.

3 July: two charges, 10 and 7 kWh, €5 + €3, ten minutes apart (so the second offers to merge). 4 July: none. 5 July: one charge, 20 kWh, €0 (unpriced),
with the charger's own 22 kWh typed in. 6 July: a charge outside the range.
"""
import re

import db as D
import db_reader
import pytest

pytest.importorskip("httpx", reason="Starlette's TestClient is built on httpx")
pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI env)")

from test_a_days_heading_sums_up_its_trips import _client
from test_trips_calendar_range import _sums, _text

_CHARGES = [(1, "03T08:00", "03T09:00", 10.0, 5.0, None), (2, "03T09:10", "03T10:00", 7.0, 3.0, None),
            (3, "05T09:00", "05T11:00", 20.0, None, 22.0), (4, "06T09:00", "06T10:00", 5.0, 2.0, None)]


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LFZTEST0000000001','B10')")
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('timezone', 'UTC')")
    for i, start, end, kwh, cost, gross in _CHARGES:
        c.execute("INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc, energy_added_kwh,"
                  " cost, gross_kwh, duration_min, charge_type, location_type) VALUES (?,1,?,?,40,60,?,?,?,60,'AC','HOME')",
                  (i, f"2026-07-{start}:00+00:00", f"2026-07-{end}:00+00:00", kwh, cost, gross))
    c.commit()
    c.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setattr(db_reader, "get_language", lambda: "en")
    return _client()


def _drawer(client, **params):
    return client.get("/api/charges/calendar/day", params={"year": 2026, "month": 7, **params}).text


def _cards(html):
    return re.findall(r'data-charge-id="(\d+)"', html)


def test_the_range_has_one_heading_then_a_heading_per_day_newest_first(client):
    html = _drawer(client, day=3, to_day=5)
    heading = _text(html.split('<div class="space-y-4">', 1)[0])
    assert heading.startswith("03 – 05 Jul 2026 3 sessions"), heading
    assert re.findall(r'data-cal-day="(\d+)"', html) == ["5", "3"]       # 4 July charged nothing
    assert _cards(html) == ["3", "2", "1"]
    assert "06 Jul" not in html and "4" not in _cards(html)


def test_the_range_adds_up_its_days(client):
    """The delivered side, the battery side where it differs, and only the priced charges' cost: the
    month strip's own rule, through the same function."""
    whole, fifth, third = _sums(_drawer(client, day=3, to_day=5))
    assert whole == "3 sessions 39 kWh delivered 37 in battery 8.00 €", whole
    assert fifth == "1 session 22 kWh delivered 20 in battery", fifth
    assert third == "2 sessions 17 kWh delivered 8.00 €", third


def test_a_days_heading_carries_its_totals_too(client):
    (day,) = _sums(_drawer(client, day=3))
    assert day == "2 sessions 17 kWh delivered 8.00 €", day


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
        assert client.get("/api/charges/calendar/day", params={"year": 2026, "month": 7, **bad}).status_code == 422


def test_the_month_view_opened_on_the_range_draws_the_same_drawer(client):
    month = client.get("/api/charges/calendar", params={"year": 2026, "month": 7, "open_day": 3, "open_to": 5}).text
    drawer = month.split('id="charges-day-drawer"', 1)[1]
    assert _text(_drawer(client, day=3, to_day=5)) in _text(drawer)
    ringed = re.findall(r'<button type="button" data-selected data-day="(\d+)"', month)
    assert ringed == ["3", "5"], ringed


def test_the_station_filter_holds_across_the_range(client, monkeypatch):
    """A station picked above the calendar narrows every day of the range, as it narrows one day."""
    seen = []

    def narrow(charges, station):
        seen.append(station)
        return [c for c in charges if c["id"] != 1]
    monkeypatch.setattr(db_reader, "_filter_by_station", narrow)
    html = _drawer(client, day=3, to_day=5, station="home")
    assert seen and set(seen) == {"home"}
    assert _cards(html) == ["3", "2"]
    assert "&station=home" in html                 # the cards' own merge and split keep it


def test_a_cards_merge_and_split_carry_the_drawers_days(client):
    """So the redraw after a merge or split can open the same days again."""
    c = client.get("/api/charges/calendar/day", params={"year": 2026, "month": 7, "day": 3, "to_day": 5})
    assert c.status_code == 200
    assert "&year=2026&month=7&day=3&to_day=5" in c.text
    one = _drawer(client, day=3)
    assert "&year=2026&month=7&day=3" in one and "to_day" not in one
