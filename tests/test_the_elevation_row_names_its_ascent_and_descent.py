"""A trip's elevation row is called Ascent / descent and lists the metres climbed, then descended.

Its two figures are sums along the drive, not the lowest and highest point, and its arrows are the
only ones left in the trip's detail. The label says what they add up; the figures follow its order,
and an info mark says how they are counted.
"""
import re
from datetime import datetime, timedelta, timezone

import db as D
import db_reader
import pytest

START = datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc)


def test_the_row_names_the_sums_and_follows_its_label(tmp_path, monkeypatch):
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
              " end_soc, elevation_gain_m, elevation_loss_m) VALUES (1,1,?,?,12.0,10,80,77,71,73)",
              (START.isoformat(), (START + timedelta(minutes=10)).isoformat()))
    for k in range(10):
        c.execute("INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude, speed_kmh, soc)"
                  " VALUES (1,?,?,9.0,?,?)", ((START + timedelta(minutes=k)).isoformat(), 45.0 + k * 0.01,
                                              60 + k, 80 - k * 0.3))
    c.commit()
    pdb._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)

    html = TestClient(main.app).get("/trips/1").text
    row = re.search(r'<span class="text-slate-400">Ascent / descent (<span[^>]*data-tip="([^"]*)"[^>]*>ⓘ</span>)'
                    r'</span>(.*?)</div>', html, re.DOTALL)
    assert row, "the elevation row does not say its figures are the ascent and the descent, with an info mark"
    assert "added up" in row.group(2), "the info mark does not say the figures are sums"
    assert re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", row.group(3)).replace("&nbsp;", " ")).strip() == "↑ 71 m ↓ 73 m"
