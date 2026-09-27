"""A poll the cloud answered without a SoC must not erase a charge from the scan.

The missed-charge scan used to select `... WHERE soc IS NOT NULL`, so frames carrying no SoC
were simply not part of the walk. The offline-charge work needs them — a fresh frame without a
SoC still proves the car was in contact, which is what breaks the outage the new exception looks
for — so the filter was dropped from the query. That also put those frames in front of the
ordinary parked-rise walk, where `_rising` refuses them and the run stops dead.

Measured on the same seed, same function, before this test existed:

    main 55ba2dc  → 1 candidate (30 % → 60 %)
    PR  14cfe2c  → 0

Not a charge split in two: a charge that disappears. The parked walk therefore has to keep
stepping over SoC-less frames, exactly as the old query did, while the offline exception goes on
seeing them.
"""
from datetime import datetime, timedelta, timezone

import db_reader
from test_missed_charge_scan import _seed

T0 = datetime(2026, 6, 1, 22, tzinfo=timezone.utc)


def _frame(db, seconds, soc, *, odo=1000, gear="P", speed=0):
    db._conn.execute(
        "INSERT INTO positions (vehicle_id, recorded_at, frame_ts, soc, odometer_km, gear,"
        " speed_kmh, charging, latitude, longitude) VALUES (1,?,?,?,?,?,?,0,45,9)",
        ((T0 + timedelta(seconds=seconds)).isoformat(),
         int((T0 + timedelta(seconds=seconds)).timestamp() * 1000), soc, odo, gear, speed))
    db._conn.commit()


def test_one_charge_survives_a_poll_that_carried_no_soc(tmp_path, monkeypatch):
    db = _seed(tmp_path, monkeypatch)
    _frame(db, 0, 30)
    _frame(db, 3600, None)          # the cloud said nothing about the SoC on this poll
    _frame(db, 7200, 60)
    got = db_reader.scan_missed_charges()
    assert len(got) == 1
    assert (got[0]["start_soc"], got[0]["end_soc"]) == (30, 60)
    assert got[0]["duration_min"] == 120


def test_several_blind_polls_in_a_row_are_stepped_over(tmp_path, monkeypatch):
    db = _seed(tmp_path, monkeypatch)
    _frame(db, 0, 30)
    for seconds in range(600, 3600, 600):
        _frame(db, seconds, None)
    _frame(db, 7200, 60)
    got = db_reader.scan_missed_charges()
    assert len(got) == 1 and (got[0]["start_soc"], got[0]["end_soc"]) == (30, 60)


def test_a_fresh_frame_without_soc_still_breaks_the_offline_exception(tmp_path, monkeypatch):
    """The reason those frames are in the walk at all: they prove the car was in contact, so
    the moving-endpoint exception must not reconstruct a charge across one."""
    db = _seed(tmp_path, monkeypatch)
    db._conn.execute(
        "INSERT INTO positions (vehicle_id, recorded_at, frame_ts, soc, odometer_km, gear,"
        " speed_kmh, charging, latitude, longitude) VALUES (1,?,?,30,1000,'D',30,0,45,9)",
        (T0.isoformat(), int(T0.timestamp() * 1000)))
    for seconds in range(60, 4 * 3600, 60):
        _frame(db, seconds, None, gear="D", speed=30)
    _frame(db, 4 * 3600, 80, odo=1002, gear="D", speed=30)
    db._conn.commit()
    assert db_reader.scan_missed_charges() == []
