"""The beta bundle has to carry the TRIPS, not only the raw signals (Silvio, 05/08/26).

Every open range-extender question is about what Mate made of the car's signals — which trips got a
getEC reading at all, whether the millilitre counter and the percentage gauge agree, whether ΔSoC
and getEC say the same thing on a drive the generator ran through. The bundle carried the signals
and the logbook and nothing else, so those questions could only be answered by reasoning from our
own BEV, which is not an answer.

Silvio's reason for needing it: *«sulle REEV non possiamo basarci sul SoC perché varia e sale e
scende in base se parte o meno il generatore»*. Deciding what replaces it takes a real
range-extender history, so the bundle has to be able to bring one.

What must NOT travel is where anybody drove. The guard is an allow-list, and the test below reads
the trips table itself — so a location column added next month fails here instead of quietly
shipping.
"""
import asyncio
import csv
import io
import json
import zipfile

import pytest

pytest.importorskip("fastapi", reason="the export endpoint needs fastapi (absent in the CI env)")

import db as PollerDB
import db_reader
import main
import research


LOCATION_COLUMNS = {"start_lat", "start_lon", "end_lat", "end_lon",
                    "start_geohash", "end_geohash"}


@pytest.fixture
def beta(tmp_path, monkeypatch):
    """A tester's machine: research on, two trips, and the seal replaced by a pass-through so the
    test can look inside the envelope it would otherwise have to hold the private key to open."""
    pdb = PollerDB.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    pdb.ensure_vehicle("LVIN0000000000001", "C10", 2025)
    db_reader.set_setting("is_reev", "1")
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km,"
        " start_soc, end_soc, ec_kwh, ec_driving, start_lat, start_lon, end_lat, end_lon,"
        " fuel_start_l, fuel_end_l, fuel_start_pct, fuel_end_pct)"
        " VALUES (1,1,'2026-07-03T08:00:00+00:00','2026-07-03T10:00:00+00:00',120.0,"
        " 80,55,14.4,11.9,45.4642,9.19,44.4949,11.3426,31.2,24.0,62.4,48.0)")
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km,"
        " start_soc, end_soc, start_lat, start_lon, end_lat, end_lon)"
        " VALUES (2,1,'2026-07-04T08:00:00+00:00','2026-07-04T08:20:00+00:00',9.0,"
        " 55,52,45.4642,9.19,45.07,7.68)")
    pdb._conn.commit()

    monkeypatch.setenv("MATE_RESEARCH", "1")
    monkeypatch.setattr(research, "encrypt_bundle", lambda b: b)     # look inside the envelope
    monkeypatch.setattr(main.command_client, "get_consumption_probe_raw", lambda: None)

    r = asyncio.run(main.research_export())
    assert r.status_code == 200, r.status_code
    return zipfile.ZipFile(io.BytesIO(r.body))


def test_the_bundle_contains_the_trips(beta):
    assert "trips.csv" in beta.namelist(), beta.namelist()


def test_and_still_contains_what_it_always_did(beta):
    """Added, not swapped: the signal history is what the trips are checked against."""
    for name in ("raw_signals_log.csv", "logbook.csv", "meta.json"):
        assert name in beta.namelist(), name


def _by_id(beta, tid):
    rows = list(csv.DictReader(io.StringIO(beta.read("trips.csv").decode())))
    return rows, next(r for r in rows if r["id"] == tid)


def test_every_trip_is_there_with_both_energy_readings(beta):
    rows, generator_trip = _by_id(beta, "1")      # newest first, so never rows[0] by luck
    assert len(rows) == 2
    assert generator_trip["distance_km"] == "120.0"
    assert generator_trip["ec_kwh"] == "14.4" and generator_trip["ec_driving"] == "11.9", \
        "both getEC figures travel — the whole argument is which of the two to use"
    assert float(generator_trip["start_soc"]) == 80 and float(generator_trip["end_soc"]) == 55
    assert generator_trip["fuel_start_l"] == "31.2" and generator_trip["fuel_start_pct"] == "62.4", \
        "the tank's two measurements travel too — millilitre counter AND percentage gauge"


def test_what_mate_computed_travels_beside_what_it_read(beta):
    """A number reported as wrong is only diagnosable next to the readings it came from."""
    _, row = _by_id(beta, "1")
    for col in ("engine_ran", "fuel_used_l", "fuel_l_100km", "reev_elec_kwh", "reev_elec_kwh_100km"):
        assert col in row, col
    assert row["engine_ran"] == "True" and float(row["fuel_used_l"]) > 0


def test_no_coordinate_ever_leaves_the_machine(beta):
    """The trips inserted above carry Milan, Bologna and Turin. None of it may appear anywhere in
    the bundle — not as a column, not as a value, not in any other file."""
    header = beta.read("trips.csv").decode().splitlines()[0]
    for col in LOCATION_COLUMNS:
        assert col not in header, col
    whole = b"".join(beta.read(n) for n in beta.namelist()).decode(errors="replace")
    for coord in ("45.4642", "9.19", "44.4949", "11.3426", "45.07", "7.68"):
        assert coord not in whole, f"{coord} travelled in the bundle"


def test_the_allow_list_covers_no_location_column():
    """Read against the real table, so a location column added later fails HERE rather than
    shipping. `get_trips` is SELECT * — a deny-list would let the new one straight through."""
    assert LOCATION_COLUMNS.isdisjoint(main._RESEARCH_TRIP_FIELDS)
    for col in main._RESEARCH_TRIP_FIELDS:
        assert not any(w in col for w in ("lat", "lon", "geohash", "address", "place")), col


def test_the_allow_list_is_a_list_not_a_leftover(tmp_path, monkeypatch):
    """Every name in it must be a real trips column or something get_trips computes — a typo would
    silently produce an empty column, and an empty column reads as "the car did not report it"."""
    pdb = PollerDB.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    pdb.ensure_vehicle("LVIN0000000000001", "C10", 2025)
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc,"
        " fuel_start_l, fuel_end_l, fuel_start_pct, fuel_end_pct, ec_kwh)"
        " VALUES (1,1,'2026-07-03T08:00:00+00:00','2026-07-03T10:00:00+00:00',120.0,80,55,"
        " 31.2,24.0,62.4,48.0,14.4)")
    pdb._conn.commit()
    produced = set(db_reader.get_trips(limit=10)[0])
    missing = [c for c in main._RESEARCH_TRIP_FIELDS if c not in produced]
    assert not missing, f"allow-listed but never produced: {missing}"


def test_meta_says_how_much_of_the_history_has_a_getec_reading(beta):
    """The coverage question, answered by the bundle instead of by us. One trip of the two here
    carries `ec_kwh`; on a real history this is the number that decides the whole basis argument."""
    meta = json.loads(beta.read("meta.json"))
    assert meta["trips"] == 2
    assert meta["trips_with_ec_kwh"] == 1
    assert meta["is_reev"] == "1"
    assert meta["trip_fields"] == list(main._RESEARCH_TRIP_FIELDS)


def test_the_export_is_still_shut_when_research_is_off(tmp_path, monkeypatch):
    """The trips are the most personal thing in the bundle now. The 404 that guards it is not a
    detail this change may weaken."""
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    PollerDB.Database(str(tmp_path / "t.db"))
    monkeypatch.delenv("MATE_RESEARCH", raising=False)
    assert asyncio.run(main.research_export()).status_code == 404
