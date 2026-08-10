"""The kilometres driven before the cloud caught up — built for @riri19, then given up.

═══ CHAPTER ONE (#130, #233) — anchor them to the trip ═══════════════════════════════════════════

@riri19 lost 3 km off the front of a trip and asked to be WARNED when it happens. The warning is
the weaker half of the answer — restarting the car doesn't create coverage — so v3.8.x did the
other half: don't lose them. A trip opens on the first FRESH frame, and while the car is out of
touch the cloud re-serves the last frame it holds, so Mate stays parked through the opening
kilometres and then opens the trip with the odometer it reads AFTER them. `_offline_head` moved the
trip's start anchors BACK over that stretch: distance, energy, and from v3.8.8 the start POSITION
too, because his 19 km drive opened 5 km from home while the frame in Mate's hand still said
"parked at home" and was half an hour stale.

═══ CHAPTER TWO (v3.10.6) — the anchor was a guess, and it is gone ═══════════════════════════════

It works when the silence really does sit at the front of THIS drive. It is wrong every other time,
and the data cannot tell the two apart. Traced end to end on #244 (@adoewa): his car went quiet at
16:15, he drove 74 km home, parked overnight, and drove 2 km to the supermarket the next morning —
and those 74 km were welded onto the front of the supermarket run. Worse, a trip's start time is the
moment the link returned, so kilometres driven on the 9th were counted under the 10th.

Three discriminants were measured on real logs and all three failed:

  * TIME — @riri19 has a correct 4 km recovery after 383 minutes of silence.
  * ABSOLUTE KILOMETRES — his largest legitimate anchor is 22 km, a long errand.
  * THE GEAR BEFORE THE SILENCE — "parked" in the good case and in the bad one alike.

Silvio's decision, 10/08/2026: *«non potendo identificare a chi appartengono i KM in maniera netta
ed univoca, bisogna escluderli da tutto e far in modo di comunicarlo»*. The kilometres are not lost
— they are recorded apart and declared on the Statistics and Viaggi pages — but no trip claims them
any more, and a trip again starts where the signal returned.

🔑 The deepest reason it is coherent: the 30-minute frozen-drive limit that closes an abandoned trip
is an arbitrary constant, and while those kilometres landed inside a trip that constant decided
WHICH trip. Now it closes a trip and attributes nothing. It stopped being a number that moves data.

This file keeps chapter one's scenarios, because they are the ones that matter — they now assert
the opposite outcome, which is the point.

→ tests/test_offline_kilometres_belong_to_no_trip.py for the recording and the card.
"""
from client import VehicleData

import db as D
import recorder as R


def _vd(*, odo, soc, gear="P", speed=0.0, lat=45.0, lon=9.0, ts_ms=0):
    return VehicleData(
        vin="TESTVIN", timestamp_ms=ts_ms, soc=soc, range_km=300, odometer_km=odo,
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
    rec.process(_vd(odo=frozen_odo, soc=frozen_soc, ts_ms=1000))
    for _ in range(frozen_polls):
        rec.process(_vd(odo=frozen_odo, soc=frozen_soc, ts_ms=1000))
    rec.process(_vd(odo=resume_odo, soc=resume_soc, gear="D", speed=50.0, ts_ms=2000))
    rec.process(_vd(odo=end_odo, soc=end_soc, gear="D", speed=50.0, ts_ms=3000))
    for _ in range(6):                                            # PARKED_CONFIRM
        rec.process(_vd(odo=end_odo, soc=end_soc, ts_ms=4000))


def _trip(db):
    return db._conn.execute("SELECT * FROM trips ORDER BY id DESC LIMIT 1").fetchone()


def _gaps(db):
    return db._conn.execute("SELECT * FROM offline_gaps ORDER BY id").fetchall()


# ── chapter two: the trip keeps only what Mate watched ─────────────────────────
def test_the_kilometres_driven_before_the_signal_came_back_leave_the_trip(tmp_path):
    """Chapter one asserted `start_odometer_km == 1000` here — the 3 km pulled into the trip. They
    are still measured, but they are no longer this trip's."""
    db, rec = _rec(tmp_path)
    _drive(rec, frozen_odo=1000, frozen_soc=80.0,
           resume_odo=1003, resume_soc=79.0, end_odo=1010, end_soc=76.0)
    t = _trip(db)
    assert t is not None, "no trip was recorded at all"
    assert t["start_odometer_km"] == 1003, "the trip starts where the signal returned"
    assert t["distance_km"] == 7, "and measures only the part Mate watched"
    assert _gaps(db)[0]["distance_km"] == 3.0, "the 3 km are declared, not discarded"


def test_the_energy_leaves_with_them(tmp_path):
    """The half that must never be forgotten. Holding out the kilometres while keeping the SoC
    anchor would divide whole energy by partial distance — a worse number than the one being
    fixed, and the exact shape of the SoH defect (v3.10.2) and of #237."""
    db, rec = _rec(tmp_path)
    _drive(rec, frozen_odo=1000, frozen_soc=80.0,
           resume_odo=1003, resume_soc=79.0, end_odo=1010, end_soc=76.0)
    assert _trip(db)["start_soc"] == 79.0
    assert _gaps(db)[0]["soc_start"] == 80.0 and _gaps(db)[0]["soc_end"] == 79.0


# ── it must change nothing for everybody else ──────────────────────────────────
def test_a_trip_seen_from_its_first_metre_is_untouched(tmp_path):
    db, rec = _rec(tmp_path)
    _drive(rec, frozen_odo=1000, frozen_soc=80.0,
           resume_odo=1000, resume_soc=80.0, end_odo=1005, end_soc=78.0)
    t = _trip(db)
    assert t["start_odometer_km"] == 1000 and t["start_soc"] == 80.0
    assert t["distance_km"] == 5
    assert _gaps(db) == [], "a healthy link declares nothing"


def test_a_jump_too_small_to_be_real_is_left_alone(tmp_path):
    """The odometer signal is whole kilometres; anything under one is unresolvable noise, not a
    drive nobody saw. Neither an anchor then, nor a declared stretch now."""
    db, rec = _rec(tmp_path)
    _drive(rec, frozen_odo=1000, frozen_soc=80.0,
           resume_odo=1000.4, resume_soc=80.0, end_odo=1006, end_soc=78.0)
    assert _trip(db)["start_odometer_km"] == 1000.4
    assert _gaps(db) == []


def test_a_zero_odometer_reading_never_becomes_a_stretch(tmp_path):
    """A 0 from a partial frame would otherwise declare the car's entire lifetime mileage."""
    db, rec = _rec(tmp_path)
    _drive(rec, frozen_odo=0, frozen_soc=80.0,
           resume_odo=1003, resume_soc=79.0, end_odo=1010, end_soc=76.0)
    assert _trip(db)["start_odometer_km"] == 1003
    assert _gaps(db) == []


# ── the two cases that made chapter one worth building, now answered differently ─
def test_the_trip_starts_where_we_found_it_and_the_rest_is_declared(tmp_path):
    """@riri19's 19 km drive (#233): parked at home, cloud dark for half an hour, trip opened 5 km
    down the road. Chapter one drew that trip as starting at home. It no longer does — because the
    5 km are not in it, and a start point that matches no kilometre is a line on the map that
    corresponds to nothing. They are declared instead."""
    db, rec = _rec(tmp_path)
    for _ in range(3):
        rec.process(_vd(odo=1000, soc=80.0, lat=45.5, lon=9.5, ts_ms=1000))     # parked at home
    rec.process(_vd(odo=1005, soc=79.0, gear="D", speed=70.0, lat=45.6, lon=9.6, ts_ms=2000))
    rec.process(_vd(odo=1010, soc=76.0, gear="D", speed=70.0, lat=45.7, lon=9.7, ts_ms=3000))
    for _ in range(6):
        rec.process(_vd(odo=1010, soc=76.0, lat=45.7, lon=9.7, ts_ms=4000))
    t = _trip(db)
    assert (t["start_lat"], t["start_lon"]) == (45.6, 9.6), "where the signal returned"
    assert t["start_odometer_km"] == 1005
    assert _gaps(db)[0]["distance_km"] == 5.0, "and the 5 km are stated on their own"


def test_a_healthy_link_still_starts_the_trip_where_the_car_is(tmp_path):
    """The overwhelmingly common case must not move: with no unseen kilometres nothing is declared
    and nothing about the trip's start changes."""
    db, rec = _rec(tmp_path)
    for _ in range(3):
        rec.process(_vd(odo=1000, soc=80.0, lat=45.5, lon=9.5, ts_ms=1000))
    rec.process(_vd(odo=1000, soc=80.0, gear="D", speed=30.0, lat=45.5, lon=9.5, ts_ms=2000))
    rec.process(_vd(odo=1004, soc=78.0, gear="D", speed=50.0, lat=45.6, lon=9.6, ts_ms=3000))
    for _ in range(6):
        rec.process(_vd(odo=1004, soc=78.0, lat=45.6, lon=9.6, ts_ms=4000))
    t = _trip(db)
    assert (t["start_lat"], t["start_lon"]) == (45.5, 9.5)
    assert t["start_odometer_km"] == 1000
    assert _gaps(db) == []


def test_a_frozen_frames_null_island_never_reaches_anything(tmp_path):
    """A frozen frame's GPS is routinely 0,0. Chapter one had to guard the position it anchored;
    the trip now takes the live fix, so the only thing left to prove is that a zero baseline still
    declares its kilometres without dragging a coordinate anywhere."""
    db, rec = _rec(tmp_path)
    for _ in range(5):
        rec.process(_vd(odo=1000, soc=80.0, lat=0.0, lon=0.0, ts_ms=1000))
    rec.process(_vd(odo=1003, soc=79.0, gear="D", speed=50.0, lat=45.0, lon=9.0, ts_ms=2000))
    rec.process(_vd(odo=1010, soc=76.0, gear="D", speed=50.0, lat=45.1, lon=9.1, ts_ms=3000))
    for _ in range(6):
        rec.process(_vd(odo=1010, soc=76.0, lat=45.1, lon=9.1, ts_ms=4000))
    t = _trip(db)
    assert t["start_lat"] == 45.0 and t["start_lon"] == 9.0
    assert t["start_odometer_km"] == 1003
    assert _gaps(db)[0]["distance_km"] == 3.0
