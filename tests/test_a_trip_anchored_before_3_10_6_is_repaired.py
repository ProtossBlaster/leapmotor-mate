"""A trip that carries the kilometres of a silence is given them back as a silence — #497, @adoewa.

THE TRIP. 10 August 2026, a C10: home to the supermarket, about 1 km of GPS track in 6 minutes —
stored as 59.0 km at 0.7 kWh/100km. Its start odometer reads 1673 and its end 1732. The 58 km in
front of it were driven on the 9th, while the cloud had nothing new to say.

WHY. Until v3.10.6 the recorder moved a late-opening trip's start odometer, SoC and position BACK to
the last reading before the silence (`_offline_head`, #130/#233). v3.10.6 removed that anchor and
sent those kilometres to `offline_gaps`, attributed to no trip (#244 — the same driver, the same
two days). But it changed only what was recorded from then on: a trip anchored before it still
carries the silence.

THE SIGNATURE. Without the anchor a trip starts on the odometer of the poll that opened it, which is
saved to `positions` just before the trip row is written. A start odometer BELOW that poll's
reading is the anchor, and nothing else: the odometer does not run backwards.

THE REPAIR does what v3.10.6 would have done on the day: start odometer, SoC and position from the
opening poll; distance and consumption recomputed; and the kilometres, with the SoC that went with
them, recorded in `offline_gaps`, so Mate's total does not silently drop below the car's odometer.
A trip converted to the cloud's energy keeps that energy — the cloud measured the drive, not the
silence — and only its per-100 km figure follows the new distance.
"""
import db as D

CAP = 83.1
CAR = 1
SILENCE = "2026-08-09T14:15:00.100000+00:00"   # the last frame the cloud had news in
POLL = "2026-08-10T06:43:00.200000+00:00"      # the poll that opened the trip
START = "2026-08-10T06:43:00.500000+00:00"     # the trip row, written just after it
END = "2026-08-10T06:49:00.000000+00:00"
HOME = (51.2185, 4.4710)
SHOP = (51.2125, 4.4840)


def a_db(tmp_path):
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    pdb.set_setting("battery_capacity_kwh", str(CAP))
    pdb._conn.execute("INSERT OR IGNORE INTO vehicles (id, vin) VALUES (?, 'VIN1')", (CAR,))
    pdb._conn.commit()
    return path, pdb


def a_poll(pdb, at, odo, soc, lat, lon, frame_ts=None):
    pdb._conn.execute(
        "INSERT INTO positions (vehicle_id, recorded_at, latitude, longitude, odometer_km, soc,"
        " frame_ts) VALUES (?, ?, ?, ?, ?, ?, ?)", (CAR, at, lat, lon, odo, soc, frame_ts))


def the_silence(pdb, *, odo_before=1673.0, soc_before=64.0, odo_open=1731.0, soc_open=54.0):
    """Parked before the silence, the same frozen frame served all night, then the opening poll."""
    a_poll(pdb, "2026-08-09T14:10:00.000000+00:00", odo_before, soc_before, *SHOP, frame_ts=100)
    a_poll(pdb, SILENCE, odo_before, soc_before, *SHOP, frame_ts=200)
    a_poll(pdb, "2026-08-09T20:00:00.000000+00:00", odo_before, soc_before, *SHOP, frame_ts=200)
    a_poll(pdb, "2026-08-10T06:30:00.000000+00:00", odo_before, soc_before, *SHOP, frame_ts=200)
    a_poll(pdb, POLL, odo_open, soc_open, *HOME, frame_ts=300)


def a_trip(pdb, trip_id=1, *, start_odo=1673.0, start_soc=64.0, start=SHOP, end_odo=1732.0,
           end_soc=53.9, started_at=START, ec_kwh=None):
    km = end_odo - start_odo
    soc_eff = (start_soc - end_soc) / 100 * CAP / km * 100
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, start_lat, start_lon, end_lat,"
        " end_lon, distance_km, start_soc, end_soc, start_odometer_km, end_odometer_km,"
        " duration_min, efficiency_kwh_100km, efficiency_soc, ec_kwh, ec_stable)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 6.0, ?, ?, ?, ?)",
        (trip_id, CAR, started_at, END, *start, *SHOP, km, start_soc, end_soc, start_odo, end_odo,
         ec_kwh / km * 100 if ec_kwh else soc_eff, soc_eff if ec_kwh else None, ec_kwh,
         1 if ec_kwh else 0))
    # About a kilometre of track, home to the shop.
    steps = 6
    for i in range(steps + 1):
        f = i / steps
        pdb._conn.execute(
            "INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude) VALUES (?,?,?,?)",
            (trip_id, START, HOME[0] + (SHOP[0] - HOME[0]) * f, HOME[1] + (SHOP[1] - HOME[1]) * f))


def reopen(path, pdb):
    pdb.set_setting("trips_offline_anchor_repair_v1", "0")
    pdb._conn.commit()
    pdb.close()
    return D.Database(path)                       # the repair runs at start-up


def trip(pdb, trip_id=1):
    return pdb._conn.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()


def as_recorded(row):
    """The trip without its geohashes, which an older start-up backfill fills in on its own."""
    return {k: row[k] for k in row.keys() if not k.endswith("_geohash")}


def gaps(pdb):
    return pdb._conn.execute("SELECT * FROM offline_gaps ORDER BY id").fetchall()


def test_the_supermarket_run_starts_where_the_signal_returned(tmp_path):
    path, pdb = a_db(tmp_path)
    the_silence(pdb)
    a_trip(pdb)
    pdb = reopen(path, pdb)
    t = trip(pdb)
    assert t["start_odometer_km"] == 1731.0
    assert t["start_soc"] == 54.0
    assert (t["start_lat"], t["start_lon"]) == HOME
    assert t["distance_km"] == 1.0
    assert round(t["efficiency_kwh_100km"], 2) == round(0.1 / 100 * CAP * 100, 2)   # 8.31
    assert t["start_geohash"] is not None


def test_the_kilometres_of_the_silence_are_declared_apart(tmp_path):
    path, pdb = a_db(tmp_path)
    the_silence(pdb)
    a_trip(pdb)
    pdb = reopen(path, pdb)
    [g] = gaps(pdb)
    assert (g["odometer_start"], g["odometer_end"], g["distance_km"]) == (1673.0, 1731.0, 58.0)
    assert (g["soc_start"], g["soc_end"]) == (64.0, 54.0)
    assert round(g["energy_kwh"], 3) == round(10.0 / 100 * CAP, 3)
    assert g["started_at"] == SILENCE             # where the news stopped, not the last frozen poll
    assert g["ended_at"] == POLL


def test_a_trip_on_the_clouds_energy_keeps_it(tmp_path):
    path, pdb = a_db(tmp_path)
    the_silence(pdb)
    a_trip(pdb, ec_kwh=0.10)
    pdb = reopen(path, pdb)
    t = trip(pdb)
    assert t["ec_kwh"] == 0.10
    assert t["efficiency_kwh_100km"] == 10.0     # 0.10 kWh over the 1 km actually driven
    assert round(t["efficiency_soc"], 2) == 8.31  # the backup follows the new distance too


def test_a_charge_in_the_silence_leaves_the_gap_without_a_soc(tmp_path):
    """A SoC that rose across the silence holds a charge: its two ends are no measure of the drive."""
    path, pdb = a_db(tmp_path)
    the_silence(pdb, soc_before=40.0, soc_open=80.0)
    a_trip(pdb, start_soc=40.0, end_soc=79.9)
    pdb = reopen(path, pdb)
    [g] = gaps(pdb)
    assert g["distance_km"] == 58.0
    assert g["soc_start"] is None and g["soc_end"] is None
    assert g["energy_kwh"] == 0
    assert trip(pdb)["start_soc"] == 80.0


def test_a_trip_that_started_on_its_own_poll_is_left_alone(tmp_path):
    """The odometer ticking on the next poll is the drive, not an anchor — nine such trips on a
    real history read as anchored when the comparison looked one poll too far."""
    path, pdb = a_db(tmp_path)
    the_silence(pdb)
    a_trip(pdb, start_odo=1731.0, start_soc=54.0, start=HOME)
    a_poll(pdb, "2026-08-10T06:43:10.000000+00:00", 1732.0, 54.0, *HOME)
    before = as_recorded(trip(pdb))
    pdb = reopen(path, pdb)
    assert as_recorded(trip(pdb)) == before
    assert gaps(pdb) == []


def test_a_trip_stamped_before_its_opening_poll_is_left_alone(tmp_path):
    """Since 3.10.6 the trip carries the poll's own start time, so the poll sits at or after it and
    the reading before it is the previous poll — at or below the start, never above."""
    path, pdb = a_db(tmp_path)
    the_silence(pdb)
    a_trip(pdb, start_odo=1731.0, start_soc=54.0, start=HOME, started_at=POLL)
    before = as_recorded(trip(pdb))
    pdb = reopen(path, pdb)
    assert as_recorded(trip(pdb)) == before
    assert gaps(pdb) == []


def test_a_trip_whose_opening_poll_was_pruned_is_left_alone(tmp_path):
    path, pdb = a_db(tmp_path)
    a_trip(pdb)
    before = as_recorded(trip(pdb))
    pdb = reopen(path, pdb)
    assert as_recorded(trip(pdb)) == before
    assert gaps(pdb) == []


def test_the_repair_runs_once(tmp_path):
    path, pdb = a_db(tmp_path)
    pdb.close()
    pdb = D.Database(path)
    assert pdb.get_setting("trips_offline_anchor_repair_v1") == "1"
    the_silence(pdb)
    a_trip(pdb)
    pdb._conn.commit()
    pdb.close()
    pdb = D.Database(path)                        # already done → the row keeps its anchor
    assert trip(pdb)["start_odometer_km"] == 1673.0
    assert gaps(pdb) == []
