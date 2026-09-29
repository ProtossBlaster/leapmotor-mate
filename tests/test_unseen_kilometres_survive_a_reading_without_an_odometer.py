"""Kilometres driven unseen survive a reading without an odometer.

A frame can arrive without the odometer (signal 1318), and the parser reads it as 0. The recorder
moved its odometer baseline to every reading, so such a frame left it at 0, and the next reading
with an odometer had nothing to measure from: the drive before it was recorded nowhere, neither as
a trip nor as an offline stretch. One parked frame without an odometer was enough, outage or not.

The baseline is now the last reading that carried an odometer, with the SoC and the time of that
same reading, across a restart too. A charge under way when it was read, or open at any moment
since, means the SoC between the two ends is not what the drive spent, so the kilometres are kept
as an offline stretch without energy. A trip moves the baseline to the reading it closes on, so
what it drove is not rebuilt a second time; one that closes on a reading without an odometer leaves
no baseline behind.
"""
from datetime import timedelta
from itertools import pairwise

import db_reader
import pytest

import recorder as R
import test_an_outage_never_leaves_a_trip_open as outage
from test_a_trip_ends_when_the_car_last_spoke import _ms, _vd, rig  # noqa: F401  (rig: fixture)

BLIND = 0.0   # what the parser makes of a frame without an odometer
KM = 0.009    # degrees of latitude in a kilometre
CLOCKS = pytest.mark.parametrize("ahead", [-45, 0, 45],
                                 ids=["car clock late", "same clocks", "car clock ahead"])


@pytest.fixture
def car(rig):
    db, rec, poll, wall = rig

    def send(seconds, *, odo, soc, gear="P", speed=0.0, recorder=None, ahead=0, **extra):
        """A fresh frame `seconds` after the previous poll, stamped by a car clock `ahead` seconds
        off ours; the moment it was polled."""
        d = _vd(ts=_ms(wall["now"] + timedelta(seconds=seconds + ahead)), odo=odo, soc=soc, gear=gear,
                speed=speed)
        for name, value in extra.items():
            setattr(d, name, value)
        if recorder is None:
            poll(seconds, d)
        else:                                   # a restarted poller, on the same clocks
            wall["now"] += timedelta(seconds=seconds)
            recorder.process(d)
        return wall["now"].isoformat()

    return db, rec, send


def _rows(db, sql):
    return [dict(r) for r in db._conn.execute(sql)]


def _rebuilt(db):
    return _rows(db, "SELECT * FROM trips WHERE reconstructed = 1")


def _gaps(db):
    return _rows(db, "SELECT * FROM offline_gaps")


def _counted_once(db):
    """Every stretch of odometer a trip or an offline stretch holds: they may touch, never overlap."""
    spans = sorted([(t["start_odometer_km"], t["end_odometer_km"]) for t in _rows(db, "SELECT * FROM trips")
                    if (t["start_odometer_km"] or 0) > 0 and (t["end_odometer_km"] or 0) > 0]
                   + [(g["odometer_start"], g["odometer_end"]) for g in _gaps(db)])
    assert all(b[0] >= a[1] for a, b in pairwise(spans)), f"counted twice: {spans}"
    return spans


@pytest.mark.parametrize("blind", [1, 5], ids=["one reading", "a run of readings"])
def test_a_drive_behind_readings_without_an_odometer_is_rebuilt_whole(car, blind):
    db, _rec, send = car
    read_at = send(0, odo=1000, soc=80.0)
    send(1800, odo=BLIND, soc=75.0)                      # back from a drive nobody saw
    for _ in range(blind - 1):
        send(60, odo=BLIND, soc=75.0)
    seen_at = send(60, odo=1020, soc=75.0)

    trip, = _rebuilt(db)
    assert (trip["start_odometer_km"], trip["end_odometer_km"], trip["distance_km"]) == (1000, 1020, 20)
    assert (trip["start_soc"], trip["end_soc"]) == (80.0, 75.0)
    assert trip["efficiency_kwh_100km"] == pytest.approx(5 / 100 * 65.0 / 20 * 100)
    assert (trip["started_at"], trip["ended_at"]) == (read_at, seen_at)
    assert _gaps(db) == [] and _counted_once(db) == [(1000, 1020)]


def test_after_a_restart_the_baseline_is_one_reading(car):
    """The SoC and the time come from the row that carried the odometer, not from a later row."""
    db, rec, send = car
    read_at = send(0, odo=1000, soc=80.0)
    send(1800, odo=BLIND, soc=78.0)
    restarted = R.Recorder(db, vehicle_id=rec._vehicle_id)
    send(1800, odo=BLIND, soc=75.0, recorder=restarted)
    send(60, odo=1020, soc=75.0, recorder=restarted)

    trip, = _rebuilt(db)
    assert (trip["start_odometer_km"], trip["start_soc"], trip["started_at"]) == (1000, 80.0, read_at)
    assert _counted_once(db) == [(1000, 1020)]


def _plugged(soc):
    return {"odo": BLIND, "soc": soc, "charging_status": 1, "plug_connected": True,
            "charge_power_kw": 7.0, "charge_current_a": -17.0, "charge_voltage_v": 400.0}


@CLOCKS
@pytest.mark.parametrize("charge", ["seen charging", "found by the SoC", "seen charging, then driving"])
def test_a_charge_in_the_silence_keeps_the_kilometres_but_not_the_energy(car, charge, ahead):
    """80 % at the last odometer, 70 % at the next: ten points net, but the drive spent twenty and
    a charge put ten back. The difference of the two ends is not the drive's."""
    db, _rec, send = car
    read_at = send(0, odo=1000, soc=80.0, ahead=ahead)
    send(1800, odo=BLIND, soc=60.0, ahead=ahead)         # driven, unseen
    if charge == "found by the SoC":
        send(3600, odo=1020, soc=70.0, ahead=ahead)      # charged while nobody watched
    else:
        for soc in (62.0, 66.0, 70.0):
            send(600, ahead=ahead, **_plugged(soc))
        send(60, odo=BLIND, soc=70.0, ahead=ahead)       # unplugged
        if charge == "seen charging":
            send(60, odo=1020, soc=70.0, ahead=ahead)
        else:                                            # the next news is a drive: its trip opens
            send(60, odo=1020, soc=70.0, gear="D", speed=50.0, ahead=ahead)
    assert _rows(db, "SELECT * FROM charges"), "the charge itself is recorded"

    assert _rebuilt(db) == [], "a trip rebuilt from 80 → 70 % claims half the energy it spent"
    gap, = _gaps(db)
    assert (gap["odometer_start"], gap["odometer_end"], gap["distance_km"]) == (1000, 1020, 20)
    assert (gap["soc_start"], gap["soc_end"], gap["energy_kwh"]) == (None, None, 0)
    assert gap["started_at"] == read_at


@pytest.mark.parametrize("gear", ["P", "D"])
@CLOCKS
def test_a_charge_that_goes_on_after_the_baseline_is_seen_whatever_the_car_clock(car, gear, ahead):
    """The baseline is read mid-charge and the charge goes on 30 s more, on a reading without an
    odometer. The charge is closed on its last charging frame, by the car's clock; with that clock
    45 s late it ends before the baseline on paper, but the SoC still rose after it."""
    db, rec, send = car
    send(0, ahead=ahead, **_plugged(50.0) | {"odo": 1000})
    read_at = send(600, ahead=ahead, **_plugged(60.0) | {"odo": 1000})
    send(30, ahead=ahead, **_plugged(61.0))
    for _ in range(3):
        rec.mark_offline()
    send(1800, ahead=ahead, odo=1020, soc=55.0, gear=gear, speed=50.0 if gear == "D" else 0.0)

    assert _rebuilt(db) == [], "a trip rebuilt from 60 → 55 % leaves out what the charge added"
    gap, = _gaps(db)
    assert (gap["odometer_start"], gap["odometer_end"], gap["started_at"]) == (1000, 1020, read_at)
    assert (gap["soc_start"], gap["soc_end"], gap["energy_kwh"]) == (None, None, 0)


@pytest.mark.parametrize("gear, restart", [("P", False), ("D", False), ("P", True)],
                         ids=["back parked", "back driving", "back parked, poller restarted"])
def test_a_baseline_read_mid_charge_does_not_vouch_for_the_soc_after_it(car, gear, restart):
    """The last odometer is read while charging at 60 %, then nothing until the car is back 20 km on
    at 55 %. Nobody saw where the charge stopped: 80 % and a drive of 25 points look the same."""
    db, rec, send = car
    send(0, **_plugged(50.0) | {"odo": 1000})
    read_at = send(600, **_plugged(60.0) | {"odo": 1000})
    for _ in range(3):
        rec.mark_offline()
    back = R.Recorder(db, vehicle_id=rec._vehicle_id) if restart else None
    send(1800, odo=1020, soc=55.0, gear=gear, speed=50.0 if gear == "D" else 0.0, recorder=back)

    assert _rebuilt(db) == [], "a trip rebuilt from 60 → 55 % leaves out what the charge added"
    gap, = _gaps(db)
    assert (gap["odometer_start"], gap["odometer_end"], gap["started_at"]) == (1000, 1020, read_at)
    assert (gap["soc_start"], gap["soc_end"], gap["energy_kwh"]) == (None, None, 0)


@CLOCKS
@pytest.mark.parametrize("gear", ["P", "D"])
@pytest.mark.parametrize("repeated, restart", [(False, False), (True, False), (True, True)],
                         ids=["fresh to the end", "its frame re-served", "re-served, poller restarted"])
def test_a_baseline_read_charging_at_full_vouches_for_the_soc_after_it(car, rig, gear, repeated,
                                                                      restart, ahead):
    """A charge seen at 100 % has nothing left to put back after that reading, so the SoC of the
    two ends is the drive's (#208: an overnight charge finished at 100 %, the cloud served that
    frame for two hours more, and the next news was the car ten kilometres on at 98.1 %). A repeat
    of the frame is no new charging reading. Still an estimate from the SoC, like any unseen drive."""
    db, rec, send = car
    _db, _rec, poll, _wall = rig
    send(0, ahead=ahead, **_plugged(99.0) | {"odo": 1000})
    read_at = send(600, ahead=ahead, **_plugged(100.0) | {"odo": 1000})
    if repeated:
        frame = _rows(db, "SELECT frame_ts FROM positions ORDER BY id DESC LIMIT 1")[0]["frame_ts"]
        again = _vd(ts=frame, odo=1000, soc=100.0, gear="P", speed=0.0)
        for name, value in _plugged(100.0).items():
            if name != "odo":
                setattr(again, name, value)
        for _ in range(240):                             # two hours of the same frame
            poll(30, again)
        assert _rows(db, "SELECT COUNT(*) AS n FROM positions WHERE charging = 1")[0]["n"] > 240
    for _ in range(3):
        rec.mark_offline()
    back = R.Recorder(db, vehicle_id=rec._vehicle_id) if restart else None
    send(1800, odo=1010, soc=98.1, gear=gear, speed=50.0 if gear == "D" else 0.0, recorder=back,
         ahead=ahead)

    if gear == "P":
        trip, = _rebuilt(db)
        assert (trip["start_odometer_km"], trip["start_soc"], trip["started_at"]) == (1000, 100.0, read_at)
        assert (trip["end_odometer_km"], trip["end_soc"]) == (1010, 98.1)
    else:
        gap, = _gaps(db)
        assert (gap["odometer_start"], gap["odometer_end"], gap["started_at"]) == (1000, 1010, read_at)
        assert (gap["soc_start"], gap["soc_end"]) == (100.0, 98.1) and gap["energy_kwh"] > 0


@pytest.mark.parametrize("refreshed", ["the same frame", "a new charging frame"])
def test_a_refresh_is_a_charging_reading_only_with_a_new_frame(car, rig, tmp_path, monkeypatch,
                                                                refreshed):
    """A Refresh or a command makes the web write the frame it read. The frame the full baseline
    was read from adds nothing; a later frame, still charging, does."""
    db, rec, send = car
    _db, _rec, _poll, wall = rig
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    send(0, **_plugged(99.0) | {"odo": 1000})
    read_at = send(600, **_plugged(100.0) | {"odo": 1000})
    frame = _rows(db, "SELECT frame_ts FROM positions ORDER BY id DESC LIMIT 1")[0]["frame_ts"]
    wall["now"] += timedelta(minutes=30)
    db_reader.save_fresh_signals({"1": frame if refreshed == "the same frame" else _ms(wall["now"]),
                                  "100003": 100.0, "1318": 1000, "1319": 0, "1010": 0, "1149": 1,
                                  "1177": 400.0, "1178": -17.0, "3": 45.0, "2": 9.0})
    db._conn.execute("UPDATE positions SET recorded_at = ? WHERE id = (SELECT MAX(id) FROM positions)",
                     (wall["now"].isoformat(),))       # the web writes by the real clock, not the rig's
    db._conn.commit()
    assert _rows(db, "SELECT charging FROM positions ORDER BY id DESC LIMIT 1") == [{"charging": 1}]
    for _ in range(3):
        rec.mark_offline()
    send(1800, odo=1010, soc=98.1)

    if refreshed == "the same frame":
        trip, = _rebuilt(db)
        assert (trip["start_soc"], trip["started_at"], trip["end_soc"]) == (100.0, read_at, 98.1)
    else:
        assert _rebuilt(db) == []
        gap, = _gaps(db)
        assert (gap["soc_start"], gap["soc_end"], gap["energy_kwh"]) == (None, None, 0)


def test_a_charge_left_on_a_reading_without_an_odometer_ends_on_it(car):
    """The last charging reading comes without an odometer, and the car is back 10 km on at 85 %.
    It moved, so how far the charge went is unknown: the charge ends on its last reading, not on
    the SoC of the return, as it would for a car that never moved."""
    db, rec, send = car
    send(0, **_plugged(78.0) | {"odo": 1000})
    send(1800, **_plugged(81.0))
    for _ in range(3):
        rec.mark_offline()
    send(3600, odo=1010, soc=85.0, gear="D", speed=50.0)

    charge, = _rows(db, "SELECT * FROM charges")
    assert charge["end_soc"] == 81.0


@pytest.mark.parametrize("back", [-1, 0, 1],
                         ids=["back before the frame's time", "back at it", "back after it"])
def test_a_full_baseline_vouches_for_the_soc_with_the_car_clock_far_ahead(car, rig, back):
    """The car's clock runs two minutes ahead, the full frame is served twice more, and the car is
    back a kilometre on around the time that frame claims: the charge ended at the baseline."""
    db, rec, send = car
    _db, _rec, poll, _wall = rig
    send(0, ahead=120, **_plugged(99.0) | {"odo": 1000})
    read_at = send(600, ahead=120, **_plugged(100.0) | {"odo": 1000})
    frame = _rows(db, "SELECT frame_ts FROM positions ORDER BY id DESC LIMIT 1")[0]["frame_ts"]
    again = _vd(ts=frame, odo=1000, soc=100.0, gear="P", speed=0.0)
    for name, value in _plugged(100.0).items():
        if name != "odo":
            setattr(again, name, value)
    for _ in range(2):
        poll(10, again)
    for _ in range(3):
        rec.mark_offline()
    send(100 + back, odo=1001, soc=99.9, ahead=120)

    trip, = _rebuilt(db)
    assert (trip["start_soc"], trip["started_at"], trip["end_soc"]) == (100.0, read_at, 99.9)


@CLOCKS
def test_a_charge_seen_after_a_full_baseline_still_counts(car, ahead):
    """Full at the baseline says nothing about a charge that came after it."""
    db, rec, send = car
    read_at = send(0, ahead=ahead, **_plugged(100.0) | {"odo": 1000})
    for _ in range(3):
        rec.mark_offline()
    send(1800, odo=BLIND, soc=90.0, ahead=ahead)         # driven unseen, parked
    for soc in (92.0, 96.0):
        send(600, ahead=ahead, **_plugged(soc))          # a second charge, seen
    send(60, odo=BLIND, soc=96.0, ahead=ahead)           # unplugged
    send(60, odo=1020, soc=96.0, ahead=ahead)

    assert _rebuilt(db) == []
    gap, = _gaps(db)
    assert (gap["odometer_start"], gap["odometer_end"], gap["started_at"]) == (1000, 1020, read_at)
    assert (gap["soc_start"], gap["soc_end"], gap["energy_kwh"]) == (None, None, 0)


@pytest.mark.parametrize("then", ["resumed", "back plugged in elsewhere"])
def test_a_charge_paused_at_the_baseline_still_counts_after_it(car, then):
    """The last odometer is read while the cable is in and the charge has paused. The session is
    still open: it may resume after that reading, and a charge ends where its charging readings
    stop, which is at the pause."""
    db, rec, send = car
    send(0, **_plugged(55.0) | {"odo": 1000})
    read_at = send(600, **_plugged(60.0) | {"odo": 1000, "charging_status": 0, "charge_power_kw": 0.0,
                                             "charge_current_a": 0.0})
    if then == "resumed":
        for soc in (62.0, 66.0):
            send(600, **_plugged(soc))
    for _ in range(3):
        rec.mark_offline()
    back = {"plug_connected": True} if then == "back plugged in elsewhere" else {}
    send(1800, odo=1020, soc=55.0, **back)

    assert _rebuilt(db) == [], "a trip rebuilt from 60 → 55 % leaves out what the charge added"
    gap, = _gaps(db)
    assert (gap["odometer_start"], gap["odometer_end"], gap["started_at"]) == (1000, 1020, read_at)
    assert (gap["soc_start"], gap["soc_end"], gap["energy_kwh"]) == (None, None, 0)


def _reev_plugged(soc, odo=BLIND):
    """A range extender charging at home: the cable in, the SoC climbing, and no charging signal."""
    return {"odo": odo, "soc": soc, "is_reev": True, "plug_connected": True, "charging_status": 0,
            "charge_current_a": None, "charge_power_kw": None}


@CLOCKS
@pytest.mark.parametrize("gear, restart", [("P", False), ("D", False), ("P", True)],
                         ids=["back parked", "back driving", "back parked, poller restarted"])
@pytest.mark.parametrize("baseline", ["before the charge", "mid-charge"])
def test_a_range_extender_charge_known_only_from_its_soc_is_a_charge_too(car, gear, restart, baseline,
                                                                          ahead):
    """A range extender charging at home sends no charging signal: Mate knows the charge only from
    the SoC climbing with the cable in, and records it as one. It sits between the two ends all the
    same, whether the last odometer was read before it or during it."""
    db, rec, send = car
    read_at = send(0, odo=1000, soc=60.0, is_reev=True, ahead=ahead)
    send(10, ahead=ahead, **_reev_plugged(60.0))
    send(600, ahead=ahead, **_reev_plugged(61.0))
    assert rec._active_charge_id is not None, "the rising SoC should have opened a charge"
    if baseline == "mid-charge":
        read_at = send(600, ahead=ahead, **_reev_plugged(62.0, odo=1000))
    for soc in (66.0, 70.0):
        send(600, ahead=ahead, **_reev_plugged(soc))
    send(60, odo=BLIND, soc=70.0, is_reev=True, ahead=ahead)   # unplugged
    charge, = _rows(db, "SELECT * FROM charges")
    assert charge["ended_at"] and not charge["reconstructed"]
    back = R.Recorder(db, vehicle_id=rec._vehicle_id) if restart else None
    send(1800, odo=1020, soc=55.0, gear=gear, speed=50.0 if gear == "D" else 0.0, is_reev=True,
         recorder=back, ahead=ahead)

    assert _rebuilt(db) == [], "a trip rebuilt from the two ends claims what the charge put back"
    gap, = _gaps(db)
    assert (gap["odometer_start"], gap["odometer_end"], gap["started_at"]) == (1000, 1020, read_at)
    assert (gap["soc_start"], gap["soc_end"], gap["energy_kwh"]) == (None, None, 0)


def test_a_trip_closed_without_an_odometer_keeps_its_last_kilometres(car):
    """The last odometer of the drive is 1003; it ends 2 km later on readings without one. Those
    2 km are the trip's, measured by its route, and not a drive of their own after it."""
    db, _rec, send = car
    for km in range(6):                                  # its route: one kilometre a minute
        send(60 if km else 0, odo=1000 + km if km <= 3 else BLIND, soc=80.0 - km * 0.2,
             gear="D", speed=50.0, latitude=45.0 + km * KM)
    for _ in range(6):
        send(10, odo=BLIND, soc=79.0, latitude=45.0 + 5 * KM)
    trip, = _rows(db, "SELECT * FROM trips")
    assert trip["ended_at"] is not None
    send(600, odo=1005, soc=79.0)

    assert _rebuilt(db) == [] and _gaps(db) == []


@pytest.mark.parametrize("close", ["sixth P poll", "plug-in"])
def test_a_trip_closed_on_its_first_odometer_after_a_silence_is_not_rebuilt_again(car, close):
    """The last kilometres are driven on readings without an odometer, and the reading the trip
    closes on is the first with one again. The trip ends on it: nothing before it is rebuilt."""
    db, _rec, send = car
    for km in range(6):
        send(60 if km else 0, odo=1000 + km if km <= 3 else BLIND, soc=80.0 - km * 0.2,
             gear="D", speed=50.0, latitude=45.0 + km * KM)
    if close == "plug-in":
        send(10, odo=1005, soc=79.0, latitude=45.0 + 5 * KM, plug_connected=True)
    else:
        for _ in range(5):
            send(10, odo=BLIND, soc=79.0, latitude=45.0 + 5 * KM)
        send(10, odo=1005, soc=79.0, latitude=45.0 + 5 * KM)
    trip, = _rows(db, "SELECT * FROM trips WHERE COALESCE(reconstructed, 0) = 0")
    assert trip["ended_at"] is not None and trip["end_odometer_km"] == 1005

    assert _rebuilt(db) == [] and _gaps(db) == []
    assert _counted_once(db) == [(1000, 1005)]


def test_after_a_frozen_drive_the_unseen_kilometres_start_where_the_news_stopped(car, rig):
    """A frame that freezes mid-drive is closed by the half-hour guard, on that same frame. What was
    driven after it began when the frame froze, not when the guard gave up on it."""
    db, _rec, send = car
    _db, _rec, poll, _wall = rig
    for km in range(5):
        frozen_at = send(60 if km else 0, odo=1000 + km, soc=80.0 - km * 0.2, gear="D", speed=50.0,
                         latitude=45.0 + km * KM)
    frozen = _rows(db, "SELECT frame_ts FROM positions ORDER BY id DESC LIMIT 1")[0]
    repeat = _vd(ts=frozen["frame_ts"], odo=1004, soc=79.2, gear="D", speed=50.0)
    repeat.latitude = 45.0 + 4 * KM
    for _ in range(186):                                 # the same frame for 31 minutes
        poll(10, repeat)
    trip, = _rows(db, "SELECT * FROM trips")
    assert trip["ended_at"] is not None, "the guard should have closed the trip"
    send(600, odo=1020, soc=76.0)

    rebuilt, = _rebuilt(db)
    assert (rebuilt["start_odometer_km"], rebuilt["started_at"]) == (1004, frozen_at)
    _counted_once(db)


@pytest.mark.parametrize("restart", [False, True], ids=["one run", "poller restarted"])
def test_behind_a_frame_repeated_while_parked_the_baseline_is_where_it_first_came(car, rig, restart):
    """The cloud re-serves one parked frame for half an hour, and a repeat moves nothing: the drive
    after it began no earlier than that frame first came. A restart among the repeats must agree."""
    db, rec, send = car
    _db, _rec, poll, _wall = rig
    read_at = send(0, odo=1000, soc=80.0)
    frame = _rows(db, "SELECT frame_ts FROM positions ORDER BY id DESC LIMIT 1")[0]["frame_ts"]
    for _ in range(180):                                 # the same frame for 30 minutes
        poll(10, _vd(ts=frame, odo=1000, soc=80.0, gear="P", speed=0.0))
    back = R.Recorder(db, vehicle_id=rec._vehicle_id) if restart else None
    send(600, odo=1020, soc=75.0, recorder=back)

    trip, = _rebuilt(db)
    assert (trip["start_odometer_km"], trip["start_soc"], trip["started_at"]) == (1000, 80.0, read_at)


@pytest.mark.parametrize("stored", ["0", "NULL"])
@pytest.mark.parametrize("restart", [False, True], ids=["one run", "poller restarted"])
def test_a_frame_that_first_came_without_its_odometer_is_the_baseline_once_it_has_one(car, rig,
                                                                                      restart, stored):
    """The cloud serves one frame without the odometer, then the same frame with it. The baseline
    is the first reading of that frame to carry one, in the running recorder and after a restart,
    and a missing odometer is skipped whether it was stored as 0 or as NULL."""
    db, rec, send = car
    _db, _rec, poll, wall = rig
    send(0, odo=1000, soc=80.0)
    send(1800, odo=BLIND, soc=78.0)                       # a new frame, without the odometer
    frame = _rows(db, "SELECT frame_ts FROM positions ORDER BY id DESC LIMIT 1")[0]["frame_ts"]
    poll(60, _vd(ts=frame, odo=1020, soc=78.0, gear="P", speed=0.0))    # the same frame, with it
    read_at = wall["now"].isoformat()
    if stored == "NULL":
        db._conn.execute("UPDATE positions SET odometer_km = NULL WHERE odometer_km = 0")
        db._conn.commit()
    back = R.Recorder(db, vehicle_id=rec._vehicle_id) if restart else None
    send(600, odo=1025, soc=77.0, recorder=back)

    assert _counted_once(db) == [(1000, 1020), (1020, 1025)]
    assert _rebuilt(db)[-1]["started_at"] == read_at


@pytest.mark.parametrize("restart", [False, True], ids=["one run", "poller restarted"])
def test_readings_without_a_frame_clock_are_never_taken_for_repeats(car, rig, restart):
    """Without the car's clock nothing says two readings are one frame: each is a reading of its
    own, and the baseline is the latest."""
    db, rec, _send = car
    _db, _rec, poll, wall = rig
    poll(0, _vd(ts=0, odo=1000, soc=80.0, gear="P", speed=0.0))
    poll(1800, _vd(ts=0, odo=1000, soc=79.5, gear="P", speed=0.0))
    read_at = wall["now"].isoformat()
    if restart:
        rec = R.Recorder(db, vehicle_id=rec._vehicle_id)
    wall["now"] += timedelta(seconds=600)
    rec.process(_vd(ts=0, odo=1020, soc=76.0, gear="P", speed=0.0))

    trip, = _rebuilt(db)
    assert (trip["start_odometer_km"], trip["start_soc"], trip["started_at"]) == (1000, 79.5, read_at)


@pytest.mark.parametrize("restart", [False, True], ids=["one run", "poller restarted"])
def test_a_host_clock_stepping_back_does_not_count_kilometres_again(car, rig, restart):
    """The host clock is corrected two minutes back between two readings. The last reading is the
    last one written, whatever time it carries, so a restart finds the baseline the running
    recorder had, and no kilometre is counted twice."""
    db, rec, send = car
    _db, _rec, _poll, wall = rig
    send(0, odo=1000, soc=80.0)
    send(600, odo=1005, soc=79.0)
    wall["now"] -= timedelta(seconds=120)
    send(60, odo=1006, soc=78.8, ahead=120)              # the car's clock goes on as before
    back = R.Recorder(db, vehicle_id=rec._vehicle_id) if restart else None
    send(180, odo=1006, soc=78.8, recorder=back)

    assert _counted_once(db) == [(1000, 1005), (1005, 1006)]


def test_a_host_clock_stepping_back_does_not_rebuild_part_of_a_live_trip_after_a_restart(car, rig):
    db, rec, send = car
    _db, _rec, _poll, wall = rig
    send(0, odo=1000, soc=80.0, gear="D", speed=50.0, latitude=45.0)
    send(600, odo=1005, soc=79.0, gear="D", speed=50.0, latitude=45.0 + 5 * KM)
    wall["now"] -= timedelta(seconds=120)
    send(60, odo=1006, soc=78.8, gear="D", speed=50.0, ahead=120, latitude=45.0 + 6 * KM)
    back = R.Recorder(db, vehicle_id=rec._vehicle_id)
    send(180, odo=1006, soc=78.8, recorder=back, latitude=45.0 + 6 * KM)

    trip, = _rows(db, "SELECT * FROM trips")
    assert not trip["reconstructed"] and trip["distance_km"] == pytest.approx(6.0, abs=0.05)


def test_a_reading_without_a_frame_timestamp_still_seeds_the_baseline(car):
    """Rows older than the frame timestamp have no frame to group repeats by: the row itself."""
    db, rec, send = car
    read_at = send(0, odo=1000, soc=80.0)
    db._conn.execute("UPDATE positions SET frame_ts = NULL")
    assert db.get_last_odometer_reading(rec._vehicle_id) == (1000.0, 80.0, read_at, False, None)


def test_a_trip_opened_without_an_odometer_is_not_counted_twice(car):
    db, _rec, send = car
    send(0, odo=1000, soc=80.0)
    send(1800, odo=BLIND, soc=76.0, gear="D", speed=50.0)   # opens on a reading without one
    for odo in (1021, 1022):
        send(60, odo=odo, soc=75.0, gear="D", speed=50.0)
    for _ in range(6):
        send(10, odo=1022, soc=75.0)
    send(600, odo=1022, soc=75.0)

    assert _rebuilt(db) == []
    _counted_once(db)


# ── a trip an outage ended ────────────────────────────────────────────────────────────────────────

def test_a_charge_after_a_long_outage_keeps_the_kilometres_without_the_soc(rig):
    """The trip closes on the reading before the silence, and the kilometres after it are a stretch
    from that reading. The charge running when the car is back sits between the two ends, so their
    SoC is no measure of the stretch."""
    db, rec, poll, _wall = rig
    outage._drive(poll)
    outage._outage(rec, poll, outage.LONG, outage._plugged(40, 1007))

    gap, = _gaps(db)
    assert (gap["odometer_start"], gap["odometer_end"]) == (1005, 1007)
    assert gap["started_at"] == outage.LAST_SPOKE, "the stretch starts where the news stopped"
    assert (gap["soc_start"], gap["soc_end"], gap["energy_kwh"]) == (None, None, 0)
    assert outage._ledger(db) == [(1000, 1005), (1005, 1007)]


def test_the_kilometres_after_a_long_outage_start_where_its_trip_closed(rig):
    """Whichever reading the trip closes on, the kilometres after it start there. With the host
    clock stepped back during the drive, that need not be the reading the baseline was left at."""
    db, rec, poll, wall = rig
    poll(0, outage._at(0, 1000))
    for i in range(1, 5):
        poll(60, outage._at(i, 1000 + i))
    wall["now"] -= timedelta(minutes=5)             # the host clock steps back
    poll(60, outage._at(5, 1005))
    outage._outage(rec, poll, outage.LONG, outage._park(40, 1025))

    trip, rebuilt = outage._trips(db)
    assert rebuilt["reconstructed"] == 1 and rebuilt["start_odometer_km"] == trip["end_odometer_km"]
    ledger = outage._ledger(db)
    assert (ledger[0][0], ledger[-1][1]) == (1000, 1025)
    assert all(a[1] == b[0] for a, b in pairwise(ledger)), "a kilometre fell between the two"
