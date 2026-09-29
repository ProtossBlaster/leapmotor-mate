"""The bundle must carry the cloud's PER-TRIP records — the table that holds `driveReevOil`.

Since 4.5.3 (`d7b832a`) every install, range-extender accounts included, stages the cloud's own
per-trip history in `api_lab_cloud_history_records`: eleven fields per drive, among them
`driveReevOil` — the fuel that trip burned, by the car's own cloud. Measured 27/09/2026 on 183
records of a B10: the field is present on all of them and reads 0.0, which is the right answer for a
BEV and no answer at all for a REEV.

It is in LITRES — settled on 29/09/2026 against @ebagnoli's 19/09 drive, whose official app figures
(77 km / 0.3 kWh / 4.9 L) the record matches on all three fields. Whether a range-extender populates
it at all,
is not measured — and it cannot be, because the bundle never carried those rows. `cloud_probes.json`
carries the AGGREGATE endpoints (getEC, the weekly rank, mileage/energy/detail) and not
`mileage/daily/detail/page`, so the field sat in the tester's database and never reached us.

Exporting the STORED rows, not another live probe: on the one readable bundle we have (21/09) all
five live probes came back `{"code":3,"message":"Token is invalid","data":null}` — the probe logs in
at export time and returns nothing when the token has expired. A staged table needs no token.

Allow-listed like the trips. `accountId` is dropped and the VIN is masked: these rows identify an
account, and the pack travels. `started_at`/`ended_at` are derived as ISO so a record can be matched
to a trip without doing millisecond arithmetic by hand — which is the whole point: one REEV trip
whose litres we know independently calibrates the unit.
"""
import json
import zipfile

import pytest

pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI env)")

_CLOUD_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS api_lab_cloud_history_records ("
    "kind TEXT NOT NULL, record_sha256 TEXT NOT NULL, source TEXT NOT NULL, "
    "imported_at TEXT NOT NULL, payload_json TEXT NOT NULL, PRIMARY KEY(kind,record_sha256))"
)

# The shape measured against the real cloud on 27/09/2026 — eleven fields, nothing else. The values
# here are @ebagnoli's trip of 19/09 as his official app shows it (beta #13): 77 km, 0.3 kWh and
# 4.9 L, 16:54→18:13. routeStartTs/EndTs are epoch MILLISECONDS.
_REEV_RECORD = {
    "accountId": "acct-0000-1111", "vin": "LFZTEST0000000001", "zone": "UTC+2",
    "routeStartTs": 1789743240000, "routeEndTs": 1789748000000,
    "totalMileage": 77.0, "totalMileageInMi": 47.8, "totalEnergy": 0.3,
    "maxSpeed": 100, "maxSpeedInMi": 62, "driveReevOil": 4.9,
}


def _bundle(tmp_path, monkeypatch, records=(_REEV_RECORD,), make_table=True):
    """The real export endpoint, decrypted back (encrypt_bundle stubbed to a pass-through)."""
    import asyncio
    import hashlib
    import io

    import db as D
    import db_reader

    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LFZTEST0000000001','C10')")
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('setup_complete','1')")
    if make_table:
        c.execute(_CLOUD_SCHEMA)
        for rec in records:
            payload = rec if isinstance(rec, str) else json.dumps(rec, sort_keys=True)
            c.execute("INSERT INTO api_lab_cloud_history_records VALUES (?,?,?,?,?)",
                      ("mileage", hashlib.sha256(payload.encode()).hexdigest(),
                       "api-v2-worker", "2026-09-28T10:00:00+00:00", payload))
    c.commit()
    pdb._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)

    import main
    monkeypatch.setattr(main.db_reader, "DB_PATH", path)
    monkeypatch.setattr(main.research, "research_enabled", lambda: True)
    monkeypatch.setattr(main.command_client, "get_consumption_probe_raw", lambda: None)
    monkeypatch.setattr(main.command_client, "get_fresh_signals", lambda: {"1204": 88})
    monkeypatch.setattr(main.research, "encrypt_bundle", lambda b: b)

    resp = asyncio.run(main.research_export())
    return zipfile.ZipFile(io.BytesIO(resp.body))


def test_the_bundle_carries_the_cloud_trip_records(tmp_path, monkeypatch):
    z = _bundle(tmp_path, monkeypatch)
    assert "cloud_trip_records.csv" in z.namelist(), \
        f"no cloud_trip_records.csv — driveReevOil still cannot leave the tester's DB: {z.namelist()}"


def test_it_carries_the_fuel_field_and_its_value(tmp_path, monkeypatch):
    body = _bundle(tmp_path, monkeypatch).read("cloud_trip_records.csv").decode()
    assert "driveReevOil" in body, "no driveReevOil column — the one field this export exists for"
    assert "4.9" in body, "the fuel value itself is missing"
    assert "0.3" in body and "77" in body, "the energy and distance that CHECK the match are missing"


def test_a_record_can_be_matched_to_a_trip_without_millisecond_arithmetic(tmp_path, monkeypatch):
    body = _bundle(tmp_path, monkeypatch).read("cloud_trip_records.csv").decode()
    assert "started_at" in body and "ended_at" in body, \
        "no readable start/end — calibrating the unit means finding the matching trip by hand"
    assert "2026-09-" in body, f"the derived timestamps are not ISO dates: {body[:300]}"


def test_it_does_not_carry_the_account_or_the_whole_vin(tmp_path, monkeypatch):
    body = _bundle(tmp_path, monkeypatch).read("cloud_trip_records.csv").decode()
    assert "acct-0000-1111" not in body, "the cloud accountId leaked into a bundle that travels"
    assert "LFZTEST0000000001" not in body, "the full VIN leaked — it must be masked like everywhere else"


def test_an_install_without_the_table_still_builds_a_bundle(tmp_path, monkeypatch):
    # Every install that has not run the cloud history worker — which is most of them.
    z = _bundle(tmp_path, monkeypatch, make_table=False)
    assert "trips.csv" in z.namelist(), "a missing cloud table broke the whole export"
    body = z.read("cloud_trip_records.csv").decode()
    assert "driveReevOil" in body and len(body.strip().splitlines()) == 1, \
        f"expected a header-only file when there is nothing staged, got: {body[:200]}"


def test_a_corrupt_payload_does_not_cost_the_good_rows(tmp_path, monkeypatch):
    z = _bundle(tmp_path, monkeypatch, records=("{not json", _REEV_RECORD))
    body = z.read("cloud_trip_records.csv").decode()
    assert "4.9" in body, "one unparseable row took the good one with it"
