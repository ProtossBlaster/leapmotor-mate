"""The plain diagnostics text must say whether the cloud history arrived, and whether it carried fuel.

The encrypted BetaTester pack now carries the staged per-trip records themselves. But most owners
send the plain diagnostics .txt, and it could not answer the first question triage has to ask of a
range-extender: did the cloud history worker run at all, how many drives did it stage, and is
`driveReevOil` populated or zero? Without that, "the fuel is wrong" cannot be told apart from "the
cloud sent nothing" — and the second is not a Mate defect.

This file is attached to PUBLIC issues, so it reports COUNTS and RANGES only: no VIN, no accountId,
no per-drive rows. What a number can say here it says; what only a row could say belongs in the
encrypted pack.
"""
import hashlib
import json

import pytest

pytest.importorskip("cryptography", reason="db_reader needs cryptography")

_CLOUD_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS api_lab_cloud_history_records ("
    "kind TEXT NOT NULL, record_sha256 TEXT NOT NULL, source TEXT NOT NULL, "
    "imported_at TEXT NOT NULL, payload_json TEXT NOT NULL, PRIMARY KEY(kind,record_sha256))"
)

_BASE = {"accountId": "acct-0000-1111", "vin": "LFZTEST0000000001", "zone": "UTC+2",
         "totalMileage": 77.0, "totalMileageInMi": 47.8, "totalEnergy": 0.3,
         "maxSpeed": 100, "maxSpeedInMi": 62}


def _record(start_ms, oil):
    return dict(_BASE, routeStartTs=start_ms, routeEndTs=start_ms + 4_760_000, driveReevOil=oil)


def _section(tmp_path, monkeypatch, records, with_table=True):
    import db as D
    import db_reader

    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LFZTEST0000000001','C10')")
    if with_table:
        c.execute(_CLOUD_SCHEMA)
        for rec in records:
            payload = json.dumps(rec, sort_keys=True)
            c.execute("INSERT INTO api_lab_cloud_history_records VALUES (?,?,?,?,?)",
                      ("mileage", hashlib.sha256(payload.encode()).hexdigest(),
                       "api-v2-worker", "2026-09-28T10:00:00+00:00", payload))
    c.commit()
    pdb._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    import diagnostics
    monkeypatch.setattr(diagnostics.db_reader, "DB_PATH", path)
    return diagnostics._cloud_history_section()


def test_it_counts_the_staged_drives(tmp_path, monkeypatch):
    body = _section(tmp_path, monkeypatch, [_record(1789743240000, 4.9), _record(1789843240000, 0.0)])
    assert "2" in body, f"the number of staged drives is not reported: {body!r}"


def test_it_says_whether_the_fuel_field_is_populated(tmp_path, monkeypatch):
    body = _section(tmp_path, monkeypatch, [_record(1789743240000, 4.9), _record(1789843240000, 0.0)])
    assert "driveReevOil" in body, "the field is not named — triage cannot ask for it by name"
    assert "4.9" in body, "the largest fuel value is missing: zero-vs-populated is the question"


def test_a_bev_reads_as_all_zero_not_as_missing(tmp_path, monkeypatch):
    # A B10 stages the field on every record with value 0.0. That is an ANSWER, and must not look
    # like "the cloud sent nothing" — the two lead to opposite conclusions.
    body = _section(tmp_path, monkeypatch, [_record(1789743240000, 0.0), _record(1789843240000, 0.0)])
    assert "0 of 2" in body or "0/2" in body, \
        f"a populated-but-zero field must be distinguishable from an absent one: {body!r}"


def test_an_install_that_never_synced_says_so(tmp_path, monkeypatch):
    body = _section(tmp_path, monkeypatch, [], with_table=False)
    assert body.strip(), "an empty section answers nothing"
    assert "driveReevOil" not in body or "—" in body or "no" in body.lower(), \
        f"it must state that no cloud history was ever staged: {body!r}"


def test_it_leaks_neither_the_account_nor_the_vin(tmp_path, monkeypatch):
    body = _section(tmp_path, monkeypatch, [_record(1789743240000, 4.9)])
    assert "acct-0000-1111" not in body, "the cloud accountId leaked into a PUBLIC text"
    assert "LFZTEST0000000001" not in body, "the full VIN leaked into a PUBLIC text"
