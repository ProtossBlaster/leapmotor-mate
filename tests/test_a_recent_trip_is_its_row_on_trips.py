"""A recent trip on the Overview is its row on the Trips page without the route: the same partial, fed by the
same preparation (db_reader._prepared_trips and trip_places), with the day over its times as a search result
has it. So it says what the Trips row says: where the trip went, the battery, the energy, that a figure is
still provisional, and that a merged trip's duration is its driving only. An address from OpenStreetMap is
credited once, as on every view that shows one. Unlike the Trips lists it prices nothing: its rows show no
cost.
"""
import re
from datetime import datetime, timedelta, timezone

import db_reader
import pytest
from test_a_trip_shows_where_it_started_and_ended import (  # noqa: F401  (car: fixture)
    CREDIT, DAY, HOME, WORK, address, car, lines, trip)


def recent(db):
    """The Recent Trips list of the Overview."""
    html = db.client.get("/").text
    at = html.rindex("<div", 0, html.index("data-recent-trips"))
    depth = 0
    for tag in re.finditer(r"<div\b|</div>", html[at:]):
        depth += 1 if tag.group(0) == "<div" else -1
        if depth == 0:
            return html[at:at + tag.end()]
    raise AssertionError("the list never closes")


def text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def driven(db, at=DAY, soc=(80, 76.5), **cols):
    """A 5 km trip from HOME to WORK at 18 kWh/100km."""
    tid = trip(db, HOME, WORK, at=at, soc=soc)
    cols = {"efficiency_kwh_100km": 18.0, **cols}
    db._conn.execute(f"UPDATE trips SET {', '.join(f'{k} = ?' for k in cols)} WHERE id = ?", (*cols.values(), tid))
    db._conn.commit()
    return tid


def test_a_recent_trip_says_when_where_and_how_much(car):
    address(HOME, road="Via Roma", locality="Torino")
    address(WORK, road="Corso Francia", locality="Torino")
    tid = driven(car)
    row = recent(car)
    assert lines(row) == [("Via Roma, Torino", "Corso Francia, Torino")]
    words = text(row)
    for part in ("15 Sep 2026 08:00 → 08:20 20 min", "80.0→76.5% (−3.5%)", "5 km 0.9 kWh 18 kWh/100km"):
        assert part in words, part
    assert 'class="soc-mini"' in row
    assert "trip-thumb" not in row and f"trips/{tid}/route.svg" not in row


def test_a_figure_still_provisional_says_so(car):
    car.set_setting("ec_trip_since", "2000-01-01")
    ended = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=1)
    driven(car, at=ended - timedelta(minutes=20), ec_stable=0)           # the car's own figure is not in yet
    assert "⏳ provisional" in text(recent(car))


def test_a_merged_trip_says_its_duration_is_the_driving(car):
    first = driven(car)
    second = driven(car, at=DAY + timedelta(minutes=23), soc=(76.5, 73))
    assert db_reader.merge_trips(first, second)["ok"]
    words = text(recent(car))
    assert "08:00 → 08:43 🔗" in words and "40 min · driving only" in words


@pytest.mark.parametrize("provider, credits", [("nominatim", 1), ("geoapify", 0)])
def test_an_openstreetmap_address_is_credited_once(car, provider, credits):
    address(WORK, provider=provider, road="Corso Francia", locality="Torino")
    trip(car, HOME, WORK)
    assert car.client.get("/").text.count(CREDIT) == credits


def test_the_overview_prices_no_trip(car, monkeypatch):
    def priced():
        raise AssertionError("the Overview priced its trips")
    monkeypatch.setattr(db_reader, "_trip_blended_rate_fn", priced)       # replays the whole charge history
    monkeypatch.setattr(db_reader, "_reev_electric_cost_by_trip", priced)
    driven(car)
    assert "5 km 0.9 kWh" in text(recent(car))
