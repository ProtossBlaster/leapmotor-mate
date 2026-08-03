"""SQLite database layer. Switch DATABASE_URL to postgresql://... for production."""
import json
import logging
import math
import sqlite3
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
    "C10": 69.9,   # RWD (EU; gross 72.0)
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

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vehicles (
    id          INTEGER PRIMARY KEY,
    vin         TEXT UNIQUE NOT NULL,
    car_type    TEXT,
    year        INTEGER,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS positions (
    id           INTEGER PRIMARY KEY,
    vehicle_id   INTEGER REFERENCES vehicles(id),
    recorded_at  TEXT NOT NULL,
    latitude     REAL,
    longitude    REAL,
    speed_kmh    REAL,
    odometer_km  REAL,
    soc                 REAL,
    outside_temp        REAL,
    inside_temp         REAL,
    climate_target_temp REAL,
    battery_min_temp    REAL,
    range_km            REAL,
    gear             TEXT,
    charging         INTEGER DEFAULT 0,
    is_locked        INTEGER DEFAULT NULL,
    climate_on       INTEGER DEFAULT NULL,
    climate_cooling  INTEGER DEFAULT NULL,
    climate_heating  INTEGER DEFAULT NULL,
    climate_defrost  INTEGER DEFAULT NULL,
    trunk_open       INTEGER DEFAULT NULL,
    windows_open     INTEGER DEFAULT NULL,
    sunshade_open    INTEGER DEFAULT NULL,
    plug_connected   INTEGER DEFAULT NULL,
    ready            INTEGER DEFAULT NULL,
    charge_completed INTEGER DEFAULT NULL,
    security_active  INTEGER DEFAULT NULL,
    windows_open_count INTEGER DEFAULT NULL,
    door_driver_open     INTEGER DEFAULT NULL,
    door_passenger_open  INTEGER DEFAULT NULL,
    door_rear_left_open  INTEGER DEFAULT NULL,
    door_rear_right_open INTEGER DEFAULT NULL,
    window_fl_open       INTEGER DEFAULT NULL,
    window_rl_open       INTEGER DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS trips (
    id                   INTEGER PRIMARY KEY,
    vehicle_id           INTEGER REFERENCES vehicles(id),
    started_at           TEXT,
    ended_at             TEXT,
    start_lat            REAL,
    start_lon            REAL,
    end_lat              REAL,
    end_lon              REAL,
    distance_km          REAL,
    start_soc            REAL,
    end_soc              REAL,
    start_odometer_km    REAL,
    end_odometer_km      REAL,
    regen_kwh            REAL DEFAULT 0,
    duration_min         REAL,
    efficiency_kwh_100km REAL,
    efficiency_soc       REAL,                    -- backup of the SoC-derived efficiency (EC override is reversible)
    ec_kwh               REAL,                    -- cloud getEC total for this trip (driving energy)
    ec_driving           REAL,
    ec_ac                REAL,
    ec_other             REAL,
    ec_tried             INTEGER DEFAULT 0,       -- EC enrichment attempts (cloud aggregation lags a fresh trip)
    ec_stable            INTEGER DEFAULT 0,       -- 1 once the cloud EC stabilised (two equal reads) → stop re-fetching
    merged_into_id       INTEGER DEFAULT NULL,
    note                 TEXT,                    -- #107: optional free-text user note (traffic, weather, road…)
    drive_mode           TEXT,                    -- #107: manual tag — 'comfort' / 'normal' / 'sport' (not in cloud)
    one_pedal            INTEGER                  -- #107: manual tag — 1 on / 0 off / NULL not set (not in cloud)
);

CREATE TABLE IF NOT EXISTS trip_positions (
    id          INTEGER PRIMARY KEY,
    trip_id     INTEGER REFERENCES trips(id),
    recorded_at TEXT NOT NULL,
    latitude    REAL NOT NULL,
    longitude   REAL NOT NULL,
    speed_kmh   REAL,
    soc         REAL
);

CREATE TABLE IF NOT EXISTS charges (
    id               INTEGER PRIMARY KEY,
    vehicle_id       INTEGER REFERENCES vehicles(id),
    started_at       TEXT,
    ended_at         TEXT,
    start_soc        REAL,
    end_soc          REAL,
    energy_added_kwh REAL,
    duration_min     REAL,
    latitude         REAL,
    longitude        REAL,
    charge_type      TEXT DEFAULT 'AC',        -- AC / DC (from power level)
    location_type    TEXT DEFAULT NULL,         -- HOME / AC / FAST / HPC (user-set)
    max_power_kw     REAL,
    cost             REAL,
    ac_energy_kwh    REAL,         -- wallbox energy a HOME charge is billed on = sum of the counter's rises
    wallbox_energy_start_kwh REAL, -- last wallbox counter reading seen (running baseline for that sum)
    wb_stuck_kwh     REAL,         -- #215: kWh the CAR reported drawing while the counter never moved
    note             TEXT          -- #107: optional free-text user note (location, shade, weather…)
);

CREATE TABLE IF NOT EXISTS maintenance_logs (
    id               INTEGER PRIMARY KEY,
    vehicle_id       INTEGER REFERENCES vehicles(id),
    service_type     TEXT NOT NULL,             -- matches a pack item's service_type
    done_date        TEXT NOT NULL,             -- ISO date the service was performed
    done_odometer_km REAL,                       -- odometer at the service (prefilled with current)
    note             TEXT,
    created_at       TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_positions_vehicle ON positions(vehicle_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_trip_positions_trip ON trip_positions(trip_id);
CREATE INDEX IF NOT EXISTS idx_trips_vehicle ON trips(vehicle_id, started_at);
CREATE INDEX IF NOT EXISTS idx_charges_vehicle ON charges(vehicle_id, started_at);
CREATE INDEX IF NOT EXISTS idx_maintenance_vehicle ON maintenance_logs(vehicle_id, service_type);
-- Charge/Wallbox queries (power curve, time-of-use cost split, "has power" EXISTS)
-- filter charging=1 and range/scan recorded_at; a small partial index keeps them
-- fast as `positions` grows to millions of rows (~8% of rows are charging=1).
CREATE INDEX IF NOT EXISTS idx_positions_charging_recorded ON positions(recorded_at) WHERE charging = 1;

-- Research / BetaTester mode only (MateBetaTesterOnly build). Full raw-signal history (delta:
-- one row per signal that changed value), plus the tester's logbook. Empty/unused in the
-- normal build. Pruned by retention so it can't grow unbounded.
CREATE TABLE IF NOT EXISTS raw_signals_log (
    id          INTEGER PRIMARY KEY,
    vehicle_id  INTEGER,
    ts          INTEGER NOT NULL,   -- epoch ms (signal timestamp)
    sig_key     TEXT NOT NULL,      -- raw Leapmotor signal id, e.g. "3235"
    value       TEXT
);
CREATE INDEX IF NOT EXISTS idx_raw_signals_ts ON raw_signals_log(ts);

CREATE TABLE IF NOT EXISTS research_logbook (
    id          INTEGER PRIMARY KEY,
    ts          INTEGER NOT NULL,   -- epoch ms the note was added
    note        TEXT NOT NULL       -- e.g. "engine started to charge while driving", "refueled to 100%"
);

-- Daily ledger of the car's OFFICIAL lifetime counters (cloud mileage/energy/detail: totalEnergy
-- includes parked/standby, integer kWh) plus the getEC driving split over the window since the
-- previous snapshot. Δ between two rows = total consumption incl. parked, error ≤ ±1 kWh at the
-- window edges REGARDLESS of span (counter sampling — errors don't accumulate); Δ − getEC = the
-- parked/standby share. Raw readings, stored as served, never corrected in place: counter resets
-- and cloud gaps are handled at READ time (total_increasing-style). Silent phase-1 collector, no
-- UI yet.
CREATE TABLE IF NOT EXISTS energy_counter_snapshots (
    id                INTEGER PRIMARY KEY,
    vin               TEXT NOT NULL,
    taken_at          TEXT NOT NULL,     -- UTC ISO
    total_energy_kwh  INTEGER,           -- lifetime consumption counter, integer kWh as served
    total_mileage_km  REAL,              -- from the 0.1-mile field ×1.609344 (finer than the km int)
    ec_driving_kwh    REAL,              -- getEC over [previous snapshot's taken_at, taken_at]
    ec_ac_kwh         REAL,
    ec_other_kwh      REAL,
    ec_status         TEXT               -- 'first' | 'ok' | 'empty' (no driving) | 'miss' (cloud gap)
);
CREATE INDEX IF NOT EXISTS idx_energy_snap_vin_taken ON energy_counter_snapshots(vin, taken_at);
"""

# self.get_battery_capacity() is now stored in settings table, not hardcoded


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        self._conn.executescript(SCHEMA)
        # migration: add battery_min_temp if missing (existing DBs)
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(positions)").fetchall()}
        if "climate_target_temp" not in cols:
            self._conn.execute("ALTER TABLE positions ADD COLUMN climate_target_temp REAL")
        if "battery_min_temp" not in cols:
            self._conn.execute("ALTER TABLE positions ADD COLUMN battery_min_temp REAL")
        if "is_locked" not in cols:
            self._conn.execute("ALTER TABLE positions ADD COLUMN is_locked INTEGER DEFAULT NULL")
        if "climate_on" not in cols:
            self._conn.execute("ALTER TABLE positions ADD COLUMN climate_on INTEGER DEFAULT NULL")
        if "climate_cooling" not in cols:
            self._conn.execute("ALTER TABLE positions ADD COLUMN climate_cooling INTEGER DEFAULT NULL")
        if "climate_heating" not in cols:
            self._conn.execute("ALTER TABLE positions ADD COLUMN climate_heating INTEGER DEFAULT NULL")
        if "climate_defrost" not in cols:
            self._conn.execute("ALTER TABLE positions ADD COLUMN climate_defrost INTEGER DEFAULT NULL")
        if "trunk_open" not in cols:
            self._conn.execute("ALTER TABLE positions ADD COLUMN trunk_open INTEGER DEFAULT NULL")
        if "windows_open" not in cols:
            self._conn.execute("ALTER TABLE positions ADD COLUMN windows_open INTEGER DEFAULT NULL")
        if "sunshade_open" not in cols:
            self._conn.execute("ALTER TABLE positions ADD COLUMN sunshade_open INTEGER DEFAULT NULL")
        if "plug_connected" not in cols:
            self._conn.execute("ALTER TABLE positions ADD COLUMN plug_connected INTEGER DEFAULT NULL")
        if "remaining_charge_min" not in cols:
            self._conn.execute("ALTER TABLE positions ADD COLUMN remaining_charge_min INTEGER DEFAULT NULL")
        if "charge_voltage_v" not in cols:
            self._conn.execute("ALTER TABLE positions ADD COLUMN charge_voltage_v REAL DEFAULT NULL")
        if "charge_current_a" not in cols:
            self._conn.execute("ALTER TABLE positions ADD COLUMN charge_current_a REAL DEFAULT NULL")
        if "ready" not in cols:
            self._conn.execute("ALTER TABLE positions ADD COLUMN ready INTEGER DEFAULT NULL")
        if "charge_completed" not in cols:
            self._conn.execute("ALTER TABLE positions ADD COLUMN charge_completed INTEGER DEFAULT NULL")
        if "security_active" not in cols:
            self._conn.execute("ALTER TABLE positions ADD COLUMN security_active INTEGER DEFAULT NULL")
        if "windows_open_count" not in cols:
            self._conn.execute("ALTER TABLE positions ADD COLUMN windows_open_count INTEGER DEFAULT NULL")
        # Per-door + left-side window state (the live Overview car image; the poller already computes
        # these — see car_image.py). Names are fixed literals, never user input.
        for _c in ("door_driver_open", "door_passenger_open", "door_rear_left_open",
                   "door_rear_right_open", "window_fl_open", "window_rl_open"):
            if _c not in cols:
                self._conn.execute(f"ALTER TABLE positions ADD COLUMN {_c} INTEGER DEFAULT NULL")
        # migration: AC-port / V2L mode (signal 47). 0 idle / 1 AC charging / 2 V2L discharge. Lets the
        # V2L monitor read per-poll mode AND lets get_vampire_drain EXCLUDE V2L periods (a parked V2L
        # discharge must NOT be counted as standby/vampire drain).
        if "ac_port_mode" not in cols:
            self._conn.execute("ALTER TABLE positions ADD COLUMN ac_port_mode INTEGER DEFAULT NULL")
        # migration: extended climate panel (validated on-car 2026-06-20) — fan level (1941 acAirVolume,
        # 1-7), recirculation (1943: 1=recirc / 0=fresh), base climate mode (3713: 0 auto/1 cool/3 heat/4 vent).
        if "fan_level" not in cols:
            self._conn.execute("ALTER TABLE positions ADD COLUMN fan_level INTEGER DEFAULT NULL")
        if "recirculation" not in cols:
            self._conn.execute("ALTER TABLE positions ADD COLUMN recirculation INTEGER DEFAULT NULL")
        if "climate_mode" not in cols:
            self._conn.execute("ALTER TABLE positions ADD COLUMN climate_mode INTEGER DEFAULT NULL")
        # migration: REEV dual-energy for the live Overview — fuel tank level % (3235), range on fuel
        # alone (3259), and combined battery+fuel range (3261). All NULL on a BEV; the status card shows
        # them only when present (range-extender models). range_km stays the EV-only range (3260).
        for _c in ("fuel_level_pct", "fuel_range_km", "combined_range_km"):
            if _c not in cols:
                self._conn.execute(f"ALTER TABLE positions ADD COLUMN {_c} REAL DEFAULT NULL")
        # migration: the litres the car itself counts (signal 3263, millilitres → litres). Everything
        # in litres was until now a percentage multiplied by an ASSUMED tank size, and the assumption
        # was wrong on the C10 (47.5 L, not 50) — so every litre figure a C10 owner ever saw was 5 %
        # high. With this the car does the counting. NULL on a BEV and on every row written before.
        if "fuel_liters" not in cols:
            self._conn.execute("ALTER TABLE positions ADD COLUMN fuel_liters REAL DEFAULT NULL")
        # migration: the CAR's own timestamp on the frame this row came from (signal sts, or 1) — #178.
        # `recorded_at` is when MATE wrote the row, which is always a few seconds ago; it says nothing
        # about how old the data inside it is. When the car can't reach the cloud, the cloud keeps
        # re-serving the last frame it got, so the row is new and its contents are not. Storing the
        # frame's own time is what lets the web tell those two apart. NULL where the car doesn't
        # report it (and on every row written before this column existed).
        if "frame_ts" not in cols:
            self._conn.execute("ALTER TABLE positions ADD COLUMN frame_ts INTEGER DEFAULT NULL")
        # migration: the car's DECLARED ability codes (VehicleAbility ints, stored as a JSON list) —
        # lets the diagnostic + future capability-gating show ONLY what a model actually supports,
        # instead of assuming every car has the same commands (#67; also covers models we don't own
        # yet, e.g. the B05). Refreshed by ensure_vehicle on every poller start.
        vcols = {r[1] for r in self._conn.execute("PRAGMA table_info(vehicles)").fetchall()}
        if "abilities" not in vcols:
            self._conn.execute("ALTER TABLE vehicles ADD COLUMN abilities TEXT DEFAULT NULL")
        # migration: PER-VEHICLE battery capacity (usable + as-new nominal for SoH). Capacity is a
        # vehicle attribute — energy is written as ΔSoC×capacity — so with >1 car per account each
        # needs its OWN (a B10's ~65 kWh vs a T03's ~36 kWh is ~80% off, and it corrupts the STORED
        # trip/charge energy, not just the display). _backfill_vehicle_capacity() then seeds existing
        # rows from the legacy global so a single-car install stays byte-identical.
        if "capacity_kwh" not in vcols:
            self._conn.execute("ALTER TABLE vehicles ADD COLUMN capacity_kwh REAL DEFAULT NULL")
        if "capacity_nominal_kwh" not in vcols:
            self._conn.execute("ALTER TABLE vehicles ADD COLUMN capacity_nominal_kwh REAL DEFAULT NULL")
        # migration: per-charge wallbox AC energy (the "wallbox, to pay" figure) on existing DBs
        ccols = {r[1] for r in self._conn.execute("PRAGMA table_info(charges)").fetchall()}
        if "ac_energy_kwh" not in ccols:
            self._conn.execute("ALTER TABLE charges ADD COLUMN ac_energy_kwh REAL")
        if "wallbox_energy_start_kwh" not in ccols:
            self._conn.execute("ALTER TABLE charges ADD COLUMN wallbox_energy_start_kwh REAL")
        # migration: #215 — energy the car reported drawing while the wallbox counter stood still
        if "wb_stuck_kwh" not in ccols:
            self._conn.execute("ALTER TABLE charges ADD COLUMN wb_stuck_kwh REAL")
        # migration: flag charges reconstructed from a SoC jump (car was asleep/offline to the
        # cloud during the charge, so it was never seen live — recorded from the SoC delta instead).
        if "reconstructed" not in ccols:
            self._conn.execute("ALTER TABLE charges ADD COLUMN reconstructed INTEGER DEFAULT 0")
        # migration: public charging-station label, resolved by the web layer from OSM
        # (web/charger_locator.py; '' = looked up, nothing found). Display-only — it never
        # feeds charge detection, costs or the HOME/AC/FAST/HPC location_type.
        if "location_name" not in ccols:
            self._conn.execute("ALTER TABLE charges ADD COLUMN location_name TEXT DEFAULT NULL")
        # migration: link back to the label's source page (openstreetmap.org / openchargemap.org),
        # when the winning candidate had one (web/charger_locator.py _osm_url / OCM's poi/details
        # URL). Display-only, like location_name — NULL on charges labelled before this column
        # existed, until they're re-swept or manually recalculated (📍 button).
        if "location_url" not in ccols:
            self._conn.execute("ALTER TABLE charges ADD COLUMN location_url TEXT DEFAULT NULL")
        # migration: #107 — optional free-text user note on a charge (station location, shade,
        # reliability, parking, weather, personal remarks). Display/context only, never computed on.
        if "note" not in ccols:
            self._conn.execute("ALTER TABLE charges ADD COLUMN note TEXT")
        # migration: #120 — mark a HOME charge as FREE (e.g. self-produced solar, or any free home
        # charge). The charge KEEPS its Home location (stays on the Home side of the Home-vs-Public
        # split) but its cost is pinned to 0 and protected from every recompute (compute_cost returns
        # 0 when is_free). "Free-away" stays the FREE location_type — this flag is HOME-only.
        if "is_free" not in ccols:
            self._conn.execute("ALTER TABLE charges ADD COLUMN is_free INTEGER DEFAULT 0")
        # migration: #188 — mark a charge the user TYPED IN (the "add a past charge" form or the CSV
        # import) as opposed to one the poller measured. location_type='MANUAL' cannot answer this:
        # it doubles as the COST BASIS someone picks to type the price of a real charge, so an edit
        # form gated on it would hand out the times and SoC of measured sessions to be rewritten.
        # Existing rows are backfilled from the signature a typed-in charge has always carried — the
        # MANUAL basis plus no telemetry whatsoever (the poller fills lat/lon at charge start and
        # duration/peak power at the end, and a reconstructed charge gets both too).
        if "manual_entry" not in ccols:
            self._conn.execute("ALTER TABLE charges ADD COLUMN manual_entry INTEGER DEFAULT 0")
            self._conn.execute(
                "UPDATE charges SET manual_entry = 1 "
                "WHERE location_type = 'MANUAL' AND COALESCE(reconstructed, 0) = 0 "
                "AND latitude IS NULL AND longitude IS NULL "
                "AND duration_min IS NULL AND max_power_kw IS NULL AND ac_energy_kwh IS NULL")
        # migration: manual trip-merge link — a child trip points to the parent it was merged into
        tcols = {r[1] for r in self._conn.execute("PRAGMA table_info(trips)").fetchall()}
        if "merged_into_id" not in tcols:
            self._conn.execute("ALTER TABLE trips ADD COLUMN merged_into_id INTEGER DEFAULT NULL")
        # migration: flag trips RECONSTRUCTED from an odometer jump (car offline/asleep to the cloud —
        # or poller down — for the whole drive, so no DRIVING poll ever fired). Twin of charges.reconstructed.
        if "reconstructed" not in tcols:
            self._conn.execute("ALTER TABLE trips ADD COLUMN reconstructed INTEGER DEFAULT 0")
        # migration: per-trip EC (driving) energy split from the cloud getEC endpoint (Phase 2).
        # efficiency_soc backs up the original SoC-derived efficiency so the EC override is fully
        # reversible; ec_tried counts enrichment attempts (cloud aggregation lags a fresh trip).
        for _c, _t in (("efficiency_soc", "REAL"), ("ec_kwh", "REAL"), ("ec_driving", "REAL"),
                       ("ec_ac", "REAL"), ("ec_other", "REAL"), ("ec_tried", "INTEGER DEFAULT 0"),
                       ("ec_stable", "INTEGER DEFAULT 0")):
            if _c not in tcols:
                self._conn.execute(f"ALTER TABLE trips ADD COLUMN {_c} {_t}")
        # migration: #107 — per-trip user note + MANUAL driving tags. drive_mode/one_pedal are user-set
        # because the Leapmotor cloud does not expose drive mode or One-Pedal (verified on-car); they
        # explain consumption differences the raw data can't. Display/context only, never computed on.
        for _c, _t in (("note", "TEXT"), ("drive_mode", "TEXT"), ("one_pedal", "INTEGER")):
            if _c not in tcols:
                self._conn.execute(f"ALTER TABLE trips ADD COLUMN {_c} {_t}")
        # migration: REEV Phase C — fuel tank level % (signal 3235) at trip start/end. The drop gives
        # the fuel burned (× tank litres) → per-trip L/100km and the EV/fuel split. NULL on a BEV.
        for _c, _t in (("fuel_start_pct", "REAL"), ("fuel_end_pct", "REAL")):
            if _c not in tcols:
                self._conn.execute(f"ALTER TABLE trips ADD COLUMN {_c} {_t}")
        # migration: the same two ends in LITRES, straight off the car's own counter (3263) instead of
        # a percentage times an assumed tank. "× tank litres" above was the whole problem — the assumed
        # tank was 50 L for everyone and a C10's is 47.5. Where these are present the burn is measured;
        # where they aren't (a BEV, or any trip recorded before this) the percentages still answer.
        for _c, _t in (("fuel_start_l", "REAL"), ("fuel_end_l", "REAL")):
            if _c not in tcols:
                self._conn.execute(f"ALTER TABLE trips ADD COLUMN {_c} {_t}")
        # migration: per-trip elevation gain/loss (metres) + outside temperature (°C), looked up
        # post-trip against Open-Meteo (the Leapmotor cloud exposes neither altitude nor an ambient
        # temperature — only lat/lon and cabin temp). Per-segment like regen_kwh — a merged group's
        # total is the sum of its segments (web/db_reader.py _trip_group_stats), so merging never
        # needs re-enrichment. elev_tried/elev_done mirror ec_tried/ec_stable but need no convergence
        # logic: terrain (and a past hour's weather) is static, one successful lookup is final.
        for _c, _t in (("elevation_gain_m", "REAL"), ("elevation_loss_m", "REAL"),
                       ("outside_temp_start_c", "REAL"), ("outside_temp_end_c", "REAL"),
                       ("elev_tried", "INTEGER DEFAULT 0"), ("elev_done", "INTEGER DEFAULT 0")):
            if _c not in tcols:
                self._conn.execute(f"ALTER TABLE trips ADD COLUMN {_c} {_t}")
        # migration: per-POINT altitude (metres) for the trip-profile chart. Only the downsampled
        # subset the enrichment sweep actually queried Open-Meteo for gets a value here — the rest
        # stay NULL and web/db_reader.py interpolates them at read time for a smooth chart line.
        tpcols = {r[1] for r in self._conn.execute("PRAGMA table_info(trip_positions)").fetchall()}
        if "elevation_m" not in tpcols:
            self._conn.execute("ALTER TABLE trip_positions ADD COLUMN elevation_m REAL")
        # migration: geohash (7 chars ≈ 150m cell) of start/end lat-lon — the "similar trips"
        # comparator's fast pre-filter (web/db_reader.py get_similar_trips groups candidates by
        # this before validating the actual route). Set at trip creation/finalize (below) going
        # forward; _backfill_trip_geohashes fills every existing trip once, offline (pure math on
        # lat/lon already stored — unlike the auto-note's reverse-geocoding, no network call, so
        # there's no reason to defer this to a web-side sweep).
        for _c in ("start_geohash", "end_geohash"):
            if _c not in tcols:
                self._conn.execute(f"ALTER TABLE trips ADD COLUMN {_c} TEXT DEFAULT NULL")
        self._conn.commit()
        self._backfill_vehicle_capacity()
        self._backfill_null_vehicle_id()
        self._backfill_trip_geohashes()
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
        for key in SECRET_KEYS:
            raw = self.get_setting(key)
            if crypto.is_encrypted(raw) and crypto.is_encrypted(crypto.decrypt(raw)):
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
                1 if data.security_active else 0,
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

    def create_trip(self, vehicle_id: int, data, head=None) -> int:
        """`head` (optional) is {"odometer_km", "soc"} from the last poll before the trip opened,
        supplied only when the car demonstrably drove while we couldn't see it — see
        Recorder._offline_head (#130). It moves the trip's START anchors back over those unseen
        kilometres so the distance and the energy include them. Position and started_at are NOT
        moved: the frozen frame's GPS is routinely 0,0, and when the drive began is unknown."""
        drive_mode, one_pedal = self._default_trip_tags()
        # start_geohash feeds the similar-trips comparator's candidate pre-filter
        # (web/db_reader.py get_similar_trips) — NULL on a missing/zero fix, same guard
        # add_trip_position uses, so a (0,0) glitch never becomes a bogus "near the
        # Gulf of Guinea" bucket.
        start_gh = geohash.encode(data.latitude, data.longitude) if data.latitude and data.longitude else None
        start_soc = head["soc"] if head else data.soc
        start_odo = head["odometer_km"] if head else data.odometer_km
        cur = self._conn.execute(
            """INSERT INTO trips (vehicle_id, started_at, start_lat, start_lon, start_geohash,
               start_soc, start_odometer_km, drive_mode, one_pedal, fuel_start_pct, fuel_start_l)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (vehicle_id, _now_iso(), data.latitude, data.longitude, start_gh,
             start_soc, start_odo, drive_mode, one_pedal,
             getattr(data, "fuel_level_pct", None),   # REEV Phase C — fuel % at trip start (NULL on BEV)
             getattr(data, "fuel_liters", None)),     # …and the car's own litre count (3263)
        )
        self._conn.commit()
        trip_id = cur.lastrowid
        log.info("Trip #%d started — SOC %.1f%% @ (%.4f, %.4f)", trip_id, data.soc, data.latitude, data.longitude)
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

    def finalize_trip(self, trip_id: int, data, regen_kwh: float = 0.0) -> Optional[float]:
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

        started_at = datetime.fromisoformat(trip["started_at"])
        duration_min = (datetime.now(timezone.utc) - started_at).total_seconds() / 60

        end_gh = geohash.encode(data.latitude, data.longitude) if data.latitude and data.longitude else None
        self._conn.execute(
            """UPDATE trips SET ended_at=?, end_lat=?, end_lon=?, end_geohash=?, end_soc=?,
               end_odometer_km=?, distance_km=?, duration_min=?,
               efficiency_kwh_100km=?, regen_kwh=?, fuel_end_pct=?, fuel_end_l=?
               WHERE id=?""",
            (_now_iso(), data.latitude, data.longitude, end_gh, data.soc,
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
            """INSERT INTO charges (vehicle_id, started_at, start_soc, latitude, longitude)
               VALUES (?,?,?,?,?)""",
            (vehicle_id, _now_iso(), data.soc, data.latitude, data.longitude),
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
                duration_min, latitude, longitude, charge_type, reconstructed)
               VALUES (?,?,?,?,?,?,?,?,?,?,1)""",
            (vehicle_id, started_at, ended_at, start_soc, data.soc, round(energy_added, 3),
             duration_min, data.latitude, data.longitude, "AC"),
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
