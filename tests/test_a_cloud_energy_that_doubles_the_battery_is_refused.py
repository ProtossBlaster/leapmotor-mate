"""A drive's official energy cannot be twice what its battery lost — #298, @arzthilfe.

THE TRIP. 21 September, a C10: 7.0 km, SoC 83.7 → 82.4, and the cloud's figure 2.90 kWh. Over 7 km
that is 41.4 kWh/100km, and it is what the Consumption-vs-Temperature chart drew. The battery's own
answer is 1.3 points of an 81.9 kWh pack — 1.07 kWh, or 15.2 kWh/100km. The two disagree by 2.7×.

WHY THE GUARD STAYED SHUT. It needed BOTH halves: an efficiency above 60 kWh/100km AND a figure
past twice the SoC delta. His trip is 2.7× the delta — well over the line — but 41.4 is under 60,
so nothing looked. The AND exists for a good reason: on a one-kilometre trip the SoC falls by two
or three 0.1 steps, and a ratio built on three steps IS the noise, not a measurement.

THE SEPARATION, measured rather than chosen. Over 412 trips carrying both a cloud figure and a
usable SoC drop, the ratio converges as the drop grows: p90 1.54 below half a point, then 0.96,
1.08, 0.99 and 1.00 as the drop reaches five points and more. Below half a point, four trips reach
1.9× to 3.1× on two to five steps. At a full point and above, exactly two exceed 2× — at 2.09 and
2.18, on 14 and 29 steps — and they are the same shape as his. So the efficiency ceiling is dropped
once the measured fall reaches a full point, and kept below it, where the ratio cannot vouch for
itself.

AND THE TRIPS ALREADY CONVERTED. The ones carrying such a figure are not left with it: a one-time
pass at start-up puts them back on the SoC estimate the conversion kept as a backup, and clears
`ec_stable` so a cloud that later settles on a sane figure is not locked out.
"""
import db as D
import ec_enrich


def soc_energy(drop_points, capacity=65.0):
    return drop_points / 100.0 * capacity


def refused(ec_kwh, km, drop_points, capacity=65.0):
    """What the live guard says about one trip, with its SoC fall measured in points."""
    return ec_enrich._ec_implausible({"total_kwh": ec_kwh}, km,
                                     soc_energy(drop_points, capacity), drop_points)


# ── the trip from #298 ───────────────────────────────────────────────────────

def test_the_seven_kilometre_drive_that_read_forty_one():
    """His numbers, with his pack: 2.90 kWh against 1.07, at 41.4 kWh/100km. Refused — and this is
    the assertion that fails without the change, because 41.4 is under the old ceiling."""
    assert refused(2.90, 7.0, 1.3, capacity=81.9) is True


def test_the_same_figure_on_three_tenths_of_a_point_is_accepted():
    """Three 0.1 steps cannot convict the cloud: the ratio is the quantization, not a measurement.
    This is why the two conditions were ANDed, and why the AND stays below a full point."""
    assert refused(0.30, 1.0, 0.2) is False


def test_a_full_point_is_where_the_battery_starts_being_believed():
    """The boundary itself, from both sides, at the same ratio and the same efficiency."""
    assert refused(2.0, 6.0, 1.0) is True
    assert refused(1.8, 5.4, 0.9) is False


def test_a_figure_inside_twice_the_battery_is_still_accepted():
    """The overshoot is what accuses; a solid SoC reference alone accuses nobody. A cloud figure at
    1.5× the delta is ordinary — the median across 412 trips is 0.87."""
    assert refused(1.46, 10.0, 1.5) is False


def test_the_old_ceiling_still_catches_a_trip_with_no_measured_fall():
    """#98's case is untouched: a 600 m trip whose window swallowed the pre-conditioning, with no
    SoC fall worth the name. Caught on the efficiency, exactly as before."""
    assert ec_enrich._ec_implausible({"total_kwh": 0.40}, 0.6, soc_energy(0.2), None) is True


def test_a_figure_far_below_the_battery_is_still_the_low_guard():
    """#96 unchanged: too low needs its own two halves, and a solid fall does not substitute."""
    assert refused(0.40, 10.0, 0.3) is False          # 4 kWh/100km, but twice the delta — consistent
    assert refused(0.05, 10.0, 2.0) is True           # under 5 kWh/100km AND under half of it


def test_a_drop_nobody_measured_is_not_a_reference():
    """`_soc_drop_points` answers None unless a real start/end pair fell: the energy can come from
    the efficiency fallback, which never saw a drop at all."""
    assert ec_enrich._soc_drop_points({"start_soc": 80.0, "end_soc": 78.5}) == 1.5
    assert ec_enrich._soc_drop_points({"start_soc": 80.0, "end_soc": 80.0}) is None
    assert ec_enrich._soc_drop_points({"start_soc": None, "end_soc": 70.0}) is None
    assert ec_enrich._soc_drop_points({"efficiency_soc": 15.0}) is None


# ── the trips already converted ──────────────────────────────────────────────

def a_trip(pdb, trip_id, km, start_soc, end_soc, ec_kwh, *, merged_into=None):
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc,"
        " ec_kwh, efficiency_kwh_100km, efficiency_soc, ec_stable, merged_into_id)"
        " VALUES (?, 1, '2026-09-21T12:29:00+00:00', '2026-09-21T12:41:00+00:00', ?, ?, ?, ?,"
        "         ?, ?, 1, ?)",
        (trip_id, km, start_soc, end_soc, ec_kwh, ec_kwh / km * 100,
         (start_soc - end_soc) / 100 * 65.0 / km * 100, merged_into))
    pdb._conn.commit()


def stored(pdb, trip_id):
    return pdb._conn.execute(
        "SELECT ec_kwh, efficiency_kwh_100km, efficiency_soc, ec_stable FROM trips WHERE id = ?",
        (trip_id,)).fetchone()


def test_a_trip_converted_before_the_fix_goes_back_on_the_battery(tmp_path):
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    pdb.set_setting("battery_capacity_kwh", "65.0")
    a_trip(pdb, 1, 10.0, 60.0, 57.1, 4.10)        # 2.18× the delta, 41 kWh/100km
    pdb.set_setting("trips_ec_overshoot_repair_v1", "0")
    pdb.close()

    pdb = D.Database(path)                         # the repair runs at start-up
    row = stored(pdb, 1)
    assert row["ec_kwh"] is None
    assert row["efficiency_soc"] is None
    assert round(row["efficiency_kwh_100km"], 2) == 18.85    # the SoC estimate, restored
    assert row["ec_stable"] == 0


def test_a_trip_whose_figure_is_sane_is_left_alone(tmp_path):
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    pdb.set_setting("battery_capacity_kwh", "65.0")
    a_trip(pdb, 1, 10.0, 60.0, 57.1, 1.70)        # 0.90× the delta
    pdb.set_setting("trips_ec_overshoot_repair_v1", "0")
    pdb.close()
    pdb = D.Database(path)
    assert stored(pdb, 1)["ec_kwh"] == 1.70


def test_a_trip_with_too_small_a_fall_is_left_alone(tmp_path):
    """The repair refuses what the live guard refuses, or start-up would undo what the sweep then
    re-applies."""
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    pdb.set_setting("battery_capacity_kwh", "65.0")
    a_trip(pdb, 1, 1.0, 60.0, 59.8, 0.30)         # 2.31× the delta, but two 0.1 steps
    pdb.set_setting("trips_ec_overshoot_repair_v1", "0")
    pdb.close()
    pdb = D.Database(path)
    assert stored(pdb, 1)["ec_kwh"] == 0.30


def test_the_repair_runs_once(tmp_path):
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    pdb.set_setting("battery_capacity_kwh", "65.0")
    pdb.close()
    pdb = D.Database(path)
    assert pdb.get_setting("trips_ec_overshoot_repair_v1") == "1"
    a_trip(pdb, 1, 10.0, 60.0, 57.1, 4.10)
    pdb.close()
    pdb = D.Database(path)                         # already done → the row keeps its figure
    assert stored(pdb, 1)["ec_kwh"] == 4.10


def test_the_two_copies_of_the_two_numbers_agree():
    """The repair lives in the poller and the guard in the web, so the numbers exist twice. One
    process lying about what an overshoot is would make start-up and the sweep fight."""
    assert D._MAX_EC_SOC_OVERSHOOT == ec_enrich._MAX_EC_SOC_OVERSHOOT
    assert D._SOC_REFERENCE_MIN_POINTS == ec_enrich._SOC_REFERENCE_MIN_POINTS
