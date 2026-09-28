"""A trip's detail gives the median of Mate's readings while moving under their average.

Both are taken over the same readings — those above walking pace — so a short fast stretch lifts the
average of a town drive while the median keeps its typical pace. The median is a grey line under the
average, like the split under the duration; a trip without a moving reading prints a dash and no line.
"""
import re
from datetime import datetime, timedelta, timezone

import db as D
import db_reader
import pytest

START = datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc)


def _speed_row(tmp_path, monkeypatch, speeds):
    pytest.importorskip("fastapi", reason="web.main needs the production web dependencies")
    pytest.importorskip("httpx", reason="Starlette TestClient needs httpx")
    import main
    from starlette.testclient import TestClient

    for var in ("MATE_AUTH_PASSWORD", "SUPERVISOR_TOKEN", "HASSIO_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LVIN0000000000001','B10')")
    for key, value in (("is_reev", "0"), ("timezone", "UTC"), ("setup_complete", "1")):
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
    c.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, duration_min, start_soc,"
              " end_soc) VALUES (1,1,?,?,8.0,10,80,78)",
              (START.isoformat(), (START + timedelta(minutes=10)).isoformat()))
    for k, kmh in enumerate(speeds):
        c.execute("INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude, speed_kmh, soc)"
                  " VALUES (1,?,?,9.0,?,?)", ((START + timedelta(minutes=k)).isoformat(), 45.0 + k * 0.01,
                                              kmh, 80 - k * 0.2))
    c.commit()
    pdb._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    html = TestClient(main.app).get("/trips/1").text
    row = re.search(r'<span class="text-slate-400">Avg speed</span>(.*?)</div>', html, re.DOTALL).group(1)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", row)).strip()


def test_the_average_and_the_median_of_the_moving_readings(tmp_path, monkeypatch):
    assert _speed_row(tmp_path, monkeypatch, [0, 30, 40, 50, 120, 0]) == "60 km/h 45 km/h median"


def test_a_trip_that_never_moved_prints_a_dash(tmp_path, monkeypatch):
    assert _speed_row(tmp_path, monkeypatch, [0, 0, 0]) == "—"
