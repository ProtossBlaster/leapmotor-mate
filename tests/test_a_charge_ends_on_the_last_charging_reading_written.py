"""A charge ends on the last charging reading written, not on the latest one by our clock.

A charge Mate finds already over (the car back on the road, back unplugged after an outage, or given
up on while the cloud re-served one frame), or one a restart left open, is closed on the last
`positions` row taken while charging (#208). That row was picked by our clock, which can step back
(an NTP correction). The latest time is then an earlier reading: the charge ended on a SoC below the
last one the car gave, and the energy it shows is short by the difference.

CI-safe: pure recorder / db logic, no fastapi.
"""
from datetime import timedelta

import pytest
from test_a_charge_never_ends_after_its_last_frame_was_read import _charge, _read
from test_a_trip_ends_when_the_car_last_spoke import rig  # noqa: F401  (fixture)


def _charge_across_a_clock_step(rig):
    """98 % and, ten minutes on, 99 %; then the host clock steps back five minutes and, a minute
    later, 100 %. The car's clock does not step, so from then on it is five minutes ahead of ours:
    the 100 % row is written last, with a time earlier than the 99 % one."""
    *_, wall = rig
    _read(rig, 0, 0, 98.0, charging=True)
    _read(rig, 600, 0, 99.0, charging=True)
    wall["now"] -= timedelta(minutes=5)
    return _read(rig, 60, 300, 100.0, charging=True)


def test_a_charge_the_car_drove_away_from_ends_on_its_last_charging_reading(rig):
    db, rec, *_ = rig
    last_read = _charge_across_a_clock_step(rig)
    for _ in range(3):
        rec.mark_offline()
    _read(rig, 1800, 300, 98.1, charging=False, odo=1010)          # back, ten kilometres on

    charge = _charge(db)
    assert charge["end_soc"] == 100.0, "the charge ended on the reading before the last one"
    assert charge["ended_at"] == last_read.isoformat()


def test_a_charge_a_restart_left_open_ends_on_its_last_charging_reading(rig):
    db, *_ = rig
    last_read = _charge_across_a_clock_step(rig)
    db.close_orphan_charges(_charge(db)["vehicle_id"])

    charge = _charge(db)
    assert charge["end_soc"] == 100.0, "the charge ended on the reading before the last one"
    assert charge["ended_at"] == last_read.isoformat()
    assert charge["energy_added_kwh"] == pytest.approx(1.30), "98 → 100 %, not 98 → 99 %"
