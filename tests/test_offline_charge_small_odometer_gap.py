"""A garage charge followed by a short drive out must not be mistaken for regeneration."""
from datetime import datetime, timedelta, timezone

import pytest

import db_reader
from test_missed_charge_scan import _seed

T0 = datetime(2026, 6, 1, 22, tzinfo=timezone.utc)


def _frame(db, seconds, soc, *, odo=1000, gear="D", speed=30, frame_seconds=None,
           charging=0):
    frame_seconds = seconds if frame_seconds is None else frame_seconds
    db._conn.execute(
        "INSERT INTO positions (vehicle_id, recorded_at, frame_ts, soc, odometer_km, gear,"
        " speed_kmh, charging, latitude, longitude) VALUES (1,?,?,?,?,?,?,?,45,9)",
        ((T0 + timedelta(seconds=seconds)).isoformat(),
         int((T0 + timedelta(seconds=frame_seconds)).timestamp() * 1000),
         soc, odo, gear, speed, charging))
    db._conn.commit()


@pytest.mark.parametrize("repeats", [False, True])
@pytest.mark.parametrize("gear,speed", [("D", 30), ("P", 0)])
@pytest.mark.parametrize("km", [1, 2, 3])
def test_garage_charge_with_short_exit_is_recovered(tmp_path, monkeypatch, repeats, gear, speed, km):
    db = _seed(tmp_path, monkeypatch)
    _frame(db, 0, 30)
    if repeats:
        for seconds in range(30, 4 * 3600, 30):
            _frame(db, seconds, 30, frame_seconds=0)
    _frame(db, 4 * 3600, 80, odo=1000 + km, gear=gear, speed=speed)
    candidates = db_reader.scan_missed_charges()
    assert len(candidates) == 1
    c = candidates[0]
    assert c["started_at"] == T0.isoformat()
    assert c["duration_min"] == 240
    assert (c["start_soc"], c["end_soc"], c["energy_kwh"]) == (30, 80, 33.55)
    assert db._conn.execute("SELECT COUNT(*) FROM charges").fetchone()[0] == 0
    assert db_reader.scan_missed_charges(apply=True) == candidates
    assert db_reader.scan_missed_charges(apply=True) == []
    row = db._conn.execute("SELECT * FROM charges").fetchone()
    assert row["reconstructed"] == 1 and row["vehicle_id"] == 1


@pytest.mark.parametrize("seconds,start,end,km", [
    (1800, 50, 56, 40),       # the original regen example
    (4 * 3600, 50, 56, 2),   # small rise over a long gap still proves little
    (4 * 3600, 50, 60, 3),   # 6.71 kWh is inside the conservative downhill budget
    (4 * 3600, 30, 80, 4),   # a longer unobserved journey remains ambiguous
    (3599, 30, 80, 2),       # not a long enough outage
    (20, 5, 90, 2),          # impossible charge power over seconds
    (4 * 3600, 0, 80, 2),    # missing SOC parsed as zero
    (4 * 3600, 30, 101, 2),  # invalid SOC
    (4 * 3600, 30, 80, -1),  # odometer rollback
])
def test_ambiguous_or_impossible_gaps_are_rejected(tmp_path, monkeypatch, seconds, start, end, km):
    db = _seed(tmp_path, monkeypatch)
    _frame(db, 0, start)
    _frame(db, seconds, end, odo=1000 + km)
    assert db_reader.scan_missed_charges() == []


@pytest.mark.parametrize("intermediate_soc", [30, None])
def test_fresh_intermediate_frames_break_the_gap(tmp_path, monkeypatch, intermediate_soc):
    db = _seed(tmp_path, monkeypatch)
    _frame(db, 0, 30)
    for seconds in range(60, 4 * 3600, 60):
        _frame(db, seconds, intermediate_soc)
    _frame(db, 4 * 3600, 80, odo=1002)
    assert db_reader.scan_missed_charges() == []


@pytest.mark.parametrize("end_frame", [0, -60, 20])
def test_poll_time_alone_cannot_turn_a_bad_frame_clock_into_a_charge(tmp_path, monkeypatch, end_frame):
    db = _seed(tmp_path, monkeypatch)
    _frame(db, 0, 30)
    _frame(db, 4 * 3600, 80, odo=1002, frame_seconds=end_frame)
    assert db_reader.scan_missed_charges() == []


def test_frame_time_alone_cannot_invent_a_long_outage(tmp_path, monkeypatch):
    db = _seed(tmp_path, monkeypatch)
    _frame(db, 0, 30)
    _frame(db, 20, 80, odo=1002, frame_seconds=4 * 3600)
    assert db_reader.scan_missed_charges() == []


@pytest.mark.parametrize("column,value", [("frame_ts", None), ("odometer_km", None),
                                          ("gear", None)])
def test_incomplete_evidence_is_not_relaxed(tmp_path, monkeypatch, column, value):
    db = _seed(tmp_path, monkeypatch)
    _frame(db, 0, 30)
    _frame(db, 4 * 3600, 80, odo=1002)
    db._conn.execute(f"UPDATE positions SET {column}=? WHERE id=2", (value,))
    db._conn.commit()
    assert db_reader.scan_missed_charges() == []


@pytest.mark.parametrize("missing", ["start", "end", "both"])
def test_a_speed_the_car_did_not_send_is_no_veto(tmp_path, monkeypatch, missing):
    """The speed is only checked for sanity: a missing one says nothing about a charge."""
    db = _seed(tmp_path, monkeypatch)
    _frame(db, 0, 30, speed=None if missing != "end" else 30)
    _frame(db, 4 * 3600, 80, odo=1002, speed=None if missing != "start" else 30)
    assert len(db_reader.scan_missed_charges()) == 1


@pytest.mark.parametrize("end", [
    dict(seconds=4 * 3600, soc=80, odo=None),
    dict(seconds=4 * 3600, soc=36, odo=1002),
    dict(seconds=4 * 3600, soc=80, odo=1002, frame_seconds=20),
    dict(seconds=20, soc=80, odo=1002, frame_seconds=4 * 3600),
    dict(seconds=4 * 3600, soc=80, odo=1002, reev=True),
], ids=["no odometer", "small rise", "car clock", "our clock", "range extender"])
def test_without_a_speed_the_rest_of_the_evidence_is_still_required(tmp_path, monkeypatch, end):
    db = _seed(tmp_path, monkeypatch)
    if end.pop("reev", False):
        monkeypatch.setattr(db_reader, "is_reev_car", lambda: True)
    _frame(db, 0, 30, speed=None)
    _frame(db, end.pop("seconds"), end.pop("soc"), speed=None, **end)
    assert db_reader.scan_missed_charges() == []


@pytest.mark.parametrize("open_session", [False, True])
def test_real_charge_overlapping_the_frozen_part_prevents_duplicates(tmp_path, monkeypatch, open_session):
    db = _seed(tmp_path, monkeypatch)
    _frame(db, 0, 30)
    _frame(db, 4 * 3600 - 10, 30, frame_seconds=0)
    _frame(db, 4 * 3600, 80, odo=1002)
    db._conn.execute(
        "INSERT INTO charges (vehicle_id, started_at, ended_at) VALUES (1,?,?)",
        ((T0 + timedelta(hours=1)).isoformat(),
         None if open_session else (T0 + timedelta(hours=3)).isoformat()))
    db._conn.commit()
    assert db_reader.scan_missed_charges(apply=True) == []
    assert db._conn.execute("SELECT COUNT(*) FROM charges").fetchone()[0] == 1


def test_a_range_extender_could_have_run_its_generator(tmp_path, monkeypatch):
    db = _seed(tmp_path, monkeypatch)
    monkeypatch.setattr(db_reader, "is_reev_car", lambda: True)
    _frame(db, 0, 30)
    _frame(db, 4 * 3600, 80, odo=1002)
    assert db_reader.scan_missed_charges() == []


def test_small_pack_gain_can_still_be_regen(tmp_path, monkeypatch):
    db = _seed(tmp_path, monkeypatch)
    db.set_battery_capacity(10)
    _frame(db, 0, 30)
    _frame(db, 4 * 3600, 80, odo=1002)
    assert db_reader.scan_missed_charges() == []


def test_online_charge_signals_are_left_to_the_live_recorder(tmp_path, monkeypatch):
    db = _seed(tmp_path, monkeypatch)
    _frame(db, 0, 30, gear="P", speed=0, charging=1)
    _frame(db, 4 * 3600, 80, odo=1002)
    assert db_reader.scan_missed_charges() == []


def test_inconsistent_payloads_with_one_clock_do_not_prove_an_outage(tmp_path, monkeypatch):
    db = _seed(tmp_path, monkeypatch)
    _frame(db, 0, 30)
    _frame(db, 4 * 3600 - 10, 30, speed=40, frame_seconds=0)
    _frame(db, 4 * 3600, 80, odo=1002)
    assert db_reader.scan_missed_charges() == []


def test_scan_uses_only_the_selected_cars_frames_and_charges(tmp_path, monkeypatch):
    db = _seed(tmp_path, monkeypatch)
    second = db.ensure_vehicle("OTHER", "B10")
    _frame(db, 0, 30)
    _frame(db, 4 * 3600, 80, odo=1002)
    db._conn.execute(
        "INSERT INTO charges (vehicle_id, started_at, ended_at) VALUES (?,?,?)",
        (second, T0.isoformat(), (T0 + timedelta(hours=4)).isoformat()))
    db._conn.commit()
    monkeypatch.setattr(db_reader, "_current_vehicle_id", lambda: second)
    assert db_reader.scan_missed_charges(apply=True) == []
    monkeypatch.setattr(db_reader, "_current_vehicle_id", lambda: 1)
    assert len(db_reader.scan_missed_charges(apply=True)) == 1
    assert db._conn.execute("SELECT vehicle_id FROM charges WHERE reconstructed=1").fetchone()[0] == 1
