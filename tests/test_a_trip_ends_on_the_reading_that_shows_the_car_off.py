"""A trip ends on the first reading that shows the car switched off, in the standstill it closes on.

The car's own record of a drive ends when the car is switched off (READY 1 → 0). Mate closes a trip
once P has held for about a minute, so its end came up to a minute after the car's.

The moment a trip closes does not change, and neither does anything that decides it. Only its end
moves, to the first reading of the final P run that shows the car off after it was last seen on,
and the whole end with it: the SoC, odometer, position and fuel of that reading, and none of the
regen or route points the polls after it added. Those are told apart by the order the rows were
written, not by any clock. A car switched on again in that run was not switched off for good; one
still on at the close, or one that never reports READY, keeps the usual end. So does a switch-off
reading that lacks something the usual end has, or one whose odometer a later reading contradicts:
the end moves only when nothing is lost with it and no kilometre is counted twice.
"""
import copy
from datetime import timedelta

import pytest
import state_machine as SM
import db as D
from test_a_trip_ends_when_the_car_last_spoke import _ms, _vd, rig  # noqa: F401  (rig: fixture)
from test_an_outage_never_leaves_a_trip_open import _ledger

KM = 0.009          # degrees of latitude in about a kilometre
STEP = 8.0 * SM.DEFAULT_POLL_DRIVING / 3600          # the regen of one poll at 8 kW


@pytest.fixture
def car(rig):
    db, rec, poll, wall = rig
    last = {}

    def send(seconds, *, odo, speed=0.0, gear="D", ready=True, skew=0, lat=None, **extra):
        """A fresh frame `seconds` after the previous poll, stamped by the car's clock (`skew`
        seconds off ours); READY None is a frame that did not carry it."""
        d = _vd(ts=_ms(wall["now"] + timedelta(seconds=seconds + skew)), odo=odo, speed=speed,
                gear=gear, soc=80.0 - (odo - 1000) * 0.2)
        d.latitude, d.longitude = (45.0 + (odo - 1000) * KM if lat is None else lat), 9.0
        d.ready, d.ready_reported = bool(ready), ready is not None
        for name, value in extra.items():
            setattr(d, name, value)
        last["frame"] = d
        poll(seconds, d)
        return wall["now"]

    def repeat(seconds):
        for _ in range(seconds // 10):
            poll(10, copy.copy(last["frame"]))

    return db, rec, send, repeat, wall


def _drive(send, start=1000, km=5, **extra):
    send(0 if start == 1000 else 60, odo=start, speed=50.0, **extra)
    for i in range(1, km + 1):
        send(60, odo=start + i, speed=50.0 if i < km else 0.0, **extra)
    return start + km


def _trips(db):
    return [dict(t) for t in db._conn.execute("SELECT * FROM trips ORDER BY id")]


def _points(db):
    return db._conn.execute("SELECT COUNT(*) FROM trip_positions").fetchone()[0]


def _park(send, odo, readies, **extra):
    """P readings, 10 s apart, with READY as given; the time of each."""
    return [send(10, odo=odo, gear="P", ready=r, **extra) for r in readies]


OFF_ON_THE_THIRD = [True, True, False, False, False, False]


def test_the_trip_ends_on_the_first_reading_that_shows_the_car_off(car):
    db, _rec, send, _repeat, _wall = car
    odo = _drive(send)
    times = _park(send, odo, OFF_ON_THE_THIRD)
    trip, = _trips(db)
    assert trip["ended_at"] == times[2].isoformat()
    assert trip["end_soc"] == pytest.approx(79.0) and trip["end_odometer_km"] == 1005
    # six drive readings, and the P readings up to the switch-off; the close adds none
    assert _points(db) == 6 + 3, "the points after the switch-off are still in the trip"


def test_the_end_takes_everything_from_the_switch_off_reading(car):
    db, _rec, send, _repeat, _wall = car
    odo = _drive(send, fuel_level_pct=40.0, fuel_liters=20.0)
    send(10, odo=odo, gear="P", ready=True, fuel_level_pct=40.0, fuel_liters=20.0)
    send(10, odo=odo, gear="P", ready=False, soc=78.9, lat=45.1, fuel_level_pct=39.9,
         fuel_liters=19.9)
    for _ in range(4):
        send(10, odo=odo, gear="P", ready=False, soc=78.8, lat=45.2, fuel_level_pct=39.8,
             fuel_liters=19.8)
    trip, = _trips(db)
    assert (trip["end_soc"], trip["end_lat"], trip["fuel_end_pct"], trip["fuel_end_l"]) == \
        (78.9, 45.1, 39.9, 19.9)


def test_a_car_still_on_when_the_trip_closes_keeps_the_usual_end(car):
    db, _rec, send, _repeat, _wall = car
    odo = _drive(send)
    times = _park(send, odo, [True] * SM.PARKED_CONFIRM)
    assert _trips(db)[0]["ended_at"] == times[-1].isoformat()


@pytest.mark.parametrize("readies, end_on", [
    ([True, False, True, False, False, False], 3),     # off, on again, off for good
    ([True, False, True, True, True, True], None),      # off, then on again until the close
], ids=["off for good the second time", "on again at the close"])
def test_a_car_switched_on_again_was_not_switched_off_for_good(car, readies, end_on):
    db, _rec, send, _repeat, _wall = car
    odo = _drive(send)
    times = _park(send, odo, readies)
    assert _trips(db)[0]["ended_at"] == times[-1 if end_on is None else end_on].isoformat()


def test_a_short_stop_in_p_mid_drive_stays_inside_one_trip(car):
    """Stopping in P for a few seconds to change the one-pedal setting — the car may even go off
    for a moment — and driving on is one drive, and it ends at the final switch-off."""
    db, _rec, send, _repeat, _wall = car
    odo = _drive(send)
    _park(send, odo, [False, False, True])              # a few seconds in P, off and on again
    odo = _drive(send, start=odo, km=3)
    times = _park(send, odo, [True, False, False, False, False, False])
    trips = _trips(db)
    assert len(trips) == 1 and trips[0]["ended_at"] == times[1].isoformat()
    assert _ledger(db) == [(1000, 1008)]


@pytest.mark.parametrize("driving_ready", [False, None], ids=["READY=0", "READY not sent"])
def test_a_zero_already_seen_is_not_a_new_switch_off(car, driving_ready):
    """Seen off in a short stop, then driving on without a READY=1: the car was never seen on again,
    so the zero in the final standstill is not a switch-off of its own and the trip keeps the usual
    end — not the short stop's, and not the first reading of the final standstill."""
    db, _rec, send, _repeat, _wall = car
    odo = _drive(send)
    _park(send, odo, [False, False])
    odo = _drive(send, start=odo, km=3, ready=driving_ready)
    times = _park(send, odo, [False] * SM.PARKED_CONFIRM)
    trip, = _trips(db)
    assert trip["ended_at"] == times[-1].isoformat() and trip["end_odometer_km"] == 1008


def test_a_zero_read_in_d_is_not_a_switch_off_in_the_standstill(car):
    """READY=0 while still in D, the car not moving, then P: a zero before the final P run is not
    the switch-off the trip closes on, however still the car stood."""
    db, _rec, send, _repeat, _wall = car
    odo = _drive(send)
    send(10, odo=odo, gear="D", ready=False)
    times = _park(send, odo, [False] * SM.PARKED_CONFIRM)
    assert _trips(db)[0]["ended_at"] == times[-1].isoformat()


def test_a_car_that_never_reports_ready_keeps_the_usual_end(car):
    db, _rec, send, _repeat, _wall = car
    odo = _drive(send, ready=None)
    times = _park(send, odo, [None] * SM.PARKED_CONFIRM)
    assert _trips(db)[0]["ended_at"] == times[-1].isoformat()


@pytest.mark.parametrize("readies, end_on", [
    ([True, None, None, False, False, False], 3),    # the first zero actually read
    ([True, None, None, None, None, None], None),    # never read off
], ids=["read off after a gap", "never read off"])
def test_a_reading_without_ready_is_not_a_switch_off(car, readies, end_on):
    db, _rec, send, _repeat, _wall = car
    odo = _drive(send)
    times = _park(send, odo, readies)
    assert _trips(db)[0]["ended_at"] == times[-1 if end_on is None else end_on].isoformat()


def test_after_a_silence_the_end_is_the_first_reading_of_a_car_already_off(car):
    """Stopped in D, the cloud quiet for six minutes, then found parked and off: the switch-off
    happened somewhere in the silence, and the first reading after it is the first that shows it."""
    db, _rec, send, repeat, _wall = car
    odo = _drive(send)
    repeat(390)
    times = _park(send, odo, [False] * SM.PARKED_CONFIRM)
    assert _trips(db)[0]["ended_at"] == times[0].isoformat()


def test_a_plug_after_the_switch_off_ends_the_trip_at_the_switch_off(car):
    db, _rec, send, _repeat, _wall = car
    odo = _drive(send)
    times = _park(send, odo, [True, False])
    send(10, odo=odo, gear="P", ready=False, plug_connected=True)
    assert _trips(db)[0]["ended_at"] == times[1].isoformat()


REGEN = {"charge_current_a": -20.0, "charge_power_kw": 8.0}


@pytest.mark.parametrize("readies, kept", [
    ([True, False, False, False, False, False], 2),    # the P reading still on, and the end itself
    ([True, False, True, False, False, False], 4),     # off, on again, off for good
], ids=["off for good", "switched on again"])
def test_regen_counted_after_the_switch_off_is_not_the_trips(car, readies, kept):
    """The regen gate asks neither READY nor the gear, and the trip is still DRIVING while P is
    confirmed: a current into the pack on those readings is counted. Only what the readings the
    trip keeps brought stays with it."""
    db, rec, send, _repeat, _wall = car
    odo = _drive(send)
    before = rec._regen_kwh
    _park(send, odo, readies, **REGEN)
    assert _trips(db)[0]["regen_kwh"] == pytest.approx(before + kept * STEP, abs=1e-3)


@pytest.mark.parametrize("skew", [40, -48], ids=["car clock ahead", "car clock behind"])
def test_the_points_kept_do_not_depend_on_the_car_clock(car, skew):
    db, _rec, send, _repeat, _wall = car
    odo = _drive(send, skew=skew)
    _park(send, odo, OFF_ON_THE_THIRD, skew=skew, **REGEN)
    assert _points(db) == 6 + 3
    assert _trips(db)[0]["regen_kwh"] == pytest.approx(3 * STEP, abs=1e-3)


def test_our_clock_stepping_back_after_the_switch_off_does_not_keep_what_came_after(car):
    db, _rec, send, _repeat, wall = car
    odo = _drive(send)
    times = _park(send, odo, [True, False], **REGEN)
    wall["now"] -= timedelta(seconds=45)                # ours, not the car's
    _park(send, odo, [False] * 4, skew=45, **REGEN)
    trip, = _trips(db)
    assert trip["ended_at"] == times[1].isoformat()
    assert _points(db) == 6 + 2 and trip["regen_kwh"] == pytest.approx(2 * STEP, abs=1e-3)


def test_the_gps_distance_is_measured_on_the_points_kept(car):
    """A manoeuvre the odometer does not see is measured on its track, and a fix that wanders off
    after the switch-off is no part of it."""
    db, _rec, send, _repeat, _wall = car
    for i in range(4):
        send(0 if i == 0 else 30, odo=1000, speed=10.0 if i < 3 else 0.0, lat=45.0 + i * 0.0009)
    end = 45.0 + 3 * 0.0009
    send(10, odo=1000, gear="P", ready=True, lat=end)
    send(10, odo=1000, gear="P", ready=False, lat=end)
    for _ in range(4):
        send(10, odo=1000, gear="P", ready=False, lat=end + 0.0036)
    trip, = _trips(db)
    assert trip["distance_km"] == pytest.approx(D.haversine_km(45.0, 9.0, end, 9.0), abs=0.01)


def test_the_next_drive_counts_its_kilometres_once(car):
    db, _rec, send, _repeat, _wall = car
    odo = _drive(send)
    _park(send, odo, [True, False, False, False, False, False])
    odo = _drive(send, start=odo, km=5)
    _park(send, odo, [True, False, False, False, False, False])
    assert _ledger(db) == [(1000, 1005), (1005, 1010)]


def test_a_closing_reading_without_an_odometer_does_not_hold_the_end_back(car):
    db, _rec, send, _repeat, _wall = car
    odo = _drive(send)
    times = _park(send, odo, [True, False, False, False, False])
    send(10, odo=0, soc=79.0, gear="P", ready=False, odometer_reported=False)
    odo = _drive(send, start=odo, km=5)
    _park(send, odo, [True] * SM.PARKED_CONFIRM)
    assert _trips(db)[0]["ended_at"] == times[1].isoformat()
    assert _ledger(db) == [(1000, 1005), (1005, 1010)]


def test_kilometres_driven_unseen_after_it_are_counted_once(car):
    db, _rec, send, _repeat, _wall = car
    odo = _drive(send)
    _park(send, odo, OFF_ON_THE_THIRD)
    send(3600, odo=odo + 10, gear="P", ready=False)      # back an hour later, 10 km on
    assert _ledger(db) == [(1000, 1005), (1005, 1015)]


@pytest.mark.parametrize("off, later", [
    ({"odo": 0, "soc": 79.0, "odometer_reported": False}, {}),     # the switch-off reading has no odometer
    ({"lat": 0.0}, {}),                               # …no position
    ({"fuel_liters": None}, {"fuel_liters": 19.8}),   # …no litres, which the close has
    ({}, {"odo": 1006}),                              # a later reading moves the odometer on
], ids=["no odometer", "no position", "no litres", "odometer moved after it"])
def test_a_switch_off_reading_that_would_lose_something_leaves_the_end_alone(car, off, later):
    db, _rec, send, _repeat, _wall = car
    odo = _drive(send)
    send(10, odo=odo, gear="P", ready=True, fuel_liters=19.8)
    send(10, **{"odo": odo, "gear": "P", "ready": False, "fuel_liters": 19.8, **off})
    times = [send(10, **{"odo": odo, "gear": "P", "ready": False, "fuel_liters": 19.8, **later})
             for _ in range(4)]
    trip, = _trips(db)
    assert trip["ended_at"] == times[-1].isoformat()
    assert _ledger(db) == [(1000, trip["end_odometer_km"])]


def test_the_close_is_all_or_nothing(car, monkeypatch):
    db, _rec, send, _repeat, _wall = car
    odo = _drive(send)
    _park(send, odo, [True, False, False, False, False])
    points = _points(db)

    def boom(*_a, **_k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(D.geohash, "encode", boom)
    with pytest.raises(RuntimeError):
        send(10, odo=odo, gear="P", ready=False)
    monkeypatch.undo()
    db._conn.commit()
    assert _points(db) == points
    assert _trips(db)[0]["ended_at"] is None
