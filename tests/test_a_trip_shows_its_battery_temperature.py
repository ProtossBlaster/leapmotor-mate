"""A trip's detail gives the battery temperature of the drive as a range, lowest to highest.

The car reports one battery temperature: its coldest cell's, in whole degrees (signal 1182, kept on
each of the trip's points) — which the row's info mark says. In winter the range
shows how cold the pack was and how far the drive warmed it; the chart has the course.
"""
import re
from datetime import datetime, timedelta, timezone

import db as D
import db_reader
import pytest

START = datetime(2026, 1, 12, 7, 10, tzinfo=timezone.utc)


def _install(tmp_path, monkeypatch, temps):
    """A moving ten-minute drive; `temps` = {minute: coldest-cell °C} kept on that minute's point."""
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LVIN0000000000001','B10')")
    for key, value in (("is_reev", "0"), ("timezone", "UTC"), ("setup_complete", "1")):
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
    c.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, duration_min,"
              " start_soc, end_soc) VALUES (1,1,?,?,12.0,10,80,77)",
              (START.isoformat(), (START + timedelta(minutes=10)).isoformat()))
    for k in range(10):
        at = START + timedelta(minutes=k)
        c.execute("INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude, speed_kmh,"
                  " soc, battery_temp_c) VALUES (1,?,?,9.0,?,?,?)",
                  (at.isoformat(), 45.0 + k * 0.01, 60 + k, 80 - k * 0.3, temps.get(k)))
    c.commit()
    pdb._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return db_reader


def test_the_coldest_cells_highest_and_lowest_reading(tmp_path, monkeypatch):
    trip = _install(tmp_path, monkeypatch, {0: 4, 2: 3, 5: 6, 9: 9}).get_trip_detail(1)
    assert (trip["battery_temp_max_c"], trip["battery_temp_min_c"]) == (9, 3)


def test_no_reading_no_figure(tmp_path, monkeypatch):
    trip = _install(tmp_path, monkeypatch, {}).get_trip_detail(1)
    assert trip["battery_temp_max_c"] is None and trip["battery_temp_min_c"] is None


def test_the_page_prints_it_after_the_power_and_names_the_coldest_cell(tmp_path, monkeypatch):
    pytest.importorskip("fastapi", reason="web.main needs the production web dependencies")
    pytest.importorskip("httpx", reason="Starlette TestClient needs httpx")
    import main
    from starlette.testclient import TestClient

    for var in ("MATE_AUTH_PASSWORD", "SUPERVISOR_TOKEN", "HASSIO_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    _install(tmp_path, monkeypatch, {0: 4, 2: 3, 5: 6, 9: 9})
    html = TestClient(main.app).get("/trips/1").text
    row = re.search(r"Battery temp.*?</div>", html, re.DOTALL).group(0)
    assert "3 – 9 °C" in row, "the readings are not one range, lowest to highest"
    assert "data-tip=" in row and "coldest cell" in row
    assert html.index("Max regen") < html.index("Battery temp") < html.index("Ascent / descent")


def test_a_range_whose_ends_print_the_same_is_one_figure(tmp_path, monkeypatch):
    pytest.importorskip("fastapi", reason="web.main needs the production web dependencies")
    pytest.importorskip("httpx", reason="Starlette TestClient needs httpx")
    import main
    from starlette.testclient import TestClient

    for var in ("MATE_AUTH_PASSWORD", "SUPERVISOR_TOKEN", "HASSIO_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    _install(tmp_path, monkeypatch, {0: 20, 5: 20.2, 9: 20})
    row = re.search(r"Battery temp.*?</div>", TestClient(main.app).get("/trips/1").text, re.DOTALL).group(0)
    assert row.count("20 °C") == 1 and "–" not in row
