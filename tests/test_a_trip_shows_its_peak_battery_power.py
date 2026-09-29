"""A trip's detail names the highest battery power out of the pack and back into it.

Each of the trip's points keeps the battery power of its poll (voltage times current, positive out of
the pack). The readings are several seconds apart, so a peak between two of them is missed: the
figures are a floor, and the row says so. On a range extender they are not shown, like the regen
figure, because with the generator running the pack's power is not the drive's.
"""
import re
from datetime import datetime, timedelta, timezone

import db as D
import db_reader
import pytest

START = datetime(2026, 9, 26, 10, 57, tzinfo=timezone.utc)


def _install(tmp_path, monkeypatch, *, power, reev=False):
    """A moving ten-minute drive, one point a minute; `power` = {minute: kW} kept on the points."""
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LVIN0000000000001','B10')")
    for key, value in (("is_reev", "1" if reev else "0"), ("timezone", "UTC"), ("setup_complete", "1")):
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
    c.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, duration_min,"
              " start_soc, end_soc) VALUES (1,1,?,?,12.0,10,80,77)",
              (START.isoformat(), (START + timedelta(minutes=10)).isoformat()))
    for k in range(10):
        c.execute("INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude, speed_kmh, soc,"
                  " power_kw) VALUES (1,?,?,9.0,?,?,?)",
                  ((START + timedelta(minutes=k)).isoformat(), 45.0 + k * 0.01, 60 + k, 80 - k * 0.3, power.get(k)))
    c.commit()
    pdb._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return db_reader


DRIVE = {1: 48.0, 3: 169.4, 5: -79.3, 7: -16.2}


def test_the_highest_power_out_and_back_in(tmp_path, monkeypatch):
    trip = _install(tmp_path, monkeypatch, power=DRIVE).get_trip_detail(1)
    assert trip["max_power_kw"] == pytest.approx(169.4)
    assert trip["max_regen_kw"] == pytest.approx(79.3)


def test_every_point_carries_its_polls_power(tmp_path, monkeypatch):
    points = _install(tmp_path, monkeypatch, power=DRIVE).get_trip_detail(1)["positions"]
    assert [p["power_kw"] for p in points][:6] == [None, 48.0, None, 169.4, None, -79.3]


def test_a_drive_without_regeneration_has_no_regen_figure(tmp_path, monkeypatch):
    trip = _install(tmp_path, monkeypatch, power={1: 48.0}).get_trip_detail(1)
    assert trip["max_power_kw"] == pytest.approx(48.0)
    assert trip["max_regen_kw"] is None


def test_no_readings_no_figures(tmp_path, monkeypatch):
    trip = _install(tmp_path, monkeypatch, power={}).get_trip_detail(1)
    assert trip["max_power_kw"] is None and trip["max_regen_kw"] is None


def _rows(tmp_path, monkeypatch, **kw):
    pytest.importorskip("fastapi", reason="web.main needs the production web dependencies")
    pytest.importorskip("httpx", reason="Starlette TestClient needs httpx")
    import main
    from starlette.testclient import TestClient

    for var in ("MATE_AUTH_PASSWORD", "SUPERVISOR_TOKEN", "HASSIO_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    _install(tmp_path, monkeypatch, **kw)
    html = TestClient(main.app).get("/trips/1").text
    return {m.group(1): m.group(0) for m in re.finditer(r"(Max power|Max regen).*?</div>", html, re.DOTALL)}


def test_the_page_prints_both_with_their_caveat(tmp_path, monkeypatch):
    rows = _rows(tmp_path, monkeypatch, power=DRIVE)
    assert "169 kW" in rows["Max power"] and "79 kW" in rows["Max regen"]
    for row in rows.values():
        assert "data-tip=" in row and "several seconds apart" in row


def test_a_range_extender_shows_neither(tmp_path, monkeypatch):
    assert _rows(tmp_path, monkeypatch, power=DRIVE, reev=True) == {}
