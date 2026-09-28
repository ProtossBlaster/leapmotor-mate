"""A trip's detail gives the outside temperature of the drive as a range, lowest to highest,
instead of the temperature at departure and arrival.

With the outside-temperature switch on, each of the trip's points keeps the reading of its poll,
and those give the trip's real highest and lowest. With it off, only the
two lookups at the ends exist, and the range is taken from them — the row's info mark says which.
"""
import re
from datetime import datetime, timedelta, timezone

import db as D
import db_reader
import pytest

START = datetime(2026, 1, 12, 7, 10, tzinfo=timezone.utc)


def _install(tmp_path, monkeypatch, along, ends=(None, None)):
    """A moving ten-minute drive; `along` = {minute: outside °C} kept on that minute's point, `ends` =
    the trip's stored departure and arrival figures."""
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LVIN0000000000001','B10')")
    for key, value in (("is_reev", "0"), ("timezone", "UTC"), ("setup_complete", "1")):
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
    c.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, duration_min,"
              " start_soc, end_soc, outside_temp_start_c, outside_temp_end_c) VALUES (1,1,?,?,12.0,10,80,77,?,?)",
              (START.isoformat(), (START + timedelta(minutes=10)).isoformat(), *ends))
    for k in range(10):
        c.execute("INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude, speed_kmh,"
                  " soc, outside_temp_c) VALUES (1,?,?,9.0,?,?,?)",
                  ((START + timedelta(minutes=k)).isoformat(), 45.0 + k * 0.01, 60 + k, 80 - k * 0.3, along.get(k)))
    c.commit()
    pdb._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return db_reader


def test_the_readings_along_the_way_give_the_pair(tmp_path, monkeypatch):
    """A valley-to-pass drive: the lowest reading is in the middle, not at either end."""
    trip = _install(tmp_path, monkeypatch, {0: 6.5, 4: -1.5, 9: 3.0}, ends=(6.5, 3.0)).get_trip_detail(1)
    assert (trip["outside_temp_max_c"], trip["outside_temp_min_c"]) == (6.5, -1.5)


def test_without_readings_the_two_lookups_give_the_pair(tmp_path, monkeypatch):
    trip = _install(tmp_path, monkeypatch, {}, ends=(4.0, 7.5)).get_trip_detail(1)
    assert (trip["outside_temp_max_c"], trip["outside_temp_min_c"]) == (7.5, 4.0)


def test_nothing_known_nothing_shown(tmp_path, monkeypatch):
    trip = _install(tmp_path, monkeypatch, {}).get_trip_detail(1)
    assert trip["outside_temp_max_c"] is None and trip["outside_temp_min_c"] is None


def test_the_page_prints_the_range(tmp_path, monkeypatch):
    pytest.importorskip("fastapi", reason="web.main needs the production web dependencies")
    pytest.importorskip("httpx", reason="Starlette TestClient needs httpx")
    import main
    from starlette.testclient import TestClient

    for var in ("MATE_AUTH_PASSWORD", "SUPERVISOR_TOKEN", "HASSIO_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    _install(tmp_path, monkeypatch, {0: 6.5, 4: -1.5, 9: 3.0}, ends=(6.5, 3.0))
    html = TestClient(main.app).get("/trips/1").text
    row = re.search(r"Outside temperature.*?</div>", html, re.DOTALL).group(0)
    assert "-1.5 – 6.5 °C" in row, "a minus sign against the dash, or not lowest to highest"
    assert "→" not in row
    assert "data-tip=" in row and "along the way" in row
