"""The BetaTester bundle must carry the WHOLE diagnostics report, not two sections of it.

Beta #13, 21/08/2026, @ebagnoli. He was told *"«Assegna Casa automaticamente» risulta ancora
spento"*. He answered: *"Ma assegna casa automaticamente e' selezionato."* He was right — the
bundle he sent cannot say either way. Its `settings.txt` is `_advanced_settings_section()`, the ten
behaviour settings; the wallbox block (entity map + `Auto-HOME`) lives in `_cost_wallbox_section()`,
which only the *diagnostics* bundle carries. The claim was INFERRED from his charge rows and
published to him as if it had been read.

This is the third time the same shape has cost us, and the file already says the first two: beta #30
(@pdifeo) arrived with no charges at all, beta #29 (@michapr) was entirely about one charge and had
to be answered from a second, different file. Each time the fix was to lift one more section across.
So: lift the whole report instead, and stop choosing in advance which question a tester is allowed
to have answered.

Safe by construction — `build_bundle`'s own contract is that it is redacted and shareable in public
(VIN masked, GPS stripped, no free text), and on top of that this bundle leaves the tester's machine
sealed to our public key.
"""
import zipfile

import pytest

pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI env)")


class _Req:
    headers = {"x-ingress-path": ""}
    cookies: dict = {}
    query_params: dict = {}


def _bundle(tmp_path, monkeypatch, fresh=lambda: {"1204": 88, "1318": 12345}):
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
    # The two lines this whole test exists for: the tester HAS turned it on.
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('wallbox_auto_home','1')")
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('wallbox_entities','sensor.wb_energy')")
    c.commit()
    pdb._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)

    import main
    monkeypatch.setattr(main.db_reader, "DB_PATH", path)
    monkeypatch.setattr(main.research, "research_enabled", lambda: True)
    monkeypatch.setattr(main.command_client, "get_consumption_probe_raw", lambda: None)
    monkeypatch.setattr(main.command_client, "get_fresh_signals", fresh)
    monkeypatch.setattr(main.research, "encrypt_bundle", lambda b: b)

    resp = asyncio.run(main.research_export())
    return zipfile.ZipFile(io.BytesIO(resp.body))


def test_the_diagnostics_report_is_in_the_bundle(tmp_path, monkeypatch):
    z = _bundle(tmp_path, monkeypatch)
    assert "diagnostics.txt" in z.namelist(), f"no diagnostics: {z.namelist()}"


def test_it_says_whether_auto_home_is_on(tmp_path, monkeypatch):
    """The exact line that was missing when @ebagnoli was told the wrong thing."""
    body = _bundle(tmp_path, monkeypatch).read("diagnostics.txt").decode()
    assert "Auto-HOME" in body, "the wallbox block is still missing"
    line = next(l for l in body.splitlines() if "Auto-HOME" in l)
    assert line.strip().endswith("1"), f"reads as off while the setting is on: {line!r}"


def test_it_says_which_entities_the_wallbox_is_mapped_to(tmp_path, monkeypatch):
    body = _bundle(tmp_path, monkeypatch).read("diagnostics.txt").decode()
    assert "sensor.wb_energy" in body, "the entity map is missing"


def test_the_report_carries_no_coordinates_and_no_free_text(tmp_path, monkeypatch):
    """`build_bundle` promises this to the world; hold it here too."""
    body = _bundle(tmp_path, monkeypatch).read("diagnostics.txt").decode()
    for needle in ("45.4642", "9.19", "casa di mia sorella", "LFZTEST0000000001"):
        assert needle not in body, f"the bundle leaked {needle!r}"


def test_what_was_already_there_is_still_there(tmp_path, monkeypatch):
    names = set(_bundle(tmp_path, monkeypatch).namelist())
    for f in ("trips.csv", "logbook.csv", "meta.json", "raw_signals_log.csv",
              "charges.txt", "settings.txt"):
        assert f in names, f"{f} disappeared: {names}"


def test_it_carries_the_cars_signals_as_they_are_right_now(tmp_path, monkeypatch):
    """'Complete' includes the live snapshot: the CSV is the history, this is the instant. A cloud
    hiccup must not cost the tester the rest of the report — hence best-effort, tested below."""
    body = _bundle(tmp_path, monkeypatch).read("diagnostics.txt").decode()
    assert "1318" in body and "12345" in body, "the live signal dump is missing"


def test_a_cloud_that_will_not_answer_still_leaves_a_report(tmp_path, monkeypatch):
    """The live fetch is the only part of this report that can fail on someone else's server."""
    def _boom():
        raise RuntimeError("cloud unreachable")
    z = _bundle(tmp_path, monkeypatch, fresh=_boom)
    assert "diagnostics.txt" in z.namelist(), f"one dead call cost the whole report: {z.namelist()}"
    assert "Auto-HOME" in z.read("diagnostics.txt").decode()
