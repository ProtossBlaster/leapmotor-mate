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


def _bundle(tmp_path, monkeypatch, fresh=lambda: {"1204": 88, "1318": 12345}, log=None):
    """The real endpoint, decrypted back — the file a tester actually attaches.

    The bundle carries a tail of the app's log, and `diagnostics.data_dir()` finds it beside the
    database named by the DB_PATH *environment variable* — which conftest points at one directory
    for the whole suite. So the log this bundle quotes was written by whatever else ran first, and
    the privacy assertions below were reading a body no test controls. On 27/09/2026 that cost a CI
    run: a line reading "cutoff set to 2026-09-27T16:06:19.194901+00:00" contains "9.19", and the
    longitude needle is "9.19". Point the variable at this test's own directory, and pass `log` to
    say what the tail holds.
    """
    import asyncio
    import io

    import db as D
    import db_reader
    import research

    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    if log is not None:
        (tmp_path / "mate-web.log").write_text(log, encoding="utf-8")
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


# What the app's own log looks like when it has something to give away: the trip-start line with a
# coordinate pair, the MQTT topic with the VIN glued in lowercase, and the account address.
_TELLING_LOG = (
    "2026-08-14 07:12:00 [INFO] db: Trip #1 started — SOC 41.0% @ (45.4642, 9.1900)\n"
    "2026-08-14 07:12:01 [INFO] mqtt: discovery homeassistant/sensor/mate_lfztest0000000001/soc\n"
    "2026-08-14 07:12:02 [INFO] api: login as silvio.tester@example.com ok\n"
)


def test_the_log_the_bundle_quotes_is_redacted_too(tmp_path, monkeypatch):
    """The tail is the one part of the report the app did not compose for sharing.

    Before this, the log the bundle quoted came from the directory the whole suite shares, so what
    the promise was checked against was an accident of test order. Give it a log that HAS the three
    things the promise names and read the tail that comes out.
    """
    body = _bundle(tmp_path, monkeypatch, log=_TELLING_LOG).read("diagnostics.txt").decode()
    assert "Trip #1 started" in body, "the log tail is not in the report at all"
    for needle in ("45.4642", "9.1900", "LFZTEST0000000001", "lfztest0000000001",
                   "silvio.tester@example.com"):
        assert needle not in body, f"the log tail leaked {needle!r}"
    assert "(45.4…, 9.1…)" in body, "the coordinate pair is not truncated, it is gone or intact"


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
