"""Each car measures its battery health against its own as-new capacity (#472, @moon-phoenix4).

A B10 and a B03X (the cloud still calls it A10) on one install: the battery health page put the
B03X at 82.5 %, and setting the B03X's SoH reference to its own ~53 kWh changed the B10's too.
`vehicles.capacity_nominal_kwh` has existed per car since the capacity went per car, and the
settings form even wrote it, but the health page and the form both read the one install-wide
setting `battery_capacity_nominal_kwh`. So every car was measured against the last value typed for
any of them.

The install-wide setting predates the per-car column. Like the install-wide capacity
(`_capacity_for_new_vehicle`), it belongs to the first car: another car never inherits it.
"""
import pytest

pytest.importorskip("fastapi", reason="web/main.py needs fastapi (absent in the minimal CI env)")
pytest.importorskip("httpx", reason="Starlette TestClient needs httpx")

import db as D
import db_reader
import main
from starlette.testclient import TestClient

B10 = "LB10000000000001"
A10 = "LA10000000000002"


@pytest.fixture
def two_cars(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    db = D.Database(path)
    db.ensure_vehicle(B10, "B10", 2025)               # vehicle 1
    db.ensure_vehicle(A10, "A10", 2026)               # vehicle 2, the B03X
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    db_reader.set_setting("setup_complete", "1")
    return db, TestClient(main.app)


def _reference(vin):
    db_reader.set_active_vehicle(vin)
    return db_reader.get_battery_health()["nominal_kwh"]


def _save(client, vin, capacity, reference):
    db_reader.set_active_vehicle(vin)
    r = client.post("/api/settings/capacity",
                    data={"battery_capacity_kwh": str(capacity), "battery_nominal_kwh": str(reference)})
    assert r.status_code == 200, r.text


def test_setting_one_cars_reference_leaves_the_other_alone(two_cars):
    _, client = two_cars
    _save(client, B10, 65.0, 67.1)
    _save(client, A10, 53.0, 53.0)
    assert _reference(A10) == 53.0
    assert _reference(B10) == 67.1


def test_the_settings_page_shows_the_reference_of_the_car_on_screen(two_cars):
    _, client = two_cars
    _save(client, B10, 65.0, 67.1)
    _save(client, A10, 53.0, 53.0)
    db_reader.set_active_vehicle(A10)
    page = client.get("/settings").text
    field = page.split('name="battery_nominal_kwh"', 1)[1][:200]
    assert 'value="53.0"' in field
    db_reader.set_active_vehicle(B10)
    page = client.get("/settings").text
    field = page.split('name="battery_nominal_kwh"', 1)[1][:200]
    assert 'value="67.1"' in field


def test_a_second_car_never_measured_against_the_first_cars_reference(two_cars):
    """An install that set its reference before the second car arrived: the B03X's own capacity is
    its reference until somebody types one, not the B10's 67.1."""
    db, _ = two_cars
    db_reader.set_setting("battery_capacity_nominal_kwh", "67.1")
    db._conn.execute("UPDATE vehicles SET capacity_kwh = 53.0, capacity_nominal_kwh = NULL WHERE vin = ?", (A10,))
    db._conn.commit()
    assert _reference(A10) == 53.0


def test_the_install_wide_reference_still_serves_the_first_car(two_cars):
    """A database written before the per-car column: the first car keeps the reference it had."""
    db, _ = two_cars
    db_reader.set_setting("battery_capacity_nominal_kwh", "67.1")
    db._conn.execute("UPDATE vehicles SET capacity_nominal_kwh = NULL")
    db._conn.commit()
    assert _reference(B10) == 67.1


def test_an_empty_reference_snapshots_the_cars_own_capacity(two_cars):
    """Sent empty, the form keeps the old snapshot: the capacity being replaced becomes the
    reference. The car's own capacity, not the install-wide one."""
    db, client = two_cars
    db._conn.execute("UPDATE vehicles SET capacity_nominal_kwh = NULL")
    db._conn.execute("UPDATE vehicles SET capacity_kwh = 52.0 WHERE vin = ?", (A10,))
    db._conn.commit()
    db_reader.set_setting("battery_capacity_kwh", "65.0")
    db_reader.set_active_vehicle(A10)
    r = client.post("/api/settings/capacity", data={"battery_capacity_kwh": "50.0", "battery_nominal_kwh": ""})
    assert r.status_code == 200, r.text
    assert _reference(A10) == 52.0


def test_the_poller_gives_the_install_wide_reference_to_the_first_car_only(tmp_path):
    db = D.Database(str(tmp_path / "p.db"))
    db.ensure_vehicle(B10, "B10", 2025)
    db.ensure_vehicle(A10, "A10", 2026)
    db._conn.execute("UPDATE vehicles SET capacity_nominal_kwh = NULL")
    db.set_setting("battery_capacity_nominal_kwh", "67.1")
    db._conn.commit()
    db._backfill_vehicle_capacity()
    rows = dict(db._conn.execute("SELECT vin, capacity_nominal_kwh FROM vehicles").fetchall())
    assert rows == {B10: 67.1, A10: None}
