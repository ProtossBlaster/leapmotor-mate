"""The Overview shows the cable while the charger holds the charge.

A wallbox on a schedule takes the cable and gives no current until its window opens. The car then
reports no charge session (signal 1149 at 0) while its AC port reports the cable (signal 47 at 1),
for hours. The pages read the cable from the session or from the AC port while parked: the cable
tag on the Overview, the car picture and its cache, the Commands page badge, the "Fully charged"
badge on the Charges page and the wallbox tile's gate. The charge session the poller keeps is untouched.
"""
import datetime as dt
from types import SimpleNamespace

import pytest

pytest.importorskip("httpx", reason="Starlette TestClient needs httpx")

import db as D
import db_reader
import main
from starlette.testclient import TestClient

VIN = "LVIN0000000000001"


@pytest.fixture
def car(tmp_path, monkeypatch):
    """A car in the database; `car.frame(**cols)` records its latest reading, `car.get(path)` a page."""
    path = str(tmp_path / "t.db")
    c = D.Database(path)._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,?,'B10')", (VIN,))
    for key, value in (("timezone", "UTC"), ("setup_complete", "1"), ("language", "en")):
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
    c.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setattr(db_reader, "_current_vehicle_id", lambda: 1)
    client = TestClient(main.app)

    def frame(**cols):
        cols = {"vehicle_id": 1, "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(), "soc": 87.3,
                "gear": "P", "speed_kmh": 0, "charging": 0, "plug_connected": 0, **cols}
        c.execute(f"INSERT INTO positions ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", list(cols.values()))
        c.commit()

    yield SimpleNamespace(frame=frame, get=lambda path: client.get(path).text)
    c.close()


def test_the_cable_the_port_reports_shows_without_a_session(car):
    car.frame(plug_connected=0, ac_port_mode=1)
    assert db_reader.get_latest_status()["cable_connected"] is True
    hero = car.get("/api/overview-hero")
    assert "Cable connected" in hero and "87%" in hero


def test_a_charge_session_shows_the_cable_as_before(car):
    car.frame(plug_connected=1, ac_port_mode=0)
    assert db_reader.get_latest_status()["cable_connected"] is True
    assert "Cable connected" in car.get("/api/overview-hero")


def test_no_cable_no_tag(car):
    car.frame(plug_connected=0, ac_port_mode=0)
    assert db_reader.get_latest_status()["cable_connected"] is False
    hero = car.get("/api/overview-hero")
    assert "Cable connected" not in hero and "Parked" in hero


def test_a_moving_car_has_no_cable_in_its_port(car):
    car.frame(plug_connected=0, ac_port_mode=1, gear="D", speed_kmh=40)
    assert db_reader.get_latest_status()["cable_connected"] is False


def test_the_v2l_adapter_is_not_a_charging_cable(car):
    car.frame(plug_connected=0, ac_port_mode=2)
    assert db_reader.get_latest_status()["cable_connected"] is False


def test_a_reading_without_a_plug_says_nothing(car):
    car.frame(plug_connected=None, ac_port_mode=None)
    assert db_reader.get_latest_status()["cable_connected"] is None


def test_the_charges_page_badge_reads_the_port(car):
    car.frame(plug_connected=0, ac_port_mode=1, charge_completed=1)
    assert "Fully charged" in car.get("/charges")


def test_the_car_picture_is_redrawn_when_the_port_reports_the_cable(car, monkeypatch, tmp_path):
    """The composed picture is memoised by the body state; the cable is part of it, however it is read."""
    import asyncio

    import car_image
    import command_client
    drawn = []
    monkeypatch.setattr(command_client, "get_car_picture_package", lambda: b"PKG")
    monkeypatch.setattr(main.command_client, "get_car_picture_package", lambda: b"PKG")
    monkeypatch.setattr(car_image, "compose", lambda pkg, status: (drawn.append(status.get("cable_connected")) or b"IMG", "image/png"))
    monkeypatch.setattr(main.car_image, "compose", lambda pkg, status: (drawn.append(status.get("cable_connected")) or b"IMG", "image/png"))
    main._car_image_memo.clear()
    main._car_pic_boot_refresh = False
    car.frame(plug_connected=0, ac_port_mode=0)
    asyncio.run(main.car_picture())
    car.frame(plug_connected=0, ac_port_mode=1)
    asyncio.run(main.car_picture())
    assert drawn == [False, True]


def test_the_commands_page_badge_reads_the_port(car):
    car.frame(plug_connected=0, ac_port_mode=1)
    assert "Cable connected" in car.get("/commands")
    car.frame(plug_connected=0, ac_port_mode=0)
    assert "Cable not connected" in car.get("/commands")


def test_the_wallbox_tile_reads_the_port(car, monkeypatch):
    """The tile shows the wallbox's session only while this car is on it."""
    live = {"configured": True, "charging": False, "power_kw": 0.0, "energy_kwh": None, "status": "Wait for car",
            "speed": None, "speed_unit": "", "max_power": None, "max_power_unit": ""}
    monkeypatch.setattr(main.ha_client, "get_live", lambda: dict(live))
    car.frame(plug_connected=0, ac_port_mode=1)
    assert "not connected" not in car.get("/api/wallbox/live")
    car.frame(plug_connected=0, ac_port_mode=0)
    assert "not connected" in car.get("/api/wallbox/live")
