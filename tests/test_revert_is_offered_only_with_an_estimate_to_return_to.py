"""A trip page offers "Revert to estimate" only when there is an estimate to go back to.

Converting a trip to the cloud's figure keeps its battery estimate aside, and Revert puts it back.
A trip filed from the cloud's history never had one: Revert there asked to confirm, reloaded the
page and changed nothing.
"""
from datetime import datetime, timedelta, timezone

import db as D
import db_reader
import pytest

START = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


def _install(tmp_path, monkeypatch, efficiency, cloud_kwh=None):
    """One 12 km drive at `efficiency` kWh/100 km; with `cloud_kwh`, filed as the history import files
    it: the cloud's energy, settled, and no estimate of Mate's own kept aside."""
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LVIN0000000000001','B10')")
    for key, value in (("is_reev", "0"), ("timezone", "UTC"), ("setup_complete", "1")):
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
    c.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, duration_min,"
              " efficiency_kwh_100km, ec_kwh, ec_tried, ec_stable) VALUES (1,1,?,?,12.0,20,?,?,?,?)",
              (START.isoformat(), (START + timedelta(minutes=20)).isoformat(), efficiency, cloud_kwh,
               int(cloud_kwh is not None), int(cloud_kwh is not None)))
    c.commit()
    pdb._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)


def _offers_revert(monkeypatch):
    pytest.importorskip("fastapi", reason="web.main needs the production web dependencies")
    pytest.importorskip("httpx", reason="Starlette TestClient needs httpx")
    import main
    from starlette.testclient import TestClient

    for var in ("MATE_AUTH_PASSWORD", "SUPERVISOR_TOKEN", "HASSIO_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    page = TestClient(main.app).get("/trips/1", follow_redirects=False)
    assert page.status_code == 200, page.status_code
    return "revert-ec" in page.text


def test_a_converted_trip_offers_its_estimate_back(tmp_path, monkeypatch):
    _install(tmp_path, monkeypatch, 15.0)
    db_reader.store_trip_ec(1, {"driving_kwh": 1.6, "ac_kwh": 0.2, "other_kwh": 0.1, "total_kwh": 1.9},
                            12.0, True, stable=True)
    assert _offers_revert(monkeypatch)


def test_a_trip_from_the_clouds_history_has_no_estimate_to_return_to(tmp_path, monkeypatch):
    _install(tmp_path, monkeypatch, 15.8, cloud_kwh=1.9)
    assert not _offers_revert(monkeypatch)
