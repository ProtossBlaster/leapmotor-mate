"""A drive the cloud reads as 0.0 kWh gets that figure from getEC, as from the cloud's history.

The cloud files some short drives at 0.0 kWh. getEC answers them with a split of zeros, which was
taken for no answer: the trip waited for the cloud for six hours, was asked every five minutes, and
ended with no energy at all, its battery having shown no fall to estimate from. Its record in the
cloud's history carries the same 0.0, and a trip matched to it already shows it. A zero on a drive
whose battery did fall is still refused, and a reply missing its figures is no zero. Nor is a total
below zero: kept, it would become the trip's consumption. And a zero counts only where the battery
read the same at both ends; with a reading missing, it is the cloud having nothing for the drive.
"""
import json
from contextlib import closing

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
