"""SQLite database layer. Switch DATABASE_URL to postgresql://... for production."""
import json
import logging
import math
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import crypto
import geohash

log = logging.getLogger(__name__)

# USABLE (net) capacity, kWh — matches web/main.py _EU_BATTERY_MAP. Used as the
# first-run fallback if the setup wizard didn't set a per-variant value.
BATTERY_CAPACITY_DEFAULTS: dict[str, float] = {
    "T03": 36.0,   # EU only variant (gross 37.3)
    "B05": 65.0,   # Pro Max 482 km WLTP (EU; gross 67.1; shares the B10 pack)
    "B10": 65.0,   # Pro Max 434 km WLTP (EU; gross 67.1, 3.1% buffer)
    "C10": 67.0,   # RWD (EU; gross 69.9, 4.1% buffer — see the note in web/main.py)
}
BATTERY_CAPACITY_FALLBACK = 65.0

# Physical ceiling for wallbox session energy (GitHub #46). A HOME wallbox can't deliver more
# energy than its power × time. We cap at a generous 22 kW (3-phase 32 A, the realistic home-AC
# ceiling) so legit charging NEVER trips it, times elapsed hours, plus headroom. A counter that
# reports a LIFETIME TOTAL (hundreds–thousands of kWh) as a single step — e.g. the entity reads 0
# at plug-in then snaps back to its cumulative value — lands far above this and is rejected.
_WB_MAX_KW = 22.0
_WB_MARGIN = 1.5
_WB_FLOOR_KWH = 1.0

# GPS hemisphere re-derived from the car's own history — see dominant_coord_signs (#232).
# The sample is in PLACES, not rows: 400 distinct coordinates is several days of real driving for
# any car, and small enough that a house move is followed within a week rather than outvoted by a
# year of the old address. 20 places is the floor below which a fresh install has nothing to say —
# it must stay well above 1, or a single stuck frame would count as "the history".
# The majority is deliberately lopsided: a car does not straddle the meridian, so anything short of
# near-unanimity means the sample is mixed and the honest answer is to keep quiet.
# #237 — how far either side of a charge's start the one-off odometer back-fill will look for the
# position row that carries it. Kept tight on purpose: the right match is written by the SAME poll,
# so a wider window buys no real coverage and only risks stamping the wrong afternoon's kilometres.
_ODO_BACKFILL_WINDOW_MIN = 5

_SIGN_SAMPLE = 400
_SIGN_MIN_PLACES = 20
_SIGN_MAJORITY = 0.9

# #144 — the temperature sensors a car can legitimately not have, MQTT topic key → positions column.
# Keyed by TOPIC because these names are the HA entity ids that already exist on people's installs.
# web/db_reader.py holds the same three columns under the WEB's key names; a test ties the two
# together, because two copies of a rule are how the page and Home Assistant come to disagree.
ABSENT_TEMP_COLUMNS = {"inside_temp": "inside_temp",
                       "battery_temp": "battery_min_temp",
                       "ac_target_temp": "climate_target_temp"}
ABSENT_TEMP_MIN_POLLS = 50    # below this, "never seen" describes the install's age, not the car
ABSENT_TEMP_WINDOW = 500      # how many recent polls the question is asked over

# The OTHER way a counter lies: it stops. @riri19 (#215) traced a session where his Tuya energy
# sensor froze for 2h18 while the car went on charging at 6.9 kW — 16.9 kWh that the meter never
# counted, so the charge was billed on 22.1 kWh instead of 38.9. A rise that is too SMALL always
# looks plausible, which is why the ceiling above never sees it.
#
# What is NOT a safe test: comparing the counter against the charge's DC energy. That figure is
# ΔSoC × the CONFIGURED battery capacity — an estimate resting on a constant the user can set — so
# a car with the capacity typed in wrong would have its perfectly good meter thrown away on every
# single charge. The weaker number must never be allowed to discredit the stronger one.
#
# So the test is between two MEASUREMENTS: the counter stands still while the CAR reports it is
# drawing power. Counted in the kWh the car says it took during the stall rather than in polls,
# because a coarse meter (1 kWh resolution at low power) legitimately sits still for many polls —
# but no meter has three kWh of resolution.
_WB_STUCK_KWH = 3.0        # car-reported energy drawn while the counter never moved → meter dead
_WB_STUCK_MIN_KW = 1.0     # below this the car isn't really charging, so a flat counter proves nothing

# A reachable home wallbox answers a counter reading even while the car charges elsewhere — and if
# an energy meter also sees the wallbox's few watts of standby, that counter slowly RISES with the
# car far away, so its per-poll creep can accumulate past the 0.05 kWh floor and mislabel a public
# charge as a home/wallbox session. Fix at the root: only attribute the wallbox counter to a charge
# that actually happened AT the wallbox. Its location is learned from the charges where the wallbox
# measured real energy (standby can't reach _WB_HOME_MIN_KWH), median-averaged so a stray GPS fix
# can't move it. Conservative by construction: unknown location or a charge without GPS → attribute
# as before, so a legitimate home charge is never dropped; only a charge KNOWN to be far is skipped.
_WB_HOME_RADIUS_KM = 1.0      # within this of the learned wallbox → treat the charge as "at home"
_WB_HOME_MIN_KWH = 2.0        # wallbox energy that rules out standby → a certain home charge
_WB_HOME_MIN_SAMPLES = 2      # min real home charges before trusting the learned location

# A reconstructed charge whose ΔSoC over its real duration implies a charge power above this is
# physically impossible (a spurious SoC=0 poll makes a full pack look "charged" in seconds) → it's
# a glitch, not a charge. Set well above any real charger (incl. DC fast-charge) so a real charge
# is never rejected.
_RECONSTRUCT_MAX_KW = 250.0

# REEV: the range-extender ran during a trip iff the fuel level dropped more than this noise floor
# (matches web/db_reader _REEV_FUEL_MIN_DROP). When it ran, the generator recharges the pack mid-drive,
# so the trip's NET SoC change ≠ the motor's traction energy → a SoC- (or getEC-over-full-distance)
# electric kWh/100km is a meaningless "consumption" and must NOT be stored (GitHub beta #10, gm27271).
# Detected purely by the fuel drop, so BEVs (fuel NULL) and pure-electric REEV trips (fuel flat) are
# never touched. The trip still shows its fuel L/100 km (computed over the generator-on distance).
_REEV_FUEL_MIN_DROP = 0.2


def _reev_extender_ran(fuel_start, fuel_end) -> bool:
    return (fuel_start is not None and fuel_end is not None
            and (fuel_start - fuel_end) > _REEV_FUEL_MIN_DROP)

# Robustness for flaky car↔cloud links (#118): a reconstructed TRIP above this distance is an odometer
# glitch, not a drive — reject it so it can't poison the (now stats-counted) history. And a
# reconstructed trip's duration is only the offline GAP (an upper bound); keep it only when it implies a
# plausible average speed — a gap padded with parked/offline time, or a glitch, gets a NULL duration so
# it never skews the duration/avg-speed stats (distance + energy + efficiency still count).
_RECONSTRUCT_MAX_TRIP_KM = 1500.0
# The odometer reads in whole kilometres: below one, a jump cannot be told from its own
# quantisation. Same floor the trip reconstruction uses, and the reason is the same.
_OFFLINE_GAP_MIN_KM = 1.0
_RECONSTRUCT_TRIP_MIN_KMH = 8.0
_RECONSTRUCT_TRIP_MAX_KMH = 160.0

# #119: drive mode / One-Pedal are NOT reported by the cloud (verified on-car) — they can only be
# tagged manually. To spare drivers with a fixed habit from re-tagging every trip, two app-level
# settings pre-fill new trips with a chosen default; "" (unset) keeps the current NULL / "not set".
_DRIVE_MODES = ("eco", "comfort", "normal", "sport", "custom")   # keep == web/db_reader.DRIVE_MODES


def _wb_energy_ceiling(max_power_kw: Optional[float], hours: Optional[float]) -> float:
    """Max plausible wallbox energy for a session of `hours` at peak `max_power_kw`."""
    kw = max(max_power_kw or 0.0, _WB_MAX_KW)
    return kw * max(hours or 0.0, 0.0) * _WB_MARGIN + _WB_FLOOR_KWH

# The schema and its migration live in their own dependency-free module so the web can run them
# too — see poller/schema.py for why that matters.
from schema import SCHEMA, ensure_schema  # noqa: F401  (SCHEMA re-exported for tests)

# self.get_battery_capacity() is now stored in settings table, not hardcoded


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _odo_or_none(data) -> Optional[float]:
    """The odometer to stamp on a charge, or None when the car did not send one (#237).

    `client` reads signal 1318 as `float(sig.get("1318") or 0)`, so an ABSENT odometer arrives as
    0.0 — and a charge stamped 0 would later read as "this session happened at kilometre zero",
    which is a wrong number rather than a missing one. A real odometer is never 0 on a car that has
    been driven off the lot, so zero is treated as silence.
    """
    odo = getattr(data, "odometer_km", None)
    return float(odo) if odo else None


def default_capacity_for(car_type: str) -> float:
    return BATTERY_CAPACITY_DEFAULTS.get(car_type.upper(), BATTERY_CAPACITY_FALLBACK)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(max(0, a)))


def _coord_valid(lat, lon) -> bool:
    """A usable GPS fix: present, in range, and not the (0,0) "null island" default."""
    return (lat is not None and lon is not None
            and -90 <= lat <= 90 and -180 <= lon <= 180
            and (abs(lat) > 1e-6 or abs(lon) > 1e-6))


def _gps_track_km(rows) -> float:
    """Sum haversine over a position track (rows with latitude/longitude), skipping
    spurious/missing fixes so a single bad point can't inject a transcontinental jump."""
    pts = [(r["latitude"], r["longitude"]) for r in rows
           if _coord_valid(r["latitude"], r["longitude"])]
    return sum(haversine_km(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1])
               for i in range(len(pts) - 1))


def trip_distance_km(gps_km: float, has_gps: bool, start_odo: float, end_odo: float):
    """Pick the trip distance from the odometer vs the GPS track.

    The odometer counts real wheel-distance (better than a 10s GPS track, which cuts
    corners), BUT it reads in WHOLE km: a few-metres manoeuvre that happens to cross a
    km boundary shows Δodo = 1 even though the car barely moved (a real 24 m driveway
    shuffle was logged as a 1.0 km trip). So:
      Δodo >= 2          → odometer (quantization error ≤ ±1 over ≥2 km, acceptable)
      Δodo == 1          → ambiguous (true distance is anywhere in 0–2 km): if the GPS
                           track says it was a sub-0.5 km manoeuvre, trust the GPS —
                           the recorder then drops it as a short hop; otherwise keep
                           the odometer's 1 km (GPS slightly underestimates real bends)
      Δodo == 0 / bogus  → GPS track (the integer odometer can't resolve short hops;
                           a 0 start would log the car's entire mileage)
      nothing valid      → None (distance unknown → trip preserved, not dropped)
    """
    odo_delta = (end_odo or 0) - (start_odo or 0)
    odo_valid = (start_odo or 0) > 0 and (end_odo or 0) > 0 and odo_delta > 0
    if odo_valid and odo_delta == 1 and has_gps and gps_km < 0.5:
        return gps_km
    if odo_valid:
        return odo_delta
    if has_gps:
        return gps_km
    return None


# Settings keys holding real secrets — encrypted at rest (see crypto.py). Everything
# else (flags, prefixes, prices, ids, identifiers) stays plaintext.
SECRET_KEYS = {"leapmotor_pass", "leapmotor_pin", "abrp_token",
               "mqtt_pass", "geocoder_key", "ha_token", "ocm_key", "tomtom_key"}


class Database:
    def __init__(self, path: str = "leapmotor_mate.db"):
        self._path = path
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        ensure_schema(self._conn)
        self._backfill_vehicle_capacity()
        self._adopt_the_cars_that_were_already_here()
        self._backfill_null_vehicle_id()
        self._backfill_trip_geohashes()
        self._backfill_charge_odometer()
        self._repair_odometer_trips()
        self._repair_quantized_trip_distance()
        self._repair_snap_to_full_charges()
        self._drop_phantom_charges()
        self._repair_phantom_zero_soc_charges()
        self._repair_negative_efficiency()
        self._repair_reev_engine_efficiency()
        self._repair_bogus_wallbox_energy()
        self.migrate_secrets()
        self._check_decryption()
        log.info("Database ready: %s", path)

    def _adopt_the_cars_that_were_already_here(self) -> None:
        """One-time: mark the cars present at THIS moment as already set up, so that only a car
        arriving later can be called unconfigured.

        The wizard has stamped `vehicle_setup_done_<vin>` since v3.13.0, and no install older than
        that carries the stamp on any car. Reading "no stamp" as "nobody configured it" would put a
        banner on every screen out there, including the thousands with one perfectly configured car
        — absent is not the same as unconfigured. What we do know is that a car already in the
        database on the day of the update went through some setup, or through months of use with
        its owner watching the numbers.

        Runs HERE, in the constructor, and not on a page render: the poller registers a new car
        during a poll, which is necessarily after this. A stamping that ran later could adopt the
        newcomer it is supposed to expose. `INSERT OR IGNORE` never overwrites a per-car answer,
        and the marker makes the whole thing a no-op from the second start on — same shape as the
        three migrations in v3.13.0."""
        if self.get_setting("vehicle_setup_backfilled") == "1":
            return
        rows = self._conn.execute(
            "SELECT vin FROM vehicles WHERE vin IS NOT NULL AND TRIM(vin) <> ''").fetchall()
        for r in rows:
            self._conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, '1')",
                (f"vehicle_setup_done_{r['vin'].strip().lower()}",))
        self.set_setting("vehicle_setup_backfilled", "1")
        if rows:
            log.info("Marked %d car(s) already on this install as set up", len(rows))

    def _repair_quantized_trip_distance(self) -> None:
        """One-time repair for manoeuvres logged as 1 km trips. The whole-km odometer
        shows Δ = 1 when a few-metres move crosses a km boundary (a real 24 m driveway
        shuffle was stored as a 1.0 km trip). Recompute closed Δodo=1 trips whose GPS
        track says sub-0.5 km: store the true GPS distance and clear the (meaningless)
        efficiency. Trips are NEVER deleted here — they stay visible as ~0 km manoeuvres
        the user can remove via the existing delete button."""
        if self.get_setting("trips_odo_quantize_repair_v1") == "1":
            return
        rows = self._conn.execute(
            """SELECT id, start_odometer_km, end_odometer_km FROM trips
               WHERE ended_at IS NOT NULL AND start_odometer_km > 0
                 AND end_odometer_km = start_odometer_km + 1""").fetchall()
        fixed = 0
        for t in rows:
            track = self._conn.execute(
                "SELECT latitude, longitude FROM trip_positions WHERE trip_id=? ORDER BY id",
                (t["id"],)).fetchall()
            if len(track) < 2:
                continue
            gps_km = _gps_track_km(track)
            if gps_km < 0.5:
                self._conn.execute(
                    "UPDATE trips SET distance_km=?, efficiency_kwh_100km=NULL WHERE id=?",
                    (round(gps_km, 2), t["id"]))
                log.info("Trip #%d: odometer-quantization repair — 1.0 km → %.2f km (GPS)",
                         t["id"], gps_km)
                fixed += 1
        self._conn.commit()
        self.set_setting("trips_odo_quantize_repair_v1", "1")
        if fixed:
            log.info("Quantized-trip repair: %d trip(s) corrected", fixed)

    def _repair_odometer_trips(self) -> None:
        """One-time repair for trips logged before the odometer-zero guard. When the
        odometer signal (1318) was missing on the trip-start poll, start_odometer_km
        was stored as 0, so the trip recorded the car's ENTIRE mileage (e.g. a 3-min
        hop showing 6441 km, inflating day/month totals and efficiency). Signature:
        start odometer 0/NULL and distance == the end odometer. Recompute distance
        from the GPS track; drop sub-0.5 km hops; refresh efficiency to match."""
        if self.get_setting("trips_odo_repair_v1") == "1":
            return
        bad = self._conn.execute(
            """SELECT id, start_soc, end_soc, end_odometer_km, vehicle_id
               FROM trips
               WHERE (start_odometer_km IS NULL OR start_odometer_km = 0)
                 AND end_odometer_km > 1
                 AND distance_km >= end_odometer_km - 1"""
        ).fetchall()
        fixed = deleted = cleared = 0
        for t in bad:
            rows = self._conn.execute(
                "SELECT latitude, longitude FROM trip_positions WHERE trip_id = ? ORDER BY id",
                (t["id"],),
            ).fetchall()
            gps_km = _gps_track_km(rows)
            has_gps = sum(1 for r in rows if _coord_valid(r["latitude"], r["longitude"])) >= 2
            if gps_km < 0.5:
                if has_gps:                        # a real few-metre short hop → drop it
                    self.delete_trip(t["id"])
                    deleted += 1
                else:                              # no GPS track → distance UNKNOWN; clear the bogus
                    self._conn.execute(            # odometer-mileage value but KEEP the trip
                        "UPDATE trips SET distance_km = NULL, efficiency_kwh_100km = NULL WHERE id = ?",
                        (t["id"],))
                    cleared += 1
                continue
            energy = ((t["start_soc"] or 0) - (t["end_soc"] or 0)) / 100.0 * self.get_battery_capacity(t["vehicle_id"])
            eff = (energy / gps_km * 100) if energy > 0 else None
            self._conn.execute(
                "UPDATE trips SET distance_km = ?, efficiency_kwh_100km = ? WHERE id = ?",
                (round(gps_km, 2), round(eff, 2) if eff else None, t["id"]),
            )
            fixed += 1
        self._conn.commit()
        self.set_setting("trips_odo_repair_v1", "1")
        if fixed or deleted or cleared:
            log.info("Trip odometer repair: %d recomputed from GPS, %d dropped (<0.5 km), %d kept (no GPS, distance cleared)",
                     fixed, deleted, cleared)

    def _repair_snap_to_full_charges(self) -> None:
        """One-time repair for charges finalized before the snap-to-full fix. On charges
        that end at 100% the BMS snaps the displayed SoC to 100.0 with zero energy
        delivered in the very poll where charging stops (top-of-charge recalibration),
        so energy_added_kwh (ΔSoC × capacity) over-stated by ~15% and the Charges page
        showed an impossible >100% efficiency next to the wallbox AC figure. Recompute
        those charges from the last SoC sampled while still charging. Charges whose cost
        was billed on the DC estimate (no wallbox AC) get the cost rescaled at the SAME
        original €/kWh; wallbox-billed (HOME + ac_energy_kwh) costs are untouched.
        Reconstructed charges and charges without surviving position samples are kept
        as they are (nothing better is available for them)."""
        if self.get_setting("charges_soc_snap_repair_v1") == "1":
            return
        rows = self._conn.execute(
            """SELECT * FROM charges
               WHERE ended_at IS NOT NULL AND end_soc >= 100.0
                 AND start_soc IS NOT NULL AND COALESCE(reconstructed, 0) = 0"""
        ).fetchall()
        fixed = 0
        for c in rows:
            last = self._last_charging_soc(c["vehicle_id"], c["started_at"], c["ended_at"])
            if last is None or last >= c["end_soc"]:
                continue
            old_e = c["energy_added_kwh"]
            new_e = round(max((last - c["start_soc"]) / 100.0 * self.get_battery_capacity(c["vehicle_id"]), 0), 3)
            if old_e is None or abs(new_e - old_e) < 0.001:
                continue
            new_cost = c["cost"]
            billed_on_ac = bool(c["ac_energy_kwh"]) and c["location_type"] == "HOME"
            # MANUAL = a user-entered total paid → never rescale it (still recompute the energy,
            # which only makes the manual €/kWh more accurate).
            if not billed_on_ac and c["location_type"] != "MANUAL" and c["cost"] and old_e > 0:
                new_cost = round(c["cost"] / old_e * new_e, 2)
            self._conn.execute("UPDATE charges SET energy_added_kwh=?, cost=? WHERE id=?",
                               (new_e, new_cost, c["id"]))
            log.info("Charge #%d: snap-to-full repair — %.3f→%.3f kWh%s",
                     c["id"], old_e, new_e,
                     "" if new_cost == c["cost"] else f" | cost {c['cost']}→{new_cost}")
            fixed += 1
        self._conn.commit()
        self.set_setting("charges_soc_snap_repair_v1", "1")
        if fixed:
            log.info("Snap-to-full charge repair: %d charge(s) recomputed", fixed)

    def _drop_phantom_charges(self) -> None:
        """One-time cleanup mirroring the live finalize_charge guard: remove charges already in the
        DB that delivered NOTHING — no SoC gained AND no wallbox-measured energy — left by a brief
        plug / charge-state blip (e.g. a charge schedule change, signal 1149 flicking 0→2→0) before
        the guard existed. STRICTLY deliver-nothing: any SoC gain (energy_added_kwh) OR any wallbox
        energy (ac_energy_kwh) keeps the row, so a real charge is never touched. Runs once."""
        if self.get_setting("charges_phantom_cleanup_v1") == "1":
            return
        n = self._conn.execute(
            "DELETE FROM charges WHERE ended_at IS NOT NULL AND COALESCE(reconstructed, 0) = 0 "
            "AND COALESCE(energy_added_kwh, 0) <= 0.05 AND COALESCE(ac_energy_kwh, 0) <= 0.05"
        ).rowcount
        self.set_setting("charges_phantom_cleanup_v1", "1")
        self._conn.commit()
        if n:
            log.info("Phantom-charge cleanup: dropped %d empty charge(s) (no SoC, no wallbox energy)", n)

    def _repair_phantom_zero_soc_charges(self) -> None:
        """One-time cleanup for the spurious-SoC=0 bug (now fixed at source in client.get_status): a
        poll that returned no SoC signal parsed as soc=0.0, got saved as a positions row with soc=0
        while the car still had range, and made the live reconstruction + the 'recover missed charges'
        scan invent a phantom 'charged from 0%'. Null the bogus soc=0 rows (so the scan — which filters
        soc IS NOT NULL — and the SoC charts ignore them) and delete the reconstructed charges that
        started at ~0% (a real EV charge never starts empty). Runs once."""
        if self.get_setting("charges_zero_soc_repair_v1") == "1":
            return
        nulled = self._conn.execute(
            "UPDATE positions SET soc=NULL WHERE soc=0 AND COALESCE(range_km, 0) > 5"
        ).rowcount
        dropped = self._conn.execute(
            "DELETE FROM charges WHERE COALESCE(reconstructed, 0)=1 AND COALESCE(start_soc, 0) < 1"
        ).rowcount
        self.set_setting("charges_zero_soc_repair_v1", "1")
        self._conn.commit()
        if nulled or dropped:
            log.info("Zero-SoC phantom repair: nulled %d bogus soc=0 position(s), dropped %d phantom charge(s)",
                     nulled, dropped)

    def _repair_negative_efficiency(self) -> None:
        """One-time cleanup: some trip rows got a NEGATIVE efficiency_kwh_100km (SoC ROSE over the
        'trip' — e.g. a trip window mis-bounded across a charge, often from an offline/session gap).
        A quantize-repair path computed it without the energy>0 guard that finalize_trip uses (now
        fixed). A negative value made the Statistics 'best efficiency' (a MIN) show nonsense like
        -39 kWh/100km. Null those so every efficiency stat skips them (NULL is already ignored). Runs once."""
        if self.get_setting("trips_neg_efficiency_repair_v1") == "1":
            return
        n = self._conn.execute(
            "UPDATE trips SET efficiency_kwh_100km = NULL WHERE efficiency_kwh_100km < 0"
        ).rowcount
        self.set_setting("trips_neg_efficiency_repair_v1", "1")
        self._conn.commit()
        if n:
            log.info("Negative-efficiency repair: nulled %d trip(s) with efficiency < 0", n)

    def _repair_reev_engine_efficiency(self) -> None:
        """One-time cleanup (GitHub beta #10): a REEV trip where the range-extender RAN has no valid
        electric kWh/100km — the generator recharges the pack mid-drive, so the trip's net SoC change
        isn't the motor's traction energy, and the stored figure (SoC- or getEC-over-full-distance) came
        out diluted/near-zero (gm27271's 0.5 vs the car's ~19). NULL the efficiency AND its SoC backup so
        nothing misleading shows and every stat skips them. Detected purely by a fuel-level drop, so BEVs
        (fuel NULL) and pure-electric REEV trips (fuel flat) are untouched. Runs once."""
        if self.get_setting("trips_reev_engine_eff_repair_v1") == "1":
            return
        n = self._conn.execute(
            "UPDATE trips SET efficiency_kwh_100km = NULL, efficiency_soc = NULL "
            "WHERE fuel_start_pct IS NOT NULL AND fuel_end_pct IS NOT NULL "
            "AND fuel_start_pct - fuel_end_pct > ?", (_REEV_FUEL_MIN_DROP,)).rowcount
        self.set_setting("trips_reev_engine_eff_repair_v1", "1")
        self._conn.commit()
        if n:
            log.info("REEV engine-on efficiency repair: nulled %d trip(s) (beta #10)", n)

    def _repair_bogus_wallbox_energy(self) -> None:
        """One-time cleanup mirroring the live finalize_charge guard (GitHub #46): fix charges whose
        stored wallbox energy (ac_energy_kwh) is physically impossible — a counter that reported a
        lifetime TOTAL as the session delta (e.g. tens of thousands of kWh for a 15-minute charge),
        inflating both the energy shown and the cost. Null the bad AC figure so the charge bills on
        the DC (SoC) energy, and rescale a cost that was billed on that AC figure to the SAME €/kWh.
        Only touches rows that clear the (generous) physical ceiling, so a real charge is never hit."""
        if self.get_setting("charges_wb_energy_repair_v1") == "1":
            return
        rows = self._conn.execute(
            "SELECT * FROM charges WHERE ended_at IS NOT NULL AND ac_energy_kwh IS NOT NULL "
            "AND ac_energy_kwh > 0").fetchall()
        fixed = 0
        for c in rows:
            ac = c["ac_energy_kwh"]
            if ac <= _wb_energy_ceiling(c["max_power_kw"], (c["duration_min"] or 0) / 60):
                continue
            dc = c["energy_added_kwh"] or 0
            new_cost = c["cost"]
            # The cost was billed on the bogus AC energy → rescale onto the DC energy at the same
            # effective €/kWh (mirrors _repair_snap_to_full_charges). Untyped/zero-cost rows are left,
            # and a MANUAL (user-entered) cost is never rescaled.
            if c["location_type"] != "MANUAL" and c["cost"] and ac > 0:
                new_cost = round(c["cost"] / ac * dc, 2)
            self._conn.execute("UPDATE charges SET ac_energy_kwh=NULL, cost=? WHERE id=?",
                               (new_cost, c["id"]))
            log.info("Charge #%d: bogus wallbox energy %.1f kWh dropped → DC billing%s",
                     c["id"], ac, "" if new_cost == c["cost"] else f" | cost {c['cost']}→{new_cost}")
            fixed += 1
        self._conn.commit()
        self.set_setting("charges_wb_energy_repair_v1", "1")
        if fixed:
            log.info("Wallbox-energy repair: %d charge(s) fixed (implausible counter)", fixed)

    # ── Settings ─────────────────────────────────────────────────────────────

    def get_setting(self, key: str, default: str = "") -> str:
        row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value))
        )
        self._conn.commit()

    def get_secret(self, key: str, default: str = "") -> str:
        """Read a secret setting, decrypting transparently (plaintext passes through)."""
        return crypto.decrypt(self.get_setting(key, default))

    def set_secret(self, key: str, value: str) -> None:
        """Write a secret setting encrypted at rest."""
        self.set_setting(key, crypto.encrypt(value or ""))

    def get_abrp_token(self, vin: str = "") -> str:
        """The ABRP token for THIS car (#186). ABRP's own model is one token per vehicle.

        🔴 It used to be one token for the install, sent from inside the per-car poll loop: two cars
        would have pushed position, SoC and speed into the SAME ABRP vehicle, interleaved. Not "it
        doesn't work" — it quietly corrupts the record with two cars pretending to be one.

        ⚠️ Falls back to the install-wide token, never to the OTHER car's: a car with no token of its
        own and nothing shared sends **nothing**, because guessing here recreates the very mixing
        this exists to stop. → [[signal-absent-is-not-signal-zero]]

        🔴 And that install-wide fallback is itself off-limits once there are TWO cars, which is the
        same door from the other side: neither car has its own token, both fall back to the shared
        one, and two cars' positions and SoCs land in a single ABRP vehicle again. The shared token
        only exists on installs that predate multi-vehicle, which is exactly the upgrade path where
        this bites. With one car there is nothing to mix, so the fallback stays — removing it there
        would silently switch ABRP off for people it works for today. (Same call
        `kerniger/leapmotor-ha` made in its 0.7.0 beta: the account-wide token is not copied onto
        every vehicle.)"""
        if vin:
            own = self.get_secret(f"abrp_token_{str(vin).lower()}", "")
            if own:
                return own
        if self._vehicle_count() > 1:
            return ""
        return self.get_secret("abrp_token", "")

    def _vehicle_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM vehicles").fetchone()
        return (row["n"] if row else 0) or 0

    def migrate_shared_ready_automation(self) -> str:
        """Twin of migrate_shared_abrp_token, for the ready-automation config: once a second car
        exists the install-wide blob belongs to the car that was here when it was written, and the
        newcomer starts with none rather than inheriting a climate answer meant for another car."""
        if self._vehicle_count() < 2:
            return ""
        shared = self.get_setting("ready_automation", "")
        if not shared:
            return ""
        row = self._conn.execute("SELECT vin FROM vehicles ORDER BY id LIMIT 1").fetchone()
        vin = (row["vin"] if row else "") or ""
        if vin and not self.get_setting(f"ready_automation_{vin.lower()}", ""):
            self.set_setting(f"ready_automation_{vin.lower()}", shared)
        self.set_setting("ready_automation", "")
        return vin

    def migrate_shared_abrp_token(self) -> str:
        """Once a second car exists, give the install-wide ABRP token to the car that was here
        first and drop the shared key. Returns that VIN, or '' when there was nothing to do.

        The shared token is not "everyone's": the Settings form only shows the car selector once
        there are two cars, so a single-car install has ALWAYS stored its token there — new ones
        included. The day a second car is registered, both cars would answer to it, and two cars'
        positions and SoCs would land in one ABRP vehicle. Which car was first is not a guess: the
        rows are keyed by VIN and keep their id, so the lowest id is the car that was already here
        when that token was typed.

        Run at registration, where the one-car → two-cars transition is visible. Idempotent, and it
        never overwrites a car's own token — a shared key surviving next to a per-car one is a
        leftover, so it is dropped rather than applied."""
        if self._vehicle_count() < 2:
            return ""
        shared = self.get_secret("abrp_token", "")
        if not shared:
            return ""
        row = self._conn.execute("SELECT vin FROM vehicles ORDER BY id LIMIT 1").fetchone()
        vin = (row["vin"] if row else "") or ""
        if vin and not self.get_secret(f"abrp_token_{vin.lower()}", ""):
            self.set_abrp_token(shared, vin)
        self.set_secret("abrp_token", "")
        return vin

    def set_abrp_token(self, token: str, vin: str) -> None:
        self.set_secret(f"abrp_token_{str(vin).lower()}", token or "")

    def get_charge_limit_percent(self, vin: str = "") -> str:
        """The charge ceiling this car last REPORTED. Per car because it is read from the car, and
        because an MQTT charge-schedule with no target SoC falls back to it — so one shared value
        would set car A's plan to car B's ceiling, a number its owner never typed anywhere."""
        if vin:
            own = self.get_setting(f"charge_limit_percent_{str(vin).lower()}", "")
            if own:
                return own
        return self.get_setting("charge_limit_percent", "")

    def set_charge_limit_percent(self, pct, vin: str) -> None:
        self.set_setting(f"charge_limit_percent_{str(vin).lower()}", str(pct))

    def get_own_charge_limit_percent(self, vin: str) -> str:
        """The per-VIN value ONLY — no shared fallback. The poll loop's "changed?" guard reads this,
        so a legacy shared `charge_limit_percent` (older versions, or the Set-limit button) that
        happens to match the car's setting can't make it skip writing the per-VIN key the Overview
        hero reads. A fresh install had that key; an upgraded one silently never got it."""
        return self.get_setting(f"charge_limit_percent_{str(vin).lower()}", "") if vin else ""

    def set_boost(self, vin: str, until: float) -> None:
        """Poll this car fast for a minute after a command, so its state syncs quickly.

        Per car: shared, a command sent to the car in the garage would also wake the one on the
        motorway — small in itself, but it spends the other car's cloud budget and muddles what the
        log says was happening to which car."""
        self.set_setting(f"boost_until_{str(vin).lower()}", str(until))

    def boosting(self, vin: str) -> bool:
        try:
            return time.time() < float(self.get_setting(f"boost_until_{str(vin).lower()}", "0") or 0)
        except (TypeError, ValueError):
            return False

    def set_charge_schedule(self, vin: str, enabled: bool, start: str, end: str = "") -> None:
        """This car's charging plan, as read from the cloud. Per car (#186): one key meant the car
        polled last overwrote the other, and the Scheduling page then showed one plan under both."""
        v = str(vin).lower()
        self.set_setting(f"charge_sched_enabled_{v}", "1" if enabled else "0")
        self.set_setting(f"charge_sched_start_{v}", start or "")
        self.set_setting(f"charge_sched_end_{v}", end or "")

    def set_gps_signs(self, vin: str, lat: str, lon: str) -> None:
        """The hemisphere this car's coordinates are in, learned from its own history.

        🔴 Per car, and this is the defect that has come back FIVE times — a car plotted in the sea
        because the sign was guessed. It is a firmware quirk, so two cars on one account can
        genuinely differ: one shared key means the second car's learning overwrites the first's, and
        that car goes back into the Gulf of Guinea. → [[car-plotted-in-the-sea-longitude-sign]]"""
        v = str(vin).lower()
        self.set_setting(f"gps_lat_sign_{v}", lat)
        self.set_setting(f"gps_lon_sign_{v}", lon)

    def get_gps_signs(self, vin: str = "") -> dict:
        """This car's remembered signs, falling back to the shared ones — which is what every
        install alive today has, and they must keep working or a single-car map moves on update."""
        out = {}
        for axis in ("lat", "lon"):
            own = self.get_setting(f"gps_{axis}_sign_{str(vin).lower()}", "") if vin else ""
            out[axis] = own or self.get_setting(f"gps_{axis}_sign", "unknown")
        return out

    def get_operate_pin(self, vin: str = "") -> str:
        """The PIN that authorises a command ON THIS CAR — its own, else the install-wide one (#186).

        The twin of web/db_reader.get_operate_pin; a test holds the two to the same answers. It
        matters here more than there: MQTT is not scoped to a picked car, the topic names the VIN,
        and a command can arrive for either — so authorising with the wrong car's four digits fails
        only sometimes, and never says why."""
        if vin:
            own = self.get_secret(f"leapmotor_pin_{str(vin).lower()}", "")
            if own:
                return own
        return self.get_secret("leapmotor_pin", "") or os.environ.get("LEAPMOTOR_PIN", "")

    def migrate_secrets(self) -> None:
        """One-time, idempotent: encrypt any plaintext secret in place. Runs every
        start; empty and already-encrypted values are skipped so re-runs are no-ops.
        The first real secret lazily triggers key generation (crypto.encrypt)."""
        for key in SECRET_KEYS:
            val = self.get_setting(key)          # raw value, no decrypt
            if not val or crypto.is_encrypted(val):
                continue
            self.set_setting(key, crypto.encrypt(val))
            log.info("Encrypted secret at rest: %s", key)

    def _check_decryption(self) -> None:
        """Warn loudly if a secret is stored encrypted but can't be decrypted with the
        current key (e.g. a DB restored WITHOUT its /data/secret.key, or a changed
        MATE_SECRET_KEY) — otherwise it only surfaces later as an obscure login failure."""
        # Asked of crypto directly. This used to test whether `decrypt` handed the ciphertext back,
        # which stopped being true the moment decrypt started returning "" on failure (#227) — the
        # warning would have gone quiet with nothing to show for it.
        for key in SECRET_KEYS:
            if not crypto.can_decrypt(self.get_setting(key)):
                log.error("Cannot decrypt stored secret '%s': wrong or missing "
                          "/data/secret.key. Restore the key together with the database, "
                          "or re-run setup to re-enter credentials.", key)
                return

    def prune_positions(self, retention_days: int) -> int:
        """Delete non-charging GPS samples older than retention_days (0/None = keep
        forever). Charging rows are kept so charge power curves survive; trips and their
        trip_positions are a separate table and are never touched. VACUUMs when rows were
        actually removed. Returns the number of rows deleted."""
        if not retention_days or retention_days <= 0:
            return 0
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        cur = self._conn.execute(
            "DELETE FROM positions WHERE recorded_at < ? AND COALESCE(charging, 0) = 0",
            (cutoff,),
        )
        self._conn.commit()
        deleted = cur.rowcount or 0
        if deleted > 0:
            self._conn.execute("VACUUM")
            log.info("Pruned %d old positions rows (retention %dd) and reclaimed space",
                     deleted, retention_days)
        return deleted

    # ── Research / BetaTester mode (MateBetaTesterOnly build) ──────────────────
    def insert_raw_signal_changes(self, vehicle_id, ts_ms: int, changed: dict) -> int:
        """Append the raw signals that CHANGED value this poll (delta logging — lean, and
        it pins the exact moment each signal moved, which is what we correlate against the
        tester's logbook). `changed` = {sig_key: value}. Returns rows inserted."""
        if not changed:
            return 0
        rows = [(vehicle_id, int(ts_ms), str(k), None if v is None else str(v))
                for k, v in changed.items()]
        self._conn.executemany(
            "INSERT INTO raw_signals_log (vehicle_id, ts, sig_key, value) VALUES (?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()
        return len(rows)

    def prune_raw_signals(self, retention_days: int) -> int:
        """Drop raw-signal rows older than retention_days (0 = keep forever) so the beta
        capture can't grow unbounded. Returns rows deleted."""
        if not retention_days or retention_days <= 0:
            return 0
        cutoff_ms = int((datetime.now(timezone.utc) - timedelta(days=retention_days)).timestamp() * 1000)
        cur = self._conn.execute("DELETE FROM raw_signals_log WHERE ts < ?", (cutoff_ms,))
        self._conn.commit()
        return cur.rowcount or 0

    def last_energy_snapshot(self, vin: str):
        """Most recent counter-ledger row for this VIN (None on a fresh install) — the sampler
        uses its taken_at both as the 24h throttle and as the getEC window's begin."""
        return self._conn.execute(
            "SELECT * FROM energy_counter_snapshots WHERE vin = ? ORDER BY taken_at DESC LIMIT 1",
            (vin,),
        ).fetchone()

    def insert_energy_snapshot(self, vin: str, taken_at: str, total_energy_kwh,
                               total_mileage_km, ec_driving_kwh, ec_ac_kwh,
                               ec_other_kwh, ec_status: str) -> int:
        """Append one counter-ledger row, as served by the cloud (raw ledger — no correction)."""
        cur = self._conn.execute(
            "INSERT INTO energy_counter_snapshots (vin, taken_at, total_energy_kwh,"
            " total_mileage_km, ec_driving_kwh, ec_ac_kwh, ec_other_kwh, ec_status)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (vin, taken_at, total_energy_kwh, total_mileage_km,
             ec_driving_kwh, ec_ac_kwh, ec_other_kwh, ec_status),
        )
        self._conn.commit()
        # lastrowid is Optional only for a cursor that last ran something other than an INSERT;
        # this one just inserted into a table with an INTEGER PRIMARY KEY, so it is the new id.
        return cur.lastrowid  # type: ignore[return-value]

    def get_or_create_device_id(self) -> str:
        """One stable device_id for this Mate install, shared by poller and web.
        Leapmotor binds sessions per device on the shared app cert — a random
        device_id per login (the library default) kept evicting other clients
        (e.g. the HA integration). INSERT OR IGNORE so concurrent processes converge
        on the same value instead of racing to overwrite it."""
        import uuid
        did = self.get_setting("mate_device_id")
        if not did:
            self._conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                ("mate_device_id", uuid.uuid4().hex),
            )
            self._conn.commit()
            did = self.get_setting("mate_device_id")
        return did

    def get_battery_capacity(self, vehicle_id: Optional[int] = None) -> float:
        """Usable pack size in kWh. PER-VEHICLE when a vehicle_id is given (vehicles.capacity_kwh):
        energy is computed as ΔSoC × capacity, and a B10's ~65 kWh vs a T03's ~36 kWh differ ~80% —
        so once >1 car shares an account each MUST use its own, or every trip/charge kWh written for
        the second car is wrong at the source. Falls back to the legacy global setting (then the
        constant) when the id is absent or the row has none, so single-car callers are unchanged."""
        if vehicle_id is not None:
            row = self._conn.execute(
                "SELECT capacity_kwh FROM vehicles WHERE id = ?", (vehicle_id,)).fetchone()
            if row and row["capacity_kwh"] is not None:
                return float(row["capacity_kwh"])
        return float(self.get_setting("battery_capacity_kwh", str(BATTERY_CAPACITY_FALLBACK)))

    def get_abilities(self, vehicle_id: Optional[int] = None) -> Optional[list]:
        """The car's DECLARED ability codes (VehicleAbility ints), or None if not reported yet.
        The MQTT bridge gates ability-dependent command buttons on these (e.g. hide 'Unlock Charge
        Cable' on a T03, which never declares code 53 — #142). None → never hide on a guess."""
        row = (self._conn.execute("SELECT abilities FROM vehicles WHERE id = ?", (vehicle_id,)).fetchone()
               if vehicle_id is not None else
               self._conn.execute("SELECT abilities FROM vehicles ORDER BY id LIMIT 1").fetchone())
        if not row or not row["abilities"]:
            return None
        try:
            return [int(a) for a in json.loads(row["abilities"])]
        except (ValueError, TypeError):
            return None

    def get_car_type(self, vehicle_id: Optional[int] = None) -> str:
        """The car's model (car_type, e.g. 'T03'/'B10'), or '' if unknown. The MQTT bridge gates
        per-model-absent entities on this (e.g. hide heated-seat entities on a T03 — #144)."""
        row = (self._conn.execute("SELECT car_type FROM vehicles WHERE id = ?", (vehicle_id,)).fetchone()
               if vehicle_id is not None else
               self._conn.execute("SELECT car_type FROM vehicles ORDER BY id LIMIT 1").fetchone())
        return (row["car_type"] if row and row["car_type"] else "")

    def never_reported_temps(self, vehicle_id: Optional[int] = None) -> set:
        """Which temperature sensors THIS car has never once reported — keyed by MQTT TOPIC (#144).

        The same measurement the status card hides its rows on, asked here because @staffhotel-beep's
        discussion is titled *"Unsupported entities for T03 model"*: the complaint is about the Home
        Assistant entities, and hiding a row in the web UI while leaving an entity that reads
        `unknown` for ever answers half of it. The seat entities went the same way in v2.6.1 — a
        retained empty config, and HA drops them.

        🔑 On the DATA, not on the model: `car_type` gates the seats because the CAR declares those
        abilities, but nothing declares a temperature sensor. "Never in the last 500 polls" is a
        measurement about this car; "T03s have no cabin sensor" would be a guess about every T03
        from one owner's. → [[a-feature-switch-must-gate-the-data]]

        ⚠️ Keys are the MQTT topic names, which differ from the web's (`ac_target_temp` here,
        `ac_target` there) because they are the entity ids HA already has. The two are held to the
        same three COLUMNS by a test — that is the only thing stopping them from drifting apart.
        """
        cols = ", ".join(f"SUM({c} IS NOT NULL) AS {k}" for k, c in ABSENT_TEMP_COLUMNS.items())
        try:
            row = self._conn.execute(
                f"SELECT COUNT(*) AS n, {cols} FROM ("
                f"  SELECT {', '.join(ABSENT_TEMP_COLUMNS.values())} FROM positions"
                "   WHERE vehicle_id = COALESCE(?, vehicle_id) ORDER BY id DESC LIMIT ?)",
                (vehicle_id, ABSENT_TEMP_WINDOW)).fetchone()
        except sqlite3.Error:
            return set()
        # Below the floor the answer is about how new the install is, not about the car. Returning
        # an empty set there is what keeps a fresh install from removing entities that work.
        if not row or int(row["n"] or 0) < ABSENT_TEMP_MIN_POLLS:
            return set()
        return {k for k in ABSENT_TEMP_COLUMNS if not int(row[k] or 0)}

    def set_battery_capacity(self, kwh: float) -> None:
        self.set_setting("battery_capacity_kwh", str(kwh))
        # Keep the single car's own row in sync so the per-vehicle write paths track the global.
        # Only mirror when there's exactly ONE vehicle: multi-car uses the per-vehicle setters, and
        # the shared global must never overwrite a second car's own capacity.
        rows = self._conn.execute("SELECT id FROM vehicles").fetchall()
        if len(rows) == 1:
            self._conn.execute("UPDATE vehicles SET capacity_kwh = ? WHERE id = ?",
                               (float(kwh), rows[0]["id"]))
            self._conn.commit()
        log.info("Battery capacity set to %.1f kWh", kwh)

    def _capacity_for_new_vehicle(self, car_type: str) -> float:
        """Pack size for a vehicle that has none stored yet. The FIRST/only car inherits the legacy
        global (it holds the setup-wizard value or a user override); an ADDITIONAL car gets its model
        default — the shared global belongs to the first car, never to a newcomer."""
        g = self.get_setting("battery_capacity_kwh")
        only = self._conn.execute("SELECT COUNT(*) AS c FROM vehicles").fetchone()["c"] <= 1
        if g and only:
            try:
                return float(g)
            except ValueError:
                pass
        return default_capacity_for(car_type or "")

    def _backfill_vehicle_capacity(self) -> None:
        """Seed every vehicle that predates the per-vehicle columns with its own capacity, so the
        per-vehicle write paths reproduce today's single-car numbers EXACTLY (no-op on upgrade).
        Idempotent — only fills NULLs. nominal (the SoH baseline) mirrors the legacy global nominal."""
        gn = self.get_setting("battery_capacity_nominal_kwh")
        for v in self._conn.execute(
                "SELECT id, car_type, capacity_kwh, capacity_nominal_kwh FROM vehicles").fetchall():
            if v["capacity_kwh"] is None:
                self._conn.execute("UPDATE vehicles SET capacity_kwh = ? WHERE id = ?",
                                   (self._capacity_for_new_vehicle(v["car_type"]), v["id"]))
            if v["capacity_nominal_kwh"] is None and gn:
                try:
                    self._conn.execute("UPDATE vehicles SET capacity_nominal_kwh = ? WHERE id = ?",
                                       (float(gn), v["id"]))
                except ValueError:
                    pass
        self._conn.commit()

    def _backfill_null_vehicle_id(self) -> None:
        """Safety net for the per-vehicle read scoping: any row with a NULL vehicle_id would be hidden
        by `WHERE vehicle_id = COALESCE(?, vehicle_id)` (NULL never equals the current id). No insert
        path writes NULL and vehicle_id has always been in the schema, so this normally finds NOTHING —
        but it GUARANTEES no trip/charge can silently vanish on upgrade. One-time (gated); assigns any
        orphan to the first (single) vehicle. Skipped until a vehicle exists (fresh install has no data
        to orphan anyway)."""
        if self.get_setting("null_vehicle_id_backfill_v1") == "1":
            return
        row = self._conn.execute("SELECT id FROM vehicles ORDER BY id LIMIT 1").fetchone()
        if row is None:
            return                                   # no vehicle yet → retry on a later start
        vid, n = row["id"], 0
        for t in ("positions", "trips", "charges", "maintenance_logs"):   # fixed table names, never input
            n += self._conn.execute(
                f"UPDATE {t} SET vehicle_id = ? WHERE vehicle_id IS NULL", (vid,)).rowcount
        self._conn.commit()
        if n:
            log.warning("Backfilled %d orphan row(s) with NULL vehicle_id → vehicle #%d "
                        "(per-vehicle scoping safety net)", n, vid)
        self.set_setting("null_vehicle_id_backfill_v1", "1")

    def _backfill_trip_geohashes(self) -> None:
        """Fill start_geohash/end_geohash on every trip that predates the column (idempotent —
        only touches NULLs, so a fresh install or a re-run after the columns already exist is a
        fast no-op). Pure math on lat/lon already stored, no network call, so unlike the
        auto-note enrichment there's no reason to defer this to a web-side background sweep."""
        rows = self._conn.execute(
            "SELECT id, start_lat, start_lon, end_lat, end_lon FROM trips "
            "WHERE (start_geohash IS NULL AND start_lat IS NOT NULL AND start_lon IS NOT NULL) "
            "OR (end_geohash IS NULL AND end_lat IS NOT NULL AND end_lon IS NOT NULL)").fetchall()
        for r in rows:
            # Same falsy guard create_trip uses, and for the same reason: _resolve_coord
            # returns 0.0 (never None) when a frame carries no usable fix, so trips with
            # start_lat/start_lon = 0.0 are already sitting in every existing database.
            # Testing `is not None` would geohash (0,0) into one bogus "Gulf of Guinea"
            # bucket — and worse, a trip whose first fix was missing would then never match
            # its own siblings on the same commute. It also means a row with one coordinate
            # NULL and the other set can no longer raise TypeError out of __init__ and stop
            # the poller from starting.
            start_gh = geohash.encode(r["start_lat"], r["start_lon"]) if (r["start_lat"] and r["start_lon"]) else None
            end_gh = geohash.encode(r["end_lat"], r["end_lon"]) if (r["end_lat"] and r["end_lon"]) else None
            self._conn.execute(
                "UPDATE trips SET start_geohash = COALESCE(start_geohash, ?), "
                "end_geohash = COALESCE(end_geohash, ?) WHERE id = ?",
                (start_gh, end_gh, r["id"]))
        if rows:
            self._conn.commit()

    def _backfill_charge_odometer(self) -> None:
        """Stamp `charges.odometer_km` on sessions recorded before the column existed (#237).

        The odometer is not looked up — it is COPIED, once, out of the position row the same poll
        wrote. Measured on a real B10: 26 of 28 charges matched with a worst-case offset of 0.0
        minutes, because `create_charge` and `save_position` both run off one frame. The two that
        did not match are older than the positions archive, and nothing can recover those.

        ⚠️ The window is deliberately TIGHT (±5 min). A generous one would look like better
        coverage while quietly stamping a charge with the odometer of a different afternoon; the
        measurement says the right matches are all within seconds, so anything further away is not
        a match that was nearly missed, it is a wrong answer. Charges typed in by hand match
        nothing at all and stay NULL — correctly: no poll of them was ever made.

        Idempotent and guarded by a one-shot setting, like every other repair here: `positions`
        can hold hundreds of thousands of rows and this must not re-scan them at every start.
        """
        if self.get_setting("charges_odometer_backfill_v1") == "1":
            return
        try:
            rows = self._conn.execute(
                "SELECT id, vehicle_id, started_at FROM charges "
                "WHERE odometer_km IS NULL AND started_at IS NOT NULL").fetchall()
        except sqlite3.Error:
            return
        filled = 0
        for r in rows:
            try:
                ts = datetime.fromisoformat(r["started_at"])
            except (TypeError, ValueError):
                continue
            lo = (ts - timedelta(minutes=_ODO_BACKFILL_WINDOW_MIN)).isoformat()
            hi = (ts + timedelta(minutes=_ODO_BACKFILL_WINDOW_MIN)).isoformat()
            # Indexed range on (vehicle_id, recorded_at). Both columns are written by _now_iso(),
            # i.e. UTC with a +00:00 offset, so the strings order the same way the instants do.
            p = self._conn.execute(
                "SELECT odometer_km FROM positions "
                " WHERE vehicle_id = ? AND recorded_at BETWEEN ? AND ? "
                "   AND odometer_km IS NOT NULL AND odometer_km > 0 "
                " ORDER BY ABS(julianday(recorded_at) - julianday(?)) LIMIT 1",
                (r["vehicle_id"], lo, hi, r["started_at"])).fetchone()
            if p:
                self._conn.execute("UPDATE charges SET odometer_km = ? WHERE id = ?",
                                   (p["odometer_km"], r["id"]))
                filled += 1
        self._conn.commit()
        self.set_setting("charges_odometer_backfill_v1", "1")
        if rows:
            log.info("Charge odometer back-fill: %d of %d recovered from positions "
                     "(the rest predate the archive or were typed in)", filled, len(rows))

    def is_setup_complete(self) -> bool:
        return self.get_setting("setup_complete") == "1"

    def mark_setup_complete(self) -> None:
        self.set_setting("setup_complete", "1")

    def factory_reset(self) -> None:
        """Destructively wipe ALL local data — every row of every table — returning the
        instance to a brand-new, unconfigured install (the setup wizard reopens). Keeps the
        schema, and the app-level TLS cert on disk (app identity, not account data) is left
        untouched, so the re-onboard only needs username/password/PIN. Run once at poller
        startup when the web 'Delete account / Factory reset' action sets the marker — the
        poller is the sole DB writer there, so the wipe can't race a concurrent poll. The whole
        wipe (including the marker itself) is one transaction, so an interrupted reset simply
        retries on the next start instead of leaving a half-wiped DB."""
        tables = [r["name"] for r in self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()]
        for t in tables:
            self._conn.execute(f'DELETE FROM "{t}"')
        try:
            self._conn.execute("DELETE FROM sqlite_sequence")   # reset AUTOINCREMENT counters
        except sqlite3.OperationalError:
            pass
        self._conn.commit()
        try:
            self._conn.execute("VACUUM")        # reclaim the space the wiped history used
            self._conn.commit()
        except sqlite3.OperationalError:
            pass
        log.warning("Factory reset complete — wiped %d tables; starting fresh setup", len(tables))

    # ── Vehicles ─────────────────────────────────────────────────────────────

    def ensure_vehicle(self, vin: str, car_type: str, year: Optional[int] = None,
                       abilities: Optional[list] = None) -> int:
        self._conn.execute(
            "INSERT OR IGNORE INTO vehicles (vin, car_type, year) VALUES (?, ?, ?)",
            (vin, car_type, year),
        )
        # Persist the car's DECLARED ability codes (VehicleAbility ints) so the diagnostic and future
        # capability-gating know what this model really supports (#67). Refreshed every start; a None
        # (lib didn't report any) leaves the stored value untouched rather than wiping it.
        if abilities:
            self._conn.execute(
                "UPDATE vehicles SET abilities = ? WHERE vin = ?",
                (json.dumps(sorted({int(a) for a in abilities})), vin),
            )
        # Seed this car's own battery capacity the first time we see it (NULL = never set). Fills only
        # NULLs, so a user override is never clobbered; the first car inherits the global (setup-wizard
        # value), an additional shared car gets its model default.
        vrow = self._conn.execute(
            "SELECT id, capacity_kwh FROM vehicles WHERE vin = ?", (vin,)).fetchone()
        if vrow and vrow["capacity_kwh"] is None:
            self._conn.execute("UPDATE vehicles SET capacity_kwh = ? WHERE id = ?",
                               (self._capacity_for_new_vehicle(car_type), vrow["id"]))
        self._conn.commit()
        row = self._conn.execute("SELECT id FROM vehicles WHERE vin = ?", (vin,)).fetchone()
        return row["id"]

    def save_position(self, vehicle_id: int, data) -> None:
        self._conn.execute(
            """INSERT INTO positions
               (vehicle_id, recorded_at, latitude, longitude, speed_kmh, odometer_km,
                soc, outside_temp, inside_temp, climate_target_temp, battery_min_temp,
                range_km, gear, charging, is_locked, climate_on,
                climate_cooling, climate_heating, climate_defrost,
                trunk_open, windows_open, sunshade_open, plug_connected,
                remaining_charge_min, charge_voltage_v, charge_current_a, ready, charge_completed, security_active,
                windows_open_count,
                door_driver_open, door_passenger_open, door_rear_left_open, door_rear_right_open,
                window_fl_open, window_rl_open, ac_port_mode,
                fan_level, recirculation, climate_mode,
                fuel_level_pct, fuel_range_km, combined_range_km,
                frame_ts, fuel_liters)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                vehicle_id, _now_iso(),
                data.latitude, data.longitude, data.speed_kmh, data.odometer_km,
                data.soc, data.outside_temp, data.inside_temp, data.climate_target_temp,
                data.battery_min_temp, data.range_km, data.gear,
                1 if data.charging_status > 0 else 0,
                1 if data.is_locked else 0,
                1 if data.climate_on else 0,
                1 if data.climate_cooling else 0,
                1 if data.climate_heating else 0,
                1 if data.climate_defrost else 0,
                1 if data.trunk_open else 0,
                1 if data.windows_open else 0,
                1 if data.sunshade_open else 0,
                1 if data.plug_connected else 0,
                data.remaining_charge_min or None,
                data.charge_voltage_v or None,
                data.charge_current_a or None,
                1 if data.ready else 0,
                1 if data.charge_completed else 0,
                None if data.security_active is None else (1 if data.security_active else 0),
                sum(1 for w in (data.window_fl_open, data.window_fr_open,
                                data.window_rl_open, data.window_rr_open) if w),
                1 if data.door_driver_open else 0,
                1 if data.door_passenger_open else 0,
                1 if data.door_rear_left_open else 0,
                1 if data.door_rear_right_open else 0,
                1 if data.window_fl_open else 0,
                1 if data.window_rl_open else 0,
                data.ac_port_mode,
                data.fan_level or None,
                1 if data.recirculation else 0,
                data.climate_mode,
                data.fuel_level_pct, data.fuel_range_km, data.combined_range_km,  # REEV dual-energy (NULL on BEV)
                data.timestamp_ms or None,   # the CAR's own clock on this frame (#178) — 0/absent → NULL
                getattr(data, "fuel_liters", None),   # litres the car counts itself (3263) — NULL on a BEV
            ),
        )
        self._conn.commit()

    def get_last_soc(self, vehicle_id: int):
        """The most recent recorded (soc, recorded_at) for this vehicle, or (None, None).
        Used to seed the recorder's SoC baseline across a poller restart so a charge that
        happened while the poller was down is still caught (SoC-jump reconstruction)."""
        row = self._conn.execute(
            "SELECT soc, recorded_at FROM positions WHERE vehicle_id = ? "
            "ORDER BY id DESC LIMIT 1", (vehicle_id,)).fetchone()
        if row is None or row["soc"] is None:
            return None, None
        return float(row["soc"]), row["recorded_at"]

    def get_last_odometer(self, vehicle_id: int):
        """The most recent recorded odometer (km) for this vehicle, or None. Seeds the recorder's
        odometer baseline across a poller restart so a drive that happened while the poller was DOWN is
        still caught (odometer-jump trip reconstruction). Ignores 0/glitch readings."""
        row = self._conn.execute(
            "SELECT odometer_km FROM positions WHERE vehicle_id = ? AND odometer_km > 0 "
            "ORDER BY id DESC LIMIT 1", (vehicle_id,)).fetchone()
        return float(row["odometer_km"]) if row and row["odometer_km"] else None

    def get_last_frame_ts(self, vehicle_id: int):
        """The newest cloud-frame timestamp on record for this vehicle, or None. Seeds the recorder's
        stale-frame baseline across a poller restart — the third of the three baselines, and the one
        that was missing.

        Without it the first poll after every restart can never be recognised as a repeat, so a frame
        the cloud is merely re-serving gets written as though it were fresh. @riri19's log shows it
        exactly: a restart at 18:51:03, and the newest row in his database timestamped 18:51:05 —
        carrying 116 km/h and a frame that was already 94 minutes old, for a car that had been parked
        for two hours. Rows that predate v2.13.3 have no frame_ts, hence the NOT NULL: what we want is
        the newest frame we can still identify, not the newest row."""
        row = self._conn.execute(
            "SELECT frame_ts FROM positions WHERE vehicle_id = ? AND frame_ts IS NOT NULL "
            "ORDER BY id DESC LIMIT 1", (vehicle_id,)).fetchone()
        return int(row["frame_ts"]) if row and row["frame_ts"] else None

    def dominant_coord_signs(self, vehicle_id: int, sample: int = _SIGN_SAMPLE) -> dict:
        """Which hemisphere this car's OWN history says it lives in, per axis (#232).

        The remembered sign used to be a single setting, and a single frame that arrived with its
        minus dropped overwrote it — after which every reader mirrored the car for good. rop12770's
        bundle is the proof: eighteen trip starts logged at longitude -7.2 between 1 and 5 August,
        and a `gps_lon_sign` of +1. Two weeks of evidence, beaten by one bad frame.

        So the sign is re-derived here from what is already on disk. A car cannot be in both
        hemispheres, and it cannot have driven a fortnight's worth of kilometres in a place it has
        never been: the history outvotes any one frame by construction.

        🔑 DISTINCT positions, not rows. When the car sleeps, the cloud re-serves one frozen frame
        and save_position() still records it every 30 s while parked (only DRIVING skips repeats) —
        rop12770 banked ~1900 identical rows over sixteen hours. Counted as rows, one stuck frame
        outvotes real driving; counted as places, it is worth exactly one vote, which is what it is.

        Zeros are excluded, not counted as east/north: (0, 0) is the no-GPS marker, not a position
        off the coast of Africa. Returns only the axes with a clear majority — an axis that is
        genuinely split (a car that really did move across the line) is left for the caller to
        decide, never guessed at."""
        signs: dict[str, float] = {}
        rows = self._conn.execute(
            "SELECT latitude, longitude FROM ("
            "  SELECT DISTINCT latitude, longitude, MAX(id) AS last_id FROM positions"
            "   WHERE vehicle_id = ? AND latitude IS NOT NULL AND longitude IS NOT NULL"
            "     AND latitude != 0 AND longitude != 0"
            "   GROUP BY latitude, longitude"
            "   ORDER BY last_id DESC LIMIT ?"
            ")", (vehicle_id, sample)).fetchall()
        for axis, col in (("lat", "latitude"), ("lon", "longitude")):
            neg = sum(1 for r in rows if r[col] < 0)
            pos = sum(1 for r in rows if r[col] > 0)
            if neg + pos < _SIGN_MIN_PLACES:
                continue          # too little history to outvote anything — say nothing
            if neg >= (neg + pos) * _SIGN_MAJORITY:
                signs[axis] = -1.0
            elif pos >= (neg + pos) * _SIGN_MAJORITY:
                signs[axis] = 1.0
        return signs

    # ── Trip ─────────────────────────────────────────────────────────────────

    def _default_trip_tags(self) -> tuple:
        """#119: the (drive_mode, one_pedal) a new trip is born with, from the user's app-level
        defaults. Validated the same way the manual per-trip edit is (save_trip_note), so a stray
        setting value can never land an invalid tag on a trip. Unset/invalid → None ("not set"),
        which is the historical behaviour."""
        dm = self.get_setting("default_drive_mode", "").strip().lower()
        drive_mode = dm if dm in _DRIVE_MODES else None
        op = self.get_setting("default_one_pedal", "").strip()
        one_pedal = int(op) if op in ("0", "1") else None
        return drive_mode, one_pedal

    def record_offline_gap(self, vehicle_id: int, *, started_at: str, ended_at: str,
                           odo_start: float, odo_end: float,
                           soc_start: float, soc_end: float) -> Optional[int]:
        """Kilometres that appeared while the cloud was quiet, kept apart from every trip.

        They cannot be attributed: the silence may hold the end of one drive, hours of parking and
        the beginning of another. Welding them onto the trip that opens next (what `_offline_head`
        did until v3.10.6) put them on the wrong trip AND the wrong day — a drive from yesterday
        counted under today, because the trip's start time is the moment the link returned.

        ⚠️ The SoC drop travels WITH the distance. Recording the kilometres and leaving the energy
        inside the trip would divide whole energy by partial distance, which is the SoH defect of
        v3.10.2 and #237 all over again. A RISE means a charge happened in the silence, so the
        energy is floored at zero rather than counted as consumption run backwards."""
        distance_km = round((odo_end or 0) - (odo_start or 0), 1)
        if distance_km < _OFFLINE_GAP_MIN_KM or (odo_start or 0) <= 0 or (odo_end or 0) <= 0:
            return None
        energy_kwh = max(((soc_start or 0) - (soc_end or 0)) / 100.0
                         * self.get_battery_capacity(vehicle_id), 0.0)
        cur = self._conn.execute(
            """INSERT INTO offline_gaps
               (vehicle_id, started_at, ended_at, odometer_start, odometer_end,
                distance_km, soc_start, soc_end, energy_kwh)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (vehicle_id, started_at, ended_at, odo_start, odo_end,
             distance_km, soc_start, soc_end, round(energy_kwh, 3)))
        self._conn.commit()
        log.info("Offline stretch recorded — %.1f km / %.1f%% SoC between %s and %s, attributed to "
                 "no trip", distance_km, (soc_start or 0) - (soc_end or 0), started_at, ended_at)
        return cur.lastrowid

    def create_trip(self, vehicle_id: int, data, head=None) -> int:
        """`head` (optional) is {"odometer_km", "soc"} — and since v3.8.8 also "latitude"/"longitude"
        when a usable one was held — from the last poll before the trip opened, supplied only when
        the car demonstrably drove while we couldn't see it (Recorder._offline_head, #130/#233). It
        moves the trip's START anchors back over those unseen kilometres, so the distance, the
        energy AND the place it set off from all include them. started_at is still NOT moved: the
        frozen window may hold hours of parking, so when the drive began is genuinely unknown."""
        drive_mode, one_pedal = self._default_trip_tags()
        # Where the trip STARTED, which is not where we first saw it. On a healthy link `head` is
        # None and this is the live fix, exactly as before; when the cloud was dark at departure the
        # live fix is wherever the car had got to by the time it started talking — 5 km down the
        # road, in @riri19's case (#233) — and the last parked position is the honest anchor.
        # _offline_head never puts a zero in there, so no (0,0) can arrive by this path.
        start_lat = (head or {}).get("latitude") or data.latitude
        start_lon = (head or {}).get("longitude") or data.longitude
        # start_geohash feeds the similar-trips comparator's candidate pre-filter
        # (web/db_reader.py get_similar_trips) — NULL on a missing/zero fix, same guard
        # add_trip_position uses, so a (0,0) glitch never becomes a bogus "near the
        # Gulf of Guinea" bucket.
        start_gh = geohash.encode(start_lat, start_lon) if start_lat and start_lon else None
        start_soc = head["soc"] if head else data.soc
        start_odo = head["odometer_km"] if head else data.odometer_km
        cur = self._conn.execute(
            """INSERT INTO trips (vehicle_id, started_at, start_lat, start_lon, start_geohash,
               start_soc, start_odometer_km, drive_mode, one_pedal, fuel_start_pct, fuel_start_l)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (vehicle_id, _now_iso(), start_lat, start_lon, start_gh,
             start_soc, start_odo, drive_mode, one_pedal,
             getattr(data, "fuel_level_pct", None),   # REEV Phase C — fuel % at trip start (NULL on BEV)
             getattr(data, "fuel_liters", None)),     # …and the car's own litre count (3263)
        )
        self._conn.commit()
        trip_id = cur.lastrowid
        log.info("Trip #%d started — SOC %.1f%% @ (%.4f, %.4f)%s", trip_id, start_soc or data.soc,
                 start_lat or 0.0, start_lon or 0.0,
                 " (anchored to where it set off, not where we found it)"
                 if (head or {}).get("latitude") else "")
        # lastrowid: Optional only for a cursor that last ran a non-INSERT — see insert_energy_snapshot.
        return trip_id  # type: ignore[return-value]

    def create_reconstructed_trip(self, vehicle_id: int, start_soc: float, start_odo: float,
                                  started_at: str, data) -> Optional[int]:
        """Record a DRIVE never seen live — the trip twin of create_reconstructed_charge (#29). The car
        was offline/asleep to the cloud (or the poller was down) for the WHOLE drive, so not a single
        DRIVING poll fired and the live state machine never opened a trip. The only trace is the ODOMETER
        that jumped while the car looked parked. Reconstruct distance from the odometer delta and
        energy/efficiency from the SoC delta. NO GPS exists (nothing was polled mid-drive) → start/end
        coordinates stay NULL and the map shows no route. Timing is approximate (start = last online,
        end = now). Marked reconstructed=1; ec_stable=1 pins it as un-enrichable — the cloud has NO record
        of a trip it never saw, so getEC can never convert it (get_trips_needing_ec also skips reconstructed)."""
        distance_km = round((data.odometer_km or 0) - (start_odo or 0), 1)
        if distance_km < 1.0 or distance_km > _RECONSTRUCT_MAX_TRIP_KM:   # sub-1 km blip / odometer glitch
            return None
        end_soc = data.soc
        energy = max((start_soc - end_soc) / 100.0 * self.get_battery_capacity(vehicle_id), 0)
        efficiency = round(energy / distance_km * 100, 2) if (distance_km > 0.5 and energy > 0) else None
        ended_at = _now_iso()
        try:
            duration_min = round(
                (datetime.fromisoformat(ended_at) - datetime.fromisoformat(started_at)).total_seconds() / 60, 1)
        except (TypeError, ValueError):
            duration_min = None
        # The gap is only an UPPER BOUND on the real drive time (the car may have been parked-offline
        # before/after driving). Trust the duration only if it implies a plausible average speed; a gap
        # padded with parked time (too slow) or a timestamp glitch (too fast) → NULL, so the now-counted
        # stats keep a clean avg-speed/duration while distance + energy + efficiency still contribute.
        if duration_min and duration_min > 0:
            kmh = distance_km / (duration_min / 60.0)
            if kmh < _RECONSTRUCT_TRIP_MIN_KMH or kmh > _RECONSTRUCT_TRIP_MAX_KMH:
                duration_min = None
        drive_mode, one_pedal = self._default_trip_tags()   # #119: same default as a live trip
        cur = self._conn.execute(
            """INSERT INTO trips
               (vehicle_id, started_at, ended_at, start_soc, end_soc, start_odometer_km, end_odometer_km,
                distance_km, duration_min, efficiency_kwh_100km, efficiency_soc, drive_mode, one_pedal,
                ec_stable, reconstructed)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1,1)""",
            (vehicle_id, started_at, ended_at, start_soc, end_soc, start_odo, data.odometer_km,
             distance_km, duration_min, efficiency, efficiency, drive_mode, one_pedal))
        self._conn.commit()
        trip_id = cur.lastrowid
        log.info("Trip #%d reconstructed — %.1f km | SOC %.1f→%.1f%% | ~%.2f kWh (car was offline, no GPS)",
                 trip_id, distance_km, start_soc, end_soc, energy)
        return trip_id

    def add_trip_position(self, trip_id: int, data) -> None:
        # Skip missing GPS: a (0,0) point draws the route to the Gulf of Guinea and
        # breaks fitBounds on the map. Only record real fixes.
        if not data.latitude or not data.longitude:
            return
        self._conn.execute(
            """INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude, speed_kmh, soc)
               VALUES (?,?,?,?,?,?)""",
            (trip_id, _now_iso(), data.latitude, data.longitude, data.speed_kmh, data.soc),
        )
        self._conn.commit()

    def trip_end_from_last_seen(self, trip_id: int) -> Optional[str]:
        """When the car was last actually HEARD inside this trip — the twin of
        `charge_end_from_last_charging` (#208), which trips never had.

        A trip abandoned on a frozen "gear D" frame is closed by the 30-minute guard (#233), and it
        used to be stamped with the moment the guard fired. That put up to half an hour of pure
        silence inside the trip: the duration grew, the average speed fell, and the car had been
        parked in the drive the whole time.

        🔑 Nothing new has to be recorded to know this. While DRIVING the recorder does not save a
        position for a repeated frame (#128), so the LAST `positions` row of such a trip already is
        the last thing the car said.

        The car's own clock (`frame_ts`) is preferred over ours, for the same reason the charge
        prefers it — it is the measurement's own time, not the time we happened to poll. ⚠️ And it
        is CHECKED the same way: a frame timestamp from before the trip opened (host skew, or a
        partial frame carrying someone else's clock) would end the trip before it began, so it is
        only taken when it lands inside the trip."""
        trip = self._conn.execute(
            "SELECT vehicle_id, started_at FROM trips WHERE id=?", (trip_id,)).fetchone()
        if trip is None or not trip["started_at"]:
            return None
        row = self._conn.execute(
            "SELECT recorded_at, frame_ts FROM positions"
            " WHERE vehicle_id=? AND recorded_at>=? ORDER BY recorded_at DESC LIMIT 1",
            (trip["vehicle_id"], trip["started_at"])).fetchone()
        if row is None:
            return None
        ended_at = row["recorded_at"]
        if row["frame_ts"]:
            frame_iso = datetime.fromtimestamp(int(row["frame_ts"]) / 1000, timezone.utc).isoformat()
            if frame_iso > trip["started_at"]:
                ended_at = frame_iso
        return ended_at

    def finalize_trip(self, trip_id: int, data, regen_kwh: float = 0.0,
                      end_at_override: Optional[str] = None) -> Optional[float]:
        rows = self._conn.execute(
            "SELECT latitude, longitude FROM trip_positions WHERE trip_id = ? ORDER BY id",
            (trip_id,),
        ).fetchall()
        trip = self._conn.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()

        # Distance: odometer vs GPS — full decision table (incl. the Δodo=1 manoeuvre
        # ambiguity and the missing-odometer fallbacks) lives in trip_distance_km().
        gps_km = _gps_track_km(rows)
        has_gps = len(rows) >= 2          # rows are real fixes only (add_trip_position skips (0,0))
        distance_km = trip_distance_km(gps_km, has_gps,
                                       trip["start_odometer_km"] or 0, data.odometer_km or 0)

        start_soc = trip["start_soc"]
        energy_used_kwh = (start_soc - data.soc) / 100.0 * self.get_battery_capacity(trip["vehicle_id"])
        # Withhold efficiency when net energy is <= 0 (SOC rose over the trip — regen
        # or a cloud SOC blip): a negative kWh/100km is meaningless, don't store it.
        efficiency = (energy_used_kwh / distance_km * 100) if (distance_km and distance_km > 0.5 and energy_used_kwh > 0) else None
        # REEV: if the range-extender ran (fuel dropped), the pack was recharged mid-drive, so this
        # SoC-based number isn't a real electric consumption — withhold it (beta #10). Fuel L/100 km
        # still shows in the trip detail. BEVs (fuel NULL) are unaffected.
        if _reev_extender_ran(trip["fuel_start_pct"], getattr(data, "fuel_level_pct", None)):
            efficiency = None

        # ONE end moment, used for both the stamp and the length. They were two — `_now_iso()` for
        # `ended_at` and `datetime.now()` for the duration — which agree in production and diverge
        # the instant the end is anything other than now. It is about to be.
        ended_at = end_at_override or _now_iso()
        started_at = datetime.fromisoformat(trip["started_at"])
        duration_min = (datetime.fromisoformat(ended_at) - started_at).total_seconds() / 60

        end_gh = geohash.encode(data.latitude, data.longitude) if data.latitude and data.longitude else None
        self._conn.execute(
            """UPDATE trips SET ended_at=?, end_lat=?, end_lon=?, end_geohash=?, end_soc=?,
               end_odometer_km=?, distance_km=?, duration_min=?,
               efficiency_kwh_100km=?, regen_kwh=?, fuel_end_pct=?, fuel_end_l=?
               WHERE id=?""",
            (ended_at, data.latitude, data.longitude, end_gh, data.soc,
             data.odometer_km, round(distance_km, 2) if distance_km is not None else None,
             round(duration_min, 1),
             round(efficiency, 2) if efficiency else None, round(regen_kwh, 3),
             getattr(data, "fuel_level_pct", None),   # REEV Phase C — fuel % at trip end (NULL on BEV)
             getattr(data, "fuel_liters", None),      # …and the car's own litre count (3263)
             trip_id),
        )
        self._conn.commit()
        log.info(
            "Trip #%d ended — %.1f km | SOC %.1f→%.1f%% | %.0f min | eff %.1f kWh/100km",
            trip_id, distance_km or 0, start_soc, data.soc, duration_min,
            efficiency or 0,
        )
        return distance_km

    def delete_trip(self, trip_id: int) -> None:
        """Remove a trip and its GPS points (used to drop sub-0.5 km hops)."""
        self._conn.execute("DELETE FROM trip_positions WHERE trip_id = ?", (trip_id,))
        self._conn.execute("DELETE FROM trips WHERE id = ?", (trip_id,))
        self._conn.commit()

    def delete_charge(self, charge_id: int) -> None:
        """Remove a charge session. The per-poll positions log is shared (not per-charge) → untouched."""
        self._conn.execute("DELETE FROM charges WHERE id = ?", (charge_id,))
        self._conn.commit()

    # ── Charge ───────────────────────────────────────────────────────────────

    def create_charge(self, vehicle_id: int, data) -> int:
        cur = self._conn.execute(
            """INSERT INTO charges (vehicle_id, started_at, start_soc, latitude, longitude,
                                    odometer_km)
               VALUES (?,?,?,?,?,?)""",
            (vehicle_id, _now_iso(), data.soc, data.latitude, data.longitude,
             _odo_or_none(data)),
        )
        self._conn.commit()
        charge_id = cur.lastrowid
        log.info("Charge #%d started — SOC %.1f%%", charge_id, data.soc)
        # lastrowid: Optional only for a cursor that last ran a non-INSERT — see insert_energy_snapshot.
        return charge_id  # type: ignore[return-value]

    def create_reconstructed_charge(self, vehicle_id: int, start_soc: float,
                                    started_at: str, data) -> Optional[int]:
        """Record a charge that was never seen live — the car was asleep/offline to the cloud
        during it, so no plug/current signal was ever polled and the only trace is a SoC that
        JUMPED up while parked. Insert a COMPLETE, already-closed row from the SoC delta so the
        charge isn't lost (GitHub #29). Timing is approximate (start = last known low-SoC time,
        end = now); energy = ΔSoC × capacity. Marked reconstructed=1 and typed AC (asleep charges
        are home AC — DC fast-charging keeps the car awake and reporting). max_power_kw is left
        NULL (unknown) and cost stays NULL until the user confirms the charge type, exactly like a
        live charge."""
        energy_added = max((data.soc - start_soc) / 100.0 * self.get_battery_capacity(vehicle_id), 0)
        ended_at = _now_iso()
        try:
            duration_min = round(
                (datetime.fromisoformat(ended_at) - datetime.fromisoformat(started_at))
                .total_seconds() / 60, 1)
        except (TypeError, ValueError):
            duration_min = None
        # Plausibility guards for the spurious-SoC=0 bug: a real missed/asleep charge never starts
        # at ~0% (you don't drive an EV to empty), and its energy over its real duration implies a
        # sane charge power. A glitch SoC=0 makes ΔSoC look like a full charge in seconds → reject
        # instead of inventing a phantom charge.
        if start_soc < 1.0:
            log.info("Reconstructed charge skipped — implausible start SoC %.1f%% (spurious/absent SoC)",
                     start_soc)
            return None
        if duration_min and duration_min > 0 and energy_added / (duration_min / 60.0) > _RECONSTRUCT_MAX_KW:
            log.info("Reconstructed charge skipped — implausible %.0f kW (%.1f kWh in %.1f min)",
                     energy_added / (duration_min / 60.0), energy_added, duration_min)
            return None
        cur = self._conn.execute(
            """INSERT INTO charges
               (vehicle_id, started_at, ended_at, start_soc, end_soc, energy_added_kwh,
                duration_min, latitude, longitude, charge_type, odometer_km, reconstructed)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,1)""",
            (vehicle_id, started_at, ended_at, start_soc, data.soc, round(energy_added, 3),
             duration_min, data.latitude, data.longitude, "AC", _odo_or_none(data)),
        )
        self._conn.commit()
        charge_id = cur.lastrowid
        log.info("Charge #%d reconstructed — SOC %.1f→%.1f%% | +%.1f kWh (car was asleep/offline)",
                 charge_id, start_soc, data.soc, energy_added)
        return charge_id

    def _learned_wallbox_location(self, vehicle_id: int):
        """Where the home wallbox is, inferred from the charges where it MEASURED real energy
        (> _WB_HOME_MIN_KWH rules out standby creep, so the sample can't be self-poisoned by the
        very artefact this guards against). Median lat/lon → one stray GPS fix can't move it.
        None until _WB_HOME_MIN_SAMPLES such charges exist, so a fresh install behaves as before."""
        rows = self._conn.execute(
            "SELECT latitude, longitude FROM charges "
            "WHERE vehicle_id = ? AND latitude IS NOT NULL AND longitude IS NOT NULL "
            "AND COALESCE(ac_energy_kwh, 0) > ?",
            (vehicle_id, _WB_HOME_MIN_KWH)).fetchall()
        if len(rows) < _WB_HOME_MIN_SAMPLES:
            return None
        lats = sorted(r["latitude"] for r in rows)
        lons = sorted(r["longitude"] for r in rows)
        return lats[len(lats) // 2], lons[len(lons) // 2]

    def wallbox_energy_applies(self, vehicle_id: int, lat, lon) -> bool:
        """Should this charge's location trust the home wallbox counter? False ONLY when we KNOW the
        charge happened far from the learned wallbox — there its counter (idle, or creeping on
        standby) is not this charge's energy. True whenever the wallbox isn't located yet or the
        charge has no GPS: never drop a legitimate home charge, only skip one known to be elsewhere."""
        home = self._learned_wallbox_location(vehicle_id)
        if home is None or lat is None or lon is None:
            return True
        return haversine_km(lat, lon, home[0], home[1]) <= _WB_HOME_RADIUS_KM

    def set_charge_wallbox_start(self, charge_id: int, kwh: float) -> None:
        """Seed the wallbox energy tracking at charge START: store the first counter reading and reset
        the running total to 0. From here accumulate_wallbox_energy() sums the counter's positive rises."""
        self._conn.execute(
            "UPDATE charges SET wallbox_energy_start_kwh=?, ac_energy_kwh=0 WHERE id=?", (kwh, charge_id))
        self._conn.commit()

    def accumulate_wallbox_energy(self, charge_id: int, reading: float,
                                  car_kwh_since_last: float = 0.0) -> None:
        """Add the wallbox counter's POSITIVE rise since the last reading to the charge's running total
        (ac_energy_kwh), called every poll while charging. Race/reset-proof: a counter that zeroes
        mid-session is a single negative step (ignored) and the post-reset rise is still counted — so it
        works whether the counter is a lifetime total or resets each session, and no matter WHEN it
        resets relative to our polls. wallbox_energy_start_kwh holds the last reading seen (the running
        baseline), persisted so the sum survives a poller restart mid-charge.

        `car_kwh_since_last` is what the CAR says it drew over this poll (its own power × the poll
        interval), and it is only used to catch a counter that has stopped (#215, see _WB_STUCK_KWH):
        while the counter does not move, that energy piles up in wb_stuck_kwh. A real rise clears it
        again — unless it has already passed the threshold, in which case it LATCHES: energy the meter
        missed while frozen stays missed even if the meter later comes back to life."""
        row = self._conn.execute(
            "SELECT wallbox_energy_start_kwh AS last, ac_energy_kwh AS accum, "
            "started_at, max_power_kw, wb_stuck_kwh AS stuck FROM charges WHERE id=?",
            (charge_id,)).fetchone()
        if row is None:
            return
        last, accum = row["last"], (row["accum"] or 0.0)
        stuck = row["stuck"] or 0.0
        moved = last is not None and reading > last
        if moved:
            if stuck < _WB_STUCK_KWH:
                stuck = 0.0                      # counter alive again, and it never went far enough
        elif car_kwh_since_last > 0:
            stuck += car_kwh_since_last          # flat counter while the car draws → count what it missed
        if last is not None and reading >= last:
            rise = reading - last
            # Physical guard (GitHub #46): a single step that exceeds what the wallbox could deliver
            # since the charge started is not energy — it's the counter jumping to a lifetime total
            # (e.g. it read ~0 at plug-in). Skip that step but still advance the baseline, so the
            # real per-poll rises AFTER it are counted normally (the session self-corrects).
            elapsed_h = 0.0
            if row["started_at"]:
                try:
                    elapsed_h = (datetime.now(timezone.utc)
                                 - datetime.fromisoformat(row["started_at"])).total_seconds() / 3600
                except (TypeError, ValueError):
                    elapsed_h = 0.0
            if rise <= _wb_energy_ceiling(row["max_power_kw"], elapsed_h):
                accum += rise
            else:
                log.warning("Charge #%d: ignoring implausible wallbox step +%.1f kWh "
                            "(counter glitch / lifetime total)", charge_id, rise)
        self._conn.execute(
            "UPDATE charges SET wallbox_energy_start_kwh=?, ac_energy_kwh=?, wb_stuck_kwh=? "
            "WHERE id=?", (reading, round(accum, 3), round(stuck, 3), charge_id))
        self._conn.commit()

    def _last_charging_soc(self, vehicle_id: int, started_at: str, ended_at: str | None = None):
        """Last SoC sampled while charging=1 within the charge window, or None.
        The B10 BMS snaps the displayed SoC to 100.0 in the very poll where charging
        flips off — top-of-charge recalibration that adds ~0.9% SoC with zero energy
        delivered — so the post-stop SoC over-states ΔSoC-based energy by ~15% on
        100%-ending charges (the "107% efficiency" artifact). Mid-charge samples are
        immune: their last charging SoC equals the end SoC.
        The window MUST be bounded on both sides: without the upper bound a recompute
        of an old charge would pick up charging samples from LATER charges."""
        row = self._conn.execute(
            "SELECT soc FROM positions WHERE vehicle_id=? AND charging=1 AND soc IS NOT NULL"
            " AND recorded_at>=? AND recorded_at<=? ORDER BY recorded_at DESC LIMIT 1",
            (vehicle_id, started_at, ended_at or _now_iso())).fetchone()
        return row["soc"] if row else None

    def _charging_end_in_window(self, vehicle_id: int, started_at: str, before: str | None = None):
        """(soc, ended_at) from the last position taken WHILE CHARGING in [started_at, before).

        Charging rows only. The rows AFTER a charge belong to whatever the car did next — for
        @mikeeeeekoo (#208) a whole morning of driving, which would close his overnight charge at
        92.3 % and half past noon. `ended_at` is the CAR's own clock when the frame carries one:
        while the cloud re-serves a frozen snapshot our `recorded_at` keeps advancing and the car's
        does not, and the car's is the one that says when the charge really stopped."""
        # Stop at the end of THIS session, not at the last charging sample in the database. Without
        # this the search walks forward for as long as no later charge row exists to cap it, and
        # @mikeeeeekoo's overnight charge (#208) closed on the first sample of that EVENING's
        # plug-in — 17:10 at 80.7 %, seventeen hours, for a charge that ended at 06:10 at 100 %.
        # The first sample that says "not charging" ends the session; a contiguity bound, not a
        # time one, because that is what "this session" actually means.
        # NULL is left as not-breaking on purpose: it means "we don't know", and rows old enough to
        # predate the column would otherwise close every historical charge at its own start.
        stop = self._conn.execute(
            "SELECT MIN(recorded_at) AS s FROM positions WHERE vehicle_id=? AND recorded_at>? "
            "AND charging = 0", (vehicle_id, started_at)).fetchone()
        stop = stop["s"] if stop else None
        if stop and (not before or stop < before):
            before = stop
        sql = ("SELECT soc, recorded_at, frame_ts FROM positions WHERE vehicle_id=? AND charging=1 "
               "AND soc IS NOT NULL AND recorded_at>=?")
        args = [vehicle_id, started_at]
        if before:
            sql += " AND recorded_at<?"
            args.append(before)
        row = self._conn.execute(sql + " ORDER BY recorded_at DESC LIMIT 1", args).fetchone()
        if row is None:
            return None
        ended_at = row["recorded_at"]
        if row["frame_ts"]:
            frame_iso = datetime.fromtimestamp(int(row["frame_ts"]) / 1000, timezone.utc).isoformat()
            # Only ahead of the start: a frame_ts from before the charge opened (clock skew, or a
            # partial frame carrying someone else's timestamp) would invert the session.
            if frame_iso > started_at and (not before or frame_iso < before):
                ended_at = frame_iso
        return row["soc"], ended_at

    def charge_end_from_last_charging(self, charge_id: int):
        """The last reading taken WHILE CHARGING in this charge's window, as (soc, ended_at).

        For a charge the car drove away from (#208) this is the only honest end: by the time Mate
        sees the car again its SoC has moved on, and the frames in between may be the cloud
        re-serving one frozen snapshot. That snapshot is still a real measurement — so the time
        comes from the CAR's own clock (`frame_ts`) rather than from when we happened to poll it,
        which on @mikeeeeekoo's overnight charge is the difference between 06:10 and 09:36.

        Returns None when the charge has no charging sample at all (nothing to stand on)."""
        charge = self._conn.execute(
            "SELECT vehicle_id, started_at FROM charges WHERE id=?", (charge_id,)).fetchone()
        if charge is None or not charge["started_at"]:
            return None
        return self._charging_end_in_window(charge["vehicle_id"], charge["started_at"])

    def finalize_charge(self, charge_id: int, data, max_power_kw: float = 0.0,
                        price_per_kwh: float = 0.0, end_override=None) -> None:
        """`end_override` = (soc, ended_at) closes the charge on a reading other than `data` —
        used when the car drove away and the live frame is no longer the end of the charge."""
        charge = self._conn.execute("SELECT * FROM charges WHERE id = ?", (charge_id,)).fetchone()
        start_soc    = charge["start_soc"]
        end_soc, end_at = (end_override if end_override else (data.soc, _now_iso()))
        # Energy from ΔSoC × capacity. ONLY on 100%-ending charges, anchor the ΔSoC to the
        # last SoC seen while still charging (see _last_charging_soc): the snap-to-full is a
        # top-of-charge phenomenon, while on mid-SoC charges the final tick (e.g. 94.9→95.0
        # in the poll where charging stops) is real energy that must stay counted.
        # end_soc itself stays data.soc — users should still see the charge reached 100%.
        soc_for_energy = end_soc
        if end_soc >= 100.0:
            last = self._last_charging_soc(charge["vehicle_id"], charge["started_at"])
            if last is not None:
                soc_for_energy = last
        energy_added = max((soc_for_energy - start_soc) / 100.0 * self.get_battery_capacity(charge["vehicle_id"]), 0)

        # Phantom-charge guard: a brief plug / charge-state blip — e.g. the car re-evaluating after
        # a charge SCHEDULE is changed, or signal 1149 flicking 0→2→0 — can open+close a "charge"
        # that delivered nothing. If it gained no SoC AND the wallbox measured no energy, it isn't a
        # real session: drop the row instead of persisting a phantom charge (a genuine charge always
        # shows one or the other). Reconstructed charges have energy by definition, so never here.
        ac_kwh = charge["ac_energy_kwh"]
        if energy_added <= 0.05 and (ac_kwh is None or ac_kwh <= 0.05) and not charge["reconstructed"]:
            self._conn.execute("DELETE FROM charges WHERE id = ?", (charge_id,))
            self._conn.commit()
            log.info("Charge #%d dropped — phantom (no SoC gained, no wallbox energy)", charge_id)
            return

        # Above this power the session is DC fast-charging. Default 11 kW (3-phase AC ceiling
        # for most home wallboxes); a 22 kW AC owner can raise it in Advanced settings so their
        # AC sessions aren't misread as DC.
        try:
            dc_min_kw = float(self.get_setting("charge_dc_min_kw", "11") or 11)
        except (TypeError, ValueError):
            dc_min_kw = 11.0
        charge_type  = "DC" if max_power_kw > dc_min_kw else "AC"
        cost         = round(energy_added * price_per_kwh, 2) if price_per_kwh else None

        # ac_energy_kwh is NOT touched here — it's the running wallbox-counter sum built up over the
        # charge by accumulate_wallbox_energy() (the wallbox-billed energy).
        started_at   = datetime.fromisoformat(charge["started_at"])
        duration_min = (datetime.fromisoformat(end_at) - started_at).total_seconds() / 60

        self._conn.execute(
            """UPDATE charges
               SET ended_at=?, end_soc=?, energy_added_kwh=?, duration_min=?,
                   charge_type=?, max_power_kw=?, cost=?
               WHERE id=?""",
            (
                end_at, end_soc, round(energy_added, 3), round(duration_min, 1),
                charge_type, round(max_power_kw, 2), cost,
                charge_id,
            ),
        )
        self._conn.commit()
        # Backstop for the per-poll guard (GitHub #46): if the final wallbox total is still
        # physically impossible for this session, the counter was unreliable — drop it so the
        # charge bills on the DC (SoC) energy instead of an absurd AC figure.
        if ac_kwh is not None and ac_kwh > _wb_energy_ceiling(max_power_kw, duration_min / 60):
            self._conn.execute("UPDATE charges SET ac_energy_kwh=NULL WHERE id=?", (charge_id,))
            self._conn.commit()
            log.warning("Charge #%d: dropped implausible wallbox energy %.1f kWh (kept DC billing)",
                        charge_id, ac_kwh)
        # The mirror of it (#215): the counter did not run away, it STOPPED. wb_stuck_kwh is the
        # energy the car itself reported drawing while the reading never moved — past the threshold
        # the meter missed more than any meter's resolution could explain, so the total it ended on
        # is short by an unknown amount. Drop it and bill on the DC (SoC) energy, which is at least
        # complete. NB deliberately NOT a comparison against that DC figure: see _WB_STUCK_KWH.
        elif (row_stuck := (charge["wb_stuck_kwh"] or 0.0)) >= _WB_STUCK_KWH and ac_kwh:
            self._conn.execute("UPDATE charges SET ac_energy_kwh=NULL WHERE id=?", (charge_id,))
            self._conn.commit()
            log.warning("Charge #%d: wallbox counter stood still through %.1f kWh the car reported "
                        "drawing — dropped its %.1f kWh total (kept DC billing)",
                        charge_id, row_stuck, ac_kwh)
        log.info(
            "Charge #%d ended — SOC %.1f→%.1f%% | +%.1f kWh | %.0f min | %s | peak %.1f kW",
            charge_id, start_soc, end_soc, energy_added, duration_min,
            charge_type, max_power_kw,
        )

    # ── Startup cleanup ───────────────────────────────────────────────────

    def close_orphan_trips(self, vehicle_id: int) -> int:
        """
        Called at poller startup. Finalizes any trip left open by a previous
        crash using the last recorded trip_position as the end point.
        Returns number of trips closed.
        """
        orphans = self._conn.execute(
            "SELECT id, start_soc, start_odometer_km, started_at FROM trips "
            "WHERE vehicle_id = ? AND ended_at IS NULL",
            (vehicle_id,),
        ).fetchall()

        closed = 0
        for trip in orphans:
            trip_id = trip["id"]
            last_pos = self._conn.execute(
                "SELECT * FROM trip_positions WHERE trip_id = ? ORDER BY id DESC LIMIT 1",
                (trip_id,),
            ).fetchone()

            positions = self._conn.execute(
                "SELECT latitude, longitude FROM trip_positions WHERE trip_id = ? ORDER BY id",
                (trip_id,),
            ).fetchall()

            if not last_pos or len(positions) < 2:
                # Not enough data — delete the orphan
                self._conn.execute("DELETE FROM trip_positions WHERE trip_id = ?", (trip_id,))
                self._conn.execute("DELETE FROM trips WHERE id = ?", (trip_id,))
                log.warning("Trip #%d had no usable positions — deleted", trip_id)
            else:
                # Filter (0,0)/null-island and out-of-range fixes before summing — a single bad
                # point slipping in before a crash would otherwise add a virtual round-trip to the
                # equator and wreck the trip's distance. Mirrors the normal path (_gps_track_km).
                distance_km = _gps_track_km(positions)
                start_soc = trip["start_soc"] or 0
                end_soc   = float(last_pos["soc"] or start_soc)
                energy    = (start_soc - end_soc) / 100.0 * self.get_battery_capacity(vehicle_id)
                # Withhold efficiency when net energy is <= 0 (SoC rose over the trip — e.g. a
                # window mis-bounded across a charge); a negative value poisons the Stats best/avg.
                efficiency = (energy / distance_km * 100) if (distance_km > 0.5 and energy > 0) else None

                started_at   = datetime.fromisoformat(trip["started_at"])
                ended_at_iso = last_pos["recorded_at"]
                # REEV: if the range-extender ran over this trip (fuel % dropped in the positions trail),
                # the SoC-based efficiency is meaningless — the generator recharges the pack mid-drive —
                # so withhold it (beta #10). trip_positions carries no fuel, so read it from positions.
                if efficiency is not None:
                    _fr = self._conn.execute(
                        "SELECT fuel_level_pct FROM positions WHERE vehicle_id=? AND recorded_at BETWEEN ? AND ? "
                        "AND fuel_level_pct IS NOT NULL ORDER BY recorded_at",
                        (vehicle_id, trip["started_at"], ended_at_iso)).fetchall()
                    if len(_fr) >= 2 and (_fr[0]["fuel_level_pct"] - _fr[-1]["fuel_level_pct"]) > _REEV_FUEL_MIN_DROP:
                        efficiency = None
                ended_at_dt  = datetime.fromisoformat(ended_at_iso)
                duration_min = (ended_at_dt - started_at).total_seconds() / 60

                end_gh = geohash.encode(last_pos["latitude"], last_pos["longitude"])
                self._conn.execute(
                    """UPDATE trips SET ended_at=?, end_lat=?, end_lon=?, end_geohash=?, end_soc=?,
                       distance_km=?, duration_min=?, efficiency_kwh_100km=?
                       WHERE id=?""",
                    (
                        ended_at_iso,
                        last_pos["latitude"], last_pos["longitude"], end_gh, end_soc,
                        round(distance_km, 3), round(duration_min, 1),
                        round(efficiency, 2) if efficiency else None,
                        trip_id,
                    ),
                )
                log.warning(
                    "Trip #%d was open (crash recovery) — closed at last known position "
                    "%.1f km | %.0f min",
                    trip_id, distance_km, duration_min,
                )
            closed += 1

        if orphans:
            self._conn.commit()
        return closed

    def close_orphan_charges(self, vehicle_id: int) -> int:
        """
        Called at poller startup. Finalizes any charge session left open
        using the last recorded position as the end point.
        Returns number of charges closed.
        """
        orphans = self._conn.execute(
            "SELECT id, start_soc, started_at FROM charges "
            "WHERE vehicle_id = ? AND ended_at IS NULL",
            (vehicle_id,),
        ).fetchall()

        closed = 0
        for charge in orphans:
            # Cap the search at the next charge's start so this orphan's ended_at/end_soc come
            # from its OWN span — never from a later charge's positions. Otherwise the orphan's
            # window bleeds past the next charge and corrupts that charge's power-window/cost.
            nxt = self._conn.execute(
                "SELECT MIN(started_at) AS s FROM charges WHERE vehicle_id = ? AND started_at > ?",
                (vehicle_id, charge["started_at"]),
            ).fetchone()
            next_start = nxt["s"] if nxt else None
            if next_start:
                last_pos = self._conn.execute(
                    "SELECT soc, recorded_at FROM positions "
                    "WHERE vehicle_id = ? AND recorded_at >= ? AND recorded_at < ? "
                    "ORDER BY id DESC LIMIT 1",
                    (vehicle_id, charge["started_at"], next_start),
                ).fetchone()
            else:
                last_pos = self._conn.execute(
                    "SELECT soc, recorded_at FROM positions "
                    "WHERE vehicle_id = ? AND recorded_at >= ? ORDER BY id DESC LIMIT 1",
                    (vehicle_id, charge["started_at"]),
                ).fetchone()

            # Prefer the last reading taken WHILE CHARGING: an orphan is usually discovered long
            # after the fact, and by then `last_pos` is whatever the car has been doing since
            # (#208 — a morning of driving would close an overnight charge at 92 % and midday).
            charging_end = self._charging_end_in_window(vehicle_id, charge["started_at"], next_start)
            if charging_end:
                end_soc, ended_at_iso = charging_end
            else:
                end_soc      = float((last_pos["soc"] if last_pos else None) or charge["start_soc"] or 0)
                ended_at_iso = (last_pos["recorded_at"] if last_pos else None) or next_start or _now_iso()
            energy_added = max((end_soc - charge["start_soc"]) / 100.0 * self.get_battery_capacity(vehicle_id), 0)

            started_at   = datetime.fromisoformat(charge["started_at"])
            ended_at_dt  = datetime.fromisoformat(ended_at_iso)
            duration_min = (ended_at_dt - started_at).total_seconds() / 60

            self._conn.execute(
                "UPDATE charges SET ended_at=?, end_soc=?, energy_added_kwh=?, duration_min=? WHERE id=?",
                (ended_at_iso, end_soc, round(energy_added, 3), round(duration_min, 1), charge["id"]),
            )
            log.warning(
                "Charge #%d was open (crash recovery) — closed: SOC %.1f→%.1f%% +%.1f kWh",
                charge["id"], charge["start_soc"], end_soc, energy_added,
            )
            closed += 1

        if orphans:
            self._conn.commit()
        return closed

    def get_open_charge(self, vehicle_id: int):
        """Latest charge session left open (ended_at NULL), or None."""
        return self._conn.execute(
            "SELECT id, start_soc, max_power_kw, started_at, latitude, longitude FROM charges "
            "WHERE vehicle_id = ? AND ended_at IS NULL ORDER BY id DESC LIMIT 1",
            (vehicle_id,),
        ).fetchone()

    def get_open_trip(self, vehicle_id: int):
        """Latest trip left open (ended_at NULL), or None."""
        return self._conn.execute(
            "SELECT id FROM trips WHERE vehicle_id = ? AND ended_at IS NULL ORDER BY id DESC LIMIT 1",
            (vehicle_id,),
        ).fetchone()

    def update_charge_max_power(self, charge_id: int, max_power_kw: float) -> None:
        """Persist the running peak power so it survives a poller restart mid-charge."""
        self._conn.execute(
            "UPDATE charges SET max_power_kw = ? WHERE id = ?",
            (round(max_power_kw, 2), charge_id),
        )
        self._conn.commit()

    def close(self):
        self._conn.close()
