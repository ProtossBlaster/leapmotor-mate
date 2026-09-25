"""Kilometres driven while the poller was down are not thrown away because the car also charged.

Measured on Silvio's own B10 on 24/09/2026. The container was stopped from the 15th to the 24th.
In those nine days the car drove **80 km** and was charged: the odometer went 5585 → 5665 and the
battery 68.8 → 91.1 %. When the poller came back it wrote, correctly, one reconstructed CHARGE for
the SoC that had risen — and nothing at all for the kilometres. No trip, no offline gap: the
odometer is simply higher than it was, and 80 km exist in no page of Mate.

The cause is one guard doing more than it says (`_maybe_reconstruct_trip`):

    if data.soc - prev_soc > 0.5:
        return                       # SoC rose → a charge, not a pure drive

It is right that this is not a pure drive — a trip reconstructed from a SoC that went UP would
invent an impossible consumption. But `return` throws the odometer delta away with it, and those
kilometres were really driven: they are exactly what `offline_gaps` exists to hold — *measured,
and attributable to no trip* (#130, #233).

So: a drive that cannot be reconstructed as a trip is still declared as a gap. The energy is left
out (`energy_kwh` stays None) precisely because the SoC rose: how much of the rise was charging and
how much was driving cannot be told apart, and half a fraction is worse than none.
"""
import datetime

from client import VehicleData
from state_machine import State
import recorder as R


def _vd(soc, odo, *, gear="P", speed=0.0, charging=0, plug=False):
    return VehicleData(
        vin="TESTVIN", timestamp_ms=0, soc=soc, range_km=300, odometer_km=odo,
        speed_kmh=speed, gear=gear, vehicle_state="parked",
        charging_status=charging, charge_power_kw=0.0, latitude=45.0, longitude=9.0,
        outside_temp=None, inside_temp=20.0, climate_target_temp=21.0, battery_min_temp=15.0,
        is_locked=True, climate_on=False, climate_cooling=False, climate_heating=False,
        climate_defrost=False, trunk_open=False, windows_open=False, sunshade_open=False,
        any_door_open=False, plug_connected=plug, remaining_charge_min=0,
        charge_voltage_v=0.0, charge_current_a=0.0)


class _DB:
    def __init__(self):
        self.trips, self.gaps = [], []

    def create_reconstructed_trip(self, vid, start_soc, start_odo, started_at, data):
        self.trips.append((start_odo, data.odometer_km))
        return len(self.trips)

    def record_offline_gap(self, vehicle_id, *, started_at, ended_at, odo_start, odo_end,
                           soc_start, soc_end):
        self.gaps.append({"odo_start": odo_start, "odo_end": odo_end,
                          "soc_start": soc_start, "soc_end": soc_end})


def _rec(last_odo=5585.0, last_soc=68.8):
    rec = R.Recorder(_DB(), vehicle_id=1)
    rec._sm.state = State.PARKED_ACTIVE
    rec._active_trip_id = None
    rec._last_odometer = last_odo
    rec._last_soc, rec._last_soc_ts = last_soc, "2026-09-15T12:51:00+00:00"
    return rec


def test_the_eighty_kilometres_are_declared_instead_of_vanishing():
    """Silvio's own nine days: 5585 → 5665 km with the battery up 68.8 → 91.1 %."""
    rec = _rec()
    rec._maybe_reconstruct_trip(_vd(91.1, 5665.0))
    assert rec._db.trips == [], "a SoC that ROSE cannot be reconstructed as a trip"
    assert len(rec._db.gaps) == 1, "the 80 km were thrown away with the trip"
    gap = rec._db.gaps[0]
    assert (gap["odo_start"], gap["odo_end"]) == (5585.0, 5665.0)


def test_a_pure_drive_is_still_a_trip_not_a_gap():
    """The path everyone is on does not move: SoC fell, so it is a drive and it is rebuilt."""
    rec = _rec(last_odo=1000.0, last_soc=60.0)
    rec._maybe_reconstruct_trip(_vd(53.0, 1015.0))
    assert rec._db.trips == [(1000.0, 1015.0)]
    assert rec._db.gaps == []


def test_a_charge_with_no_kilometres_declares_nothing():
    """Charging while parked is not a silence to declare: the odometer never moved."""
    rec = _rec(last_odo=1000.0, last_soc=60.0)
    rec._maybe_reconstruct_trip(_vd(85.0, 1000.0))
    assert rec._db.trips == [] and rec._db.gaps == []


def test_a_sub_kilometre_blip_is_not_a_gap():
    """The same floor the trip reconstruction uses: under a kilometre is noise, not a drive."""
    rec = _rec(last_odo=1000.0, last_soc=60.0)
    rec._maybe_reconstruct_trip(_vd(85.0, 1000.4))
    assert rec._db.gaps == []


def test_the_baseline_still_advances_so_it_fires_once():
    """A gap declared twice would double the kilometres it holds."""
    rec = _rec()
    rec._maybe_reconstruct_trip(_vd(91.1, 5665.0))
    rec._maybe_reconstruct_trip(_vd(91.1, 5665.0))
    assert len(rec._db.gaps) == 1
    assert rec._last_odometer == 5665.0
