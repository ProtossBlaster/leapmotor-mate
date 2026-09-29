"""A charge never ends after Mate first read the frame it ended on.

A charge closed on its last charging reading takes that frame's own clock (#208): while the cloud
re-serves a finished charge's frame, the car's clock says when it stopped and ours does not. But
the frame was made before Mate read it, so an end later than that first reading is a car clock
running ahead of ours, not a charge going on. The end is then the first reading of that frame.
"""
from datetime import timedelta

import pytest
from test_a_trip_ends_when_the_car_last_spoke import _ms, _vd, rig  # noqa: F401  (rig: fixture)

CLOCKS = pytest.mark.parametrize("ahead", [-45, 0, 45],
                                 ids=["car clock late", "same clocks", "car clock ahead"])


def _frame(wall, ahead, soc, *, charging, odo=1000):
    """A frame read at `wall["now"]`, stamped by a car clock `ahead` seconds off ours."""
    d = _vd(ts=_ms(wall["now"] + timedelta(seconds=ahead)), odo=odo, soc=soc, gear="P", speed=0.0)
    if charging:
        d.charging_status, d.plug_connected = 1, True
        d.charge_power_kw, d.charge_current_a, d.charge_voltage_v = 7.0, -17.0, 400.0
    return d


def _read(rig, seconds, ahead, soc, *, charging, odo=1000):
    _db, _rec, poll, wall = rig
    wall["now"] += timedelta(seconds=seconds)
    poll(0, _frame(wall, ahead, soc, charging=charging, odo=odo))
    return wall["now"]


def _charge(db):
    return dict(db._conn.execute("SELECT * FROM charges").fetchone())


@CLOCKS
@pytest.mark.parametrize("repeated", [False, True], ids=["fresh", "its frame re-served"])
def test_a_charge_closed_on_its_last_charging_reading(rig, ahead, repeated):
    db, rec, poll, wall = rig
    _read(rig, 0, ahead, 99.0, charging=True)
    read_at = _read(rig, 600, ahead, 100.0, charging=True)
    if repeated:
        again = _frame(wall, ahead, 100.0, charging=True)
        for _ in range(10):
            poll(30, again)
    for _ in range(3):
        rec.mark_offline()
    _read(rig, 1800, ahead, 98.1, charging=False, odo=1010)          # back, ten kilometres on

    frame_at = read_at + timedelta(seconds=ahead)
    assert _charge(db)["ended_at"] == min(frame_at, read_at).isoformat()


@CLOCKS
def test_a_charge_seen_higher_when_the_car_is_back_ends_on_that_reading(rig, ahead):
    """Not moved and higher than its last charging reading: it went on charging while nobody saw,
    and it ended no later than the reading that shows it."""
    db, rec, _poll, _wall = rig
    _read(rig, 0, ahead, 50.0, charging=True)
    _read(rig, 600, ahead, 60.0, charging=True)
    for _ in range(3):
        rec.mark_offline()
    back_at = _read(rig, 3600, ahead, 80.0, charging=False)

    charge = _charge(db)
    assert charge["end_soc"] == 80.0
    assert charge["ended_at"] == min(back_at + timedelta(seconds=ahead), back_at).isoformat()


@pytest.mark.parametrize("back", [-1, 0, 1],
                         ids=["back before the frame's time", "back at it", "back after it"])
def test_a_charge_ends_at_the_first_read_of_its_frame_whenever_the_car_is_back(rig, back):
    """The car's clock runs two minutes ahead, the last charging frame is served twice more, and
    the car is back around the time that frame claims. Whether that time still falls inside the
    charge or not, the charge ended no later than Mate first read the frame."""
    _db, rec, poll, wall = rig
    _read(rig, 0, 120, 99.0, charging=True)
    read_at = _read(rig, 600, 120, 100.0, charging=True)
    again = _frame(wall, 120, 100.0, charging=True)
    for _ in range(2):
        poll(10, again)
    for _ in range(3):
        rec.mark_offline()
    _read(rig, 100 + back, 120, 99.9, charging=False, odo=1001)   # 120 s + `back` after read_at

    assert _charge(_db)["ended_at"] == read_at.isoformat()


def test_a_charge_whose_frame_claims_a_time_before_it_began_ends_at_its_first_read(rig):
    """A car clock a quarter of an hour late stamps the last charging frame before the charge
    began, so that time is not taken. The frame is served twice more, and the charge still ended
    no later than Mate first read it."""
    _db, rec, poll, wall = rig
    _read(rig, 0, -900, 99.0, charging=True)
    read_at = _read(rig, 600, -900, 100.0, charging=True)
    again = _frame(wall, -900, 100.0, charging=True)
    for _ in range(2):
        poll(10, again)
    for _ in range(3):
        rec.mark_offline()
    _read(rig, 1800, -900, 98.1, charging=False, odo=1010)

    charge = _charge(_db)
    assert charge["started_at"] < read_at.isoformat()
    assert charge["ended_at"] == read_at.isoformat()
