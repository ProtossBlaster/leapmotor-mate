"""A trip closed by crash recovery keeps the regen its polls measured.

The third column that path dropped, after the end odometer (#298) and the end fuel. `regen_kwh` is
a running total the recorder keeps in memory and hands to `finalize_trip`; a poller that restarts
mid-drive loses it, and `close_orphan_trips` never wrote the column at all — so the trip is closed
reading 0.00 kWh recovered, which on a BEV is a number on screen and not a blank.

It is recomputable, exactly, because every input is already stored per poll: `positions` carries
`charge_current_a`, `charge_voltage_v`, `plug_connected` and `fuel_liters` for every reading of the
drive. The rule is the recorder's own — unplugged, pack current clearly negative — through the same
`_generator_was_running` the live path uses, so a range-extender's generator is not counted here
either (tests/test_a_reevs_regen_is_braking_not_its_own_generator.py).

Integrated over the REAL interval between rows rather than the nominal poll period, and intervals
longer than `_RECOVERY_MAX_GAP_S` are skipped: a current read five minutes ago says nothing about
the five minutes of silence after it, and carrying it across would invent energy exactly where the
car was least observed.
"""
import db as D

VIN = "LVINORPHANREGEN01"


def _rig(polls, *, plugged=0):
    """polls: (minute, current_a, voltage_v, litres). Current is + discharge / − charge."""
    pdb = D.Database(":memory:")
    vid = pdb.ensure_vehicle(VIN, "C10")
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, start_soc, start_odometer_km)"
        " VALUES (1, ?, '2026-06-13T08:00:00+00:00', 80, 1000.0)", (vid,))
    for i, (minute, cur, volt, litres) in enumerate(polls):
        at = f"2026-06-13T08:{minute:02d}:00+00:00"
        pdb._conn.execute(
            "INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude, soc)"
            " VALUES (1, ?, ?, 9.19, 79)", (at, 45.46 + i * 0.01))
        pdb._conn.execute(
            "INSERT INTO positions (vehicle_id, recorded_at, latitude, longitude, soc,"
            " odometer_km, charge_current_a, charge_voltage_v, plug_connected, fuel_liters)"
            " VALUES (?,?,?,9.19,79,?,?,?,?,?)",
            (vid, at, 45.46 + i * 0.01, 1000.0 + i, cur, volt, plugged, litres))
    pdb._conn.commit()
    return pdb, vid


def _regen(pdb, vid):
    pdb.close_orphan_trips(vid)
    return pdb._conn.execute("SELECT regen_kwh FROM trips WHERE id=1").fetchone()["regen_kwh"]


def test_the_regen_of_a_recovered_trip_is_the_one_its_polls_measured():
    """−50 A at 400 V is 20 kW into the pack; held for the minute to the next reading, 0.333 kWh."""
    got = _regen(*_rig([(0, -50.0, 400.0, None), (1, -50.0, 400.0, None), (2, 0.0, 400.0, None)]))
    assert round(got, 3) == 0.667, "a trip closed by crash recovery reads 0.00 kWh recovered"


def test_energy_leaving_the_pack_is_not_regen():
    got = _regen(*_rig([(0, 120.0, 400.0, None), (1, 120.0, 400.0, None), (2, 120.0, 400.0, None)]))
    assert got == 0


def test_the_range_extenders_generator_is_not_counted_here_either():
    """Same rule as the live path: the counter fell over that interval, so petrol made it."""
    got = _regen(*_rig([(0, -50.0, 400.0, 30.000), (1, -50.0, 400.0, 29.950),
                        (2, -50.0, 400.0, 29.900)]))
    assert got == 0, "the generator's output was recovered into the trip as braking regen"


def test_a_range_extender_braking_with_the_generator_off_is_counted():
    got = _regen(*_rig([(0, -50.0, 400.0, 30.0), (1, -50.0, 400.0, 30.0), (2, 0.0, 400.0, 30.0)]))
    assert round(got, 3) == 0.333, (
        "the first interval has no earlier reading to compare with, so it is refused; the second "
        "is a flat counter and must count")


def test_a_silence_between_two_polls_does_not_become_energy():
    """The poller was down for an hour; the current before it says nothing about that hour."""
    got = _regen(*_rig([(0, -50.0, 400.0, None), (59, -50.0, 400.0, None)]))
    assert got == 0


def test_polls_that_carried_no_current_leave_it_at_zero():
    got = _regen(*_rig([(0, None, None, None), (1, None, None, None), (2, None, None, None)]))
    assert got == 0


def test_the_trip_is_still_closed_either_way():
    pdb, vid = _rig([(0, None, None, None), (1, None, None, None), (2, None, None, None)])
    pdb.close_orphan_trips(vid)
    assert pdb._conn.execute("SELECT ended_at FROM trips WHERE id=1").fetchone()["ended_at"]


def test_a_poll_taken_with_the_plug_in_is_charging_not_regen():
    """The recorder's own first condition, mirrored: energy arriving through the cable is a charge,
    and a drive is not where it gets counted. Pinned because this path rebuilds the rule from
    stored rows, and a condition nothing exercises is a condition that can quietly go missing."""
    got = _regen(*_rig([(0, -50.0, 400.0, None), (1, -50.0, 400.0, None), (2, 0.0, 400.0, None)],
                       plugged=1))
    assert got == 0
