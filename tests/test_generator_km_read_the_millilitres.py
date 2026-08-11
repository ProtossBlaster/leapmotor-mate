"""The generator's distance was counted on the coarse signal, and lost 42 % of it.

@pdifeo, beta #28, 11/08/26 — a C10 REEV, five drives, and the one thing we almost never get:
**photographs of the car's own dashboard** beside the bundle. The car states how far it ran on
petrol per drive. Mate stated much less:

    trip      the car says      before    after
    #112        26.3 km            11.0      24.0
    #113         1.5 km           nothing     1.0
    #114        32.4 km            24.0      29.0
    total       60.2 km            35.0      54.0      −42 % → −10 %

**The litres were never wrong** — 3.591 L measured against 3.591 L on the car's own counter, and
over the long window (since his 03/08 refuel) 14.248 L against the 14.37 his dashboard works out
from "239.5 km · 6 L/100 km", 0.8 % apart. Only the DISTANCE was short.

🔑 **The cause is a unit, not an algorithm.** `_reev_engine_on` asks, sample by sample, "did the
odometer rise AND the fuel fall in this SAME interval?" — and it asked the tank in **percent**.
The car reports that tank twice: signal 3235 as a percentage that moves in steps of 0.1, and signal
3263 in **millilitres**. One step of the percentage is 47.5 mL, about 600 m of generator driving, so
a row where the car has moved but the coarse gauge has not yet ticked is dropped, and there are a
lot of them. In his bundle 3263 gave 1048 distinct readings where 3235 gave 285.

The millilitres were **already in the same row**: `positions.fuel_liters` has been written since
v2.14.1. Only this walk was reading the other column.

⚠️ This is the SECOND time the same mistake is fixed in this file. `_reev_trip_fuel` had its noise
floor on the percentage while reading the millilitres (beta #22/#23) — "the guard was on the wrong
signal". Same disease, one function up.

⛔ **A rule that looks better and is not.** "Anchor on the last fuel CHANGE and credit every
kilometre since" scores −2 % if you rebuild the trail from the raw signal log, and **+54 %** on the
real one: it hands the generator the whole electric middle of a drive that burned a little at each
end. The raw log records a signal only WHEN IT CHANGES, so rebuilding from it gives a timeline 3×
sparser than `positions`, which gets a row every poll (~11 s while driving). Any rule tuned on that
reconstruction is tuned on an artefact — model the poll grid instead.

⛔ And a second cul-de-sac: there is **no "generator running" signal** in the cloud. Signal 1277 looks exactly like one — 0 across a pure-electric drive, 1 across
a petrol one — and it is not: checked across all 13 days of the bundle it turns on in one-minute
bursts at the start and end of drives, with 0.001 L burned inside them against 24.9 L outside.
"""
import sqlite3

import db_reader


def _pos_db(rows):
    """positions with BOTH fuel columns — percentage and the car's own millilitre count."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE positions (id INTEGER PRIMARY KEY, vehicle_id INT, "
                 "recorded_at TEXT, odometer_km REAL, fuel_level_pct REAL, fuel_liters REAL)")
    for i, (ts, odo, pct, litres) in enumerate(rows):
        conn.execute("INSERT INTO positions VALUES (?,?,?,?,?,?)", (i, 1, ts, odo, pct, litres))
    return conn


def _walk(conn, a="2026-08-11T06:00:00", b="2026-08-11T07:00:00"):
    return db_reader._reev_engine_on(conn, 1, a, b)


# ── the defect, in the smallest shape that shows it ───────────────────────────
def test_kilometres_are_not_lost_while_the_percentage_has_not_ticked_yet():
    """Four samples, the generator running throughout: 40.0 L → 39.8 L, 200 mL, 4 km.

    The percentage sits at 84.2 for the first three and only drops on the last — exactly what a
    0.1-point grid does to a 200 mL burn. Read that way, 3 of the 4 kilometres vanish."""
    conn = _pos_db([
        ("2026-08-11T06:00:00", 100.0, 84.2, 40.000),
        ("2026-08-11T06:10:00", 101.0, 84.2, 39.950),   # +1 km, −50 mL, percentage flat
        ("2026-08-11T06:20:00", 102.0, 84.2, 39.900),   # +1 km, −50 mL, percentage flat
        ("2026-08-11T06:30:00", 104.0, 84.1, 39.800),   # +2 km, −100 mL, percentage finally moves
    ])
    assert _walk(conn)["engine_km"] == 4.0


def test_a_short_generator_stretch_is_not_lost_entirely():
    """@pdifeo's trip #113: 3 km, 96 mL — two tenths of a percentage point. Counted on the
    percentage it produced nothing at all, so the trip said `engine_ran` and then had no distance
    to show for it."""
    conn = _pos_db([
        ("2026-08-11T06:00:00", 100.0, 75.5, 35.906),
        ("2026-08-11T06:03:00", 101.5, 75.5, 35.858),   # +1.5 km, −48 mL, percentage flat
        ("2026-08-11T06:07:00", 103.0, 75.3, 35.810),   # +1.5 km, −48 mL
    ])
    assert _walk(conn)["engine_km"] == 3.0


# ── what must NOT change: the two exclusions the walk exists for ──────────────
def test_pure_electric_stretches_are_still_excluded():
    conn = _pos_db([
        ("2026-08-11T06:00:00", 100.0, 84.2, 40.000),
        ("2026-08-11T06:10:00", 110.0, 84.1, 39.500),   # +10 km burning → counts
        ("2026-08-11T06:20:00", 120.0, 84.1, 39.500),   # +10 km, tank flat → electric, excluded
    ])
    assert _walk(conn)["engine_km"] == 10.0


def test_charging_the_battery_while_parked_is_still_excluded():
    """Fuel burned over zero kilometres must not be blamed on the driving distance."""
    conn = _pos_db([
        ("2026-08-11T06:00:00", 100.0, 84.2, 40.000),
        ("2026-08-11T06:10:00", 110.0, 84.1, 39.500),   # +10 km burning → counts
        ("2026-08-11T06:20:00", 110.0, 83.0, 39.000),   # 0 km, half a litre → stationary, excluded
    ])
    assert _walk(conn)["engine_km"] == 10.0


# ── the fallback, for every car and every row that has no millilitres ─────────
def test_a_trail_without_millilitres_still_works_on_the_percentage():
    """Trips recorded before v2.14.1, and any car that does not send 3263. The coarse walk is worse
    but it is what there is, and it must not become nothing."""
    conn = _pos_db([
        ("2026-08-11T06:00:00", 100.0, 96.0, None),
        ("2026-08-11T06:05:00", 110.0, 95.0, None),
        ("2026-08-11T06:15:00", 120.0, 95.0, None),
    ])
    assert _walk(conn) == {"engine_km": 10.0, "engine_fuel_pct": 1.0}


def test_a_half_filled_trail_does_not_mix_the_two_grids():
    """One column or the other for the whole walk. Switching per interval would count some
    kilometres on a 47.5 mL grid and others on a 1 mL one, and the total would mean nothing."""
    conn = _pos_db([
        ("2026-08-11T06:00:00", 100.0, 96.0, 40.000),
        ("2026-08-11T06:05:00", 110.0, 95.0, None),     # the car stopped sending 3263 mid-trip
        ("2026-08-11T06:15:00", 120.0, 95.0, 39.000),
    ])
    assert _walk(conn) == {"engine_km": 10.0, "engine_fuel_pct": 1.0}


def test_less_than_half_a_kilometre_reads_as_no_trail_at_all():
    """The floor is not decoration. `_reev_trip_fuel` re-checks it before showing engine_km, but
    `reev_fuel_summary` does NOT — it adds whatever comes back straight into the page total. So a
    quarter-kilometre of "generator driving", which is what a rounding wobble in the odometer looks
    like, has to be stopped here or it accumulates across a history."""
    conn = _pos_db([
        ("2026-08-11T06:00:00", 100.00, 84.2, 40.000),
        ("2026-08-11T06:10:00", 100.25, 84.2, 39.950),   # +250 m, burning — under the floor
    ])
    assert _walk(conn) is None


def test_still_none_when_there_is_no_fuel_trail_at_all():
    conn = _pos_db([("2026-08-11T06:00:00", 100.0, None, None),
                    ("2026-08-11T06:05:00", 110.0, None, None)])
    assert _walk(conn) is None


def test_the_percentage_is_still_reported_when_the_walk_reads_millilitres():
    """`reev_fuel_summary` still reads `engine_fuel_pct`. It has to keep coming back, and it has to
    describe the SAME intervals the kilometres were counted over."""
    conn = _pos_db([
        ("2026-08-11T06:00:00", 100.0, 84.2, 40.000),
        ("2026-08-11T06:10:00", 110.0, 84.0, 39.500),   # +10 km, −0.2 pt, −500 mL
        ("2026-08-11T06:20:00", 120.0, 84.0, 39.500),   # electric — in neither total
    ])
    eng = _walk(conn)
    assert eng["engine_km"] == 10.0
    assert eng["engine_fuel_pct"] == 0.2
