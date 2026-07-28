"""CSV export (Viaggi/Ricariche "Esporta CSV") — regression: a merged trip's dict carries
an extra "segment_ids" key (_trip_group_stats, only present when there ARE merged
segments) that the FIRST trip in the list may not have. csv.DictWriter's default fieldnames
= first row's keys, so it raised ValueError the moment it reached a merged trip later in
the list — a real 500 a user hit clicking "Esporta CSV" on Viaggi the moment their history
contained even one merged trip, silently reproducing the bug every time until fixed here.
"""
import asyncio
import csv
import io

import pytest

import db as D
import db_reader


def _setup(tmp_path, monkeypatch):
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    return pdb


def _seed_trip(pdb, tid, started, *, ended=None, km=10.0, eff=18.0, merged_into=None):
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc,"
        " efficiency_kwh_100km, duration_min, merged_into_id) VALUES (?,1,?,?,?,?,?,?,?,?)",
        (tid, started, ended or started, km, 60, 50, eff, 15.0, merged_into))
    pdb._conn.commit()


# ── _csv_response: the general fix ────────────────────────────────────────────────

def test_csv_response_handles_rows_with_different_key_sets():
    pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI test env)")
    import main
    rows = [{"id": 1, "distance_km": 10.0}, {"id": 2, "distance_km": 20.0, "segment_ids": [2, 3]}]

    resp = main._csv_response(rows, "test.csv")

    reader = csv.DictReader(io.StringIO(resp.body.decode()))
    assert reader.fieldnames == ["id", "distance_km"]   # segment_ids dropped, no crash
    out = list(reader)
    assert out[0]["id"] == "1" and out[1]["id"] == "2"


def test_csv_response_drops_list_and_dict_valued_fields():
    pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI test env)")
    import main
    rows = [{"id": 1, "tags": ["a", "b"], "meta": {"k": "v"}, "note": "fine"}]

    resp = main._csv_response(rows, "test.csv")

    reader = csv.DictReader(io.StringIO(resp.body.decode()))
    assert reader.fieldnames == ["id", "note"]


def test_csv_response_empty_rows_returns_just_headers_row_none():
    pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI test env)")
    import main
    resp = main._csv_response([], "test.csv")
    assert resp.body.decode() == ""


# ── export_trips_csv: end-to-end regression ──────────────────────────────────────

def test_export_trips_csv_does_not_crash_with_a_merged_trip(tmp_path, monkeypatch):
    pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI test env)")
    import main
    pdb = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(main.db_reader, "DB_PATH", str(tmp_path / "t.db"))
    # trip 1 has NO children (no segment_ids key) and sorts FIRST (most recent) —
    # exactly the ordering that broke csv.DictWriter's default fieldnames.
    _seed_trip(pdb, 1, "2026-07-10T10:00:00+00:00")
    _seed_trip(pdb, 2, "2026-07-01T10:00:00+00:00")           # merge parent
    _seed_trip(pdb, 3, "2026-07-01T10:20:00+00:00", merged_into=2)   # merge child

    resp = asyncio.run(main.export_trips_csv())

    body = resp.body.decode()
    reader = csv.DictReader(io.StringIO(body))
    rows = list(reader)
    assert "segment_ids" not in reader.fieldnames
    assert {r["id"] for r in rows} == {"1", "2"}   # merged child (3) not listed on its own


def test_export_charges_csv_still_works(tmp_path, monkeypatch):
    pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI test env)")
    import main
    pdb = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(main.db_reader, "DB_PATH", str(tmp_path / "t.db"))
    pdb._conn.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc)"
        " VALUES (1,1,'2026-07-01T10:00:00+00:00','2026-07-01T10:30:00+00:00',40,80)")
    pdb._conn.commit()

    resp = asyncio.run(main.export_charges_csv())

    rows = list(csv.DictReader(io.StringIO(resp.body.decode())))
    assert rows[0]["id"] == "1"
