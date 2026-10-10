"""A charge says where it happened: its card names the station with the address after it, or else the
nearest charging place or the address, and a search finds it by any of them.

The name is worked out when the charge is read (db_reader.charge_places), in one order: the charging
place assigned to it, the station, the charging place whose radius holds its point, the address stored
for its spot (place_lookup). Every route that redraws the card's 📍 line names it the same way. A missing
address is looked up on request by the line's 🧭; the note is the user's, Mate writes nothing into it.
"""
import csv
import io
import re
from datetime import timedelta

import db_reader
import pytest
import test_a_trip_shows_where_it_started_and_ended as trips
from test_a_trip_shows_where_it_started_and_ended import (
    CREDIT,
    DAY,
    HOME,
    SHOP,
    WORK,
    address,
    charging_place,
)

pytest.importorskip("fastapi", reason="web/main.py needs fastapi (absent in the minimal CI env)")

car = trips.car          # the fixture


def charge(db, point, at=DAY, station=None, url=None, kind="AC", note=None):
    cur = db._conn.execute(
        "INSERT INTO charges (vehicle_id, started_at, ended_at, start_soc, end_soc, energy_added_kwh, latitude,"
        " longitude, location_type, location_name, location_url, note) VALUES (?, ?, ?, 40, 60, 9, ?, ?, ?, ?, ?, ?)",
        (db.vid, at.isoformat(), (at + timedelta(hours=1)).isoformat(), *(point or (None, None)), kind, station,
         url, note))
    db._conn.commit()
    return cur.lastrowid


def place_id(db, name):
    return db._conn.execute("SELECT id FROM charging_places WHERE name = ?", (name,)).fetchone()[0]


def drawer(db, day=DAY):
    return db.client.get(f"/api/charges/calendar/day?year={day.year}&month={day.month}&day={day.day}").text


def where(html, cid):
    """What the card's 📍 line reads, without its link and buttons; None without the line."""
    m = re.search(rf'<div id="loc-{cid}"[^>]*>(.*?)(?:<button|<form|</div>)', html, re.DOTALL)
    if not m:
        return None
    text = re.sub(r"<a [^>]*>.*?</a>", "", m.group(1), flags=re.DOTALL)
    return " ".join(re.sub(r"<[^>]+>", " ", text).split())


def osm_marked(html, cid):
    """Whether the card's 📍 line shows an address from OpenStreetMap."""
    m = re.search(rf'<div id="loc-{cid}".*?(?:<button|<form|</div>)', html, re.DOTALL)
    return "data-address-osm" in m.group(0)


# ── the card ──────────────────────────────────────────────────────────────────

def test_a_station_is_named_with_its_address(car):
    """Nominatim names a charger it knows by the charger's own name: beside the station it would say it twice."""
    address(WORK, name="Ionity Torino", road="Corso Francia", locality="Torino")
    cid = charge(car, WORK, station="Ionity Torino", url="https://openstreetmap.org/node/1")
    html = drawer(car)
    assert where(html, cid) == "📍 Ionity Torino · Corso Francia, Torino"
    assert osm_marked(html, cid)


def test_a_charge_without_a_station_is_named_by_its_address(car):
    address(WORK, road="Corso Francia", locality="Torino")
    cid = charge(car, WORK)
    html = drawer(car)
    assert where(html, cid) == "📍 Corso Francia, Torino"
    assert osm_marked(html, cid)


def test_a_charge_inside_a_charging_place_is_named_by_it(car):
    """Before any address, and marked, so "42" does not read as a house number; a home charge too."""
    address(HOME, road="Via Roma", locality="Torino")
    charging_place(car, (HOME[0] + 0.0003, HOME[1]), "42")               # about 33 m away
    cid = charge(car, HOME, kind="HOME")
    html = drawer(car)
    assert where(html, cid) == "📍 42 (charging place)"
    assert not osm_marked(html, cid)


def test_an_assigned_place_keeps_its_own_line(car):
    address(WORK, road="Corso Francia", locality="Torino")
    charging_place(car, WORK, "Office")
    cid = charge(car, WORK)
    assert car.client.post(f"/api/charges/{cid}/place", data={"place_id": place_id(car, "Office")}).status_code == 200
    html = drawer(car)
    assert where(html, cid) == "", "the place line below names it"
    assert re.search(rf'id="place-{cid}"[^>]*>\s*📍 Office · ', html)


def test_without_coordinates_or_an_address_the_line_names_nothing(car):
    address(HOME, road="Via Roma", locality="Torino")
    typed = charge(car, None)
    unknown = charge(car, SHOP, at=DAY + timedelta(hours=4))
    html = drawer(car)
    assert where(html, typed) == where(html, unknown) == ""


def test_a_lookup_that_found_no_station_is_named_by_its_address(car):
    """'' is the station lookup's "nothing here", not a name."""
    address(WORK, road="Corso Francia", locality="Torino")
    cid = charge(car, WORK, station="")
    assert where(drawer(car), cid) == "📍 Corso Francia, Torino"


# ── the routes that redraw the line ───────────────────────────────────────────

def test_assigning_a_place_and_taking_it_back_redraw_the_line(car):
    address(HOME, road="Via Roma", locality="Torino")
    charging_place(car, (HOME[0] + 0.0003, HOME[1]), "42")
    charging_place(car, HOME, "Garage", radius_m=10)
    cid = charge(car, HOME, kind="HOME")
    assert where(drawer(car), cid) == "📍 Garage (charging place)", "the nearest of the two"
    done = car.client.post(f"/api/charges/{cid}/place", data={"place_id": place_id(car, "42")}).text
    assert re.search(rf'<div id="loc-{cid}"[^>]*hx-swap-oob="true"', done)
    assert where(done, cid) == ""
    back = car.client.post(f"/api/charges/{cid}/place", data={"place_id": 0}).text
    assert where(back, cid) == "📍 Garage (charging place)"


def test_relocating_or_typing_a_station_keeps_the_address(car, monkeypatch):
    import charger_locator
    address(WORK, road="Corso Francia", locality="Torino")
    cid = charge(car, WORK)
    monkeypatch.setattr(charger_locator, "find_station_candidates",
                        lambda lat, lon: ([{"name": "Ionity Torino", "url": None}], True))
    found = car.client.post(f"/api/charges/{cid}/locate").text
    assert where(found, cid) == "📍 Ionity Torino · Corso Francia, Torino" and osm_marked(found, cid)
    typed = car.client.post(f"/api/charges/{cid}/locate/manual", data={"name": "Colonnina Lingotto"}).text
    assert where(typed, cid) == "📍 Colonnina Lingotto · Corso Francia, Torino"
    picked = car.client.post(f"/api/charges/{cid}/locate/confirm", data={"name": ""}).text
    assert where(picked, cid) == "📍 Corso Francia, Torino", "an empty pick is a lookup that found nothing"
    assert where(car.client.get(f"/api/charges/{cid}/locate/cancel").text, cid) == "📍 Corso Francia, Torino"


# ── the 🧭 ──────────────────────────────────────────────────────────────────────

def look_up(cid):
    return f'hx-post="api/charges/{cid}/address"'


def test_the_charge_card_has_no_compass_for_the_note_any_more(car):
    cid = charge(car, WORK)
    assert f"api/charges/{cid}/auto-note" not in drawer(car)
    assert car.client.post(f"/api/charges/{cid}/auto-note").status_code in (404, 405)
    card = car.client.get("/settings").text.split('hx-post="api/settings/geocoder"')[1].split("</form>")[0]
    assert "auto_note" not in card


def test_a_missing_address_is_looked_up_on_request(car, monkeypatch):
    """Whatever the switch in Address lookup says and however old the charge (DAY is weeks ago): its point is
    asked about once, and the line comes back with the address, without the 🧭."""
    import geocode
    from test_the_places_of_recent_trips_are_looked_up import Providers, nominatim, spot
    providers = Providers()
    providers.answers[("nominatim.openstreetmap.org", spot(*WORK))] = nominatim("Corso Francia")
    monkeypatch.setattr(geocode, "_get", providers)
    car.set_setting("place_lookup", "0")
    cid = charge(car, WORK, station="Ionity Torino")
    assert look_up(cid) in drawer(car)
    redrawn = car.client.post(f"/api/charges/{cid}/address").text
    assert providers.calls() == [spot(*WORK)]
    assert where(redrawn, cid) == "📍 Ionity Torino · Corso Francia, Torino" and osm_marked(redrawn, cid)
    assert look_up(cid) not in redrawn


def _station_with_its_address(car):
    address(WORK, road="Corso Francia", locality="Torino")
    return charge(car, WORK, station="Ionity Torino")


def _inside_a_charging_place(car):
    charging_place(car, WORK, "Office")
    return charge(car, WORK)


def _a_place_assigned(car):
    charging_place(car, SHOP, "Office")
    cid = charge(car, WORK)
    db_reader.assign_charging_place(cid, place_id(car, "Office"))
    return cid


def _only_the_providers_full_text(car):
    """The provider answered, without a part the line shows beside a station: asking again brings the same."""
    address(WORK, display_name="Località Sconosciuta, Piemonte, Italia")
    return charge(car, WORK, station="Ionity Torino")


@pytest.mark.parametrize("make, compass", [
    pytest.param(lambda car: charge(car, WORK, station="Ionity Torino"), True, id="a station without an address"),
    pytest.param(lambda car: charge(car, WORK), True, id="nothing names it"),
    pytest.param(lambda car: charge(car, HOME, kind="HOME"), True, id="at home"),
    pytest.param(lambda car: charge(car, (0.0, 18.0)), True, id="on the equator"),
    pytest.param(lambda car: charge(car, (51.0, 0.0)), True, id="on the prime meridian"),
    pytest.param(_station_with_its_address, False, id="a station with its address"),
    pytest.param(_inside_a_charging_place, False, id="inside a charging place"),
    pytest.param(_a_place_assigned, False, id="a place assigned"),
    pytest.param(_only_the_providers_full_text, False, id="an address of the provider's full text only"),
    pytest.param(lambda car: charge(car, (0.0, 0.0)), False, id="no GPS fix"),
    pytest.param(lambda car: charge(car, None), False, id="typed in, without coordinates"),
])
def test_the_compass_shows_only_where_an_address_is_missing(car, make, compass):
    cid = make(car)
    assert (look_up(cid) in drawer(car)) is compass


@pytest.mark.parametrize("answer, says", [
    pytest.param({"error": "Unable to geocode"}, "Nominatim has no address here", id="nothing there"),
    pytest.param(TimeoutError("timed out"), "Asking Nominatim failed: timed out", id="no answer"),
])
def test_a_lookup_on_request_that_finds_nothing_says_why(car, monkeypatch, answer, says):
    """Else the unchanged line would read as a button that did nothing; why, as the trip's 🧭 says it."""
    import geocode
    from test_the_places_of_recent_trips_are_looked_up import Providers, spot
    providers = Providers()
    providers.answers[("nominatim.openstreetmap.org", spot(*WORK))] = answer
    monkeypatch.setattr(geocode, "_get", providers)
    cid = charge(car, WORK)
    redrawn = car.client.post(f"/api/charges/{cid}/address").text
    assert providers.calls() == [spot(*WORK)]
    assert says in redrawn and look_up(cid) in redrawn


# ── the credit ────────────────────────────────────────────────────────────────

def test_a_list_of_charges_carries_the_credit_and_marks_the_addresses_from_openstreetmap(car):
    """The credit is always there; base.html shows it only while a card shows an address from OSM."""
    address(WORK, road="Corso Francia", locality="Torino")
    address(SHOP, provider="geoapify", road="Via Po", locality="Torino")
    osm = charge(car, WORK, station="Ionity Torino")
    keyed = charge(car, SHOP, at=DAY + timedelta(hours=3))
    for html in (drawer(car), car.client.get("/api/charges/search?q=torino").text):
        assert html.count(CREDIT) == 1 and "data-charges-list" in html
        assert osm_marked(html, osm) and not osm_marked(html, keyed)


# ── the search ────────────────────────────────────────────────────────────────

def test_the_search_finds_a_charge_by_its_town_or_its_charging_place(car):
    address(HOME, road="Via Roma", locality="Torino", display_name="Via Roma, Torino, Piemonte, Italia")
    address(WORK, road="Via Garibaldi", locality="Chieri", display_name="Via Garibaldi, Chieri, Piemonte, Italia")
    charging_place(car, HOME, "Casa")
    at_work = charge(car, WORK, station="Ionity Chieri")
    at_home = charge(car, HOME, at=DAY + timedelta(hours=3), kind="HOME", note="slow")

    def found(q):
        html = car.client.get(f"/api/charges/search?q={q}").text
        return sorted(int(i) for i in re.findall(r'data-charge-id="(\d+)"', html))
    assert found("garibaldi") == [at_work], "the address, also beside a station"
    assert found("casa") == [at_home]
    assert found("piemonte") == [at_work, at_home]
    assert found("slow") == [at_home] and found("ionity") == [at_work], "the note and the station as before"


def test_the_search_finds_a_charge_by_the_name_its_card_shows(car):
    """The provider's full text puts the street between a shop's name and its town."""
    address(SHOP, name="Caffè Aurora", road="Via Nizza", house_number="12", locality="Torino",
            display_name="Caffè Aurora, 12, Via Nizza, San Salvario, Torino, Piemonte, Italia")
    cid = charge(car, SHOP)
    assert where(drawer(car), cid) == "📍 Caffè Aurora, Torino"
    html = car.client.get("/api/charges/search", params={"q": "Caffè Aurora, Torino"}).text
    assert re.findall(r'data-charge-id="(\d+)"', html) == [str(cid)]


def test_the_search_finds_a_charge_by_the_place_assigned_to_it(car):
    charging_place(car, WORK, "Office")
    cid = charge(car, SHOP)
    db_reader.assign_charging_place(cid, place_id(car, "Office"))
    html = car.client.get("/api/charges/search?q=office").text
    assert re.findall(r'data-charge-id="(\d+)"', html) == [str(cid)]


# ── the export ────────────────────────────────────────────────────────────────

def test_the_csv_export_says_where_each_charge_happened(car):
    """Each charge's place as its card names it, and its address, also where a station names it."""
    address(WORK, road="Corso Francia", locality="Torino")
    address(HOME, road="Via Roma", locality="Torino")
    charge(car, WORK, station="Ionity Torino")
    charge(car, HOME, at=DAY + timedelta(hours=3))
    rows = list(csv.DictReader(io.StringIO(car.client.get("/api/export/charges.csv").text)))
    assert [(r["place"], r["address"]) for r in rows] == [("Via Roma, Torino", "Via Roma, Torino"),
                                                          ("Ionity Torino", "Corso Francia, Torino")]
    probe = {"vehicle_id": car.vid, "latitude": WORK[0], "longitude": WORK[1]}
    db_reader.charge_places([probe])
    helpers = set(probe) - {"vehicle_id", "latitude", "longitude", "place", "address"}
    assert helpers and not helpers & set(rows[0]), "what only a card needs stays out of the export"
