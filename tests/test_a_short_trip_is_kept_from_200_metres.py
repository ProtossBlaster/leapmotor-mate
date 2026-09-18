"""A trip is kept from 200 metres, not 500 (beta D #47, @michapr; Silvio's call, 18/09/2026).

The floor came in with v1.0.4 (2 June 2026) at 0.5 km to match Home Assistant's `leapmotor_trip`
integration, and it deleted the trip outright — row and GPS track — so a 330 m drive to the bakery
disappeared from the kilometres and from the list. michapr's point: nobody needs 500 m to manoeuvre;
200 covers moving the car to another space. Below that it is still a shuffle, and still dropped.

Two neighbouring numbers are deliberately NOT this one and are guarded here:
- `trip_distance_km` trusts the GPS over a whole-km odometer step only under 0.5 km. Moving that to
  0.2 would book a 330 m hop that crosses a km boundary as a full kilometre.
- efficiency is only computed above 0.5 km: a 0.4 % SoC step over 330 m would print as a consumption
  no car has, so a short trip keeps its distance and shows no average.
"""
import db as D
import recorder as R
from client import VehicleData
from state_machine import State, StateEvent


def _vd(odometer_km, soc=84.3):
    return VehicleData(
        vin="TESTVIN", timestamp_ms=0, soc=soc, range_km=71, odometer_km=odometer_km,
        speed_kmh=0.0, gear="P", vehicle_state="parked",
        charging_status=0, charge_power_kw=0.0, latitude=45.0, longitude=9.0,
        outside_temp=None, inside_temp=20.0, climate_target_temp=21.0, battery_min_temp=15.0,
        is_locked=True, climate_on=False, climate_cooling=False, climate_heating=False,
        climate_defrost=False, trunk_open=False, windows_open=False, sunshade_open=False,
        any_door_open=False, plug_connected=False, remaining_charge_min=0,
        charge_voltage_v=0.0, charge_current_a=0.0,
    )


def _drive(tmp_path, km, end_soc=83.9):
    db = D.Database(str(tmp_path / "t.db"))
    rec = R.Recorder(db, vehicle_id=1)
    rec._read_wallbox_energy = lambda: None
    rec._auto_note_trip = lambda tid: None
    start = _vd(1000.0)
    rec._handle_event(StateEvent(State.PARKED_ACTIVE, State.DRIVING, start), start)
    trip_id = rec._active_trip_id
    end = _vd(1000.0 + km, soc=end_soc)
    rec._handle_event(StateEvent(State.DRIVING, State.PARKED_ACTIVE, end), end)
    return db._conn.execute("SELECT distance_km, efficiency_kwh_100km FROM trips WHERE id = ?",
                            (trip_id,)).fetchone()


def test_a_330_metre_drive_is_kept(tmp_path):
    row = _drive(tmp_path, 0.33)
    assert row is not None, "michapr's 0.33 km trip was deleted"
    assert round(row["distance_km"], 2) == 0.33


def test_the_floor_itself_is_kept(tmp_path):
    assert _drive(tmp_path, 0.2) is not None


def test_a_manoeuvre_under_200_metres_is_still_dropped(tmp_path):
    assert _drive(tmp_path, 0.15) is None


def test_a_short_trip_shows_no_average(tmp_path):
    assert _drive(tmp_path, 0.33)["efficiency_kwh_100km"] is None


def test_a_330_metre_hop_across_a_km_boundary_keeps_its_gps_distance():
    # michapr's own log: odometer 2396 -> 2397 (whole km), GPS track 0.33 km.
    assert D.trip_distance_km(0.33, True, 2396, 2397) == 0.33
