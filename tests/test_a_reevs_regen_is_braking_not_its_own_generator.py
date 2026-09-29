"""On a range-extender, energy flowing into the pack is not proof of braking.

The recorder counts regen as "unplugged, and the pack current is clearly negative". On a BEV that is
exactly right. On a range-extender the generator refills the pack **while driving, unplugged**, with
the same sign — so petrol burned to make electricity is recorded as recovered braking energy.

Measured on @ebagnoli's raw signal log, on the one drive of his month where the generator ran
(19/09/2026, 77 km, 4.9 L). Splitting the intervals by whether the car's millilitre counter (3263)
moved, under the recorder's own current gate:

    generator running              3.761 kWh   89.0%   over 72 intervals
    generator off (real braking)   0.465 kWh   11.0%   over 14 intervals

Across the 50 drives in his signal window the generator share is 6.4% — because it ran on exactly
ONE of them. That is the shape of this defect: not a small error spread over every trip, but a
figure that is *right* on a pure-electric drive and almost entirely wrong on a generator drive.

🔑 The discriminator is the fuel counter itself, and it is on the same poll: signal 3263 counts
MILLILITRES, and a generator burns tens of them between two polls. Fuel fell since the last reading
→ the generator was running over that interval and the current cannot be attributed to braking.
Fuel flat → nothing was burned, so whatever went into the pack came from the wheels.

The result is a LOWER BOUND, deliberately: braking while the generator runs is real regen and is
dropped with it, because the pack current carries no way to split the two. A number that is only
ever too small is one an owner can act on; a number silently inflated by petrol is not.

⚠️ One owner, one generator drive. The 89% is what we can measure, not a constant.
"""
from client import VehicleData
from state_machine import State
import recorder as R


def _vd(ts, *, litres=None, current=-20.0, power=8.0, odo=1000.0, soc=80.0):
    return VehicleData(
        vin="TESTVIN", timestamp_ms=ts, soc=soc, range_km=300, odometer_km=odo,
        speed_kmh=50.0, gear="D", vehicle_state="driving",
        charging_status=0, charge_power_kw=power, latitude=51.8, longitude=5.8,
        outside_temp=None, inside_temp=20.0, climate_target_temp=21.0, battery_min_temp=15.0,
        is_locked=False, climate_on=False, climate_cooling=False, climate_heating=False,
        climate_defrost=False, trunk_open=False, windows_open=False, sunshade_open=False,
        any_door_open=False, plug_connected=False, remaining_charge_min=0,
        charge_voltage_v=400.0, charge_current_a=current, fuel_liters=litres)


class _SpyDB:
    def save_position(self, vid, data): pass
    def add_trip_position(self, trip_id, data): pass
    def get_open_charge(self, vid): return None
    def get_open_trip(self, vid): return None
    def get_last_soc(self, vid): return None, None
    def get_last_odometer(self, vid): return None
    def close_orphan_charges(self, vid): pass
    def close_orphan_trips(self, vid): pass


def _driving():
    rec = R.Recorder(_SpyDB(), vehicle_id=1)
    rec._started = True
    rec._sm.state = State.DRIVING
    rec._active_trip_id = 7
    rec._last_soc, rec._odometer_reading = 80.0, R.OdometerReading(1000.0, 80.0, None)
    return rec


def _run(frames):
    rec = _driving()
    for i, f in enumerate(frames):
        rec.process(f)
    return rec._regen_kwh


# ── the two cases that differ ─────────────────────────────────────────────────

def test_a_bev_counts_every_amp_that_went_into_the_pack():
    """No tank, no gate: this is the behaviour that was always right and must not move."""
    assert _run([_vd(1_000), _vd(2_000, odo=1001.0), _vd(3_000, odo=1002.0)]) > 0


def test_a_range_extender_braking_with_the_generator_off_counts_it():
    """The counter is flat, so nothing was burned: what went in came from the wheels."""
    got = _run([_vd(1_000, litres=30.0), _vd(2_000, litres=30.0, odo=1001.0),
                _vd(3_000, litres=30.0, odo=1002.0)])
    assert got > 0, "a pure-electric drive lost its braking regen"


def test_the_generator_refilling_the_pack_is_not_regen():
    """Fuel fell between the polls: petrol made this electricity, and calling it recovered energy
    is the defect (@ebagnoli 19/09 — 3.761 of 4.226 kWh)."""
    got = _run([_vd(1_000, litres=30.000), _vd(2_000, litres=29.950, odo=1001.0),
                _vd(3_000, litres=29.900, odo=1002.0)])
    assert got == 0, "petrol burned to make electricity was recorded as braking regen"


def test_only_the_intervals_the_generator_ran_are_dropped():
    """It cuts in and out over a drive. The braking before and after it still counts."""
    both = _run([_vd(1_000, litres=30.0), _vd(2_000, litres=30.0, odo=1001.0),
                 _vd(3_000, litres=29.9, odo=1002.0), _vd(4_000, litres=29.9, odo=1003.0)])
    clean = _run([_vd(1_000, litres=30.0), _vd(2_000, litres=30.0, odo=1001.0),
                  _vd(3_000, litres=30.0, odo=1002.0), _vd(4_000, litres=30.0, odo=1003.0)])
    assert 0 < both < clean, "the whole drive was dropped, or none of it was"


# ── what must NOT change ──────────────────────────────────────────────────────

def test_a_poll_that_did_not_carry_the_counter_still_counts():
    """3263 does not arrive on every poll — 145 readings against 215 current readings inside the
    19/09 drive. A missing reading is not evidence the generator ran, and refusing to count without
    it would quietly zero the regen of a range-extender that reports the signal intermittently."""
    got = _run([_vd(1_000, litres=30.0), _vd(2_000, litres=None, odo=1001.0),
                _vd(3_000, litres=None, odo=1002.0)])
    assert got > 0


def test_a_tank_that_rose_is_not_the_generator():
    """He filled up. The counter goes UP, and only a FALL is fuel burned — a guard written on the
    size of the change instead of its direction would read a refuel as the generator running.
    Asserted against the flat case rather than against zero: a later poll would otherwise carry the
    test on its own and the direction could be dropped without anything going red."""
    rose = _run([_vd(1_000, litres=10.0), _vd(2_000, litres=45.0, odo=1001.0)])
    flat = _run([_vd(1_000, litres=10.0), _vd(2_000, litres=10.0, odo=1001.0)])
    assert rose == flat > 0, "the refuel poll was dropped as if the generator had run through it"


def test_a_discharging_pack_is_still_not_regen():
    """The gate this one sits behind: positive current is the car using energy, tank or no tank."""
    assert _run([_vd(1_000, litres=30.0, current=+40.0),
                 _vd(2_000, litres=30.0, current=+40.0, odo=1001.0)]) == 0


def test_the_first_poll_of_a_car_with_a_tank_is_not_counted_blind():
    """Nothing came before it, so nothing says whether the generator ran through it. One poll per
    poller start, refused rather than guessed. A BEV never reports a counter and is unaffected."""
    assert _run([_vd(1_000, litres=30.0)]) == 0
    assert _run([_vd(1_000)]) > 0


def test_a_single_millilitre_is_already_the_generator():
    """What the threshold means: 3263 counts whole millilitres, so any fall at all is fuel that was
    burned. The constant is there to keep float arithmetic on litres from inventing a fall, not to
    set a noise floor — the counter does not drift downwards."""
    assert _run([_vd(1_000, litres=30.000), _vd(2_000, litres=29.999, odo=1001.0)]) == 0
