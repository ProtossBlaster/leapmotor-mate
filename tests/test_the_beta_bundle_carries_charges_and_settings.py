"""The BetaTester bundle must carry the charges and the behaviour settings (beta #30, @pdifeo).

His note opens with *"Ricarica e poi ritorno alla base"* — a charge, then the drive home on the
generator. The bundle he attached carries 96,987 raw signal rows, 98 trips, his own notes and the
cloud probes… and **not one charge**. The half of his day he wrote about first is invisible.

It has cost us before and we simply did not name it:

  * beta #29 (@michapr) was **entirely** about one charge recorded as two, and it had to be answered
    from a *diagnostics* bundle — a second, different file we asked the tester to produce;
  * a range-extender's cost is the paid electric stock drawn down FIFO, so **no cost question from a
    beta tester can be checked at all** without charges.

And the behaviour settings, for the same reason a day later: #250 was solved by ten lines of them —
a charge-detection floor of 13.5 A on a car that charges at 13.0-13.4 A, which recorded half a
charge and read as lost data. Without those lines on a beta report we would hunt a defect that isn't
there.

Both blocks already exist, already redacted, in the diagnostics bundle: the charge line carries no
coordinates, no location name and no free text, and a test holds that. On 17 days of a
range-extender the charges are 20-40 rows — about 5 KB against a bundle whose raw-signal log is
2.6 MB, 98% of the payload.
"""
import json
import zipfile

import pytest

pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI env)")


class _Req:
    headers = {"x-ingress-path": ""}
    cookies: dict = {}
    query_params: dict = {}


def _bundle(tmp_path, monkeypatch):
    """The real endpoint, decrypted back — the file a tester actually attaches."""
    import asyncio
    import io

    import db as D
    import db_reader
    import research

    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LFZTEST0000000001','C10')")
    c.execute("INSERT INTO charges (id, vehicle_id, started_at, ended_at, energy_added_kwh,"
              " duration_min, start_soc, end_soc, cost, location_type, charge_type, latitude,"
              " longitude, note) VALUES (1,1,'2026-08-14T05:00:00+00:00','2026-08-14T07:12:00+00:00',"
              "18.4,132,41.0,88.0,3.31,'HOME','AC',45.4642,9.19,'casa di mia sorella')")
    c.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, start_soc,"
              " end_soc) VALUES (1,1,'2026-08-14T08:00:00+00:00','2026-08-14T08:40:00+00:00',33,88,64)")
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('setup_complete','1')")
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('charge_detect_min_a','13.5')")
    c.commit()
    pdb._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)

    import main
    monkeypatch.setattr(main.db_reader, "DB_PATH", path)
    monkeypatch.setattr(main.research, "research_enabled", lambda: True)
    monkeypatch.setattr(main.command_client, "get_consumption_probe_raw", lambda: None)
    # The export encrypts to the beta public key; unwrap it with the same module that wrapped it.
    monkeypatch.setattr(main.research, "encrypt_bundle", lambda b: b)

    resp = asyncio.run(main.research_export())
    return zipfile.ZipFile(io.BytesIO(resp.body))


def test_the_charges_are_in_the_bundle(tmp_path, monkeypatch):
    z = _bundle(tmp_path, monkeypatch)
    assert "charges.txt" in z.namelist(), f"no charges: {z.namelist()}"
    body = z.read("charges.txt").decode()
    assert "2026-08-14" in body and "18.4" in body, body[:300]


def test_the_charge_carries_no_coordinates_and_no_free_text(tmp_path, monkeypatch):
    """The same promise the diagnostics bundle makes, on a file that leaves the tester's house
    encrypted but still leaves it."""
    body = _bundle(tmp_path, monkeypatch).read("charges.txt").decode()
    for needle in ("45.4642", "9.19", "casa di mia sorella"):
        assert needle not in body, f"the bundle leaked {needle!r}"


def test_the_behaviour_settings_are_in_the_bundle(tmp_path, monkeypatch):
    """The ten lines that solved #250: a floor of 13.5 A on a car charging at 13."""
    body = _bundle(tmp_path, monkeypatch).read("settings.txt").decode()
    assert "charge_detect_min_a" in body
    assert "13.5" in body


def test_the_manifest_counts_what_it_now_carries(tmp_path, monkeypatch):
    """meta.json is what a reader trusts before opening anything else."""
    meta = json.loads(_bundle(tmp_path, monkeypatch).read("meta.json").decode())
    assert meta["charges"] == 1


def test_what_was_already_there_is_still_there(tmp_path, monkeypatch):
    names = set(_bundle(tmp_path, monkeypatch).namelist())
    for f in ("trips.csv", "logbook.csv", "meta.json", "raw_signals_log.csv"):
        assert f in names, f"{f} disappeared: {names}"
