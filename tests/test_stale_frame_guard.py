"""Stale cloud frames while driving — issue #128 (Wartopia).

When the car cannot reach the cloud (4G dead zone, or the eSIM re-registering on a foreign network
after a border crossing), the cloud does not report "unknown": it keeps re-serving the LAST frame it
received — identical payload, identical `sts` timestamp — poll after poll. Wartopia's diagnostics
caught it twice in one evening, on both legs of a Nijmegen↔Germany run: ~5.5 minutes of frames frozen
at 52 and 72 km/h, while the odometer proved the car had actually driven 1 and 4 km meanwhile.

Recording those repeats invents data: a flat speed plateau in the chart, a route standing still, ~34
phantom GPS points per episode, and regen accrued from a frozen current.

The test is FRAME IDENTITY, deliberately not a frame-age threshold: the same logs show the host clock
running ~48s behind the cloud, so "age > N seconds" would be measuring the wrong thing. A repeated
timestamp means the same packet, whatever either clock says.

These pin the guard and, just as importantly, its limits: it must never fire while parked (a sleeping
car legitimately freezes for hours) and must never hide frames from the state machine.
"""
from client import VehicleData
from state_machine import State
import recorder as R


def _vd(ts, *, gear="D", speed=50.0, odo=1000.0, soc=80.0, current=0.0, power=0.0):
    return VehicleData(
        vin="TESTVIN", timestamp_ms=ts, soc=soc, range_km=300, odometer_km=odo,
        speed_kmh=speed, gear=gear, vehicle_state="driving",
        charging_status=0, charge_power_kw=power, latitude=51.8, longitude=5.8,
        outside_temp=None, inside_temp=20.0, climate_target_temp=21.0, battery_min_temp=15.0,
        is_locked=False, climate_on=False, climate_cooling=False, climate_heating=False,
        climate_defrost=False, trunk_open=False, windows_open=False, sunshade_open=False,
        any_door_open=False, plug_connected=False, remaining_charge_min=0,
        charge_voltage_v=0.0, charge_current_a=current)


class _SpyDB:
    """Counts what the recorder writes; everything else is a no-op."""
    def __init__(self):
        self.positions = 0
        self.trip_points = 0
        self.finalized = []

    def save_position(self, vid, data):
        self.positions += 1

    def finalize_trip(self, trip_id, data, regen_kwh=0.0):
        self.finalized.append(trip_id)
        return 17.0        # a real distance, so the short-hop discard stays out of the way

    def add_trip_position(self, trip_id, data):
        self.trip_points += 1

    def get_open_charge(self, vid):
        return None

    def get_open_trip(self, vid):
        return None

    def get_last_soc(self, vid):
        return None, None

    def get_last_odometer(self, vid):
        return None

    def close_orphan_charges(self, vid):
        pass

    def close_orphan_trips(self, vid):
        pass


def _driving_recorder():
    """A recorder already mid-trip, so process() takes the DRIVING path."""
    rec = R.Recorder(_SpyDB(), vehicle_id=1)
    rec._started = True
    rec._sm.state = State.DRIVING
    rec._active_trip_id = 7
    rec._last_soc, rec._last_odometer = 80.0, 1000.0
    return rec


def test_repeated_frame_while_driving_records_nothing():
    """The core of #128: 34 repeats of one frame must not become 34 samples."""
    rec = _driving_recorder()
    rec.process(_vd(1_000))                       # fresh frame → recorded
    for _ in range(34):                           # the cloud re-serves it, poll after poll
        rec.process(_vd(1_000))

    assert rec._db.positions == 1
    assert rec._db.trip_points == 1


def test_fresh_frames_while_driving_are_all_recorded():
    """The guard must not cost us real samples: a moving car sends a new timestamp every poll."""
    rec = _driving_recorder()
    for i in range(5):
        rec.process(_vd(1_000 + i * 10_000, odo=1000.0 + i))

    assert rec._db.positions == 5
    assert rec._db.trip_points == 5


def test_repeated_frame_accrues_no_phantom_regen():
    """Regen accumulates per poll — a frozen negative current would mint energy from nothing."""
    rec = _driving_recorder()
    rec.process(_vd(1_000, current=-10.0, power=5.0))
    first = rec._regen_kwh
    for _ in range(20):
        rec.process(_vd(1_000, current=-10.0, power=5.0))

    assert rec._regen_kwh == first


def test_repeated_frame_while_parked_is_still_recorded():
    """A sleeping car legitimately freezes for hours (5.7h seen in the wild) — that is not the bug,
    and the guard must stay out of the parked path."""
    rec = R.Recorder(_SpyDB(), vehicle_id=1)
    rec._started = True
    rec._sm.state = State.PARKED_SLEEP
    rec._last_soc, rec._last_odometer = 80.0, 1000.0
    for _ in range(5):
        rec.process(_vd(1_000, gear="P", speed=0.0))

    assert rec._db.positions == 5


def test_missing_timestamp_is_never_treated_as_repeat():
    """A car that never sends `sts` reports 0 every poll. Identical, but unknown — not proof of a
    repeat, so the guard must abstain rather than silently stop recording that car."""
    rec = _driving_recorder()
    for _ in range(6):
        rec.process(_vd(0))

    assert rec._db.positions == 6
    assert rec._db.trip_points == 6


def test_repeated_parked_frame_still_closes_the_trip():
    """The regression that would recreate #128 from the opposite side: car parks, THEN the modem
    drops, so the last real frame says P and the cloud repeats it. The state machine must still see
    those repeats and count its PARKED_CONFIRM readings — hiding them would strand the trip open
    until the link returns, which is the very complaint we are fixing."""
    rec = _driving_recorder()
    for _ in range(10):
        rec.process(_vd(2_000, gear="P", speed=0.0))

    assert rec._sm.state == State.PARKED_ACTIVE
