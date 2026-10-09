"""The Events rows of a trip name where it started and where it ended, as Trips does.

A start or an end inside a charging place was already named by the place; elsewhere the row now says
the address stored for the spot (db_reader.trip_places), and the list credits OpenStreetMap once,
under itself, when one of its rows shows an address from it: the map beside it shows no credit.
"""
import html as html_lib
import re
from datetime import datetime, timedelta, timezone

import db_reader
import geohash
import place_lookup
from events_fixture import Car, event_row, row_text, web

NOW = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=2)
HOME, SHOP = (44.49, 11.34), (44.51, 11.36)
CREDIT = "openstreetmap.org/copyright"


def _trip(car):
    cur = car.db._conn.execute(
        "INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km, start_lat, start_lon, end_lat, end_lon)"
        " VALUES (?, ?, ?, 9.0, ?, ?, ?, ?)",
        (car.vid, (NOW - timedelta(minutes=20)).isoformat(), NOW.isoformat(), *HOME, *SHOP))
    car.db._conn.commit()
    return cur.lastrowid


def _address(point, provider="nominatim", **parts):
    place_lookup._store(geohash.encode(*point, 8), *point, provider, NOW, "found", parts)


def test_a_trips_rows_say_where_it_started_and_ended(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    car.db._conn.execute("INSERT INTO charging_places (vehicle_id, name, latitude, longitude, radius_m, rate, enabled)"
                         " VALUES (?, 'Casa', ?, ?, 100, 0.2, 1)", (car.vid, *HOME))
    car.db._conn.commit()
    tid = _trip(car)
    _address(HOME, road="Via Saragozza", house_number="7", locality="Bologna")
    _address(SHOP, name="Coop", road="Via Andrea Costa", locality="Bologna")
    html = client.get("/events").text
    assert "Casa" in row_text(html, f"ev-trip-{tid}-on"), "a charging place wins over the address"
    assert "Coop, Bologna" in row_text(html, f"ev-trip-{tid}-off")
    assert html.count(CREDIT) == 1


def test_no_credit_without_an_address_from_openstreetmap(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    tid = _trip(car)
    _address(SHOP, provider="geoapify", road="Via Andrea Costa", locality="Bologna")
    html = client.get("/events").text
    assert "Via Andrea Costa, Bologna" in row_text(html, f"ev-trip-{tid}-off")
    assert CREDIT not in html


def test_an_address_found_between_two_parts_brings_the_list_again(tmp_path, monkeypatch):
    """The lookup runs in the background, so a trip can be named after the list's first part was read: the
    next part then comes with the whole list again, its trip named and the credit under it once."""
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    monkeypatch.setattr(db_reader, "EVENTS_PART_ROWS", 5)
    tid = _trip(car)
    for k in range(10):                                         # older rows, for a second part
        event_row(car, "unlocked", NOW - timedelta(hours=1, minutes=2 * k))
        event_row(car, "unlocked", NOW - timedelta(hours=1, minutes=2 * k - 1), state=0)
    first = client.get("/events").text
    assert "Bologna" not in row_text(first, f"ev-trip-{tid}-off") and CREDIT not in first
    more = html_lib.unescape(re.search(r'hx-get="(api/events/search\?[^"]*part=1)"', first).group(1))

    _address(SHOP, name="Coop", road="Via Andrea Costa", locality="Bologna")
    r = client.get("/" + more)
    assert r.headers.get("hx-retarget") == "#events-list", "the part would join a list that changed"
    assert "Coop, Bologna" in row_text(r.text, f"ev-trip-{tid}-off")
    assert r.text.count(CREDIT) == 1
