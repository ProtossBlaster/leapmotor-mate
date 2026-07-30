"""The kilometres driven before the cloud caught up (#130 @riri19).

@riri19 lost 3 km off the front of a trip and asked to be WARNED when it happens. The warning is
the weaker half of the answer — restarting the car doesn't create coverage — so this is the other
half: don't lose them in the first place.

Why they were lost. A trip opens on the first FRESH frame. While the car is out of touch the cloud
re-serves the last frame it holds — gear P, speed 0 — so Mate stays parked through the opening
kilometres and then opens the trip with the odometer it reads AFTER them. The odometer-jump
reconstruction (#118) can't catch them either: it advances its baseline and then bows out to the
live trip in the very same poll, so the jump is consumed and discarded.

What this fixes, and what it deliberately does not. DISTANCE and ENERGY are cumulative physical
facts, and the previous parked reading is still in memory when the trip opens, so both are
anchored to it. `started_at` is NOT backdated: when the drive began is genuinely unknown — the car
may have sat parked for hours inside the frozen window. Duration therefore stays as observed, and
the average speed of such a trip reads high. The efficiency, which is what this app is about,
comes out right. The start POSITION also stays where the signal returned, because a frozen frame's
GPS is frequently 0,0 and would plant the trip in the Gulf of Guinea.
"""
from client import VehicleData
import db as D
import recorder as R


def _vd(*, odo, soc, gear="P", speed=0.0, lat=45.0, lon=9.0):
    return VehicleData(
        vin="TESTVIN", timestamp_ms=0, soc=soc, range_km=300, odometer_km=odo,
        speed_kmh=speed, gear=gear, vehicle_state="parked",
        charging_status=0, charge_power_kw=0.0, latitude=lat, longitude=lon,
        outside_temp=None, inside_temp=20.0, climate_target_temp=21.0, battery_min_temp=15.0,
        is_locked=True, climate_on=False, climate_cooling=False, climate_heating=False,
        climate_defrost=False, trunk_open=False, windows_open=False, sunshade_open=False,
        any_door_open=False, plug_connected=False, remaining_charge_min=0,
        charge_voltage_v=0.0, charge_current_a=0.0,
    )


def _rec(tmp_path):
    db = D.Database(str(tmp_path / "t.db"))
    db.set_battery_capacity(65.0)
    vid = db.ensure_vehicle("TESTVIN", "B10")
    return db, R.Recorder(db, vehicle_id=vid)


def _drive(rec, *, frozen_odo, frozen_soc, resume_odo, resume_soc, end_odo, end_soc,
           frozen_polls=4):
    """Park, freeze, then have the signal return mid-drive, drive on, and park again."""
    rec.process(_vd(odo=frozen_odo, soc=frozen_soc))              # seeds the baselines
    for _ in range(frozen_polls):                                 # the cloud repeats the frame
        rec.process(_vd(odo=frozen_odo, soc=frozen_soc))
    rec.process(_vd(odo=resume_odo, soc=resume_soc, gear="D", speed=50.0))   # signal is back
    rec.process(_vd(odo=end_odo, soc=end_soc, gear="D", speed=50.0))
    for _ in range(6):                                            # PARKED_CONFIRM
        rec.process(_vd(odo=end_odo, soc=end_soc))


def _trip(db):
    return db._conn.execute("SELECT * FROM trips ORDER BY id DESC LIMIT 1").fetchone()


# ── the fix ────────────────────────────────────────────────────────────────────

def test_the_kilometres_driven_before_the_signal_came_back_are_counted(tmp_path):
    db, rec = _rec(tmp_path)
    _drive(rec, frozen_odo=1000, frozen_soc=80.0,
           resume_odo=1003, resume_soc=79.0, end_odo=1010, end_soc=76.0)
    t = _trip(db)
    assert t is not None, "no trip was recorded at all"
    assert t["start_odometer_km"] == 1000, "the trip still starts after the lost kilometres"
    assert t["distance_km"] == 10, f"3 km went missing off the front (got {t['distance_km']})"


def test_the_energy_that_moved_them_is_counted_too(tmp_path):
    """Anchoring the distance but not the SoC would invent a NEW wrong number: more kilometres
    over the same energy reads as an efficiency the car never achieved."""
    db, rec = _rec(tmp_path)
    _drive(rec, frozen_odo=1000, frozen_soc=80.0,
           resume_odo=1003, resume_soc=79.0, end_odo=1010, end_soc=76.0)
    t = _trip(db)
    assert t["start_soc"] == 80.0, "the head's energy is still missing"


# ── it must change nothing for everybody else ──────────────────────────────────

def test_a_trip_seen_from_its_first_metre_is_untouched(tmp_path):
    db, rec = _rec(tmp_path)
    _drive(rec, frozen_odo=1000, frozen_soc=80.0,
           resume_odo=1000, resume_soc=80.0, end_odo=1005, end_soc=78.0)
    t = _trip(db)
    assert t["start_odometer_km"] == 1000 and t["start_soc"] == 80.0
    assert t["distance_km"] == 5


def test_a_jump_too_small_to_be_real_is_left_alone(tmp_path):
    """The odometer signal is whole kilometres; anything under one is unresolvable noise, not a
    drive nobody saw."""
    db, rec = _rec(tmp_path)
    _drive(rec, frozen_odo=1000, frozen_soc=80.0,
           resume_odo=1000.4, resume_soc=80.0, end_odo=1006, end_soc=78.0)
    t = _trip(db)
    assert t["start_odometer_km"] == 1000.4, "anchored on sub-kilometre noise"


def test_a_zero_odometer_reading_never_anchors_a_trip(tmp_path):
    """A 0 from a partial frame would hand the trip the car's entire lifetime mileage."""
    db, rec = _rec(tmp_path)
    _drive(rec, frozen_odo=0, frozen_soc=80.0,
           resume_odo=1003, resume_soc=79.0, end_odo=1010, end_soc=76.0)
    t = _trip(db)
    assert t["start_odometer_km"] == 1003, "anchored to a bogus zero odometer"


# ── the simplifications, pinned so nobody 'fixes' them by accident ─────────────

def test_the_head_carries_distance_and_energy_and_nothing_else(tmp_path):
    """The anchor deliberately contains no timestamp and no position: we know the car covered those
    kilometres, but NOT when it set off (the frozen window may hold hours of parking) nor from
    where (a frozen frame's GPS is routinely 0,0). Asserting on the trip's `duration_min` could not
    catch a regression here — every timestamp in this harness is 'now' — so pin the contract at the
    source instead. Add a key to _offline_head and this goes red."""
    db, rec = _rec(tmp_path)
    rec.process(_vd(odo=1000, soc=80.0))
    rec.process(_vd(odo=1000, soc=80.0))
    head = rec._offline_head(_vd(odo=1003, soc=79.0, gear="D", speed=50.0))
    assert head == {"odometer_km": 1000, "soc": 80.0}


def test_the_start_position_stays_where_the_signal_returned(tmp_path):
    """A frozen frame's GPS is routinely 0,0 — anchoring the position too would plant the trip in
    the Gulf of Guinea (see the charges Null Island guard)."""
    db, rec = _rec(tmp_path)
    rec.process(_vd(odo=1000, soc=80.0, lat=0.0, lon=0.0))
    for _ in range(4):
        rec.process(_vd(odo=1000, soc=80.0, lat=0.0, lon=0.0))
    rec.process(_vd(odo=1003, soc=79.0, gear="D", speed=50.0, lat=45.0, lon=9.0))
    rec.process(_vd(odo=1010, soc=76.0, gear="D", speed=50.0, lat=45.1, lon=9.1))
    for _ in range(6):
        rec.process(_vd(odo=1010, soc=76.0, lat=45.1, lon=9.1))
    t = _trip(db)
    assert t["start_lat"] == 45.0 and t["start_lon"] == 9.0
    assert t["start_odometer_km"] == 1000, "the distance fix should still apply"
