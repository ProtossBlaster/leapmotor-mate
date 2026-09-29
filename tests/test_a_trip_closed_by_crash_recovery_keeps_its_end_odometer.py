"""A trip closed by crash recovery keeps the odometer it ended on.

@arzthilfe's bundle (#298) had one trip reading `odo 3233→—`: 41.4 km driven, a start odometer, and
no end. His log names the path — the poller restarted 74 minutes into the drive:

    17:28:05  Trip #25 started — SOC 92.7%
    18:42:29  Database ready                       ← the poller restarted
    18:42:36  Trip #25 was open (crash recovery) — closed at last known position 41.4 km | 37 min

`close_orphan_trips` closes such a trip from its `trip_positions` — which carry no odometer — and
its UPDATE never listed `end_odometer_km`. So every trip ever closed that way keeps an empty end,
and the odometer chain that finds missing kilometres breaks exactly there: the method that answered
his report is defeated by a defect of ours. → [[a-bundle-is-read-by-the-odometer]]

The reading exists. `positions` holds the odometer of every poll of that drive, and the same
function already reaches into that table for the range-extender's fuel a few lines above.
"""
import db as D

VIN = "LVINORPHANODO0001"
START = "2026-06-13T08:00:00+00:00"


def _rig(*, odo_in_positions=True, start_odo=1000.0):
    pdb = D.Database(":memory:")
    vid = pdb.ensure_vehicle(VIN, "B10")
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, start_soc, start_odometer_km)"
        " VALUES (1, ?, ?, 80, ?)", (vid, START, start_odo))          # ended_at NULL → orphan
    for i, (lat, lon, odo) in enumerate([(45.460, 9.190, 1000.0),
                                         (45.470, 9.200, 1001.0),
                                         (45.485, 9.215, 1002.0)]):
        at = f"2026-06-13T08:0{i}:00+00:00"
        pdb._conn.execute(
            "INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude, soc)"
            " VALUES (1, ?, ?, ?, 79)", (at, lat, lon))
        pdb._conn.execute(
            "INSERT INTO positions (vehicle_id, recorded_at, latitude, longitude, soc, odometer_km)"
            " VALUES (?,?,?,?,79,?)",
            (vid, at, lat, lon, odo if odo_in_positions else None))
    pdb._conn.commit()
    return pdb, vid


def _trip(pdb):
    return pdb._conn.execute("SELECT * FROM trips WHERE id=1").fetchone()


def test_the_end_odometer_is_the_last_one_the_drive_reported():
    pdb, vid = _rig()
    assert pdb.close_orphan_trips(vid) == 1
    assert _trip(pdb)["end_odometer_km"] == 1002.0, (
        "the trip was closed with no end odometer, so the odometer chain breaks here (#298)")


def test_the_chain_closes_on_that_trip():
    """What the diagnostics bundle is read with: end odometer minus start is the distance driven."""
    pdb, vid = _rig()
    pdb.close_orphan_trips(vid)
    t = _trip(pdb)
    assert t["end_odometer_km"] - t["start_odometer_km"] == 2.0


def test_a_drive_whose_polls_carried_no_odometer_is_left_alone():
    """Nothing better exists for it: an empty end is honest, a guess is not."""
    pdb, vid = _rig(odo_in_positions=False)
    pdb.close_orphan_trips(vid)
    assert _trip(pdb)["end_odometer_km"] is None
    assert _trip(pdb)["ended_at"] is not None, "the trip must still be closed"


def test_the_end_odometer_is_never_below_the_start():
    """A reading that would run the trip backwards is not written: the chain would read worse
    with it than without it."""
    pdb, vid = _rig(start_odo=5000.0)
    pdb.close_orphan_trips(vid)
    t = _trip(pdb)
    assert t["end_odometer_km"] is None or t["end_odometer_km"] >= t["start_odometer_km"], dict(t)
