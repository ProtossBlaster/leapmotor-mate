"""Kilometres the car drove while Mate could not see it are kept when the first news is a charge.

The odometer is the one trace a drive nobody watched leaves behind. When it has jumped on a poll
where the car is parked, the reconstruction records it: as a trip, or as an offline gap when the
SoC rose. But the reconstruction only looked while parked, and the odometer baseline moves on to
every new reading, so a jump first seen on a poll that is already CHARGING was dropped for good.
That is the ordinary end of a drive home — plug in, then the link or the poller comes back. The
charge sits between the two ends, so the stretch is kept without their SoC.
"""
from datetime import timedelta

import recorder as R
import state_machine as SM
from test_a_trip_ends_when_the_car_last_spoke import T0, _ms, _vd, rig  # noqa: F401
from test_an_outage_never_leaves_a_trip_open import _ledger


def _kept_without_soc(db, since):
    gap, = db._conn.execute("SELECT * FROM offline_gaps").fetchall()
    assert gap["started_at"] == since, "the stretch starts at the reading it is measured from"
    assert (gap["soc_start"], gap["soc_end"], gap["energy_kwh"]) == (None, None, 0)
    assert db._conn.execute("SELECT COUNT(*) FROM trips").fetchone()[0] == 0, "no trip rebuilt"


def _plugged(minute, odo, soc):
    d = _vd(ts=_ms(T0 + timedelta(minutes=minute)), odo=odo, soc=soc, gear="P", speed=0.0)
    d.charging_status, d.plug_connected = 1, True
    return d


def test_an_outage_that_ends_on_a_charge_keeps_the_kilometres_before_it(rig):
    db, rec, poll, _wall = rig
    poll(0, _vd(ts=_ms(T0), odo=1000, gear="P", speed=0.0))
    for _ in range(3):
        rec.mark_offline()
    poll(3600, _plugged(60, 1020, soc=75.0))

    assert rec.state == SM.State.CHARGING
    assert _ledger(db) == [(1000, 1020)]
    _kept_without_soc(db, T0.isoformat())


def test_a_restart_that_finds_the_car_charging_keeps_the_kilometres_before_it(rig):
    db, rec, poll, wall = rig
    poll(0, _vd(ts=_ms(T0), odo=1000, gear="P", speed=0.0))
    after_restart = R.Recorder(db, vehicle_id=rec._vehicle_id)
    wall["now"] += timedelta(hours=1)
    after_restart.process(_plugged(60, 1020, soc=75.0))

    assert after_restart.state == SM.State.CHARGING
    assert _ledger(db) == [(1000, 1020)]
    _kept_without_soc(db, T0.isoformat())


def test_a_charge_that_follows_a_parked_poll_counts_nothing(rig):
    db, rec, poll, _wall = rig
    poll(0, _vd(ts=_ms(T0), odo=1000, gear="P", speed=0.0))
    poll(60, _plugged(1, 1000, soc=80.0))
    poll(60, _plugged(2, 1000, soc=80.5))

    assert rec.state == SM.State.CHARGING
    assert _ledger(db) == []
