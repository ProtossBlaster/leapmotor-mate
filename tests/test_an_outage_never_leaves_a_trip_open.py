"""A trip the cloud went quiet on is resumed or closed when the cloud answers again — never left open.

Three refused polls put the recorder in OFFLINE, and the first answer after that decides what the
car is doing now. Only OFFLINE → DRIVING looked at the open trip (resumed after a brief silence,
D #331). Every other way out ignored it:

- OFFLINE → PARKED left it open, with nothing left to close it but a poller restart. The next
  departure opened a second trip beside it, and the kilometres between the two belonged to both
  and to neither.
- OFFLINE → DRIVING after a long silence opened the new trip on top of the old one, which stayed
  open the same way.
- OFFLINE → CHARGING closed it on the charging frame, however long the silence was.

The bound between "brief" and "long" is the half hour that already ends a frozen drive
(`FROZEN_DRIVE_LIMIT_S`): a drive cannot have a longer hole in its middle.

- Brief, and the car is parked or charging: the same drive, over. It closes on the frame in hand,
  as a drive whose P arrives late always has, and the kilometres of the hole are its own.
- Long: the drive ended somewhere in the silence. It closes on the last observation before it,
  and the kilometres after that are declared the way unseen kilometres always are: a
  reconstructed trip when parked, an offline gap when a new drive opens or a charge starts.

Every test checks the odometer ledger: the trips and gaps must tile the kilometres the odometer
counted, each counted once.
"""
from datetime import datetime, timedelta

import db as D
import pytest
import state_machine as SM
from test_a_trip_ends_when_the_car_last_spoke import T0, _ms, _vd, rig

KM = 0.009          # degrees of latitude per kilometre, near enough


def _at(minute, odo, *, gear="D", speed=50.0, soc=None, gps=True, **extra):
    """A fresh frame at T0 + `minute`, a kilometre north per kilometre of odometer."""
    d = _vd(ts=_ms(T0 + timedelta(minutes=minute)), odo=odo, gear=gear, speed=speed,
            soc=80.0 - (odo - 1000) * 0.2 if soc is None else soc)
    d.latitude, d.longitude = (45.0 + (odo - 1000) * KM, 9.0) if gps else (None, None)
    for name, value in extra.items():
        setattr(d, name, value)
    return d


def _drive(poll, **extra):
    """Five minutes on fresh frames, a kilometre a minute: odometer 1000 → 1005."""
    poll(0, _at(0, 1000, **extra))
    for i in range(1, 6):
        poll(60, _at(i, 1000 + i, **extra))


def _outage(rec, poll, seconds, data):
    """Three refused polls, `seconds` of silence since the last fresh frame, then `data`."""
    for _ in range(3):
        rec.mark_offline()
    assert rec.state == SM.State.OFFLINE
    poll(seconds, data)


def _park(minute, odo, **extra):
    return _at(minute, odo, gear="P", speed=0.0, **extra)


def _plugged(minute, odo):
    return _park(minute, odo, charging_status=1, plug_connected=True)


def _trips(db):
    return db._conn.execute("SELECT * FROM trips ORDER BY id").fetchall()


def _ledger(db):
    """Every span of odometer the database accounts for — trips, reconstructed or live, and
    offline gaps — in order. A span counted twice overlaps; a span lost leaves a hole."""
    spans = [(t["start_odometer_km"], t["end_odometer_km"]) for t in _trips(db)]
    spans += [(g["odometer_start"], g["odometer_end"])
              for g in db._conn.execute("SELECT * FROM offline_gaps").fetchall()]
    return sorted(spans, key=lambda s: (s[0], s[1] is None, s[1] or 0))


BRIEF, LONG = 120, SM.FROZEN_DRIVE_LIMIT_S + 60
LAST_SPOKE = (T0 + timedelta(minutes=5)).isoformat()


def test_parked_after_a_brief_outage_the_trip_is_closed(rig):
    db, rec, poll, _wall = rig
    _drive(poll)
    _outage(rec, poll, BRIEF, _park(7, 1007))

    trip, = _trips(db)
    assert trip["ended_at"] is not None, "the trip was left open behind the outage"
    assert rec._active_trip_id is None
    assert trip["ended_at"] == (T0 + timedelta(minutes=7)).isoformat(), (
        "a brief silence is the drive's own: it ends on the frame that says parked")
    assert _ledger(db) == [(1000, 1007)], "the kilometres of the hole are the trip's, once"


def test_the_next_drive_opens_its_own_trip_and_the_closed_one_stays_closed(rig):
    db, rec, poll, _wall = rig
    _drive(poll)
    _outage(rec, poll, BRIEF, _park(7, 1007))
    closed = dict(_trips(db)[0])
    for i in range(8, 8 + SM.PARKED_CONFIRM):                 # more P frames, fresh ones
        poll(60, _park(i, 1007))
    assert dict(_trips(db)[0]) == closed, "a parked frame closed the trip a second time"

    for i in range(20, 23):                                    # drive away, then park
        poll(60, _at(i, 1007 + i - 20))
    for i in range(23, 23 + SM.PARKED_CONFIRM):
        poll(60, _park(i, 1009))

    first, second = _trips(db)
    assert dict(first) == closed
    assert second["ended_at"] is not None
    assert _ledger(db) == [(1000, 1007), (1007, 1009)]


def test_parked_after_a_long_outage_the_trip_ends_where_the_car_last_spoke(rig):
    db, rec, poll, _wall = rig
    _drive(poll)
    _outage(rec, poll, LONG, _park(40, 1025))

    trip, rebuilt = _trips(db)
    assert trip["ended_at"] == LAST_SPOKE
    assert trip["duration_min"] == pytest.approx(5.0)
    assert (trip["end_odometer_km"], trip["distance_km"]) == (1005, 5)
    assert trip["end_soc"] == pytest.approx(79.0)
    assert rebuilt["reconstructed"] == 1, "the kilometres after the silence are nobody's drive"
    assert _ledger(db) == [(1000, 1005), (1005, 1025)]


def test_a_new_drive_after_a_long_outage_closes_the_old_trip_first(rig):
    db, rec, poll, _wall = rig
    _drive(poll)
    _outage(rec, poll, LONG, _at(40, 1025))

    old, new = _trips(db)
    assert old["ended_at"] == LAST_SPOKE and old["end_odometer_km"] == 1005
    assert new["ended_at"] is None and rec._active_trip_id == new["id"]
    assert _ledger(db) == [(1000, 1005), (1005, 1025), (1025, None)]


@pytest.mark.parametrize("silence, end_odo", [(BRIEF, 1007), (LONG, 1005)])
def test_charging_after_an_outage_closes_the_trip_and_opens_the_charge(rig, silence, end_odo):
    db, rec, poll, _wall = rig
    _drive(poll)
    _outage(rec, poll, silence, _plugged(7 if silence == BRIEF else 40, 1007))

    trip, = _trips(db)
    assert trip["ended_at"] is not None and trip["end_odometer_km"] == end_odo
    assert rec.state == SM.State.CHARGING and rec._active_charge_id is not None
    charge = db._conn.execute("SELECT * FROM charges").fetchone()
    assert charge["ended_at"] is None
    assert _ledger(db) == ([(1000, 1007)] if silence == BRIEF else [(1000, 1005), (1005, 1007)])


@pytest.mark.parametrize("silence, brief", [(SM.FROZEN_DRIVE_LIMIT_S - 1, True),
                                            (SM.FROZEN_DRIVE_LIMIT_S, False)])
def test_the_bound_is_the_frozen_drive_limit(rig, silence, brief):
    db, rec, poll, _wall = rig
    _drive(poll)
    _outage(rec, poll, silence, _park(40, 1025))

    trip = _trips(db)[0]
    assert trip["end_odometer_km"] == (1025 if brief else 1005)
    assert _ledger(db) == ([(1000, 1025)] if brief else [(1000, 1005), (1005, 1025)])


def test_the_end_is_the_observation_before_the_silence_not_the_frame_after_it(rig):
    """Position, odometer, SoC, fuel and the regen summed so far all describe one moment. The frame
    after the silence is twenty kilometres further on, with less in the tank."""
    db, rec, poll, _wall = rig
    _drive(poll, fuel_level_pct=40.0, fuel_liters=20.0,
           charge_current_a=-20.0, charge_power_kw=8.0)
    regen = rec._regen_kwh
    assert regen > 0
    _outage(rec, poll, LONG, _park(40, 1025, fuel_level_pct=35.0, fuel_liters=17.5))

    trip = _trips(db)[0]
    assert trip["end_lat"] == pytest.approx(45.0 + 5 * KM)
    assert (trip["fuel_end_pct"], trip["fuel_end_l"]) == (40.0, 20.0)
    assert trip["regen_kwh"] == pytest.approx(regen, abs=1e-3)


def test_a_trip_without_gps_is_closed_on_its_odometer_not_dropped(rig):
    db, rec, poll, _wall = rig
    _drive(poll, gps=False)
    _outage(rec, poll, LONG, _park(40, 1025, gps=False))

    trip = _trips(db)[0]
    assert trip["ended_at"] == LAST_SPOKE and trip["distance_km"] == 5
    assert _ledger(db) == [(1000, 1005), (1005, 1025)]


@pytest.mark.parametrize("opening_row_inside", [True, False])
def test_a_trip_that_heard_nothing_after_it_opened_closes_where_it_opened(rig, opening_row_inside):
    """Its only reading is the frame it opened on. When that row is gone from inside the trip too,
    the trip's own opening is the last thing it heard, which is the same end the row gives."""
    db, rec, poll, _wall = rig
    poll(0, _at(0, 1000))
    if not opening_row_inside:
        db._conn.execute("UPDATE positions SET recorded_at = ?",
                         ((T0 - timedelta(milliseconds=1)).isoformat(),))
    _outage(rec, poll, LONG, _park(40, 1025))

    trip, rebuilt = _trips(db)
    assert (trip["ended_at"], trip["duration_min"]) == (T0.isoformat(), 0)
    assert trip["end_odometer_km"] == 1000 and rebuilt["reconstructed"] == 1
    assert _ledger(db) == [(1000, 1000), (1000, 1025)]


@pytest.mark.parametrize("gps", [True, False])
def test_retention_keeps_what_an_open_trip_will_be_closed_on(rig, monkeypatch, gps):
    """GPS retention prunes `positions` once a day, and an outage can outlast it. The readings of a
    trip still open are what it is closed on, so they stay until it is."""
    db, rec, poll, _wall = rig
    _drive(poll, gps=gps)
    for _ in range(3):
        rec.mark_offline()
    days = 181

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return (T0 + timedelta(days=days)).astimezone(tz)

    with monkeypatch.context() as m:
        m.setattr(D, "datetime", Later)
        db.prune_positions(180)
    poll(days * 86400, _park(days * 1440, 1025, gps=gps))

    trip = _trips(db)[0]
    assert trip["ended_at"] == LAST_SPOKE
    assert (trip["end_odometer_km"], trip["distance_km"], trip["end_soc"]) == (1005, 5, 79.0)
    assert _ledger(db) == [(1000, 1005), (1005, 1025)]

    with monkeypatch.context() as m:
        m.setattr(D, "datetime", Later)
        assert db.prune_positions(180) == 6, "once the trip is closed, its readings age out as before"


def _left_open(db, wall, vid, minute, odo):
    """A trip as an earlier version could leave it: opened, driven three minutes, never closed."""
    wall["now"] = T0 + timedelta(minutes=minute)
    trip_id = db.create_trip(vid, _at(minute, odo))
    for i in range(3):
        wall["now"] = T0 + timedelta(minutes=minute + i)
        db.save_position(vid, _at(minute + i, odo + i))
        db.add_trip_position(trip_id, _at(minute + i, odo + i))
    return trip_id


def _open_trips(db):
    return [t["id"] for t in db._conn.execute("SELECT id FROM trips WHERE ended_at IS NULL ORDER BY id")]


def test_every_trip_an_earlier_version_left_open_is_settled_when_the_newest_is_resumed(rig):
    """Earlier versions left a trip open when an outage ended in P, and the next departure opened
    another beside it, so a database can hold several. A restart with the car driving resumes the
    newest; the others cannot be this drive, and are closed on their own points as any orphan is."""
    db, rec, _poll, wall = rig
    older = _left_open(db, wall, rec._vehicle_id, 0, 1000)
    newer = _left_open(db, wall, rec._vehicle_id, 60, 1010)

    wall["now"] = T0 + timedelta(minutes=70)
    rec.process(_at(70, 1013))

    assert _open_trips(db) == [newer], "only the newest can still be the drive in progress"
    closed = db._conn.execute("SELECT * FROM trips WHERE id = ?", (older,)).fetchone()
    assert closed["ended_at"] == (T0 + timedelta(minutes=2)).isoformat()


def _daily(db, wall, vid, days, charging_day=None):
    """A reading a day for `days` days from T0; on `charging_day` the car is charging."""
    for day in range(days):
        wall["now"] = T0 + timedelta(days=day)
        d = _at(day * 1440, 1000 + day, gear="P", speed=0.0, charging_status=int(day == charging_day))
        db.save_position(vid, d)


def _opened(db, wall, vid, day):
    wall["now"] = T0 + timedelta(days=day)
    return db.create_trip(vid, _at(day * 1440, 1000 + day))


@pytest.mark.parametrize("cars", ["one gone from the account, one still polled",
                                  "two with trips open since different days",
                                  "none with a trip open"])
def test_an_open_trip_holds_back_the_pruning_of_its_own_car_only(tmp_path, monkeypatch, cars):
    """The retention keeps a car's readings from its oldest open trip on, and only that car's:
    another car is pruned as if the trip did not exist. A car the poller no longer reaches keeps
    just its own readings, which no longer grow. Charging readings stay, as always."""
    wall = {"now": T0}
    monkeypatch.setattr(D, "_now_iso", lambda: wall["now"].isoformat())
    db = D.Database(str(tmp_path / "t.db"))
    a, b = db.ensure_vehicle("VIN-A", "C10"), db.ensure_vehicle("VIN-B", "B10")
    if cars == "one gone from the account, one still polled":
        _opened(db, wall, a, 0)
        _daily(db, wall, a, 3, charging_day=1)           # then gone: nothing after its third day
        _daily(db, wall, b, 300, charging_day=5)
        kept = {a: (3, 0), b: (81, 5)}                   # rows left, the oldest one's day
    elif cars == "two with trips open since different days":
        _daily(db, wall, a, 300, charging_day=5)
        _daily(db, wall, b, 300, charging_day=5)
        _opened(db, wall, a, 50)
        _opened(db, wall, b, 150)
        kept = {a: (251, 5), b: (151, 5)}
    else:
        _daily(db, wall, a, 300, charging_day=5)
        _daily(db, wall, b, 300, charging_day=5)
        kept = {a: (81, 5), b: (81, 5)}

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return (T0 + timedelta(days=400)).astimezone(tz)

    before = db._conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    monkeypatch.setattr(D, "datetime", Later)
    deleted = db.prune_positions(180)                    # keeps day 220 on, but for open trips
    left = {vid: (n, (datetime.fromisoformat(oldest) - T0).days) for vid, n, oldest in db._conn.execute(
        "SELECT vehicle_id, COUNT(*), MIN(recorded_at) FROM positions GROUP BY vehicle_id")}
    assert left == kept
    assert deleted == before - sum(n for n, _ in kept.values())


@pytest.mark.parametrize("left_open", [1, 2])
@pytest.mark.parametrize("back", ["parked", "driving"])
def test_open_trips_hold_back_the_retention_only_until_they_are_settled(rig, monkeypatch, back,
                                                                       left_open):
    """The retention keeps everything since the oldest open trip began, so a trip left open for good
    would keep the whole history from then on. What bounds it across a restart is the first poll:
    every trip an earlier run left open is closed there, except the newest when the car is found
    driving, which is resumed until that drive ends."""
    db, rec, _poll, wall = rig
    for n in range(left_open):
        _left_open(db, wall, rec._vehicle_id, n * 60, 1000 + n * 10)
    days = 400
    wall["now"] = T0 + timedelta(days=days)

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return wall["now"].astimezone(tz)

    def prune():
        with monkeypatch.context() as m:
            m.setattr(D, "datetime", Later)
            return db.prune_positions(180)

    if back == "driving":
        rec.process(_at(days * 1440, 1025))
        assert len(_open_trips(db)) == 1, "a car found driving resumes the newest"
        assert prune() == 3 * (left_open - 1), "a drive in progress keeps what it will be closed on"
        for minute in range(1, 8):
            wall["now"] += timedelta(minutes=1)
            rec.process(_park(days * 1440 + minute, 1026))
        assert _open_trips(db) == []
        assert prune() == 3, "once the drive ends, its old readings age out too"
    else:
        rec.process(_park(days * 1440, 1025))
        assert _open_trips(db) == []
        assert prune() == 3 * left_open, "once they are settled, the old readings age out"
