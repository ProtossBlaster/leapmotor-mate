"""A speed or odometer the car did not send is stored as unknown, not as zero.

The parser turned an absent speed (1319) or odometer (1318) into 0, and `positions` kept that 0. A
measured standstill and a frame that said nothing about speed then read the same, and so did a
missing odometer and a car whose odometer never moved. Anything that needs positive evidence the
car stood still, such as a charge found in the history, cannot get it from such a row.

The numbers in memory stay 0 on purpose: the state machine and the recorder compare them as
numbers. What changes is the stored row, which is where history is read from.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

import client
import db as D
import db_reader
import main
import pytest
import recorder as R
import state_machine as SM

_ABSENT = object()


def _signal(speed=_ABSENT, odometer=_ABSENT):
    sig = {"1": int(datetime(2026, 8, 11, 18, 0, tzinfo=timezone.utc).timestamp() * 1000),
           "100003": 80.0, "1010": 0, "3": 45.0, "2": 9.0}
    if speed is not _ABSENT:
        sig["1319"] = speed
    if odometer is not _ABSENT:
        sig["1318"] = odometer
    return sig


def _stored(tmp_path, sig):
    """Parse the signals and let the recorder save them, as the poller does; the stored row."""
    db = D.Database(str(tmp_path / "t.db"))
    rec = R.Recorder(db, vehicle_id=db.ensure_vehicle("TESTVIN", "B10"))
    rec.process(client._parse_signal("TESTVIN", sig))
    return db._conn.execute("SELECT speed_kmh, odometer_km FROM positions").fetchone()


@pytest.mark.parametrize("sent, stored", [(0, 0.0), ("0.0", 0.0), (37.5, 37.5),
                                          (_ABSENT, None), (None, None), ("", None)])
def test_the_stored_speed_is_the_one_the_car_sent(tmp_path, sent, stored):
    row = _stored(tmp_path, _signal(speed=sent, odometer=12345))
    assert row["speed_kmh"] == stored


@pytest.mark.parametrize("sent, stored", [(12345, 12345.0), ("12345.0", 12345.0),
                                          (_ABSENT, None), (None, None)])
def test_the_stored_odometer_is_the_one_the_car_sent(tmp_path, sent, stored):
    row = _stored(tmp_path, _signal(speed=0, odometer=sent))
    assert row["odometer_km"] == stored


def test_the_numbers_in_memory_stay_numbers():
    data = client._parse_signal("TESTVIN", _signal())
    assert (data.speed_kmh, data.odometer_km) == (0.0, 0.0)
    assert (data.speed_reported, data.odometer_reported) == (False, False)


@pytest.mark.parametrize("fields, reported", [({"speed": 0, "totalMileage": 1234}, True),
                                              ({"speed": None, "totalMileage": None}, False),
                                              ({}, False)])
def test_named_field_responses_say_the_same(fields, reported):
    """T03 / EU responses carry named fields instead of numeric signals."""
    sig = client._named_fields_to_signal({"preciseSoc": 80.0, "gearStatus": 0, **fields})
    data = client._parse_signal("TESTVIN", sig)
    assert (data.speed_reported, data.odometer_reported) == (reported, reported)


def test_the_web_writes_the_same_after_a_command(tmp_path, monkeypatch):
    path = str(tmp_path / "web.db")
    D.Database(path)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO vehicles (id, vin) VALUES (1, 'TESTVIN')")
    con.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    db_reader.save_fresh_signals(_signal())
    db_reader.save_fresh_signals(_signal(speed=0, odometer=12345))
    rows = con.execute("SELECT speed_kmh, odometer_km FROM positions ORDER BY id").fetchall()
    con.close()
    assert rows == [(None, None), (0.0, 12345.0)]


def test_a_status_without_a_speed_is_not_driving():
    assert main._driving({"gear": "P", "speed_kmh": None}) is False


def test_a_missing_odometer_is_no_proof_the_car_stood_still(tmp_path, monkeypatch):
    """The missed-charge scan wants the odometer unchanged across a SoC rise. A reading that is
    not there proves nothing either way, as the 0 it used to be did not."""
    path = str(tmp_path / "t.db")
    db = D.Database(path)
    db._conn.execute("INSERT INTO vehicles (id, vin) VALUES (1, 'VIN1')")
    db.set_battery_capacity(67.1)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    for ts, soc, odo in (("2026-06-01T22:00:00+00:00", 50.0, None),
                         ("2026-06-02T06:00:00+00:00", 80.0, 1000.0)):
        db._conn.execute("INSERT INTO positions (vehicle_id, recorded_at, soc, charging, speed_kmh,"
                         " odometer_km, latitude, longitude) VALUES (1,?,?,0,0,?,45.0,9.0)",
                         (ts, soc, odo))
    db._conn.commit()
    assert db_reader.scan_missed_charges(apply=False) == []


def test_crash_recovery_ends_a_trip_on_the_last_odometer_the_car_sent(tmp_path, monkeypatch):
    """Crash recovery ends a trip on the last reading with an odometer in its window. A missing
    odometer stored as 0 was that reading, and the trip lost its end; a NULL is skipped."""
    wall = {"now": datetime(2026, 8, 11, 18, 0, tzinfo=timezone.utc)}
    mono = {"t": 10_000.0}
    monkeypatch.setattr(SM.time, "monotonic", lambda: mono["t"])
    monkeypatch.setattr(D, "_now_iso", lambda: wall["now"].isoformat())
    monkeypatch.setattr(R, "_now_iso", lambda: wall["now"].isoformat())
    db = D.Database(str(tmp_path / "t.db"))
    vid = db.ensure_vehicle("TESTVIN", "B10")

    def poll(rec, odometer, gear, speed):
        wall["now"] += timedelta(seconds=60)
        mono["t"] += 60
        sig = _signal(speed=speed, odometer=odometer)
        sig.update({"1": int(wall["now"].timestamp() * 1000), "1010": gear})
        rec.process(client._parse_signal("TESTVIN", sig))

    rec = R.Recorder(db, vehicle_id=vid)
    for odometer in (1000.0, 1002.0, 1005.0, _ABSENT):          # the last poll of the drive
        poll(rec, odometer, 3, 50.0)
    poll(R.Recorder(db, vehicle_id=vid), 1005.0, 0, 0)          # restarted, the car parked
    trip = db._conn.execute("SELECT * FROM trips").fetchone()
    assert trip["ended_at"] is not None
    assert (trip["start_odometer_km"], trip["end_odometer_km"]) == (1000.0, 1005.0)
