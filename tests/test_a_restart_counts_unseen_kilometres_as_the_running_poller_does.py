"""A restart counts the kilometres nobody saw as the running poller does.

The running recorder measures unseen kilometres from a baseline it keeps in memory; a restart takes
it back from `positions`. A trip that closes on a reading without an odometer leaves the running
recorder no baseline (the kilometres up to its end are the trip's, measured by its route), but the
rows still hold the last odometer inside that trip, and a restart measured from there: the end of
the drive came back as a trip of its own.

Each scenario is played twice on the real recorder and SQLite, once without a restart and once
with the poller restarted, or killed mid-poll, at a given moment. A restart closes the database
connection without committing and opens the file again, as a process that died would leave it. The
trips and offline stretches written, with their kilometres, start, SoC and energy, must be the same.
The run without a restart is checked on its own too, so that two wrong answers cannot agree; that
does not make its balance complete (see the long outage below).

The recorder keeps its baseline, or that it has none, with the write that decides it, so a restart
does not depend on which rows are still there. That covers the cases below, not a poll killed at
any point: one killed after it saved a reading that moved the odometer, before it measured the
kilometres to it, still loses them, as the rows did.
"""
from datetime import timedelta

import db as D
import pytest
import recorder as R
import state_machine as SM
from test_a_trip_ends_when_the_car_last_spoke import _ms, _vd, make_rig

KM = 0.009                                              # degrees of latitude in a kilometre
UNSENT = {"odo": 0.0, "odometer_reported": False}      # what the parser hands over without 1318
LONG = 3600                                             # a silence no drive has in its middle


class _Killed(Exception):
    """The process dies here, mid-poll."""


class Run:
    """Cars polled by the real recorder on one database. At a named moment between two polls the
    poller may be restarted, or armed to die inside the next poll: right after the trip's closing
    write, or right after the first reading with an odometer is saved."""

    def __init__(self, path, monkeypatch, *, restart=(), kill=None):
        path.mkdir()
        self.path = path / "t.db"
        self.db, rec, _poll, self.wall = make_rig(path, monkeypatch)
        self.mono = {"t": 10_000.0}
        monkeypatch.setattr(SM.time, "monotonic", lambda: self.mono["t"])
        self.recs = {rec._vehicle_id: rec}
        self.car = rec._vehicle_id
        self.restart, self.kill = set(restart), kill or (None, None)
        self.starts = 0
        self._start()

    def add_car(self, vin):
        vid = self.db.ensure_vehicle(vin, "B10")
        self.recs[vid] = R.Recorder(self.db, vehicle_id=vid)
        return vid

    def send(self, seconds, *, car=None, ts=None, soc, gear="P", speed=0.0, lat=45.0, **frame):
        """A frame `seconds` after the previous poll, fresh unless `ts` repeats one; when it was polled."""
        self.wall["now"] += timedelta(seconds=seconds)
        self.mono["t"] += seconds
        d = _vd(ts=ts or _ms(self.wall["now"]), odo=frame.pop("odo"), soc=soc, gear=gear, speed=speed)
        d.latitude = lat
        for name, value in frame.items():
            setattr(d, name, value)
        try:
            self.recs[car or self.car].process(d)
        except _Killed:
            self._restart()
        return self.wall["now"].isoformat()

    def offline(self):
        for _ in range(3):
            self.recs[self.car].mark_offline()

    def moment(self, name):
        if name in self.restart:
            self._restart()
        if self.kill[0] == name:
            {"on the close": self._die_after_the_close,
             "on the odometer": self._die_after_saving_an_odometer}[self.kill[1]]()

    def _restart(self, older=False):
        self.db._conn.close()                       # no commit: what was not committed is lost
        self.db = D.Database(str(self.path))
        self._start(older)
        self.recs = {vid: R.Recorder(self.db, vehicle_id=vid) for vid in self.recs}

    def _start(self, older=False):
        """As the poller's main() does; an older version stamps its start and keeps no baseline."""
        self.starts += 1
        if older:
            self.db.set_setting("poller_started_ts", str(self.starts))
            self.db.get_odometer_baseline = lambda vehicle_id: (False, None)
            self.db._put_odometer_baseline = lambda vehicle_id, baseline: None
        else:
            self.db.start_poller(str(self.starts))

    def _die_after_the_close(self):
        close = self.db.finalize_trip

        def closed_then_killed(*args, **kwargs):
            close(*args, **kwargs)
            raise _Killed
        self.db.finalize_trip = closed_then_killed

    def _die_after_saving_an_odometer(self):
        save = self.db.save_position

        def saved_then_killed(vehicle_id, data, *args, **kwargs):
            save(vehicle_id, data, *args, **kwargs)
            if data.odometer_km:
                raise _Killed
        self.db.save_position = saved_then_killed

    def written(self):
        """What the database holds for the kilometres and the energy, per car."""
        trips = self.db._conn.execute(
            "SELECT vehicle_id, reconstructed, started_at, ended_at, start_odometer_km, end_odometer_km,"
            " distance_km, start_soc, end_soc, efficiency_kwh_100km FROM trips ORDER BY id").fetchall()
        gaps = self.db._conn.execute(
            "SELECT vehicle_id, started_at, ended_at, odometer_start, odometer_end, distance_km,"
            " soc_start, soc_end, energy_kwh FROM offline_gaps ORDER BY id").fetchall()
        return [tuple(t) for t in trips], [tuple(g) for g in gaps]

    def rebuilt(self, car=None):
        """The reconstructed trips of a car: (start odometer, end odometer, started at, start SoC)."""
        return [(t[4], t[5], t[2], t[7]) for t in self.written()[0]
                if t[1] and t[0] == (car or self.car)]


def _both(tmp_path, monkeypatch, scenario, **disturbance):
    """The scenario without a restart, and with the disturbance: the two runs and what each noted."""
    calm, calm_notes = Run(tmp_path / "calm", monkeypatch), {}
    scenario(calm, calm_notes)
    hit, hit_notes = Run(tmp_path / "hit", monkeypatch, **disturbance), {}
    scenario(hit, hit_notes)
    assert calm_notes == hit_notes, "the two runs must be polled on the same clocks"
    return calm, hit, calm_notes


def _closes_without_an_odometer(run, car=None, after=0):
    """1000 → 1003 with an odometer, 2 km more without one, and parked on readings without one."""
    for km in range(6):
        run.send(60 if km else after, car=car, soc=80.0 - km * 0.2, gear="D", speed=50.0,
                 lat=45.0 + km * KM, **({"odo": 1000 + km} if km <= 3 else UNSENT))
    run.moment("before the close")
    for _ in range(6):
        run.send(10, car=car, soc=79.0, lat=45.0 + 5 * KM, **UNSENT)


def _the_odometer_comes_back_then_a_drive_nobody_sees(run, notes, car=None):
    run.moment("after the close")
    run.moment("before the odometer is back")
    notes["back"] = run.send(600, car=car, odo=1005, soc=79.0, lat=45.0 + 5 * KM)
    run.moment("after the odometer is back")
    run.send(LONG, car=car, odo=1020, soc=76.0, lat=45.0 + 20 * KM)


def closed_without_an_odometer(run, notes):
    _closes_without_an_odometer(run)
    _the_odometer_comes_back_then_a_drive_nobody_sees(run, notes)


def closed_without_an_odometer_then_pruned(run, notes):
    _closes_without_an_odometer(run)
    notes["highest id"] = run.db._conn.execute("SELECT MAX(id) FROM positions").fetchone()[0]
    assert run.db.prune_positions(1) > 0                # every row so far is older than a day
    _the_odometer_comes_back_then_a_drive_nobody_sees(run, notes)
    notes["id reused"] = run.db._conn.execute(
        "SELECT MAX(id) FROM positions").fetchone()[0] < notes["highest id"]


def closed_without_an_odometer_after_a_charge_then_pruned(run, notes):
    """Retention keeps charging rows, so a charge before the drive leaves an odometer older than the
    trip in `positions` after everything else of that day is gone."""
    for soc in (76.0, 78.0, 80.0):
        run.send(60, odo=1000, soc=soc, charging_status=1, plug_connected=True, charge_power_kw=7.0,
                 charge_current_a=-17.0, charge_voltage_v=400.0)
    run.send(60, odo=1000, soc=80.0)
    _closes_without_an_odometer(run)
    run.db.prune_positions(1)
    notes["kept"] = run.db._conn.execute(
        "SELECT COUNT(*) FROM positions WHERE charging = 1 AND odometer_km = 1000").fetchone()[0]
    _the_odometer_comes_back_then_a_drive_nobody_sees(run, notes)


def upgraded_then_closed_without_an_odometer(run, notes):
    """A database an older version wrote keeps no baseline, and the first start of this one takes it
    from the rows. A trip this version closes without an odometer keeps its none from then on."""
    run.send(0, odo=1000, soc=80.0)
    run.db._conn.execute("DELETE FROM settings WHERE key LIKE 'odometer_baseline_%'")
    run.db._conn.commit()
    run._restart()                                      # the upgrade, in both runs
    _closes_without_an_odometer(run, after=60)
    _the_odometer_comes_back_then_a_drive_nobody_sees(run, notes)


def a_frame_repeated_across_the_close(run, notes):
    """A frame first seen at 10:00, parked; its copies close the trip, the sixth without the
    odometer at 10:10; a copy with it at 10:20; a new frame at 10:30, 7 km on."""
    for km in range(4):
        run.send(60 if km else 0, soc=80.0 - km * 0.2, gear="D", speed=50.0, lat=45.0 + km * KM,
                 odo=1000 + km)
    frame = _ms(run.wall["now"] + timedelta(seconds=60))
    notes["first copy"] = run.send(60, ts=frame, odo=1003, soc=79.4, lat=45.0 + 3 * KM)
    for _ in range(4):
        run.send(120, ts=frame, odo=1003, soc=79.4, lat=45.0 + 3 * KM)
    run.moment("before the close")
    run.send(120, ts=frame, soc=79.4, lat=45.0 + 3 * KM, **UNSENT)
    run.moment("after the close")
    run.moment("before the odometer is back")
    notes["back"] = run.send(600, ts=frame, odo=1003, soc=79.4, lat=45.0 + 3 * KM)
    run.moment("after the odometer is back")
    run.send(600, odo=1010, soc=78.0, lat=45.0 + 10 * KM)


def _a_long_outage_ends_the_trip_earlier(back_with_odometer):
    """2 km without an odometer, then an hour of silence: the trip ends on its last reading before
    it. Back without an odometer, the running poller records the 20 km driven in the silence nowhere,
    since nothing says where the trip ended; only the difference a restart makes is checked here."""
    def scenario(run, notes):
        for km in range(6):
            run.send(60 if km else 0, soc=80.0 - km * 0.2, gear="D", speed=50.0, lat=45.0 + km * KM,
                     **({"odo": 1000 + km} if km <= 3 else UNSENT))
        run.offline()
        run.moment("before the close")
        notes["back"] = run.send(LONG, soc=75.0, lat=45.0 + 25 * KM,
                                 **({"odo": 1025} if back_with_odometer else UNSENT))
        run.moment("after the close")
        if not back_with_odometer:
            run.moment("before the odometer is back")
            notes["back"] = run.send(60, odo=1025, soc=75.0, lat=45.0 + 25 * KM)
        run.moment("after the odometer is back")
        run.send(LONG, odo=1040, soc=72.0, lat=45.0 + 40 * KM)
    return scenario


def two_cars(run, notes):
    """The second car is parked at 5000 throughout, and driven 30 km unseen at the end."""
    other = run.add_car("TESTVIN2")
    run.send(1, car=other, odo=5000, soc=60.0)
    _closes_without_an_odometer(run)
    notes["other"] = run.send(1, car=other, odo=5000, soc=60.0)      # a new frame: the baseline
    _the_odometer_comes_back_then_a_drive_nobody_sees(run, notes)
    run.send(1, car=other, odo=5030, soc=56.0)


RESTARTS = ["after the close", "after the odometer is back"]


@pytest.mark.parametrize("restart", RESTARTS + ["both"])
def test_a_trip_closed_without_an_odometer(tmp_path, monkeypatch, restart):
    calm, hit, notes = _both(tmp_path, monkeypatch, closed_without_an_odometer,
                             restart=RESTARTS if restart == "both" else [restart])
    assert calm.rebuilt() == [(1005, 1020, notes["back"], 79.0)]
    assert hit.written() == calm.written()


@pytest.mark.parametrize("kill", ["on the close", "on the odometer"])
def test_a_poller_killed_mid_poll_after_a_trip_closed_without_an_odometer(tmp_path, monkeypatch, kill):
    at = "before the close" if kill == "on the close" else "before the odometer is back"
    calm, hit, notes = _both(tmp_path, monkeypatch, closed_without_an_odometer, kill=(at, kill))
    assert calm.rebuilt() == [(1005, 1020, notes["back"], 79.0)]
    assert hit.written() == calm.written()


@pytest.mark.parametrize("restart", RESTARTS)
def test_retention_empties_positions_and_their_ids_are_used_again(tmp_path, monkeypatch, restart):
    calm, hit, notes = _both(tmp_path, monkeypatch, closed_without_an_odometer_then_pruned,
                             restart=[restart])
    assert notes["id reused"], "the scenario needs SQLite to hand out an id it gave before"
    assert calm.rebuilt() == [(1005, 1020, notes["back"], 79.0)]
    assert hit.written() == calm.written()


@pytest.mark.parametrize("restart", RESTARTS)
def test_retention_keeps_a_charging_reading_older_than_the_trip(tmp_path, monkeypatch, restart):
    calm, hit, notes = _both(tmp_path, monkeypatch, closed_without_an_odometer_after_a_charge_then_pruned,
                             restart=[restart])
    assert notes["kept"] == 3, "the scenario needs the charging rows to outlive the retention"
    assert calm.rebuilt() == [(1005, 1020, notes["back"], 79.0)]
    assert hit.written() == calm.written()


@pytest.mark.parametrize("restart", RESTARTS)
def test_a_trip_closed_without_an_odometer_after_an_upgrade(tmp_path, monkeypatch, restart):
    calm, hit, notes = _both(tmp_path, monkeypatch, upgraded_then_closed_without_an_odometer,
                             restart=[restart])
    assert calm.rebuilt() == [(1005, 1020, notes["back"], 79.0)]
    assert hit.written() == calm.written()


@pytest.mark.parametrize("restart, kill", [("after the close", None), ("after the odometer is back", None),
                                           (None, ("before the odometer is back", "on the odometer"))],
                         ids=["restart after the close", "restart after the copy with the odometer",
                              "killed saving the copy with the odometer"])
def test_a_frame_repeated_across_the_close_of_a_trip(tmp_path, monkeypatch, restart, kill):
    calm, hit, notes = _both(tmp_path, monkeypatch, a_frame_repeated_across_the_close,
                             restart=[restart] if restart else (), kill=kill)
    assert calm.rebuilt() == [(1003, 1010, notes["back"], 79.4)], \
        "the running poller measures from the copy after the close, not the first one"
    assert hit.written() == calm.written()


@pytest.mark.parametrize("back_with_odometer", [True, False], ids=["back with", "back without"])
@pytest.mark.parametrize("disturbance", ["restart after the close", "restart after the odometer is back",
                                         "killed on the close"])
def test_a_long_outage_that_ends_the_trip_on_an_earlier_reading(tmp_path, monkeypatch,
                                                                 back_with_odometer, disturbance):
    how = ({"kill": ("before the close", "on the close")} if disturbance == "killed on the close"
           else {"restart": [disturbance.removeprefix("restart ")]})
    calm, hit, notes = _both(tmp_path, monkeypatch, _a_long_outage_ends_the_trip_earlier(back_with_odometer),
                             **how)
    assert calm.rebuilt() == [(1025, 1040, notes["back"], 75.0)]
    assert hit.written() == calm.written()


def test_an_older_poller_in_between_leaves_no_baseline_to_measure_from(tmp_path, monkeypatch):
    """This version keeps its baseline at 1000; an older one, which keeps none, then records a trip
    1000 → 1010 on the same database; this version starts again and must not measure from 1000."""
    run = Run(tmp_path / "run", monkeypatch)
    run.send(0, odo=1000, soc=80.0)
    run._restart(older=True)
    for km in range(11):
        run.send(60, odo=1000 + km, soc=80.0 - km * 0.2, gear="D", speed=50.0, lat=45.0 + km * KM)
    for _ in range(6):
        run.send(10, odo=1010, soc=78.0, lat=45.0 + 10 * KM)
    run._restart()
    back = run.send(600, odo=1010, soc=78.0, lat=45.0 + 10 * KM)
    run.send(LONG, odo=1020, soc=76.0, lat=45.0 + 20 * KM)

    trips, gaps = run.written()
    assert [(t[4], t[5]) for t in trips if not t[1]] == [(1000, 1010)], "the older version's trip"
    assert run.rebuilt() == [(1010, 1020, back, 78.0)] and gaps == []


@pytest.mark.parametrize("restart", RESTARTS)
def test_each_car_keeps_its_own_baseline(tmp_path, monkeypatch, restart):
    calm, hit, notes = _both(tmp_path, monkeypatch, two_cars, restart=[restart])
    other = calm.car + 1
    assert calm.rebuilt() == [(1005, 1020, notes["back"], 79.0)]
    assert calm.rebuilt(other) == [(5000, 5030, notes["other"], 60.0)]
    assert hit.written() == calm.written()
