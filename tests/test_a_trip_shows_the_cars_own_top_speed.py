"""A trip's "Max speed" is the car's own figure whenever its cloud record of the drive is matched.

Mate derives the top speed from its own samples, and while driving a new frame arrives only every
several seconds (a median of 11.6 s on a B10, 21–28/09/2026). A short peak falls between two of them:
on 24 of 29 drives the sampled maximum was below the one the car reported to its cloud, by up to
21 km/h (147 against 126). The car's record carries `maxSpeed`, and Mate already matches those
records to its trips for the energy figure — the same match, under the same safeguards, now gives
the top speed. Where there is no match the samples stay, and the page says why the number may be
low.
"""
import json
import re
from datetime import datetime, timedelta, timezone

import db as D
import db_reader
import pytest

VIN = "LVIN0000000000001"
START = datetime(2026, 9, 26, 10, 57, tzinfo=timezone.utc)


def _install(tmp_path, monkeypatch, *, records=(), sampled_top=126, merged=False):
    """One 24 km drive (or two pieces joined into one) with samples topping at `sampled_top`.
    `records` = [(minutes_from_START, duration_min, km, max_speed)] staged cloud records."""
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,?,'B10')", (VIN,))
    for key, value in (("is_reev", "0"), ("timezone", "UTC"), ("setup_complete", "1")):
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
    pieces = [(1, 0, 20, 24.0)] if not merged else [(1, 0, 10, 12.0), (2, 12, 10, 12.0)]
    for tid, offset, duration, km in pieces:
        a = START + timedelta(minutes=offset)
        b = a + timedelta(minutes=duration)
        c.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, duration_min,"
                  " start_soc, end_soc, merged_into_id) VALUES (?,1,?,?,?,?,80,76,?)",
                  (tid, a.isoformat(), b.isoformat(), km, duration, 1 if tid == 2 else None))
        for k in range(duration):
            c.execute("INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude,"
                      " speed_kmh, soc) VALUES (?,?,?,9.0,?,?)",
                      (tid, (a + timedelta(minutes=k)).isoformat(), 45.0 + (offset + k) * 0.01,
                       sampled_top if k == 3 else 60 + k, 80 - (offset + k) * 0.2))
    c.execute("CREATE TABLE IF NOT EXISTS api_lab_cloud_history_records"
              " (id INTEGER PRIMARY KEY, kind TEXT, payload_json TEXT)")
    for offset, duration, km, top in records:
        a = START + timedelta(minutes=offset)
        b = a + timedelta(minutes=duration)
        payload = {"vin": VIN, "routeStartTs": int(a.timestamp() * 1000),
                   "routeEndTs": int(b.timestamp() * 1000), "totalEnergy": 3.1, "totalMileage": km}
        if top is not None:
            payload["maxSpeed"] = top
        c.execute("INSERT INTO api_lab_cloud_history_records (kind, payload_json) VALUES ('mileage',?)",
                  (json.dumps(payload),))
    c.commit()
    pdb._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return db_reader


def test_the_cars_figure_replaces_the_sampled_one(tmp_path, monkeypatch):
    d = _install(tmp_path, monkeypatch, records=[(0, 20, 24.0, 147)])
    trip = d.get_trip_detail(1)
    assert trip["max_speed_kmh"] == 147
    assert trip["max_speed_sampled"] is False


def test_without_a_record_the_samples_stay(tmp_path, monkeypatch):
    d = _install(tmp_path, monkeypatch)
    trip = d.get_trip_detail(1)
    assert trip["max_speed_kmh"] == 126
    assert trip["max_speed_sampled"] is True


def test_a_record_the_energy_match_rejects_gives_no_speed_either(tmp_path, monkeypatch):
    """Twice the distance: not this drive, whatever its times say."""
    d = _install(tmp_path, monkeypatch, records=[(0, 20, 48.0, 147)])
    trip = d.get_trip_detail(1)
    assert trip["max_speed_kmh"] == 126
    assert trip["max_speed_sampled"] is True


def test_a_record_without_a_top_speed_keeps_the_samples(tmp_path, monkeypatch):
    d = _install(tmp_path, monkeypatch, records=[(0, 20, 24.0, None)])
    trip = d.get_trip_detail(1)
    assert trip["energy_source"] == "cloud"
    assert (trip["max_speed_kmh"], trip["max_speed_sampled"]) == (126, True)


def test_two_versions_that_disagree_on_the_top_speed_give_none(tmp_path, monkeypatch):
    d = _install(tmp_path, monkeypatch, records=[(0, 20, 24.0, 147), (0, 20, 24.0, 139)])
    assert d.get_trip_detail(1)["max_speed_kmh"] == 126


def test_a_joined_journey_takes_the_higher_of_its_pieces(tmp_path, monkeypatch):
    d = _install(tmp_path, monkeypatch, merged=True,
                 records=[(0, 10, 12.0, 121), (12, 10, 12.0, 147)])
    assert d.get_trip_detail(1)["max_speed_kmh"] == 147


def _page(tmp_path, monkeypatch, **kw):
    pytest.importorskip("fastapi", reason="web.main needs the production web dependencies")
    pytest.importorskip("httpx", reason="Starlette TestClient needs httpx")
    import main
    from starlette.testclient import TestClient

    for var in ("MATE_AUTH_PASSWORD", "SUPERVISOR_TOKEN", "HASSIO_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    _install(tmp_path, monkeypatch, **kw)
    html = TestClient(main.app).get("/trips/1").text
    row = re.search(r"Max speed.*?</div>", html, re.DOTALL)
    assert row, "the Max speed row is gone"
    return row.group(0)


def test_the_page_prints_the_cars_figure_without_a_caveat(tmp_path, monkeypatch):
    row = _page(tmp_path, monkeypatch, records=[(0, 20, 24.0, 147)])
    assert "147" in row
    assert "data-tip" not in row


def test_the_page_says_why_a_sampled_figure_may_be_low(tmp_path, monkeypatch):
    row = _page(tmp_path, monkeypatch)
    assert "126" in row
    assert "data-tip=" in row and "several seconds apart" in row
