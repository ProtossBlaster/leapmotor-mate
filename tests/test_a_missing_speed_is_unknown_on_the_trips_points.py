"""A speed the car did not send is unknown on a trip's points too, not a standstill.

`positions` stores such a reading as NULL, while the number in memory stays 0 for the state machine.
The trip's own points (`trip_positions`) took that 0: the trip page counted the minute after the
point as time stopped. The chart under the map is measured in a real browser, in
tests/test_a_missing_speed_is_a_gap_in_the_trip_chart_in_a_browser.py.

CI-safe: the real recorder and the trip detail, no fastapi.
"""
from dataclasses import replace
from datetime import timedelta

import db_reader
import pytest
from test_a_trip_ends_when_the_car_last_spoke import T0, _ms, _vd, make_rig


def _trip_after(tmp_path, monkeypatch, speeds):
    """A reading a minute in gear D, the car moving on each, then parked until the trip ends.
    A speed of None is a frame that did not carry one; the parser makes that 0, not reported."""
    tmp_path.mkdir()
    db, _, poll, _ = make_rig(tmp_path, monkeypatch)
    db.set_setting("timezone", "UTC")
    minute = 0

    def read(gear, kmh, km):
        frame = _vd(ts=_ms(T0 + timedelta(minutes=minute)), odo=1000 + km, soc=80.0 - minute * 0.2,
                    gear=gear, speed=kmh or 0.0)
        poll(0 if minute == 0 else 60, replace(frame, latitude=45.0 + km * 0.01,
                                               speed_reported=kmh is not None))

    for km, kmh in enumerate(speeds):
        read("D", kmh, km)
        minute += 1
    for _ in range(7):                                       # PARKED_CONFIRM on fresh frames
        read("P", 0.0, len(speeds) - 1)
        minute += 1
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    trip_id = db._conn.execute("SELECT MAX(id) FROM trips").fetchone()[0]
    points = [r[0] for r in db._conn.execute(
        "SELECT speed_kmh FROM trip_positions WHERE trip_id = ? ORDER BY id", (trip_id,))]
    return points, db_reader.get_trip_detail(trip_id)


def _split(trip):
    return trip["driving_min"], trip["stopped_min"], trip["unknown_min"]


def test_the_point_keeps_no_speed(tmp_path, monkeypatch):
    points, _ = _trip_after(tmp_path / "t", monkeypatch, [50.0, 60.0, None, 60.0, 50.0])
    assert points[:5] == [50.0, 60.0, None, 60.0, 50.0], "the 0 in memory is not a measured standstill"


def test_the_minute_after_it_is_unknown_not_stopped(tmp_path, monkeypatch):
    _, every_speed = _trip_after(tmp_path / "all", monkeypatch, [50.0, 60.0, 70.0, 60.0, 50.0])
    _, one_missing = _trip_after(tmp_path / "gap", monkeypatch, [50.0, 60.0, None, 60.0, 50.0])
    driving, stopped, unknown = _split(every_speed)
    assert _split(one_missing) == (driving - 1, stopped, unknown + 1)


def test_a_measured_standstill_is_still_stopped(tmp_path, monkeypatch):
    _, every_speed = _trip_after(tmp_path / "all", monkeypatch, [50.0, 60.0, 70.0, 60.0, 50.0])
    _, standstill = _trip_after(tmp_path / "zero", monkeypatch, [50.0, 60.0, 0.0, 60.0, 50.0])
    driving, stopped, unknown = _split(every_speed)
    assert _split(standstill) == (driving - 1, stopped + 1, unknown)


def test_the_speed_figures_skip_it(tmp_path, monkeypatch):
    _, trip = _trip_after(tmp_path / "t", monkeypatch, [50.0, 60.0, None, 60.0, 50.0])
    assert trip["max_speed_kmh"] == 60
    assert trip["avg_speed_kmh"] == pytest.approx(55)
