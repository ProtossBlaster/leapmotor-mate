"""The Events rows of a charge name where it happened, as its card on Charges does (db_reader.charge_places):
the place assigned to it, the station, the charging place holding its point, or the address stored for
its spot. An address from OpenStreetMap is credited once under the list, and a search finds the charge
by its address.
"""
import re
from datetime import datetime, timedelta, timezone

from events_fixture import Car, row_text, web
from test_the_events_name_a_trips_start_and_end import CREDIT, _address

NOW = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=2)
HOME, SHOP = (45.0700, 7.6800), (45.0900, 7.7000)


def _charge(car, point, ended=True, station=None):
    cur = car.db._conn.execute(
        "INSERT INTO charges (vehicle_id, started_at, ended_at, energy_added_kwh, latitude, longitude, location_name)"
        " VALUES (?, ?, ?, 9.0, ?, ?, ?)",
        (car.vid, (NOW - timedelta(hours=1)).isoformat(), NOW.isoformat() if ended else None, *point, station))
    car.db._conn.commit()
    return cur.lastrowid


def _ids(html):
    return sorted({int(i) for i in re.findall(r'id="ev-charge-(\d+)-(?:on|off)"', html)})


def test_a_charges_rows_say_where_it_happened(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    _address(SHOP, name="Panificio", road="Via Nizza", locality="Torino",
             display_name="Panificio, Via Nizza, San Salvario, Torino, Piemonte, Italia")
    cid = _charge(car, SHOP)
    html = client.get("/events").text
    assert "Panificio, Torino" in row_text(html, f"ev-charge-{cid}-on")
    assert "Panificio, Torino" in row_text(html, f"ev-charge-{cid}-off")
    assert html.count(CREDIT) == 1
    assert _ids(client.get("/api/events/search?q=salvario").text) == [cid], "found by its full address"


def test_the_charge_in_progress_is_named_by_its_charging_place(tmp_path, monkeypatch):
    """Before the address of its spot, as on its card: the place belongs to the car the charge belongs to."""
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    car.db._conn.execute("INSERT INTO charging_places (vehicle_id, name, latitude, longitude, radius_m, rate, enabled)"
                         " VALUES (?, 'Casa', ?, ?, 100, 0.2, 1)", (car.vid, *HOME))
    car.db._conn.commit()
    _address(HOME, road="Via Nizza", house_number="7", locality="Torino")
    cid = _charge(car, HOME, ended=False)
    html = client.get("/events").text
    assert "Casa" in row_text(html, f"ev-charge-{cid}-on") and "Via Nizza" not in row_text(html, f"ev-charge-{cid}-on")
    assert CREDIT not in html


def test_a_station_names_the_charge_before_its_address(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    _address(SHOP, road="Via Nizza", locality="Torino")
    cid = _charge(car, SHOP, station="Ładowarka przy Lipowej")
    html = client.get("/events").text
    assert "Ładowarka przy Lipowej" in row_text(html, f"ev-charge-{cid}-off")
    assert CREDIT not in html, "the row shows no address"
