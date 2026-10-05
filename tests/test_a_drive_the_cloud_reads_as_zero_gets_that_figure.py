"""A drive the cloud reads as 0.0 kWh gets that figure from getEC, as from the cloud's history.

The cloud files some short drives at 0.0 kWh. getEC answers them with a split of zeros, which was
taken for no answer: the trip waited for the cloud for six hours, was asked every five minutes, and
ended with no energy at all, its battery having shown no fall to estimate from. Its record in the
cloud's history carries the same 0.0, and a trip matched to it already shows it. A zero on a drive
whose battery did fall is still refused, and a reply missing its figures is no zero. Nor is a total
below zero: kept, it would become the trip's consumption. And a zero counts only where the battery
read the same at both ends; with a reading missing, it is the cloud having nothing for the drive.
The background sweep judges a merged trip as its whole group, as Convert does. A zero taken
counts in every average that divides getEC energy by its kilometres, and a zero the battery does
not vouch for, as the cloud's trip history stores one for a drive without SoC, counts in none of
them. A zero for a whole period is checked against Mate's own trips, as any cloud total far
short of them is.
"""
import json
import re
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest

import command_client
import db_reader
import ec_enrich
from test_ec_enrich_lock import _setup
from test_probe_mileage_energy import _FakeApi, _FakeVehicle

ZERO = {"result": 0, "code": 0, "data": {"driverEC": "0.0", "acEC": "0.0", "otherEC": "0.0"}}


def _short_drive(tmp_path, monkeypatch, reply=ZERO):
    """A drive an hour ago, 460 m by GPS, the battery at 82.9 % on both ends; the cloud answers `reply`."""
    pdb = _setup(tmp_path, monkeypatch, age_min=60)
    pdb._conn.execute("UPDATE trips SET distance_km=0.46, start_soc=82.9, end_soc=82.9,"
                      " efficiency_kwh_100km=NULL WHERE id=1")
    pdb._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1, 'LVIN0000000000001', 'B10')")
    pdb._conn.commit()
    db_reader.set_setting("is_reev", "0")
    session = command_client.LeapmotorSession()
    session._api, session._vehicle = _FakeApi(json.dumps(reply)), _FakeVehicle()
    monkeypatch.setattr(session, "_connect", lambda: None)
    monkeypatch.setattr("leapmotor_cloud.mate_compat.adapter_owned_headers",
                        lambda **kw: type("H", (), {"to_dict": lambda self: {}})())
    monkeypatch.setattr(command_client, "_session", session)
    return session._api


def test_the_sweep_settles_on_the_zero(tmp_path, monkeypatch):
    cloud = _short_drive(tmp_path, monkeypatch)
    for _ in range(3):
        ec_enrich._sweep_now()

    trip = db_reader.get_trip_detail(1)
    assert (trip.get("energy_source"), trip.get("energy_kwh"), trip["ec_pending"]) == ("getec", 0.0, False)
    assert len(cloud.posted) == 2, "a settled trip is asked again"


def test_convert_applies_the_zero(tmp_path, monkeypatch):
    _short_drive(tmp_path, monkeypatch)

    assert ec_enrich.convert_trip(1)["ok"] is True
    assert db_reader.get_trip_detail(1)["energy_kwh"] == 0.0


@pytest.mark.parametrize("data", [{"driverEC": None, "acEC": None, "otherEC": None}, {"driverEC": "0.0"}],
                         ids=["null", "missing"])
def test_a_reply_missing_its_figures_is_no_zero(tmp_path, monkeypatch, data):
    _short_drive(tmp_path, monkeypatch, {"result": 0, "code": 0, "data": data})
    ec_enrich._sweep_now()
    ec_enrich._sweep_now()

    assert ec_enrich.convert_trip(1)["ok"] is False
    assert db_reader.get_trip_detail(1)["ec_stable"] == 0


def test_a_negative_total_is_no_answer(tmp_path, monkeypatch):
    """Below zero is no reading of a drive: kept, it would be the trip's consumption, −65 kWh/100 km here."""
    _short_drive(tmp_path, monkeypatch, {"result": 0, "code": 0,
                                         "data": {"driverEC": "-0.3", "acEC": "0.0", "otherEC": "0.0"}})
    ec_enrich._sweep_now()
    ec_enrich._sweep_now()

    assert ec_enrich.convert_trip(1)["ok"] is False
    assert db_reader.get_trip_detail(1)["ec_stable"] == 0


def test_a_zero_needs_the_battery_to_have_held(tmp_path, monkeypatch):
    """5 km cannot cost nothing: with no battery reading to vouch for it, the zero is the cloud having nothing."""
    cloud = _short_drive(tmp_path, monkeypatch)
    with closing(db_reader._conn_rw()) as db:
        db.execute("UPDATE trips SET distance_km = 5.0, start_soc = NULL, end_soc = NULL WHERE id = 1")
        db.commit()
    for _ in range(3):
        ec_enrich._sweep_now()

    assert ec_enrich.convert_trip(1)["ok"] is False
    assert db_reader.get_trip_detail(1)["ec_stable"] == 0
    assert len(cloud.posted) == 4, "the trip did not keep waiting for the cloud"


def test_the_sweep_judges_a_merged_trip_as_a_whole(tmp_path, monkeypatch):
    """The battery held over the first drive and fell over the one merged into it: the group did use energy."""
    cloud = _short_drive(tmp_path, monkeypatch)
    with closing(db_reader._conn_rw()) as db:
        end = db.execute("SELECT ended_at FROM trips WHERE id = 1").fetchone()[0]
        start, stop = (datetime.fromisoformat(end) + timedelta(minutes=m) for m in (2, 7))
        db.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc)"
                   " VALUES (2, 1, ?, ?, 1.0, 82.9, 81.9)", (start.isoformat(), stop.isoformat()))
        db.commit()
    assert db_reader.merge_trips(1, 2)["ok"] is True
    ec_enrich._sweep_now()
    ec_enrich._sweep_now()

    assert db_reader.get_trip_detail(1)["ec_stable"] == 0, "a zero for the first drive alone was taken for the group"
    assert len(cloud.posted) == 2


def _a_zero_and_a_measured_drive(tmp_path, monkeypatch):
    """The 460 m drive at 0.0 kWh and, 40 minutes later, 10 km at 1.5 kWh, both taken from getEC, on one day of July."""
    cloud = _short_drive(tmp_path, monkeypatch)
    with closing(db_reader._conn_rw()) as db:
        end = datetime(2026, 7, 5, 10, 3, tzinfo=timezone.utc)
        db.execute("UPDATE trips SET started_at = ?, ended_at = ? WHERE id = 1",
                   ((end - timedelta(minutes=3)).isoformat(), end.isoformat()))
        start, stop = (end + timedelta(minutes=m) for m in (40, 55))
        db.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, duration_min,"
                   " start_soc, end_soc) VALUES (2, 1, ?, ?, 10.0, 15, 80.0, 78.0)",
                   (start.isoformat(), stop.isoformat()))
        db.commit()
    assert ec_enrich.convert_trip(1)["ok"] is True
    cloud._body = json.dumps({"result": 0, "code": 0, "data": {"driverEC": "1.2", "acEC": "0.2", "otherEC": "0.1"}})
    assert ec_enrich.convert_trip(2)["ok"] is True
    return start


def test_a_taken_zero_counts_in_every_average(tmp_path, monkeypatch):
    """1.5 kWh over 10.46 km is 14.3 on every page. 15.0 means a page left the zero out of its kilometres."""
    started = _a_zero_and_a_measured_drive(tmp_path, monkeypatch)
    begin, end = int(started.timestamp()) - 7200, int(started.timestamp()) + 7200

    assert db_reader.trips_totals(db_reader.get_trips())["kwh_100km"] == 14.3, "the Trips strip"
    assert db_reader.get_stats_summary()["avg_efficiency"] == 14.3, "the Statistics card"
    tot = db_reader.get_trip_totals_between(begin, end)
    assert (tot["ec_km"], tot["ec_kwh_sum"]) == (10.46, 1.5), "the period card's pair"
    month = started.astimezone(db_reader._local_tz()).strftime("%Y-%m")
    assert db_reader._collect_monthly_buckets()[month]["avg_efficiency_measured"] == 14.3, "the Report's months"

    pytest.importorskip("fastapi", reason="the period card lives in web.main")
    import main
    assert main._enrich_eb_with_trip_totals({"total_kwh": 1.5}, begin, end)["avg_kwh100"] == 14.3


def test_both_totals_paths_pick_the_same_trips(tmp_path, monkeypatch):
    """A merged or an electric history is summed in Python, a range-extender's without merges in SQL."""
    started = _a_zero_and_a_measured_drive(tmp_path, monkeypatch)
    begin, end = int(started.timestamp()) - 7200, int(started.timestamp()) + 7200
    grouped = db_reader.get_trip_totals_between(begin, end)
    db_reader.set_setting("is_reev", "1")
    plain = db_reader.get_trip_totals_between(begin, end)

    assert (plain["ec_km"], plain["ec_kwh_sum"]) == (grouped["ec_km"], grouped["ec_kwh_sum"]) == (10.46, 1.5)
    assert db_reader.get_stats_summary()["avg_efficiency"] == 14.3, "Convert did not write the zero as the trip's efficiency"


@pytest.mark.parametrize("start_soc, end_soc", [(None, None), (80.0, 79.8)], ids=["no SoC", "the battery fell"])
def test_a_zero_the_battery_does_not_vouch_for_counts_in_no_average(tmp_path, monkeypatch, start_soc, end_soc):
    """Two 1 km drives the cloud's history files at 0.0 kWh, beside the two drives: one stored as the import
    stores it (the zero as its efficiency too), one of Mate's own, without getEC, that the matcher pairs with
    the record while its battery fell. Every page still reads 14.3, in both summing paths; 13.1 or 12.0 means
    a page took a zero over its kilometre, through the getEC pair or through the efficiency."""
    started = _a_zero_and_a_measured_drive(tmp_path, monkeypatch)
    with closing(db_reader._conn_rw()) as db:
        start, stop = (started + timedelta(minutes=m) for m in (60, 65))
        db.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, duration_min, start_soc,"
                   " end_soc, ec_kwh, efficiency_kwh_100km, ec_tried, ec_stable) VALUES (3, 1, ?, ?, 1.0, 5, ?, ?, 0.0, 0.0, 1, 1)",
                   (start.isoformat(), stop.isoformat(), start_soc, end_soc))
        start, stop = (started + timedelta(minutes=m) for m in (80, 85))
        db.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, duration_min, start_soc,"
                   " end_soc, efficiency_kwh_100km) VALUES (4, 1, ?, ?, 1.0, 5, 80.0, 79.8, 20.0)",
                   (start.isoformat(), stop.isoformat()))
        db.execute("CREATE TABLE IF NOT EXISTS api_lab_cloud_history_records"
                   " (id INTEGER PRIMARY KEY, kind TEXT, payload_json TEXT)")
        db.execute("INSERT INTO api_lab_cloud_history_records (kind, payload_json) VALUES ('mileage', ?)",
                   (json.dumps({"vin": "LVIN0000000000001", "routeStartTs": int(start.timestamp() * 1000),
                                "routeEndTs": int(stop.timestamp() * 1000), "totalEnergy": 0.0, "totalMileage": 1.0}),))
        db.commit()
    begin, end = int(started.timestamp()) - 7200, int(started.timestamp()) + 7200
    month = started.astimezone(db_reader._local_tz()).strftime("%Y-%m")

    for reev in ("0", "1"):
        db_reader.set_setting("is_reev", reev)
        assert db_reader.trips_totals(db_reader.get_trips())["kwh_100km"] == 14.3, "the Trips strip"
        assert db_reader.get_stats_summary()["avg_efficiency"] == 14.3, "the Statistics card"
        tot = db_reader.get_trip_totals_between(begin, end)
        assert (tot["ec_km"], tot["ec_kwh_sum"]) == (10.46, 1.5), "the period card's pair"
        # The matcher serves electric cars only: on a range-extender the fourth drive is one without
        # getEC, and its estimate stays in the kilometres with an efficiency.
        assert tot["eff_km"] == (10.46 if reev == "0" else 11.46), "the kilometres carrying an efficiency"
        assert db_reader._collect_monthly_buckets()[month]["avg_efficiency_measured"] == 14.3, "the Report's months"


def test_a_zero_battery_drive_keeps_the_range_extenders_battery_average(tmp_path, monkeypatch):
    """Statistics divides a range-extender's battery-only pair; a zero drive on the battery beside a generator
    drive is that pair at 0.0, not a reason to fall back to the generator's kilometres."""
    pytest.importorskip("fastapi", reason="the period card lives in web.main")
    import main
    _short_drive(tmp_path, monkeypatch)
    db_reader.set_setting("is_reev", "1")
    assert ec_enrich.convert_trip(1)["ok"] is True
    with closing(db_reader._conn_rw()) as db:
        end = db.execute("SELECT ended_at FROM trips WHERE id = 1").fetchone()[0]
        start, stop = (datetime.fromisoformat(end) + timedelta(minutes=m) for m in (40, 100))
        db.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, duration_min, start_soc,"
                   " end_soc, fuel_start_pct, fuel_end_pct, ec_kwh, ec_stable) VALUES (2, 1, ?, ?, 100.0, 60, 70, 70, 80, 60, 15.0, 1)",
                   (start.isoformat(), stop.isoformat()))
        db.commit()
    begin, end = int(start.timestamp()) - 7200, int(stop.timestamp()) + 60

    eb = main._enrich_eb_with_trip_totals({"total_kwh": 15.0}, begin, end, battery_only=True)
    assert (eb["avg_kwh100_basis"], eb["avg_kwh100"], eb["avg_kwh100_km"]) == ("battery", 0.0, 0.46)


def _report_tile(tmp_path, monkeypatch, km, efficiency):
    """The Report's driving tiles for a month of one drive, the cloud answering 0.0 kWh for the whole of it."""
    pytest.importorskip("fastapi", reason="the Report tiles live in web.main")
    pytest.importorskip("httpx", reason="Starlette TestClient needs httpx")
    import main
    from starlette.testclient import TestClient

    for var in ("MATE_AUTH_PASSWORD", "SUPERVISOR_TOKEN", "HASSIO_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    _short_drive(tmp_path, monkeypatch)
    with closing(db_reader._conn_rw()) as db:
        db.execute("UPDATE trips SET distance_km = ?, efficiency_kwh_100km = ? WHERE id = 1", (km, efficiency))
        db.commit()
    for key, value in (("timezone", "UTC"), ("setup_complete", "1")):
        db_reader.set_setting(key, value)
    with closing(db_reader._conn_rw()) as db:
        db.execute("UPDATE trips SET started_at = '2026-07-05T10:00:00+00:00', ended_at = '2026-07-05T10:03:00+00:00'"
                   " WHERE id = 1")
        db.commit()
    html = TestClient(main.app).get("/api/report-driving?month=2026-07&refresh=1").text
    used = re.search(r'<span class="stat-value[^"]*">([^<]*)</span><span[^>]*>kWh', html)
    return used.group(1).strip(), "upload every session to the cloud" in html


def test_a_zero_for_the_month_is_checked_against_the_trips(tmp_path, monkeypatch):
    """100 km at 15 kWh cannot have cost nothing: the tile shows Mate's own sum and says why, as for any cloud total far short of it."""
    assert _report_tile(tmp_path, monkeypatch, 100.0, 15.0) == ("15", True)


def test_a_month_of_zero_drives_keeps_its_zero(tmp_path, monkeypatch):
    assert _report_tile(tmp_path, monkeypatch, 0.46, None) == ("0", False)
