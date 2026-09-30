"""A reading and the row it is saved in carry one time.

The row was stamped when it was written, and the odometer baseline taken from the same reading when
the poll's work was done, a little later on a clock that moves. A restart takes the baseline back
from the row, so the running poller and a restarted one started an unseen drive at two different
times. The baseline's own row must still not count as a reading after it: without the car's clock
only the time tells them apart, and a charge at 100 % would lose the SoC it vouches for.
"""
from datetime import timedelta

import pytest
import recorder as R
from test_a_trip_ends_when_the_car_last_spoke import _vd, make_rig


def _no_frame_clock(soc, odo=1000, plugged=True):
    d = _vd(ts=0, odo=odo, soc=soc, gear="P", speed=0.0)
    if plugged:
        d.charging_status, d.plug_connected = 1, True
        d.charge_power_kw, d.charge_current_a, d.charge_voltage_v = 7.0, -17.0, 400.0
    return d


@pytest.mark.parametrize("restart", [False, True], ids=["one run", "poller restarted"])
def test_an_unseen_drive_after_a_charge_to_full_starts_at_its_last_charging_row(tmp_path, monkeypatch,
                                                                                 restart):
    db, rec, poll, wall = make_rig(tmp_path, monkeypatch, tick=timedelta(seconds=1))
    poll(0, _no_frame_clock(99.0))
    poll(600, _no_frame_clock(100.0))
    full_at = db._conn.execute("SELECT recorded_at FROM positions ORDER BY id DESC LIMIT 1").fetchone()[0]
    for _ in range(3):
        rec.mark_offline()
    back = _no_frame_clock(98.1, odo=1010, plugged=False)
    if restart:
        wall["now"] += timedelta(seconds=1800)
        R.Recorder(db, vehicle_id=rec._vehicle_id).process(back)
    else:
        poll(1800, back)

    trip, = db._conn.execute("SELECT * FROM trips WHERE reconstructed = 1").fetchall()
    assert (trip["start_odometer_km"], trip["start_soc"], trip["end_soc"]) == (1000, 100.0, 98.1), \
        "the charge at 100 % vouches for the SoC of the drive"
    assert trip["started_at"] == full_at
