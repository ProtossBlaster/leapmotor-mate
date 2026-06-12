"""SQLite database layer. Switch DATABASE_URL to postgresql://... for production."""
import logging
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import crypto

log = logging.getLogger(__name__)

# USABLE (net) capacity, kWh — matches web/main.py _EU_BATTERY_MAP. Used as the
# first-run fallback if the setup wizard didn't set a per-variant value.
BATTERY_CAPACITY_DEFAULTS: dict[str, float] = {
    "T03": 36.0,   # EU only variant (gross 37.3)
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
    ready            INTEGER DEFAULT NULL
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
    merged_into_id       INTEGER DEFAULT NULL
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
    wallbox_energy_start_kwh REAL  -- last wallbox counter reading seen (running baseline for that sum)
);

CREATE INDEX IF NOT EXISTS idx_positions_vehicle ON positions(vehicle_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_trip_positions_trip ON trip_positions(trip_id);
CREATE INDEX IF NOT EXISTS idx_trips_vehicle ON trips(vehicle_id, started_at);
CREATE INDEX IF NOT EXISTS idx_charges_vehicle ON charges(vehicle_id, started_at);
-- Charge/Wallbox queries (power curve, time-of-use cost split, "has power" EXISTS)
-- filter charging=1 and range/scan recorded_at; a small partial index keeps them
-- fast as `positions` grows to millions of rows (~8% of rows are charging=1).
CREATE INDEX IF NOT EXISTS idx_positions_charging_recorded ON positions(recorded_at) WHERE charging = 1;
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
               "mqtt_pass", "geocoder_key", "ha_token"}


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
        # migration: per-charge wallbox AC energy (the "wallbox, to pay" figure) on existing DBs
        ccols = {r[1] for r in self._conn.execute("PRAGMA table_info(charges)").fetchall()}
        if "ac_energy_kwh" not in ccols:
            self._conn.execute("ALTER TABLE charges ADD COLUMN ac_energy_kwh REAL")
        if "wallbox_energy_start_kwh" not in ccols:
            self._conn.execute("ALTER TABLE charges ADD COLUMN wallbox_energy_start_kwh REAL")
        # migration: flag charges reconstructed from a SoC jump (car was asleep/offline to the
        # cloud during the charge, so it was never seen live — recorded from the SoC delta instead).
        if "reconstructed" not in ccols:
            self._conn.execute("ALTER TABLE charges ADD COLUMN reconstructed INTEGER DEFAULT 0")
        if "location_name" not in ccols:
            self._conn.execute("ALTER TABLE charges ADD COLUMN location_name TEXT DEFAULT NULL")
        # migration: manual trip-merge link — a child trip points to the parent it was merged into
        tcols = {r[1] for r in self._conn.execute("PRAGMA table_info(trips)").fetchall()}
        if "merged_into_id" not in tcols:
            self._conn.execute("ALTER TABLE trips ADD COLUMN merged_into_id INTEGER DEFAULT NULL")
        self._conn.commit()
        self._repair_odometer_trips()
        self._repair_quantized_trip_distance()
        self._repair_snap_to_full_charges()
        self._drop_phantom_charges()
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
            """SELECT id, start_soc, end_soc, end_odometer_km
               FROM trips
               WHERE (start_odometer_km IS NULL OR start_odometer_km = 0)
                 AND end_odometer_km > 1
                 AND distance_km >= end_odometer_km - 1"""
        ).fetchall()
        cap = self.get_battery_capacity()
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
            energy = ((t["start_soc"] or 0) - (t["end_soc"] or 0)) / 100.0 * cap
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
            new_e = round(max((last - c["start_soc"]) / 100.0 * self.get_battery_capacity(), 0), 3)
            if old_e is None or abs(new_e - old_e) < 0.001:
                continue
            new_cost = c["cost"]
            billed_on_ac = bool(c["ac_energy_kwh"]) and c["location_type"] == "HOME"
            if not billed_on_ac and c["cost"] and old_e > 0:
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
            # effective €/kWh (mirrors _repair_snap_to_full_charges). Untyped/zero-cost rows are left.
            if c["cost"] and ac > 0:
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

    def get_battery_capacity(self) -> float:
        return float(self.get_setting("battery_capacity_kwh", str(BATTERY_CAPACITY_FALLBACK)))

    def set_battery_capacity(self, kwh: float) -> None:
        self.set_setting("battery_capacity_kwh", str(kwh))
        log.info("Battery capacity set to %.1f kWh", kwh)

    def is_setup_complete(self) -> bool:
        return self.get_setting("setup_complete") == "1"

    def mark_setup_complete(self) -> None:
        self.set_setting("setup_complete", "1")

    # ── Vehicles ─────────────────────────────────────────────────────────────

    def ensure_vehicle(self, vin: str, car_type: str, year: Optional[int] = None) -> int:
        self._conn.execute(
            "INSERT OR IGNORE INTO vehicles (vin, car_type, year) VALUES (?, ?, ?)",
            (vin, car_type, year),
        )
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
                remaining_charge_min, charge_voltage_v, charge_current_a, ready)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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

    # ── Trip ─────────────────────────────────────────────────────────────────

    def create_trip(self, vehicle_id: int, data) -> int:
        cur = self._conn.execute(
            """INSERT INTO trips (vehicle_id, started_at, start_lat, start_lon,
               start_soc, start_odometer_km)
               VALUES (?,?,?,?,?,?)""",
            (vehicle_id, _now_iso(), data.latitude, data.longitude,
             data.soc, data.odometer_km),
        )
        self._conn.commit()
        trip_id = cur.lastrowid
        log.info("Trip #%d started — SOC %.1f%% @ (%.4f, %.4f)", trip_id, data.soc, data.latitude, data.longitude)
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

    def finalize_trip(self, trip_id: int, data, regen_kwh: float = 0.0) -> None:
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
        energy_used_kwh = (start_soc - data.soc) / 100.0 * self.get_battery_capacity()
        # Withhold efficiency when net energy is <= 0 (SOC rose over the trip — regen
        # or a cloud SOC blip): a negative kWh/100km is meaningless, don't store it.
        efficiency = (energy_used_kwh / distance_km * 100) if (distance_km and distance_km > 0.5 and energy_used_kwh > 0) else None

        started_at = datetime.fromisoformat(trip["started_at"])
        duration_min = (datetime.now(timezone.utc) - started_at).total_seconds() / 60

        self._conn.execute(
            """UPDATE trips SET ended_at=?, end_lat=?, end_lon=?, end_soc=?,
               end_odometer_km=?, distance_km=?, duration_min=?,
               efficiency_kwh_100km=?, regen_kwh=?
               WHERE id=?""",
            (_now_iso(), data.latitude, data.longitude, data.soc,
             data.odometer_km, round(distance_km, 2) if distance_km is not None else None,
             round(duration_min, 1),
             round(efficiency, 2) if efficiency else None, round(regen_kwh, 3),
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
        return charge_id

    def set_charge_location_name(self, charge_id: int, name: str) -> None:
        self._conn.execute(
            "UPDATE charges SET location_name=? WHERE id=?", (name, charge_id)
        )
        self._conn.commit()

    def create_reconstructed_charge(self, vehicle_id: int, start_soc: float,
                                    started_at: str, data) -> int:
        """Record a charge that was never seen live — the car was asleep/offline to the cloud
        during it, so no plug/current signal was ever polled and the only trace is a SoC that
        JUMPED up while parked. Insert a COMPLETE, already-closed row from the SoC delta so the
        charge isn't lost (GitHub #29). Timing is approximate (start = last known low-SoC time,
        end = now); energy = ΔSoC × capacity. Marked reconstructed=1 and typed AC (asleep charges
        are home AC — DC fast-charging keeps the car awake and reporting). max_power_kw is left
        NULL (unknown) and cost stays NULL until the user confirms the charge type, exactly like a
        live charge."""
        energy_added = max((data.soc - start_soc) / 100.0 * self.get_battery_capacity(), 0)
        ended_at = _now_iso()
        try:
            duration_min = round(
                (datetime.fromisoformat(ended_at) - datetime.fromisoformat(started_at))
                .total_seconds() / 60, 1)
        except (TypeError, ValueError):
            duration_min = None
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

    def set_charge_wallbox_start(self, charge_id: int, kwh: float) -> None:
        """Seed the wallbox energy tracking at charge START: store the first counter reading and reset
        the running total to 0. From here accumulate_wallbox_energy() sums the counter's positive rises."""
        self._conn.execute(
            "UPDATE charges SET wallbox_energy_start_kwh=?, ac_energy_kwh=0 WHERE id=?", (kwh, charge_id))
        self._conn.commit()

    def accumulate_wallbox_energy(self, charge_id: int, reading: float) -> None:
        """Add the wallbox counter's POSITIVE rise since the last reading to the charge's running total
        (ac_energy_kwh), called every poll while charging. Race/reset-proof: a counter that zeroes
        mid-session is a single negative step (ignored) and the post-reset rise is still counted — so it
        works whether the counter is a lifetime total or resets each session, and no matter WHEN it
        resets relative to our polls. wallbox_energy_start_kwh holds the last reading seen (the running
        baseline), persisted so the sum survives a poller restart mid-charge."""
        row = self._conn.execute(
            "SELECT wallbox_energy_start_kwh AS last, ac_energy_kwh AS accum, "
            "started_at, max_power_kw FROM charges WHERE id=?",
            (charge_id,)).fetchone()
        if row is None:
            return
        last, accum = row["last"], (row["accum"] or 0.0)
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
            "UPDATE charges SET wallbox_energy_start_kwh=?, ac_energy_kwh=? WHERE id=?",
            (reading, round(accum, 3), charge_id))
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

    def finalize_charge(self, charge_id: int, data, max_power_kw: float = 0.0,
                        price_per_kwh: float = 0.0) -> None:
        charge = self._conn.execute("SELECT * FROM charges WHERE id = ?", (charge_id,)).fetchone()
        start_soc    = charge["start_soc"]
        # Energy from ΔSoC × capacity. ONLY on 100%-ending charges, anchor the ΔSoC to the
        # last SoC seen while still charging (see _last_charging_soc): the snap-to-full is a
        # top-of-charge phenomenon, while on mid-SoC charges the final tick (e.g. 94.9→95.0
        # in the poll where charging stops) is real energy that must stay counted.
        # end_soc itself stays data.soc — users should still see the charge reached 100%.
        soc_for_energy = data.soc
        if data.soc >= 100.0:
            last = self._last_charging_soc(charge["vehicle_id"], charge["started_at"])
            if last is not None:
                soc_for_energy = last
        energy_added = max((soc_for_energy - start_soc) / 100.0 * self.get_battery_capacity(), 0)

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
        duration_min = (datetime.now(timezone.utc) - started_at).total_seconds() / 60

        self._conn.execute(
            """UPDATE charges
               SET ended_at=?, end_soc=?, energy_added_kwh=?, duration_min=?,
                   charge_type=?, max_power_kw=?, cost=?
               WHERE id=?""",
            (
                _now_iso(), data.soc, round(energy_added, 3), round(duration_min, 1),
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
        log.info(
            "Charge #%d ended — SOC %.1f→%.1f%% | +%.1f kWh | %.0f min | %s | peak %.1f kW",
            charge_id, start_soc, data.soc, energy_added, duration_min,
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
                distance_km = sum(
                    haversine_km(
                        positions[i]["latitude"], positions[i]["longitude"],
                        positions[i + 1]["latitude"], positions[i + 1]["longitude"],
                    )
                    for i in range(len(positions) - 1)
                )
                start_soc = trip["start_soc"] or 0
                end_soc   = float(last_pos["soc"] or start_soc)
                energy    = (start_soc - end_soc) / 100.0 * self.get_battery_capacity()
                efficiency = (energy / distance_km * 100) if distance_km > 0.5 else None

                started_at   = datetime.fromisoformat(trip["started_at"])
                ended_at_iso = last_pos["recorded_at"]
                ended_at_dt  = datetime.fromisoformat(ended_at_iso)
                duration_min = (ended_at_dt - started_at).total_seconds() / 60

                self._conn.execute(
                    """UPDATE trips SET ended_at=?, end_lat=?, end_lon=?, end_soc=?,
                       distance_km=?, duration_min=?, efficiency_kwh_100km=?
                       WHERE id=?""",
                    (
                        ended_at_iso,
                        last_pos["latitude"], last_pos["longitude"], end_soc,
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

            end_soc      = float((last_pos["soc"] if last_pos else None) or charge["start_soc"] or 0)
            ended_at_iso = (last_pos["recorded_at"] if last_pos else None) or next_start or _now_iso()
            energy_added = max((end_soc - charge["start_soc"]) / 100.0 * self.get_battery_capacity(), 0)

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
            "SELECT id, start_soc, max_power_kw, started_at FROM charges "
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
