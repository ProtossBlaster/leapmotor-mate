"""A trip closed by crash recovery keeps the fuel it ended on.

The same hole as the end odometer next door, one column over. `close_orphan_trips` closes a trip
from its `trip_positions` — which carry no fuel — and its UPDATE never listed `fuel_end_pct` or
`fuel_end_l`. So a drive the poller was restarted during keeps its START fuel and loses its end,
for good: `_reev_trip_fuel` gets one end of a subtraction and answers "unknown" forever.

Measured on @ebagnoli's own history (29/09/2026), which is what found this. Eleven of his 150 trips
carry a start reading and no end at all, and every one of them carries the fingerprint of this path:
`close_orphan_trips` rounds the distance to THREE decimals (`round(distance_km, 3)`) where
`finalize_trip` rounds to two. Ten read 42.569, 12.932, 17.537, 2.871, 3.031, 1.357, 1.426, 5.624,
3.775, 3.007 km; the eleventh is 4.18, a third decimal that happens to be zero. Not one trip in any
other group has three decimals.

It is not a rare path. Counting `Trip #… was open (crash recovery)` across the twelve bundles we
hold: 7 of 158 closures on one 4.1.0 install, 1 of 36 on a 4.5.2, 1 of 34 on another 4.1.0, and 52
of 158 on an old 2.5.6. On a BEV it costs nothing — there is no fuel. On a range-extender it is the
difference between a drive that says what it burned and a drive that says nothing.

The reading exists. `positions` holds `fuel_level_pct` and `fuel_liters` for every poll of the
drive, and this same function already reaches into that table for the fuel a few lines above, to
decide whether to withhold the efficiency.
"""
import db as D
import db_reader

VIN = "LVINORPHANFUEL001"
START = "2026-06-13T08:00:00+00:00"
# Signal 3263 counts millilitres, so these are real litre values, not tenths of a tank.
TRAIL = [(70.8, 33.646), (70.2, 33.361), (69.5, 33.028)]


def _rig(trail=TRAIL, *, start_pct=70.8, start_l=33.646):
    pdb = D.Database(":memory:")
    vid = pdb.ensure_vehicle(VIN, "C10")
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, start_soc, start_odometer_km,"
        " fuel_start_pct, fuel_start_l) VALUES (1, ?, ?, 80, 1000.0, ?, ?)",
        (vid, START, start_pct, start_l))                              # ended_at NULL → orphan
    for i, ((lat, lon), (pct, litres)) in enumerate(zip(
            [(45.460, 9.190), (45.470, 9.200), (45.485, 9.215)], trail)):
        at = f"2026-06-13T08:0{i}:00+00:00"
        pdb._conn.execute(
            "INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude, soc)"
            " VALUES (1, ?, ?, ?, 79)", (at, lat, lon))
        pdb._conn.execute(
            "INSERT INTO positions (vehicle_id, recorded_at, latitude, longitude, soc,"
            " odometer_km, fuel_level_pct, fuel_liters) VALUES (?,?,?,?,79,?,?,?)",
            (vid, at, lat, lon, 1000.0 + i, pct, litres))
    pdb._conn.commit()
    return pdb, vid


def _trip(pdb):
    return pdb._conn.execute("SELECT * FROM trips WHERE id=1").fetchone()


def test_the_end_fuel_is_the_last_the_drive_reported():
    pdb, vid = _rig()
    assert pdb.close_orphan_trips(vid) == 1
    t = _trip(pdb)
    assert t["fuel_end_l"] == 33.028, "the drive's last litre reading was thrown away"
    assert t["fuel_end_pct"] == 69.5, "the drive's last tank percentage was thrown away"


def test_the_trip_can_then_say_what_it_burned():
    """The point of keeping it: one end of a subtraction is not a measurement."""
    pdb, vid = _rig()
    pdb.close_orphan_trips(vid)
    t = _trip(pdb)
    out = db_reader._reev_trip_fuel(t["fuel_start_pct"], t["fuel_end_pct"], 12.0,
                                    fuel_start_l=t["fuel_start_l"], fuel_end_l=t["fuel_end_l"])
    assert out["fuel_used_l"] == 0.618 and out["engine_ran"] is True


def test_a_tank_that_rose_is_still_recorded():
    """He filled up mid-drive. The end reading is the truth of the tank, and `_reev_trip_fuel` is
    what decides that no consumption can be worked out from it — a guard here would delete the
    evidence instead, and publish a refuelled drive as pure electric (beta #30, @pdifeo)."""
    pdb, vid = _rig([(20.0, 9.5), (19.4, 9.215), (95.0, 45.1)], start_pct=20.0, start_l=9.5)
    pdb.close_orphan_trips(vid)
    t = _trip(pdb)
    assert t["fuel_end_l"] == 45.1
    assert db_reader._reev_trip_fuel(t["fuel_start_pct"], t["fuel_end_pct"], 12.0,
                                     fuel_start_l=t["fuel_start_l"],
                                     fuel_end_l=t["fuel_end_l"])["fuel_refuelled"] is True


def test_a_drive_whose_polls_carried_no_fuel_is_left_alone():
    """A BEV, or a range-extender whose polls missed the signal: an empty end is honest."""
    pdb, vid = _rig([(None, None), (None, None), (None, None)], start_pct=None, start_l=None)
    pdb.close_orphan_trips(vid)
    t = _trip(pdb)
    assert t["fuel_end_pct"] is None and t["fuel_end_l"] is None
    assert t["ended_at"] is not None, "the trip must still be closed"


def test_the_percentage_survives_a_poll_that_missed_the_litre_counter():
    """The two signals are read independently — 3235 and 3263 — and one arriving without the other
    is measured, not hypothetical: 9 of @ebagnoli's trips carry an end percentage and no end litres.
    Whichever one came through is kept."""
    pdb, vid = _rig([(70.8, None), (70.2, None), (69.5, None)], start_l=None)
    pdb.close_orphan_trips(vid)
    t = _trip(pdb)
    assert t["fuel_end_pct"] == 69.5 and t["fuel_end_l"] is None


def test_the_last_poll_that_carried_a_reading_wins_over_a_later_silent_one():
    """The drive's final polls can stop carrying the signal. The last READING is the end of the
    drive, not the last row — the same rule the end odometer follows next door."""
    pdb, vid = _rig([(70.8, 33.646), (69.5, 33.028), (None, None)])
    pdb.close_orphan_trips(vid)
    t = _trip(pdb)
    assert t["fuel_end_l"] == 33.028 and t["fuel_end_pct"] == 69.5
