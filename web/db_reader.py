"""Read-only DB queries for the web layer."""
import json
import math
import sqlite3
import time
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
import os

import i18n
import crypto  # hard import at module top: a missing crypto dep must fail web boot loudly,
              # never silently degrade a per-request secret read
import capability_profile

# Timestamps are stored in UTC (poller uses datetime.now(timezone.utc)); the UI must show
# LOCAL time. The zone is resolved with this precedence (see _local_tz):
#   1. the user's explicit choice in Settings → settings['timezone'] (an IANA name)
#   2. else the container's TZ env (standalone Docker compose sets it)
#   3. else None → astimezone(None) honours the system local time (HA add-on /etc/localtime)
# #145: layer 1 exists because a bare Docker container is UTC — and an HA whose zone Mate can't
# see reads UTC too — so the user MUST be able to override it. No hardcoded Europe/Rome (that once
# made every non-Italian user see the wrong time). Display-only: the DB always stays UTC.
try:
    from zoneinfo import ZoneInfo, available_timezones
    _ZONEINFO_OK = True
except Exception:                        # no zoneinfo/tzdata → Auto (system-local) only
    _ZONEINFO_OK = False
    def available_timezones():           # type: ignore
        return set()


def _env_tz():
    """The container timezone: explicit TZ env → its ZoneInfo, else None (= system local time)."""
    env = os.environ.get("TZ")
    if env and _ZONEINFO_OK:
        try:
            return ZoneInfo(env)
        except Exception:
            pass
    return None


# _local_dt runs in tight loops (100+ trips per page) and ZoneInfo() parses a tzdata file, so the
# resolved zone is memoised and rebuilt only when the stored 'timezone' setting changes. Keyed by
# the raw setting string ('' = Auto); the fresh get_setting read makes a change self-detect (no
# explicit invalidation needed — set_timezone in another request just changes the stored value).
_TZ_CACHE = {"key": "\x00", "tz": None}   # '\x00' sentinel = not yet computed


def _resolve_tz(name: str):
    """User's explicit IANA choice wins; '' (Auto) or a stale/unknown name → container/system tz."""
    if name and _ZONEINFO_OK:
        try:
            return ZoneInfo(name)
        except Exception:
            pass           # a zone that vanished from tzdata must never wedge every date render
    return _env_tz()


def _local_tz():
    """The zone every timestamp is displayed in — precedence UI setting > env TZ > system local.
    Cheap: one indexed settings read + a memoised ZoneInfo. Never raises (broken DB → container tz)."""
    try:
        name = get_setting("timezone", "")
    except Exception:
        return _env_tz()
    if _TZ_CACHE["key"] != name:
        _TZ_CACHE["tz"] = _resolve_tz(name)
        _TZ_CACHE["key"] = name
    return _TZ_CACHE["tz"]


def _local_dt(s) -> Optional[datetime]:
    """Parse a stored UTC timestamp and return it as an aware datetime in the
    local timezone. Returns None if the value is missing/unparseable."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace(" ", "T").rstrip("Z"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_local_tz())


def local_to_utc_iso(s, tz=None):
    """A wall-clock time the user typed → the UTC ISO string the DB stores. The inverse of _local_dt
    above, and the reason it exists: _local_dt reads a zone-less value AS UTC, so a hand-entered time
    saved verbatim comes back on screen pushed forward by the whole offset — +7 h for the reporter of
    #181, on all 150 of his imported charges, and quietly on every manually added charge before that.

    Returns the value UNCHANGED when it already carries a zone, which makes this idempotent: running
    the repair twice cannot shift anything twice. The offset used is the one in force ON THAT DATE
    (ZoneInfo resolves it per instant), so a January charge gets +01:00 and a July one +02:00 —
    a blanket "add today's offset" would fix one half of the year and break the other."""
    if not s:
        return s
    try:
        dt = datetime.fromisoformat(str(s).replace(" ", "T").replace("Z", "+00:00"))
    except Exception:
        return s
    if dt.tzinfo is not None:
        return s
    return dt.replace(tzinfo=tz or _local_tz()).astimezone(timezone.utc).isoformat()


def get_timezone() -> str:
    """The user's chosen IANA zone name, or '' for Auto (container/system tz). Display-only."""
    return get_setting("timezone", "")


def set_timezone(name: str) -> None:
    """Persist the display zone. '' = Auto. Validated against the tz database so a typo can't wedge
    every date render; the next _local_tz() re-resolves (its key check self-detects the change)."""
    name = (name or "").strip()
    if name and name not in available_timezones():
        name = ""                        # unknown zone → Auto, never store garbage
    set_setting("timezone", name)


# The 10 canonical IANA continent prefixes. Everything else available_timezones() returns is a
# legacy alias we DELIBERATELY drop from the picker: country-name aliases (US/*, Brazil/*, Canada/*
# — redundant with America/*), SystemV, bare GB/Eire, and Etc/GMT±N whose sign is INVERTED
# (Etc/GMT+1 is UTC−1 — a trap). A plain 'UTC' is offered separately for the unambiguous case.
_TZ_REGIONS = ("Africa", "America", "Antarctica", "Arctic", "Asia",
               "Atlantic", "Australia", "Europe", "Indian", "Pacific")


def timezone_options() -> dict:
    """Canonical IANA zones grouped by continent for the Settings <select>, as
    {region: [(value, label), …]} sorted by label, plus a standalone 'UTC' group. Legacy aliases
    and the sign-inverted Etc/GMT±N zones are excluded (see _TZ_REGIONS) so the picker can't mislead."""
    groups: dict = {}
    for z in available_timezones():
        region, _, rest = z.partition("/")
        if region not in _TZ_REGIONS:
            continue
        label = rest.replace("_", " ").replace("/", " / ")
        groups.setdefault(region, []).append((z, label))
    out = {k: sorted(groups[k], key=lambda t: t[1]) for k in sorted(groups)}
    out["UTC"] = [("UTC", "UTC")]     # universal, unambiguous fallback for anyone who wants it
    return out


def _local_iso(s):
    """Convert a stored UTC timestamp string to a local-time ISO string, so that
    template slices like started_at[11:16] display local time. Falls back to input."""
    dt = _local_dt(s)
    return dt.isoformat() if dt else s


def today_local() -> date:
    """Today's calendar date in the user's configured timezone — the Charges calendar's
    default Month view opens here."""
    return datetime.now(_local_tz()).date()


def get_charge_local_date(charge_id: int) -> "date | None":
    """The local calendar date a charge falls on, or None if it doesn't exist — used to
    open the Charges calendar on the right month when following a ?highlight=<id> link
    (e.g. from a map popup) that may point at a charge outside the current month."""
    row = _get().execute("SELECT started_at FROM charges WHERE id=?", (charge_id,)).fetchone()
    if not row or not row["started_at"]:
        return None
    dt = _local_dt(row["started_at"])
    return dt.date() if dt else None

# In-memory optimistic overlay: after a command, keep the expected state for
# _OPT_TTL seconds so the poller can't overwrite it before the UI refreshes.
_opt_overrides: dict = {}
_opt_expiry: float = 0.0
_OPT_TTL = 30


# Labels are intentionally language-neutral (international loanwords + universal
# electrical acronyms) so they never need translating across UI languages.
CHARGE_TYPES = {
    "HOME": {"label": "Home", "icon": "🏠", "color": "#22c55e"},
    "AC":   {"label": "AC",   "icon": "🔌", "color": "#60a5fa"},
    "FAST": {"label": "DC",   "icon": "⚡", "color": "#fb923c"},
    "HPC":  {"label": "HPC",  "icon": "🚀", "color": "#e879f9"},
    "FREE": {"label": "FREE", "icon": "🆓", "color": "#a3e635"},
    "MANUAL": {"label": "Manual", "icon": "✎", "color": "#94a3b8"},
}

PRICE_KEYS = {
    "HOME": "price_home_kwh",
    "AC":   "price_ac_kwh",
    "FAST": "price_fast_kwh",
    "HPC":  "price_hpc_kwh",
}

# REEV Phase C — fuel tank litres (C10/B10 REEV both 50 L, confirmed) and the minimum fuel-% drop over
# a trip that counts as the engine having run (the 3235 signal steps at 0.1% ≈ 50 mL; 0.2% guards the
# single-tick noise). A real range-extender drive drops several %.
_REEV_TANK_L = 50.0
_REEV_FUEL_MIN_DROP = 0.2

# REEV only: signal 3736 does not mean what its name says, so on a range-extender it is not read
# at all.
#
# It was mapped as "chargeCompleted" with the note "validate on a real charge". Nobody had. Nine
# complete charges over sixteen days from a B10 REEV (beta #12, michapr) say it is the opposite:
#
#   flag → 1   cable just connected, current −2.0…−3.8 A, 85 to 915 minutes remaining, SoC 15-76 %
#   flag → 0   current back to 0.1 A, minutes at 5, and three times SoC exactly 90 % — his limit
#
# Nine out of nine, no exception. On this car 3736 is "a charge is running", and Mate was printing
# "Fully charged" for precisely the hours the car was filling.
#
# What stood here before was a tolerance: ignore the flag when the SoC is more than 15 points below
# the charge limit. That was fitted to the first report — the flag seen at 23 % with the limit at
# 90 % — and it did hide the lie at the start of every charge, which is why this looked like a rare
# leftover rather than an inversion. It could not hide it past 75 %, so the claim came back exactly
# when a charge was nearly done and a user was most likely to look. A tolerance is the wrong shape
# of fix for a signal read backwards.
#
# So on a REEV the flag is dropped. Mate says "plugged in", which is true in every frame of the
# bundle, instead of a completion it cannot establish. Deriving a real "finished" is the next step
# and needs one thing this data does not settle: a car plugged in and WAITING for a scheduled
# charge also sits at flag 0, so the inverse reading alone would announce a completion for a charge
# that never happened.
#
# BEVs are untouched. There is no BEV bundle carrying 3736, the flag may well be honest there, and
# it is working today — this is not the moment to change it blind.


def _reev_engine_on(db, vehicle_id, started_at, ended_at) -> Optional[dict]:
    """REEV Phase C — the range-extender's DRIVING footprint over a trip, walked from the positions
    log (per-sample odometer + fuel %). Sums only the intervals where the car was MOVING *and* the
    generator was running — odometer rising AND fuel % falling. Deliberately excludes:
      • pure-electric stretches (fuel % flat) → they'd dilute the L/100 km (this is the bug we fix), and
      • stationary battery-charging (odometer flat, fuel % falling) → that fuel burned over zero km and
        must NOT be blamed on the driving distance (it inflates the figure ~3× if counted).
    So {engine_km, engine_fuel_pct} describe fuel-while-driving over distance-while-driving — the number
    the car itself shows. Returns None when the trail lacks odometer/fuel (old, pruned trips) so the
    caller can fall back to the whole-trip distance."""
    if not (vehicle_id and started_at and ended_at):
        return None
    try:
        rows = db.execute(
            "SELECT odometer_km, fuel_level_pct FROM positions "
            "WHERE vehicle_id = ? AND recorded_at BETWEEN ? AND ? ORDER BY recorded_at, id",
            (vehicle_id, started_at, ended_at)).fetchall()
    except sqlite3.Error:
        return None
    pts = [(r["odometer_km"], r["fuel_level_pct"]) for r in rows
           if r["odometer_km"] is not None and r["fuel_level_pct"] is not None]
    if len(pts) < 2:
        return None
    engine_km = engine_fuel_pct = 0.0
    for (o0, f0), (o1, f1) in zip(pts, pts[1:]):
        dkm, dfuel = o1 - o0, f0 - f1
        if dkm > 0 and dfuel > 0:            # moving AND burning → generator driving the car
            engine_km += dkm
            engine_fuel_pct += dfuel
    if engine_km <= 0.5:
        return None
    return {"engine_km": round(engine_km, 1), "engine_fuel_pct": round(engine_fuel_pct, 2)}


def _reev_trip_fuel(fuel_start_pct, fuel_end_pct, distance_km, engine=None) -> dict:
    """REEV Phase C — per-trip fuel from the tank-% drop. There's no 'engine on' PID: the range-extender
    ran iff the fuel level dropped more than the signal-noise floor. `engine` (from _reev_engine_on) is
    the generator's driving footprint; when present the L/100 km is fuel-burned-while-driving over
    distance-while-driving — matching the car — instead of spreading the litres over the WHOLE trip
    (which under-reports on a mixed EV+generator drive). Falls back to the whole-trip distance when the
    per-position trail isn't available (old, pruned trips). Returns {fuel_used_l, fuel_l_100km,
    engine_ran, engine_km}; all inert when there's no fuel data (BEV) or the drive was pure-electric."""
    out = {"fuel_used_l": None, "fuel_l_100km": None, "engine_ran": False, "engine_km": None}
    if fuel_start_pct is None or fuel_end_pct is None:
        return out
    drop = fuel_start_pct - fuel_end_pct
    if drop <= _REEV_FUEL_MIN_DROP:
        return out
    out["fuel_used_l"] = round(drop / 100.0 * _REEV_TANK_L, 2)   # total litres that left the tank
    out["engine_ran"] = True
    if engine and engine.get("engine_km", 0) > 0.5:
        out["engine_km"] = engine["engine_km"]
        out["fuel_l_100km"] = round((engine["engine_fuel_pct"] / 100.0 * _REEV_TANK_L)
                                    / engine["engine_km"] * 100, 1)
    elif distance_km and distance_km > 0.5:
        out["fuel_l_100km"] = round(out["fuel_used_l"] / distance_km * 100, 1)
    return out


def _reev_trip_elec(ec_driving, distance_km, engine_ran) -> dict:
    """REEV Phase D (beta #10 step 2) — the ELECTRIC side of an engine-on trip, from the cloud's METERED
    getEC (driverEC), NOT from ΔSoC. On a series hybrid the generator recharges the pack mid-drive, so the
    net SoC change isn't the motor's appetite (that's the diluted ~0.5 the SoC path yields and we suppress);
    getEC counts real consumption, generator-proof. Over the FULL distance — the electric motor drives the
    whole trip, so (unlike fuel) there's no generator-on sub-distance to normalise over. Inert on a BEV /
    pure-electric / not-yet-enriched trip (returns None, None → the UI shows a 'getEC pending' hint)."""
    out = {"reev_elec_kwh": None, "reev_elec_kwh_100km": None}
    if engine_ran and ec_driving and distance_km and distance_km > 0:
        out["reev_elec_kwh"] = round(ec_driving, 2)
        out["reev_elec_kwh_100km"] = round(ec_driving / distance_km * 100, 1)
    return out


def auto_location_type(max_power_kw: float) -> str:
    p = max_power_kw or 0
    if p <= 8:   return "HOME"
    if p <= 22:  return "AC"
    if p <= 80:  return "FAST"
    return "HPC"


def _conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


DB_PATH = os.environ.get("DB_PATH", "leapmotor_mate.db")


def _get():
    return _conn(DB_PATH)


def _current_vehicle_id():
    """The vehicle every read is scoped to (multi-car prep). Single-car = the only/first vehicle;
    the multi-car step swaps this for the user-selected VIN. Returns None only before the first
    vehicle is registered — the `vehicle_id = COALESCE(?, vehicle_id)` scope then no-ops (matches
    every row), so a fresh/vehicle-less DB behaves exactly as before. On a single car, filtering by
    its one id is a no-op too, so this whole pass is invisible until a second car exists."""
    try:
        row = _get().execute("SELECT id FROM vehicles ORDER BY id LIMIT 1").fetchone()
    except sqlite3.OperationalError:      # a partial/minimal DB with no vehicles table → don't scope
        return None
    return row["id"] if row else None


def _conn_rw() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def get_setting(key: str, default: str = "") -> str:
    db = _get()
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    db = _conn_rw()
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, str(value)))
    db.commit()


def set_vehicle_capacity_current(kwh: float, nominal: float = None) -> None:
    """Mirror a capacity override onto the CURRENT vehicle's own row (vehicles.capacity_kwh), so the
    poller's per-vehicle energy math honours it — the poller reads the vehicle column, not the global
    setting, so writing only the global would leave the override ignored. Single-car today = the only
    row; the multi-car step will resolve 'current' to the selected vehicle instead of the first."""
    db = _conn_rw()
    row = db.execute("SELECT id FROM vehicles ORDER BY id LIMIT 1").fetchone()
    if not row:
        return
    db.execute("UPDATE vehicles SET capacity_kwh = ? WHERE id = ?", (float(kwh), row["id"]))
    if nominal is not None:
        db.execute("UPDATE vehicles SET capacity_nominal_kwh = ? WHERE id = ?", (float(nominal), row["id"]))
    db.commit()


# ── Research / BetaTester mode (MateBetaTesterOnly build) ──────────────────────
def add_logbook_note(note: str) -> None:
    """Append a timestamped tester note (e.g. 'engine started to charge while driving')."""
    import time
    note = (note or "").strip()
    if not note:
        return
    db = _conn_rw()
    db.execute("INSERT INTO research_logbook (ts, note) VALUES (?, ?)",
               (int(time.time() * 1000), note[:2000]))
    db.commit()


def get_logbook(limit: int = 200):
    """Recent logbook notes, newest first → [{ts, note}]. Empty if the table isn't there yet."""
    try:
        rows = _get().execute(
            "SELECT ts, note FROM research_logbook ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [{"ts": r["ts"], "note": r["note"]} for r in rows]
    except Exception:  # noqa: BLE001
        return []


def count_raw_signals() -> int:
    """How many raw-signal rows have been captured (shown in the beta UI)."""
    try:
        return _get().execute("SELECT COUNT(*) c FROM raw_signals_log").fetchone()["c"]
    except Exception:  # noqa: BLE001
        return 0


def latest_raw_signals() -> dict:
    """Latest value per raw signal id from the research capture — {sig_key: value}. Lets the REEV
    dashboard render from the last stored signals when a live cloud fetch isn't available (a replayed
    tester bundle, or a transient hiccup). Empty when nothing was captured (the normal build)."""
    try:
        rows = _get().execute(
            "SELECT sig_key, value FROM raw_signals_log "
            "WHERE id IN (SELECT MAX(id) FROM raw_signals_log GROUP BY sig_key)").fetchall()
        return {r["sig_key"]: r["value"] for r in rows}
    except Exception:  # noqa: BLE001
        return {}


def get_raw_signal_rows():
    """All captured raw-signal rows (ts, sig_key, value), oldest first — for the export."""
    try:
        rows = _get().execute(
            "SELECT ts, sig_key, value FROM raw_signals_log ORDER BY ts ASC").fetchall()
        return [(r["ts"], r["sig_key"], r["value"]) for r in rows]
    except Exception:  # noqa: BLE001
        return []


def get_db_size_bytes() -> int:
    """Total on-disk size of the SQLite DB (main file + WAL/SHM sidecars)."""
    total = 0
    for suffix in ("", "-wal", "-shm"):
        try:
            total += os.path.getsize(DB_PATH + suffix)
        except OSError:
            pass
    return total


def get_trip_track(trip_id: int) -> list[dict]:
    """Full ordered GPS track for one trip (for GPX export — not downsampled). Group-aware: a
    merged trip returns the union of all its segments' tracks, in chronological order."""
    db = _get()
    ids = _segment_ids(db, trip_id)
    ph = ",".join("?" * len(ids))
    rows = db.execute(
        "SELECT recorded_at, latitude, longitude, speed_kmh, soc FROM trip_positions "
        f"WHERE trip_id IN ({ph}) AND latitude IS NOT NULL AND longitude IS NOT NULL "
        "ORDER BY recorded_at, id",
        ids,
    ).fetchall()
    return [dict(r) for r in rows]


def checkpoint() -> None:
    """Flush the WAL into the main DB file so a file copy/download is consistent."""
    c = _conn_rw()
    try:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        c.commit()
    finally:
        c.close()


_SECRET_PREFIX = "enc:v1:"                                    # marks Fernet-encrypted secrets (crypto._PREFIX)
_RESTORE_REQUIRED_TABLES = frozenset({"settings", "vehicles", "positions"})


def _safe_unlink(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def restore_database(blob: bytes) -> dict:
    """Replace the live DB with an uploaded `leapmotor_mate.db` backup — losing ZERO data — while
    KEEPING the current install's freshly-entered credentials, so the user stays logged in.

    Why the secret-splice: the backup's own secrets were sealed with a DIFFERENT `/data/secret.key`
    (never exported, for security), so they'd be unreadable on this install. We therefore carry over
    the CURRENT encrypted secrets (the login the user just did) into the restored DB; EVERYTHING else
    — every row of research signals, trips, charges, positions, logbook, settings — comes from the
    backup byte-for-byte. If the user restored BEFORE logging in, there are no fresh secrets to keep
    and they simply log in afterwards.

    Raises ValueError on a bad/foreign file WITHOUT touching the live DB. The caller restarts the app
    (exit 42 → run.sh) so both processes reopen the restored DB and run migrations."""
    if blob[:16] != b"SQLite format 3\x00":
        raise ValueError("not a valid SQLite database file")
    tmp = DB_PATH + ".restore.tmp"
    with open(tmp, "wb") as f:
        f.write(blob)
    try:
        con = sqlite3.connect(tmp)
        con.row_factory = sqlite3.Row
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = _RESTORE_REQUIRED_TABLES - tables
        if missing:
            raise ValueError("not a LeapMotor Mate backup (missing tables: %s)" % ", ".join(sorted(missing)))
        # Carry over the CURRENT (fresh) encrypted secrets so the just-entered login survives the swap.
        rw = _conn_rw()
        try:
            fresh = rw.execute("SELECT key, value FROM settings WHERE value LIKE ?",
                               (_SECRET_PREFIX + "%",)).fetchall()
        finally:
            rw.close()
        con.execute("DELETE FROM settings WHERE value LIKE ?", (_SECRET_PREFIX + "%",))
        for r in fresh:
            con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (r["key"], r["value"]))
        con.commit()
        counts = {}
        for t in ("raw_signals_log", "positions", "trips", "charges", "research_logbook"):
            counts[t] = con.execute("SELECT COUNT(*) c FROM \"%s\"" % t).fetchone()["c"] if t in tables else 0
        con.close()
    except Exception:
        _safe_unlink(tmp)
        raise
    # Atomic swap, then drop the OLD WAL sidecars so the new file is never read with a stale WAL.
    os.replace(tmp, DB_PATH)
    for ext in ("-wal", "-shm"):
        _safe_unlink(DB_PATH + ext)
    return {"counts": counts, "secrets_preserved": len(fresh)}


def get_secret(key: str, default: str = "") -> str:
    """Read a secret setting, decrypting transparently (plaintext passes through)."""
    return crypto.decrypt(get_setting(key, default))


def set_secret(key: str, value: str) -> None:
    """Write a secret setting encrypted at rest (matches the poller's crypto/key)."""
    set_setting(key, crypto.encrypt(value or ""))


def get_or_create_device_id() -> str:
    """One stable device_id for this Mate install, shared by poller and web.
    Must match the poller's value so the whole app is a single Leapmotor device on
    the shared app cert (a random per-login device_id kept evicting other clients).
    INSERT OR IGNORE so poller and web converge on the same value."""
    import uuid
    did = get_setting("mate_device_id")
    if not did:
        db = _conn_rw()
        db.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)",
            ("mate_device_id", uuid.uuid4().hex),
        )
        db.commit()
        did = get_setting("mate_device_id")
    return did


def is_setup_complete() -> bool:
    return get_setting("setup_complete") == "1"


def get_language() -> str:
    return get_setting("language", "en")


# ── Currency ──────────────────────────────────────────────────────────────────
# Monetary amounts are formatted via the Jinja `money` filter using this table.
# Stored setting `currency` holds the ISO 4217 code; default EUR keeps the old
# behaviour. `pos` = symbol placement, `dec` = decimal digits. Names stay in
# English (international convention) so they need no translation.
CURRENCIES = {
    "EUR": {"name": "Euro",            "symbol": "€",   "pos": "after",  "dec": 2},
    "USD": {"name": "US Dollar",       "symbol": "$",   "pos": "before", "dec": 2},
    "GBP": {"name": "British Pound",   "symbol": "£",   "pos": "before", "dec": 2},
    "CHF": {"name": "Swiss Franc",     "symbol": "CHF", "pos": "before", "dec": 2},
    "SEK": {"name": "Swedish Krona",   "symbol": "kr",  "pos": "after",  "dec": 2},
    "NOK": {"name": "Norwegian Krone", "symbol": "kr",  "pos": "after",  "dec": 2},
    "DKK": {"name": "Danish Krone",    "symbol": "kr",  "pos": "after",  "dec": 2},
    "PLN": {"name": "Polish Złoty",    "symbol": "zł",  "pos": "after",  "dec": 2},
    "CZK": {"name": "Czech Koruna",    "symbol": "Kč",  "pos": "after",  "dec": 2},
    "HUF": {"name": "Hungarian Forint","symbol": "Ft",  "pos": "after",  "dec": 0},
    "RON": {"name": "Romanian Leu",    "symbol": "lei", "pos": "after",  "dec": 2},
    "BGN": {"name": "Bulgarian Lev",   "symbol": "лв",  "pos": "after",  "dec": 2},
    "HRK": {"name": "Croatian Kuna",   "symbol": "kn",  "pos": "after",  "dec": 2},
    "TRY": {"name": "Turkish Lira",    "symbol": "₺",   "pos": "before", "dec": 2},
    "CAD": {"name": "Canadian Dollar", "symbol": "$",   "pos": "before", "dec": 2},
    "AUD": {"name": "Australian Dollar","symbol": "$",  "pos": "before", "dec": 2},
    "NZD": {"name": "New Zealand Dollar","symbol": "$", "pos": "before", "dec": 2},
    "JPY": {"name": "Japanese Yen",    "symbol": "¥",   "pos": "before", "dec": 0},
    "CNY": {"name": "Chinese Yuan",    "symbol": "¥",   "pos": "before", "dec": 2},
    "INR": {"name": "Indian Rupee",    "symbol": "₹",   "pos": "before", "dec": 2},
    "BRL": {"name": "Brazilian Real",  "symbol": "R$",  "pos": "before", "dec": 2},
    "MXN": {"name": "Mexican Peso",    "symbol": "$",   "pos": "before", "dec": 2},
    "ZAR": {"name": "South African Rand","symbol": "R", "pos": "before", "dec": 2},
    "RUB": {"name": "Russian Ruble",   "symbol": "₽",   "pos": "after",  "dec": 2},
    "UAH": {"name": "Ukrainian Hryvnia","symbol": "₴",  "pos": "after",  "dec": 2},
    "ILS": {"name": "Israeli Shekel",  "symbol": "₪",   "pos": "before", "dec": 2},
    "KRW": {"name": "South Korean Won","symbol": "₩",   "pos": "before", "dec": 0},
    "SGD": {"name": "Singapore Dollar","symbol": "$",   "pos": "before", "dec": 2},
    "HKD": {"name": "Hong Kong Dollar","symbol": "$",   "pos": "before", "dec": 2},
    "THB": {"name": "Thai Baht",       "symbol": "฿",   "pos": "before", "dec": 2},
    "MYR": {"name": "Malaysian Ringgit","symbol": "RM", "pos": "before", "dec": 2},
}
_DEFAULT_CURRENCY = "EUR"


def get_currency_code() -> str:
    code = get_setting("currency", _DEFAULT_CURRENCY)
    return code if code in CURRENCIES else _DEFAULT_CURRENCY


def get_currency() -> dict:
    """Full metadata dict for the configured currency (always valid)."""
    return CURRENCIES[get_currency_code()]


def set_currency(code: str) -> None:
    if code in CURRENCIES:
        set_setting("currency", code)


def get_charge_prices() -> dict:
    db = _get()
    rows = db.execute(
        "SELECT key, value FROM settings WHERE key LIKE 'price_%_kwh'"
    ).fetchall()
    return {r["key"]: float(r["value"]) for r in rows}


# ── Charging-cost configuration (flat 24h vs time-of-use bands) ───────────────
# Stored in `settings`: cost_mode = 'flat'|'tou', tou_method = 'split'|'start',
# tou_bands = JSON list of {start, end, prices:{HOME,AC,FAST,HPC}}. The flat
# price_*_kwh values double as the "off-band" price in time-of-use mode.
_TOU_TYPES = ["HOME", "AC", "FAST", "HPC"]


def _mode_allowed(ctype: str, mode: str) -> bool:
    """Dynamic (HA sensor) is HOME-ONLY (Silvio 02/07): no HA integration exposes a price for
    public AC/DC/HPC charging — those are operator-billed, not a home tariff — so 'dynamic' on
    an away type is never a valid choice, whatever wrote it (UI, a raw API call, or a value
    saved before this rule existed)."""
    return mode in ("flat", "tou") or (mode == "dynamic" and ctype == "HOME")


def get_cost_config() -> dict:
    """Pricing config for the Costs page: mode, calc method and the user bands.

    `modes` (#106 fix) = the pricing mode PER CHARGE TYPE {HOME,AC,FAST,HPC}, resolved from the
    `cost_modes` JSON setting; types not explicitly set — or set to a mode `_mode_allowed`
    rejects (dynamic on an away type) — default from the legacy global `cost_mode`, read-time
    resolution, no write migration. The legacy-'dynamic' default is CORRECTIVE, see
    `_default_mode_for`."""
    raw = get_setting("tou_bands", "")
    try:
        bands = json.loads(raw) if raw else []
        if not isinstance(bands, list):
            bands = []
    except (ValueError, TypeError):
        bands = []
    legacy = get_setting("cost_mode", "flat")
    try:
        m = json.loads(get_setting("cost_modes", "") or "{}")
        m = m if isinstance(m, dict) else {}
    except (ValueError, TypeError):
        m = {}
    modes = {t: (m.get(t) if m.get(t) in ("flat", "tou", "dynamic") and _mode_allowed(t, m.get(t))
                 else _default_mode_for(t, legacy))
             for t in _TOU_TYPES}
    return {
        "mode":   legacy,
        "modes":  modes,
        "method": get_setting("tou_method", "split"),
        "bands":  bands,
    }


def _default_mode_for(ctype: str, legacy: str) -> str:
    """Per-type default when `cost_modes` doesn't name a type. Legacy global 'dynamic' was a
    pricing BUG for away charges (the single home-tariff sensor priced public AC/DC/HPC too —
    spot prices can sit near zero → silently wrong costs, the #106 report): the fix's migration
    CORRECTS it rather than preserving it — dynamic carries over to HOME only, public types drop
    to their fixed base prices. flat/tou never had the bug → they apply to every type as
    before."""
    if legacy == "dynamic":
        return "dynamic" if ctype == "HOME" else "flat"
    return legacy


def save_cost_modes(modes: dict) -> None:
    """Persist the per-charge-type pricing modes (#106). Values are sanitised to the three known
    modes AND to `_mode_allowed` (dynamic is HOME-only — rejected here too, not just at read
    time, so a raw API call can't park an away type on 'dynamic' in storage); unknown/rejected/
    missing types fall back to the legacy global mode at read time. When all four types agree,
    the legacy `cost_mode` is aligned too, so single-mode users keep a coherent value
    everywhere."""
    clean = {t: v for t, v in (modes or {}).items()
             if t in _TOU_TYPES and v in ("flat", "tou", "dynamic") and _mode_allowed(t, v)}
    set_setting("cost_modes", json.dumps(clean))
    vals = set(clean.values())
    if len(clean) == len(_TOU_TYPES) and len(vals) == 1:
        set_setting("cost_mode", vals.pop())


def get_dynamic_price_entity() -> str:
    """Saved HA entity_id for the 'dynamic sensor' pricing mode, or '' if none chosen."""
    return get_setting("dynamic_price_entity_id", "")


def save_dynamic_price_entity(entity_id: str) -> None:
    set_setting("dynamic_price_entity_id", (entity_id or "").strip())


def get_dynamic_price_entity_for(ctype: str) -> str:
    """Dynamic-price sensor for ONE charge type (#106 fix): the per-type choice from the
    `dynamic_price_entities` JSON map. Only HOME falls back to the legacy single entity (that
    sensor IS the home tariff — a pre-fix dynamic setup keeps its home pricing with zero
    reconfiguration). Other types get NO silent fallback: an away type explicitly set to
    dynamic without its own sensor prices at its base — falling back to the home sensor would
    re-introduce the very bug this fixes."""
    try:
        raw = get_setting("dynamic_price_entities", "")
        m = json.loads(raw) if raw else {}
        e = (m.get(ctype) or "").strip() if isinstance(m, dict) else ""
    except Exception:  # noqa: BLE001 — settings table may not exist in minimal test DBs
        e = ""
    if e:
        return e
    return get_dynamic_price_entity() if ctype == "HOME" else ""


def save_dynamic_price_entity_for(ctype: str, entity_id: str) -> None:
    if ctype not in _TOU_TYPES:
        return
    try:
        raw = get_setting("dynamic_price_entities", "")
        m = json.loads(raw) if raw else {}
        m = m if isinstance(m, dict) else {}
    except (ValueError, TypeError):
        m = {}
    m[ctype] = (entity_id or "").strip()
    set_setting("dynamic_price_entities", json.dumps(m))


# ── Ready-triggered "prepare now" automation (design agreed 2026-07-02) ────────
# One JSON setting, read every poll by poller/ready_automation.py (which re-sanitises
# independently — defense in depth, same pattern already used for the per-type pricing
# modes: a write-time and a read-time guard, neither trusting the other alone).
_READY_PRESETS    = {"cool", "heat", "vent", "defrost", "none"}
_READY_SEAT_MODES = {"off", "heat", "vent"}


def get_ready_automation_config() -> dict:
    """Sanitised config for the Prepara Veicolo page's automation section."""
    try:
        raw = json.loads(get_setting("ready_automation", "") or "{}")
        if not isinstance(raw, dict):
            raw = {}
    except (ValueError, TypeError):
        raw = {}
    ac_preset = raw.get("ac_preset")
    if ac_preset not in _READY_PRESETS:
        ac_preset = None
    try:
        ac_temperature = int(float(raw.get("ac_temperature")))
    except (TypeError, ValueError):
        ac_temperature = 22
    windows_pct = raw.get("windows_pct")
    try:
        windows_pct = None if windows_pct is None else max(0, min(int(windows_pct), 100))
    except (TypeError, ValueError):
        windows_pct = None

    def _seat(key):
        v = raw.get(key)
        return v if v in _READY_SEAT_MODES else "off"

    try:
        temp_value = float(raw.get("temp_value"))
    except (TypeError, ValueError):
        temp_value = 25.0
    return {
        "enabled":         bool(raw.get("enabled")),
        "temp_enabled":    bool(raw.get("temp_enabled")),
        "temp_comparator": raw.get("temp_comparator") if raw.get("temp_comparator") in (">", "<") else ">",
        "temp_value":      temp_value,
        "ac_preset":       ac_preset or "off",   # "off" is a real <select> option, ac_preset=None isn't
        "ac_temperature":  ac_temperature,
        "windows_pct":     windows_pct,
        "seat_driver":     _seat("seat_driver"),
        "seat_copilot":    _seat("seat_copilot"),
        "steering":        bool(raw.get("steering")),
        "mirror":          bool(raw.get("mirror")),
    }


def save_ready_automation_config(form) -> None:
    """Parse + sanitise the automation form (Werkzeug/Starlette FormData) and persist it as one
    JSON setting. Mirrors _parse_prepare_form's field names (ac_mode/ac_temperature/seat_driver/
    seat_copilot/steering/mirror — the shared bundle_fields() macro) plus the automation-only
    fields (enabled/temp_*/windows_*)."""
    ac_mode = (form.get("ac_mode") or "off").strip()
    ac_preset = ac_mode if ac_mode in _READY_PRESETS else None
    try:
        ac_temperature = int(float(form.get("ac_temperature") or 22))
    except (TypeError, ValueError):
        ac_temperature = 22
    windows_enabled = (form.get("windows_enabled") or "") in ("1", "on", "true", "True")
    windows_pct = None
    if windows_enabled:
        try:
            windows_pct = max(0, min(int(float(form.get("windows_pct") or 0)), 100))
        except (TypeError, ValueError):
            windows_pct = 0

    def _seat(name):
        v = form.get("seat_" + name) or "off"
        return v if v in _READY_SEAT_MODES else "off"

    try:
        temp_value = float(form.get("temp_value") or 25)
    except (TypeError, ValueError):
        temp_value = 25.0
    cfg = {
        "enabled":         (form.get("ready_enabled") or "") in ("1", "on", "true", "True"),
        "temp_enabled":    (form.get("temp_enabled") or "") in ("1", "on", "true", "True"),
        "temp_comparator": form.get("temp_comparator") if form.get("temp_comparator") in (">", "<") else ">",
        "temp_value":      round(temp_value, 1),
        "ac_preset":       ac_preset,
        "ac_temperature":  ac_temperature,
        "windows_pct":     windows_pct,
        "seat_driver":     _seat("driver"),
        "seat_copilot":    _seat("copilot"),
        "steering":        (form.get("steering") or "") in ("1", "on", "true", "True"),
        "mirror":          (form.get("mirror") or "") in ("1", "on", "true", "True"),
    }
    set_setting("ready_automation", json.dumps(cfg))


def save_cost_config(mode: str, method: str, bands: list) -> None:
    """Persist the Costs-page config. Bands are sanitised to {start,end,prices}."""
    mode   = mode   if mode   in ("flat", "tou", "dynamic") else "flat"
    method = method if method in ("split", "start") else "split"
    clean = []
    for b in bands or []:
        if not isinstance(b, dict):
            continue
        start = str(b.get("start", "")).strip()
        end   = str(b.get("end", "")).strip()
        if not start or not end:
            continue
        prices, src = {}, (b.get("prices") or {})
        for t in _TOU_TYPES:
            try:
                prices[t] = round(float(src.get(t)), 4)
            except (TypeError, ValueError):
                prices[t] = None
        # Days of the week the band applies to (0=Mon … 6=Sun). Empty/invalid =
        # every day, so a band always applies somewhere.
        raw_days = b.get("days")
        days = sorted({int(d) for d in raw_days
                       if isinstance(d, (int, float)) and 0 <= int(d) <= 6}) \
            if isinstance(raw_days, list) else []
        if not days:
            days = list(range(7))
        clean.append({"start": start, "end": end, "days": days, "prices": prices})
    set_setting("cost_mode", mode)
    set_setting("tou_method", method)
    set_setting("tou_bands", json.dumps(clean))


def _parse_hhmm(s) -> Optional[int]:
    """'HH:MM' → minute-of-day (0–1440), or None if unparseable."""
    try:
        h, m = str(s).split(":")
        v = int(h) * 60 + int(m)
        return v if 0 <= v <= 24 * 60 else None
    except (ValueError, AttributeError):
        return None


def _time_in_window(minute: int, start_min: int, end_min: int) -> bool:
    """Is minute-of-day inside [start, end)? Handles windows crossing midnight
    (start > end, e.g. 23:30→06:30). start == end means the whole day."""
    if start_min == end_min:
        return True
    if start_min < end_min:
        return start_min <= minute < end_min
    return minute >= start_min or minute < end_min


def _band_covers(b: dict, weekday: int, minute: int) -> bool:
    """Does this band cover (weekday, minute-of-day)? A band crossing midnight (start > end,
    e.g. 23:30→07:30) is anchored to the day it STARTS: its pre-midnight part [start,24:00)
    applies when that day is in `days`; its post-midnight part [00:00,end) belongs to the
    PREVIOUS day's membership — so a Saturday-only off-peak band also covers the early Sunday
    hours, but a Sunday-only band does not."""
    days = b.get("days")
    if not isinstance(days, list) or not days:
        days = list(range(7))
    s, e = _parse_hhmm(b.get("start")), _parse_hhmm(b.get("end"))
    if s is None or e is None:
        return False
    if s == e:                                        # whole-day band
        return weekday in days
    if s < e:                                         # same-day window
        return s <= minute < e and weekday in days
    if minute >= s and weekday in days:               # crosses midnight: pre-midnight → this day
        return True
    return minute < e and (weekday - 1) % 7 in days   # post-midnight → previous day


def _match_band(bands: list, weekday: int, minute: int):
    """First band that covers this (weekday, minute-of-day), regardless of charge type."""
    for b in bands:
        if _band_covers(b, weekday, minute):
            return b
    return None


def _resolve_band_price(bands: list, ctype: str, weekday: int, minute: int,
                        base: float, base_set: bool):
    """TYPE-AWARE band price for a moment (#106 fix): the first band covering this moment
    WITH a price set for this charge type wins — a blank cell means "this band is not for
    this type", so overlapping windows can serve different types (the home 23-07 off-peak and
    a public AC network's own 22-06 band coexist; each type reads its own). Previously the
    first time-matching band won for every type and a blank cell dropped straight to base,
    which silently killed any later overlapping band. No band prices this type at this
    moment → the type's base price (is_set=False when that base isn't configured either →
    not costed)."""
    for b in bands:
        if _band_covers(b, weekday, minute):
            bp = (b.get("prices") or {}).get(ctype)
            if bp is not None:
                return float(bp), True
    return base, base_set


def _next_charge_start_utc(db, started_at) -> Optional[str]:
    """UTC start of the first charge beginning strictly after `started_at` (a raw stored
    value), or None. Used to cap a charge's power-sample window: an orphan/overlapping
    charge whose ended_at bled past a later charge (see the poller's close_orphan_charges)
    must NOT absorb the next charge's power samples into its own window or cost."""
    try:
        row = db.execute(
            "SELECT MIN(started_at) AS s FROM charges WHERE vehicle_id = COALESCE(?, vehicle_id) AND started_at > ?",
            (_current_vehicle_id(), started_at)
        ).fetchone()
    except sqlite3.Error:
        return None   # no charges table (isolated unit tests) → no cap
    return _iso_to_utc(row["s"]) if (row and row["s"]) else None


def _power_window_bounds(db, started_at, ended_at):
    """(lower_utc, upper, upper_is_exclusive) for a charge's charging=1 samples, capping
    the upper bound at the next charge's start so a window/cost never leaks across charges.
    When capped, the upper bound is EXCLUSIVE (the next charge owns samples at its start)."""
    lo = _iso_to_utc(started_at) or started_at
    hi = _iso_to_utc(ended_at) or lo
    nxt = _next_charge_start_utc(db, started_at)
    if nxt and nxt <= hi:
        return lo, nxt, True
    return lo, hi, False


def _dynamic_sensor_cost(charge, energy: float, base: float, ctype: str = None) -> Optional[float]:
    """Cost from a live HA price-sensor history (Nordpool/Tibber/ENTSO-E-style dynamic
    tariffs): integrate the charge's real power curve same as TOU 'split', but price each
    interval by the sensor's value AT that instant (step-hold — these sensors update once
    an hour) instead of a static band. Falls back to the flat base price whenever the sensor
    isn't configured, HA is unreachable, or it has no history for the window (never leaves
    a charge silently uncosted just because one live lookup failed).
    `ctype` (#106): the charge type, to resolve its own per-type sensor; None = legacy single."""
    entity_id = get_dynamic_price_entity_for(ctype) if ctype else get_dynamic_price_entity()
    if not entity_id or not charge["ended_at"]:
        return round(energy * base, 2) if base else None

    import ha_client   # local: ha_client imports db_reader, so this avoids a circular import
    db = _get()
    lo, hi, excl = _power_window_bounds(db, charge["started_at"], charge["ended_at"])
    rows = db.execute(
        "SELECT recorded_at, charge_voltage_v, charge_current_a FROM positions "
        "WHERE vehicle_id = COALESCE(?, vehicle_id) AND charging = 1 AND recorded_at >= ? AND recorded_at "
        + ("<" if excl else "<=")
        + " ? ORDER BY recorded_at",
        (_current_vehicle_id(), lo, hi),
    ).fetchall()
    samples = []
    for r in rows:
        dt = _local_dt(r["recorded_at"])
        if dt is not None:
            power = abs((r["charge_voltage_v"] or 0) * (r["charge_current_a"] or 0)) / 1000.0
            samples.append((dt, power))
    if len(samples) < 2:
        return round(energy * base, 2) if base else None

    price_hist = ha_client.get_history(entity_id, lo, hi)
    if not price_hist:
        return round(energy * base, 2) if base else None

    idx, total_e, weighted = 0, 0.0, 0.0
    for (dt0, p0), (dt1, p1) in zip(samples, samples[1:]):
        hours = (dt1 - dt0).total_seconds() / 3600.0
        if hours <= 0 or hours > 0.25:   # mirrors compute_cost's TOU-split gap guard
            continue
        e = (p0 + p1) / 2.0 * hours
        if e <= 0:
            continue
        ts0 = dt0.timestamp()
        while idx + 1 < len(price_hist) and price_hist[idx + 1][0] <= ts0:
            idx += 1
        total_e += e
        weighted += e * price_hist[idx][1]

    if total_e <= 0:
        return round(energy * base, 2) if base else None
    # scale the time-weighted average price onto the authoritative (SOC) energy, same as
    # the TOU-split method, so the total stays consistent with the energy shown elsewhere.
    return round(energy * (weighted / total_e), 2)


def compute_cost(charge, config: Optional[dict] = None, ac_kwh: Optional[float] = None):
    """Cost for ONE charge using the pricing config in effect *now*. This is the
    single place a charge's cost is set, and it is frozen afterwards (no retroactive
    recompute when prices/bands change later). Returns a float (0.0 = free) or None
    when the type/price isn't known yet.
        flat        → energy × base price for the charge's type
        TOU 'start' → price of the band matching the start day+time (else base)
        TOU 'split' → energy split across bands by the real power curve, each
                      sample priced by the band matching its own day+time
        dynamic     → same power-curve split as TOU 'split', priced by a live HA
                      sensor's history instead of a static band (see _dynamic_sensor_cost)

    `ac_kwh`: for HOME charges on a configured wallbox, the caller passes the real AC energy the
    wallbox delivered (what you actually pay the utility, incl. AC→DC conversion losses). When given
    (>0) it replaces the DC SOC-energy as the billed amount; otherwise we bill the DC energy (the only
    figure we have for public/away charges). The band-weighting (timing) is unchanged — AC and DC flow
    at the same times — so only the total energy differs.
    """
    location_type = charge["location_type"]
    # #120: a charge the user marked FREE (a home solar/free charge kept under Home) costs 0, full
    # stop — authoritative over any tariff and unconditional, so every recompute path (auto-confirm,
    # the one-time repairs, a re-tag) that routes through here keeps it at 0.
    if "is_free" in charge.keys() and charge["is_free"]:
        return 0.0
    # `ac_kwh` (when given) is the wallbox energy the poller MEASURED for this charge — the counter
    # delta start→stop, an exact figure, not an estimate. HOME charges are billed on it; everything
    # else (and HOME without a wallbox) on the battery (DC/SoC) energy. The caller picks which.
    energy = ac_kwh if (ac_kwh and ac_kwh > 0) else (charge["energy_added_kwh"] or 0)
    if not location_type or energy <= 0:
        return None
    if location_type == "FREE":
        return 0.0

    if config is None:
        config = get_cost_config()
    prices = get_charge_prices()
    key = PRICE_KEYS.get(location_type, "")
    base_set = key in prices
    base = float(prices.get(key, 0.0) or 0.0)

    # Pricing mode PER CHARGE TYPE (#106): this charge's type picks its own mode; a config
    # without the per-type map (older caller / pre-#106 settings) falls back to the global one.
    mode = (config.get("modes") or {}).get(location_type) or config.get("mode", "flat")
    if mode == "dynamic" and not _mode_allowed(location_type, mode):
        mode = "flat"   # defense in depth — dynamic is HOME-only, whatever handed us this config

    if mode == "dynamic":
        return _dynamic_sensor_cost(charge, energy, base, ctype=location_type)

    bands = config.get("bands") or []
    if mode != "tou" or not bands:
        return round(energy * base, 2) if base else None

    def _start_band_cost():
        dt = _local_dt(charge["started_at"])
        if dt is None:
            return round(energy * base, 2) if base else None
        price, is_set = _resolve_band_price(bands, location_type,
                                            dt.weekday(), dt.hour * 60 + dt.minute,
                                            base, base_set)
        if not is_set and price == 0:
            return None
        return round(energy * price, 2)

    if config.get("method") == "start":
        return _start_band_cost()

    # An in-progress charge (no ended_at) has no integrable curve yet → price by start band.
    if not charge["ended_at"]:
        return _start_band_cost()

    # method 'split': integrate the power curve, price each interval by its band. The window
    # is capped at the next charge's start so an orphan/overlapping charge can't integrate a
    # later charge's power (which would also distort the band weighting).
    db = _get()
    lo, hi, excl = _power_window_bounds(db, charge["started_at"], charge["ended_at"])
    rows = db.execute(
        "SELECT recorded_at, charge_voltage_v, charge_current_a FROM positions "
        "WHERE vehicle_id = COALESCE(?, vehicle_id) AND charging = 1 AND recorded_at >= ? AND recorded_at "
        + ("<" if excl else "<=")
        + " ? ORDER BY recorded_at",
        (_current_vehicle_id(), lo, hi),
    ).fetchall()
    samples = []
    for r in rows:
        dt = _local_dt(r["recorded_at"])
        if dt is not None:
            power = abs((r["charge_voltage_v"] or 0) * (r["charge_current_a"] or 0)) / 1000.0
            samples.append((dt, power))

    total_e, weighted, any_set = 0.0, 0.0, False
    for (dt0, p0), (dt1, p1) in zip(samples, samples[1:]):
        hours = (dt1 - dt0).total_seconds() / 3600.0
        if hours <= 0 or hours > 0.25:   # skip non-positive AND multi-hour gaps (charger
            continue                     # paused / poll miss): never price a phantom interval
                                         # across the gap (mirrors _integrate_charge_energy_kwh)
        e = (p0 + p1) / 2.0 * hours
        if e <= 0:
            continue
        price, is_set = _resolve_band_price(bands, location_type,
                                            dt0.weekday(), dt0.hour * 60 + dt0.minute,
                                            base, base_set)
        any_set = any_set or is_set
        total_e += e
        weighted += e * price

    if total_e <= 0:               # no usable curve → fall back to the start band
        return _start_band_cost()
    if not any_set and weighted == 0:
        return None
    # scale the time-weighted average price onto the authoritative (SOC) energy,
    # so the total stays consistent with the energy shown elsewhere.
    return round(energy * (weighted / total_e), 2)


def update_charge_type(charge_id: int, location_type: str,
                       manual_cost: Optional[float] = None) -> dict:
    """Set location_type and (re)compute the cost from the pricing config in effect now (flat or
    time-of-use). Frozen afterwards (the 'new charges only' rule). HOME charges are billed on the
    wallbox energy the POLLER measured at charge start/stop (charges.ac_energy_kwh = the counter
    delta — exact, not estimated) when available; otherwise, and for every other type, on the
    battery (DC/SoC) energy.

    `MANUAL` is the user-entered total actually paid (the public-charging jungle — subscriptions,
    session/idle fees, pay-method rates — can't be modelled by a per-kWh tariff). It OVERRIDES the
    automatic cost: `manual_cost` is stored verbatim and the automatic costers (auto-confirm and the
    one-time repairs) leave a MANUAL charge's cost alone. It still feeds the WAC like any priced
    charge (rate = cost ÷ billed DC energy)."""
    db = _conn_rw()
    row = db.execute("SELECT * FROM charges WHERE id=?", (charge_id,)).fetchone()
    if not row:
        return {}

    charge = dict(row)
    charge["location_type"] = location_type
    # #120: the FREE mark is HOME-only — switching to any other type drops it (free-away is the
    # FREE location_type). Kept as-is when the charge stays HOME.
    free = 1 if (location_type == "HOME" and charge.get("is_free")) else 0
    charge["is_free"] = free
    if location_type == "MANUAL":
        # Keep the existing cost if no amount was supplied (e.g. re-tagging without re-typing it).
        cost = round(manual_cost, 2) if manual_cost is not None else charge.get("cost")
    else:
        meter = charge.get("ac_energy_kwh")
        billed = meter if (location_type == "HOME" and meter and meter > 0) else None
        cost = compute_cost(charge, ac_kwh=billed)   # returns 0.0 when the charge is marked free

    db.execute(
        "UPDATE charges SET location_type=?, cost=?, is_free=? WHERE id=?",
        (location_type, cost, free, charge_id)
    )
    db.commit()
    return dict(db.execute("SELECT * FROM charges WHERE id=?", (charge_id,)).fetchone())


def set_charge_free(charge_id: int, free: bool) -> dict:
    """#120: mark/unmark a HOME charge as FREE — a home charge that cost nothing (self-produced
    solar, or any free charge at home). Mate can't tell solar from grid (no metering behind the
    meter), so this is a user declaration, not a measurement. The charge KEEPS its Home location
    (so it stays on the Home side of the Home-vs-Public split, unlike the FREE location_type which
    is 'free away') and its cost is pinned to 0. Unmarking recomputes the normal home cost. HOME-only:
    a no-op on any other type (free-away is the FREE type)."""
    db = _conn_rw()
    row = db.execute("SELECT * FROM charges WHERE id=?", (charge_id,)).fetchone()
    if not row:
        return {}
    charge = dict(row)
    if charge.get("location_type") != "HOME":
        return charge   # the free mark lives only on HOME charges
    flag = 1 if free else 0
    charge["is_free"] = flag
    if flag:
        cost = 0.0
    else:
        meter = charge.get("ac_energy_kwh")
        billed = meter if (meter and meter > 0) else None
        cost = compute_cost(charge, ac_kwh=billed)
    db.execute("UPDATE charges SET is_free=?, cost=? WHERE id=?", (flag, cost, charge_id))
    db.commit()
    return dict(db.execute("SELECT * FROM charges WHERE id=?", (charge_id,)).fetchone())


def auto_confirm_home_charges() -> int:
    """Auto-assign HOME to closed, still-untyped charges where the wallbox measured real AC
    energy (opt-in `wallbox_auto_home` setting; idea credit: @hubcasale, PR #47): if YOUR
    wallbox saw energy flow during the session, the charge happened at home. DC/public
    charges and reconstructed ones carry no wallbox session energy, so they stay manual.
    Each hit goes through update_charge_type — the SAME path as a manual badge confirm —
    so the cost honours the pricing config (flat or TOU bands) and the AC-energy billing;
    the type stays user-editable afterwards. The 0.05 kWh floor mirrors the phantom-charge
    threshold (meter jitter must not tag a charge). Runs on page renders (a settings probe
    + one SELECT, normally 0 rows) and when the toggle is switched on; returns # confirmed."""
    try:
        if get_setting("wallbox_auto_home", "0") != "1":
            return 0
        rows = _get().execute(
            "SELECT id FROM charges WHERE vehicle_id = COALESCE(?, vehicle_id) "
            "AND location_type IS NULL AND ended_at IS NOT NULL "
            "AND COALESCE(reconstructed, 0) = 0 AND COALESCE(ac_energy_kwh, 0) > 0.05",
            (_current_vehicle_id(),)
        ).fetchall()
    except sqlite3.Error:   # fresh install — settings/charges tables not created yet
        return 0
    for r in rows:
        update_charge_type(r["id"], "HOME")
    return len(rows)


# ── 📍 charging-station labels (resolved by web/charger_locator.py) ───────────
# A candidate is a closed public charge with a GPS fix and no label yet. Home charges
# are excluded twice over — by the HOME type and by any wallbox session evidence — so a
# pure-home install never triggers a single network lookup.
_LOCATION_CANDIDATES_WHERE = (
    "ended_at IS NOT NULL AND location_name IS NULL "
    "AND latitude IS NOT NULL AND longitude IS NOT NULL "
    "AND latitude <> 0 AND longitude <> 0 "
    "AND COALESCE(location_type, '') <> 'HOME' "
    "AND wallbox_energy_start_kwh IS NULL AND COALESCE(ac_energy_kwh, 0) <= 0.05"
)


def has_location_lookup_candidates() -> bool:
    try:
        return _get().execute(
            f"SELECT 1 FROM charges WHERE vehicle_id = COALESCE(?, vehicle_id) AND {_LOCATION_CANDIDATES_WHERE} LIMIT 1",
            (_current_vehicle_id(),)
        ).fetchone() is not None
    except sqlite3.Error:  # fresh install — column not migrated by the poller yet
        return False


def get_location_lookup_candidates(limit: int = 40) -> list[dict]:
    try:
        rows = _get().execute(
            f"SELECT id, latitude, longitude FROM charges WHERE vehicle_id = COALESCE(?, vehicle_id) "
            f"AND {_LOCATION_CANDIDATES_WHERE} "
            "ORDER BY started_at DESC LIMIT ?", (_current_vehicle_id(), limit)).fetchall()
    except sqlite3.Error:
        return []
    return [dict(r) for r in rows]


def get_labelled_locations() -> list[tuple]:
    """(lat, lon, label, url) of every already-resolved charge — '' sentinels included —
    so a charge at an already-known spot reuses the answer (label AND link) instead of
    re-asking Overpass."""
    try:
        rows = _get().execute(
            "SELECT latitude, longitude, location_name, location_url FROM charges "
            "WHERE vehicle_id = COALESCE(?, vehicle_id) "
            "AND location_name IS NOT NULL AND latitude IS NOT NULL AND longitude IS NOT NULL",
            (_current_vehicle_id(),)
        ).fetchall()
    except sqlite3.Error:
        return []
    return [(r["latitude"], r["longitude"], r["location_name"], r["location_url"]) for r in rows]


def set_charge_location_name(charge_id: int, name: str, url: "str | None" = None) -> None:
    db = _conn_rw()
    db.execute("UPDATE charges SET location_name=?, location_url=? WHERE id=?", (name, url, charge_id))
    db.commit()


def get_charge_location(charge_id: int) -> Optional[dict]:
    """Single-charge lookup for the 📍 manual recalc button — unlike
    get_location_lookup_candidates (only lists NOT-yet-labelled charges for the
    background sweep), this fetches any one charge regardless of its current label."""
    row = _get().execute(
        "SELECT id, latitude, longitude, location_type, location_name, location_url "
        "FROM charges WHERE id=?",
        (charge_id,)).fetchone()
    return dict(row) if row else None


def get_labelled_charges_missing_url(limit: int = 200) -> list[dict]:
    """Already-labelled charges with no link yet — the Settings 'recover missing
    links' backfill's queue. These predate the location_url column, or were resolved
    from a source that has none on its own (PUN alone). The mirror-image of
    get_location_lookup_candidates (which lists UN-labelled charges for the ongoing
    sweep)."""
    try:
        rows = _get().execute(
            "SELECT id, latitude, longitude, location_name FROM charges "
            "WHERE vehicle_id = COALESCE(?, vehicle_id) "
            "AND location_name IS NOT NULL AND location_name != '' AND location_url IS NULL "
            "AND latitude IS NOT NULL AND longitude IS NOT NULL "
            "ORDER BY started_at DESC LIMIT ?",
            (_current_vehicle_id(), limit)).fetchall()
    except sqlite3.Error:
        return []
    return [dict(r) for r in rows]


def set_charge_location_url(charge_id: int, url: str) -> None:
    """Backfill-only: fills in JUST the link, leaving the already-saved name (which
    may have been hand-picked from an ambiguity popup) untouched."""
    db = _conn_rw()
    db.execute("UPDATE charges SET location_url=? WHERE id=?", (url, charge_id))
    db.commit()


def save_charge_note(charge_id: int, note: str) -> None:
    """#107: persist the optional free-text user note on a charge (empty string clears it)."""
    note = (note or "").strip()[:1000]
    db = _conn_rw()
    db.execute("UPDATE charges SET note=? WHERE id=?", (note or None, charge_id))
    db.commit()


# #107: driving-mode tag values Mate accepts (manual — the cloud doesn't expose drive mode).
DRIVE_MODES = ("eco", "comfort", "normal", "sport", "custom")
# One list for every car, in the order the screen shows them. The C10's own display (photographed on
# @adoewa's MY2026 full-electric, discussion #180) offers ECO · Comfort · Sport · Custom — no
# "normal" at all — while @gm27271 reports Sport · Normal · Individual on his range-extender. Two
# cars, two lists, and the three we shipped matched neither. A union rather than a per-model table
# because this is a label the driver picks BY HAND: an entry their car doesn't have costs nothing,
# a missing one is the bug that was reported, and dropping "normal" would orphan every trip already
# tagged with it. "Custom" covers what some markets call Individual.


def save_trip_note(trip_id: int, note: str,
                   drive_mode: Optional[str] = None,
                   one_pedal: Optional[int] = None) -> None:
    """#107: persist the trip user note + manual driving tags. drive_mode is one of DRIVE_MODES
    (anything else clears it); one_pedal is 1/0/None (None = not set). Empty note clears it.
    Writes to the trip id as given — the detail page already resolves a merged child to its parent."""
    note = (note or "").strip()[:1000]
    dm = drive_mode if drive_mode in DRIVE_MODES else None
    op = one_pedal if one_pedal in (0, 1) else None
    db = _conn_rw()
    db.execute("UPDATE trips SET note=?, drive_mode=?, one_pedal=? WHERE id=?",
               (note or None, dm, op, trip_id))
    db.commit()


def update_charge_price(key: str, value: float) -> None:
    """Persist a base €/kWh price. Per the 'new charges only' rule, this does NOT
    retroactively recompute already-recorded charges: a charge's cost is frozen
    when its type is confirmed, and only charges confirmed from here on use the
    new price. Same goes for time-of-use band/mode edits."""
    set_setting(key, str(value))


def add_manual_charge(started_at: str, energy_kwh: float, cost: Optional[float] = None,
                      charge_type: str = "AC", ended_at: Optional[str] = None,
                      start_soc: Optional[float] = None, end_soc: Optional[float] = None) -> int:
    """Insert a user-entered historical charge — e.g. sessions from before Mate was installed —
    so the lifetime totals / monthly report reflect them (#87). Date + energy are the essentials;
    cost, AC/DC and — optionally — start/end SoC can be given (the latter drives the card's SoC-gain
    tile, requested by @rossiadobe on #67). It stays SoH-safe either way: a manual charge has no power
    curve, so get_battery_health integrates ~0 energy and skips it regardless of SoC. location_type=
    'MANUAL' keeps the automatic costers from overwriting the cost the user typed."""
    db = _conn_rw()
    try:
        vrow = db.execute("SELECT id FROM vehicles ORDER BY id LIMIT 1").fetchone()
        vehicle_id = vrow["id"] if vrow else None
        ct = "DC" if str(charge_type).upper() in ("DC", "FAST", "HPC") else "AC"
        cur = db.execute(
            "INSERT INTO charges (vehicle_id, started_at, ended_at, energy_added_kwh, "
            "charge_type, location_type, cost, start_soc, end_soc, reconstructed) "
            "VALUES (?, ?, ?, ?, ?, 'MANUAL', ?, ?, ?, 0)",
            (vehicle_id, started_at, ended_at or started_at, energy_kwh, ct, cost, start_soc, end_soc))
        db.commit()
        return cur.lastrowid
    finally:
        db.close()


TZ_REPAIR_ZONE_KEY = "charge_tz_repair_zone"      # which zone the conversion was made in
TZ_REPAIR_MAXID_KEY = "charge_tz_repair_max_id"   # the last row it covered


def _reanchor_iso(s, old_tz, new_tz):
    """A converted timestamp, moved from one assumed zone to another. Rendering it in the zone the
    conversion USED gives back the wall clock the user originally typed; anchoring that to the zone
    they have now is what they meant all along. Zone-less values are left alone — those never went
    through a conversion and belong to the normal path."""
    if not s:
        return s
    try:
        dt = datetime.fromisoformat(str(s).replace(" ", "T").replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return s
    if dt.tzinfo is None:
        return s
    typed = dt.astimezone(old_tz).replace(tzinfo=None)
    return typed.replace(tzinfo=new_tz).astimezone(timezone.utc).isoformat()


def repair_manual_charge_timezones() -> int:
    """Anchor hand-entered charges to the zone the user actually reads them in (#181). Returns how
    many rows moved. ONLY touches location_type='MANUAL' — the poller's rows have always been UTC.

    v2.12.1 shipped this with one marker: a row carrying a zone was considered done. That is
    idempotent, and it was wrong, because it freezes whatever zone happened to be configured at the
    FIRST start after the update. Installing and then choosing your zone is the normal order of
    events, so a whole install could be stamped as UTC and never look at itself again —
    @ghuaywen-ai's 150 charges, still eight hours out with the marker saying "converted".

    Two changes. **Nothing is converted until a zone has actually been chosen**: with no answer to
    "whose clock is this?", the honest move is to wait rather than guess and mark it settled. And the
    zone used is now recorded, so if it later changes, the rows this pass converted are re-anchored
    to the new one — bounded by the highest id it covered, because a charge added afterwards was
    already written correctly and must not move."""
    db = _conn_rw()
    try:
        chosen = (get_setting("timezone", "") or "").strip()
        if not chosen:
            return 0     # see above: converting now would bake in a zone the user hasn't picked
        tz = _local_tz()
        prev_zone = (get_setting(TZ_REPAIR_ZONE_KEY, "") or "").strip()
        rows = db.execute(
            "SELECT id, started_at, ended_at FROM charges WHERE location_type = 'MANUAL'").fetchall()
        try:
            covered = int(get_setting(TZ_REPAIR_MAXID_KEY, "0") or 0)
        except (TypeError, ValueError):
            covered = 0

        fixed = 0
        for r in rows:
            if prev_zone and prev_zone != chosen and r["id"] <= covered:
                old = _resolve_tz(prev_zone)
                started = _reanchor_iso(r["started_at"], old, tz)
                ended = _reanchor_iso(r["ended_at"], old, tz) if r["ended_at"] else r["ended_at"]
            else:
                started = local_to_utc_iso(r["started_at"], tz)
                ended = local_to_utc_iso(r["ended_at"], tz) if r["ended_at"] else r["ended_at"]
            if started != r["started_at"] or ended != r["ended_at"]:
                db.execute("UPDATE charges SET started_at = ?, ended_at = ? WHERE id = ?",
                           (started, ended, r["id"]))
                fixed += 1
        if fixed:
            db.commit()
        set_setting(TZ_REPAIR_ZONE_KEY, chosen)
        # The bound is written ONCE, by the pass that actually converted wall-clock text, and never
        # raised afterwards. A charge entered later was already stored correctly for the zone in
        # force at the time; re-anchoring it on a later zone change would corrupt a right answer —
        # moving to another country doesn't change when you plugged in.
        if not prev_zone and rows:
            set_setting(TZ_REPAIR_MAXID_KEY, str(max(r["id"] for r in rows)))
        return fixed
    except Exception:      # noqa: BLE001 — a repair must never stop the app from starting
        return 0
    finally:
        db.close()


# ── REEV fuel purchases (user-logged refuels → the fuel WAC €/L blend) ────────────
# A REEV's per-trip fuel COST needs a price for the litres it burned. There's no price in the cloud,
# so the user logs each refuel here (litres + €/L, or total + litres). Web-owned table (create-if-
# missing, like command_log) because the data is entered from the web UI — no poller round-trip.
def _ensure_fuel_purchases(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS fuel_purchases ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_id INTEGER, ts TEXT NOT NULL, "
        "liters REAL NOT NULL, price_per_l REAL NOT NULL, total_cost REAL, "
        "fuel_before_pct REAL, note TEXT, created_at TEXT)")


def _fuel_before_pct(db: sqlite3.Connection, vehicle_id, ts: str):
    """The tank % measured just BEFORE `ts` — the residual fuel the WAC weights the refuel against
    (the fuel twin of a charge's start_soc). Snapshotted at insert time so the blend survives the
    positions log being pruned. None when the car logged no fuel level before then."""
    try:
        r = db.execute(
            "SELECT fuel_level_pct FROM positions WHERE vehicle_id = ? AND recorded_at <= ? "
            "AND fuel_level_pct IS NOT NULL ORDER BY recorded_at DESC LIMIT 1",
            (vehicle_id, ts)).fetchone()
        return r["fuel_level_pct"] if r else None
    except sqlite3.Error:
        return None


def add_fuel_purchase(ts: str, liters: float, price_per_l: Optional[float] = None,
                      total_cost: Optional[float] = None, note: Optional[str] = None,
                      fuel_before_pct: Optional[float] = None) -> int:
    """Log one REEV refuel. Either `price_per_l` or `total_cost` is enough — the other is derived
    (€/L = total/litres, or total = €/L·litres). Snapshots the tank % just before `ts` so the WAC
    weight is frozen against pruning. Feeds fuel_blended_price_at → the per-trip fuel cost.

    `fuel_before_pct` overrides that snapshot, and confirming a detected refuel is why it exists: the
    detection's own instant is the first reading that already shows the FULL tank, so re-deriving the
    residual from "the last reading at or before ts" would hand back the level after the fill."""
    liters = float(liters)
    if liters <= 0:
        raise ValueError("liters must be > 0")
    ppl = None if price_per_l in (None, "") else float(price_per_l)
    tot = None if total_cost in (None, "") else float(total_cost)
    if ppl is None and tot is None:
        raise ValueError("need price_per_l or total_cost")
    if ppl is None:
        ppl = tot / liters
    if tot is None:
        tot = ppl * liters
    if ppl <= 0:
        raise ValueError("price must be > 0")
    db = _conn_rw()
    try:
        _ensure_fuel_purchases(db)
        vrow = db.execute("SELECT id FROM vehicles ORDER BY id LIMIT 1").fetchone()
        vehicle_id = vrow["id"] if vrow else None
        fb = fuel_before_pct if fuel_before_pct is not None else _fuel_before_pct(db, vehicle_id, ts)
        cur = db.execute(
            "INSERT INTO fuel_purchases (vehicle_id, ts, liters, price_per_l, total_cost, "
            "fuel_before_pct, note, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (vehicle_id, ts, liters, round(ppl, 4), round(tot, 2), fb, note,
             datetime.now(timezone.utc).isoformat()))
        db.commit()
        return cur.lastrowid
    finally:
        db.close()


def list_fuel_purchases(limit: int = 200) -> list:
    """The user's refuels, newest first — for the Rifornimenti page and the tank state."""
    db = _conn_rw()
    try:
        _ensure_fuel_purchases(db)
        rows = db.execute(
            "SELECT id, ts, liters, price_per_l, total_cost, fuel_before_pct, note "
            "FROM fuel_purchases ORDER BY ts DESC, id DESC LIMIT ?", (int(limit),)).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


def get_fuel_calendar_month(year: int, month: int) -> dict:
    """Per-day totals for the Rifornimenti calendar's Month view (beta #14 @gm27271, seconded by
    @michapr): how many refuels, how many litres and how much they cost on each day, plus the
    month's own total. The twin of get_charges_calendar_month, and deliberately built the same way —
    the grid only ever needs ~31 small numbers; the day's actual entries load when a cell is clicked.

    A refuel has no duration and no end, so unlike a charge there is nothing to group: one row is one
    stop at the pump. `has_cost` mirrors the charges calendar, where a total of 0 and a total nobody
    has priced yet must not look the same."""
    days: dict[int, dict] = {}
    total = {"count": 0, "liters": 0.0, "cost": 0.0, "has_cost": False}
    for p in list_fuel_purchases(limit=1_000_000):
        dt = _local_dt(p.get("ts"))
        if dt is None or dt.year != year or dt.month != month:
            continue
        d = days.setdefault(dt.day, {"count": 0, "liters": 0.0, "cost": 0.0, "has_cost": False})
        for node in (d, total):
            node["count"] += 1
            node["liters"] = round(node["liters"] + (p.get("liters") or 0), 2)
            if p.get("total_cost") is not None:
                node["cost"] = round(node["cost"] + (p["total_cost"] or 0), 2)
                node["has_cost"] = True
    return {"year": year, "month": month, "days": days, "total": total}


def get_fuel_calendar_day(year: int, month: int, day: int) -> list[dict]:
    """One calendar day's refuels, newest first — the Month view's day drawer. Timestamps come back
    localised, like the charges twin, so the template can slice them without converting again."""
    out = []
    for p in list_fuel_purchases(limit=1_000_000):
        dt = _local_dt(p.get("ts"))
        if dt is None or (dt.year, dt.month, dt.day) != (year, month, day):
            continue
        p = dict(p)
        p["ts"] = _local_iso(p.get("ts"))
        out.append(p)
    return out


def delete_fuel_purchase(purchase_id: int) -> bool:
    db = _conn_rw()
    try:
        _ensure_fuel_purchases(db)
        cur = db.execute("DELETE FROM fuel_purchases WHERE id = ?", (int(purchase_id),))
        db.commit()
        return cur.rowcount > 0
    finally:
        db.close()


# ── Refuel auto-detection (beta #14 @gm27271) ───────────────────────────────────────────────────
# A tank can only rise one way: somebody put fuel in it. Nothing recuperates into it, nothing
# refills it while driving — so a rise in the car's OWN gauge *is* a refuel, and the only thing left
# to reject is the gauge's noise. That gives one rule and one guard:
#   • the level rises by at least _FUEL_DETECT_MIN_RISE_PCT between two consecutive readings, and
#   • it has not fallen back on the reading after that — a single high sample is a spike, not a fill.
# Deliberately NOTHING about gear or speed. A car can fall asleep at the pump and only report the
# new level when it wakes for the next drive, so demanding "parked in both frames" would lose
# exactly the refuels most worth catching.
#
# What we can and cannot know is the whole reason a detection is not a refuel until the user says so:
#   WHEN   an interval — after the last reading at the old level, by the first at the new one
#   LITRES an estimate — Δ% of a 50 L tank; the gauge is a float, not a flow meter
#   PRICE  never. The cloud has no idea what you paid, so that field is the user's and only his.
_FUEL_DETECT_MIN_RISE_PCT = 2.0   # 2 % of 50 L ≈ 1 L. The gauge itself steps at 0.1 % ≈ 50 mL, so
                                  # this is a noise floor, not a sensitivity limit — tune on real data.
_FUEL_DETECT_DEDUP_H = 12         # a rise this close to a refuel already logged is that same refuel


def _ensure_fuel_detected(db: sqlite3.Connection) -> None:
    """Detections live in their OWN table, never among the real refuels: until the user confirms one
    it must not move the tank value, the blended €/L or any month total. Confirming deletes the row
    (it becomes a fuel_purchases row); dismissing keeps it as status='dismissed' so the next scan
    cannot resurrect what he has already said no to."""
    db.execute(
        "CREATE TABLE IF NOT EXISTS fuel_detected ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_id INTEGER, ts TEXT NOT NULL, "
        "ts_from TEXT NOT NULL, liters REAL NOT NULL, "
        "fuel_before_pct REAL, fuel_after_pct REAL, "
        "status TEXT NOT NULL DEFAULT 'pending', created_at TEXT)")


def scan_fuel_refuels(vehicle_id: Optional[int] = None) -> int:
    """Walk the tank readings the car has already logged and record every rise as a pending refuel.
    Returns how many new ones it found.

    Runs over history, not as a live sentinel, which is the point: the first run finds the refuels
    from BEFORE the feature existed. It is incremental afterwards — a watermark holds the last
    reading examined — and idempotent regardless, because a rise is skipped when it was already
    dismissed or when a refuel is already logged near it."""
    vid = vehicle_id if vehicle_id is not None else _current_vehicle_id()
    if vid is None:
        return 0
    db = _conn_rw()
    try:
        _ensure_fuel_detected(db)
        _ensure_fuel_purchases(db)
        mark = get_setting("fuel_scan_watermark", "")
        rows = db.execute(
            "SELECT recorded_at, fuel_level_pct FROM positions "
            "WHERE vehicle_id = ? AND fuel_level_pct IS NOT NULL AND recorded_at >= ? "
            "ORDER BY recorded_at", (vid, mark or "")).fetchall()
        if len(rows) < 2:
            return 0
        found = 0
        for i in range(len(rows) - 1):
            before, after = rows[i]["fuel_level_pct"], rows[i + 1]["fuel_level_pct"]
            if after - before < _FUEL_DETECT_MIN_RISE_PCT:
                continue
            # Confirm the rise held: the next reading must not have dropped back to the old level.
            # (The last pair in the log has nothing after it — leave it for the next scan, when it
            # will have.)
            nxt = rows[i + 2]["fuel_level_pct"] if i + 2 < len(rows) else None
            if nxt is None or nxt < before + _FUEL_DETECT_MIN_RISE_PCT / 2:
                continue
            ts_from, ts = rows[i]["recorded_at"], rows[i + 1]["recorded_at"]
            lo = (_iso_shift(ts_from, -_FUEL_DETECT_DEDUP_H), _iso_shift(ts, _FUEL_DETECT_DEDUP_H))
            if db.execute("SELECT 1 FROM fuel_purchases WHERE (vehicle_id = ? OR vehicle_id IS NULL) "
                          "AND ts BETWEEN ? AND ? LIMIT 1", (vid, lo[0], lo[1])).fetchone():
                continue                                   # already logged by hand — same refuel
            if db.execute("SELECT 1 FROM fuel_detected WHERE vehicle_id = ? AND ts = ? LIMIT 1",
                          (vid, ts)).fetchone():
                continue                                   # already known (pending or dismissed)
            db.execute(
                "INSERT INTO fuel_detected (vehicle_id, ts, ts_from, liters, fuel_before_pct, "
                "fuel_after_pct, status, created_at) VALUES (?,?,?,?,?,?,'pending',?)",
                (vid, ts, ts_from, round((after - before) / 100.0 * _REEV_TANK_L, 2),
                 before, after, datetime.now(timezone.utc).isoformat()))
            found += 1
        # Stop one pair short: the final reading may yet be the "before" of a rise still arriving.
        db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('fuel_scan_watermark', ?)",
                   (rows[-2]["recorded_at"],))
        db.commit()
        return found
    except sqlite3.Error:
        return 0
    finally:
        db.close()


def _iso_shift(ts: str, hours: float) -> str:
    """`ts` moved by `hours`, as a stored-format ISO string — for the dedup window around a rise."""
    try:
        return (datetime.fromisoformat(str(ts)) + timedelta(hours=hours)).isoformat()
    except (ValueError, TypeError):
        return str(ts)


def list_fuel_detected(vehicle_id: Optional[int] = None) -> list:
    """Refuels Mate spotted and the user has not yet ruled on, newest first."""
    vid = vehicle_id if vehicle_id is not None else _current_vehicle_id()
    db = _conn_rw()
    try:
        _ensure_fuel_detected(db)
        rows = db.execute(
            "SELECT id, ts, ts_from, liters, fuel_before_pct, fuel_after_pct FROM fuel_detected "
            "WHERE vehicle_id = COALESCE(?, vehicle_id) AND status = 'pending' "
            "ORDER BY ts DESC, id DESC", (vid,)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []
    finally:
        db.close()


def confirm_fuel_detected(det_id: int, liters: Optional[float] = None,
                          price_per_l: Optional[float] = None,
                          total_cost: Optional[float] = None,
                          note: Optional[str] = None) -> Optional[int]:
    """Turn a detection into a real refuel. The litres may be corrected (the estimate is a gauge
    reading, the pump gave him a number); the price is his either way. Returns the new purchase id.

    The refuel is filed at the detection's OWN instant, not "now" — which is also why its residual
    is exact where a hand-typed one can only be as good as the time typed."""
    db = _conn_rw()
    try:
        _ensure_fuel_detected(db)
        row = db.execute("SELECT * FROM fuel_detected WHERE id = ? AND status = 'pending'",
                         (int(det_id),)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        db.close()
    if row is None:
        return None
    n_liters = float(liters) if liters else float(row["liters"])
    pid = add_fuel_purchase(row["ts"], n_liters, price_per_l=price_per_l,
                            total_cost=total_cost, note=note,
                            fuel_before_pct=row["fuel_before_pct"])
    db = _conn_rw()
    try:
        db.execute("DELETE FROM fuel_detected WHERE id = ?", (int(det_id),))
        db.commit()
    finally:
        db.close()
    return pid


def dismiss_fuel_detected(det_id: int) -> bool:
    """"That was not a refuel." Kept as a tombstone rather than deleted — the scan reads the same
    positions again and would otherwise offer it back every single time."""
    db = _conn_rw()
    try:
        _ensure_fuel_detected(db)
        cur = db.execute("UPDATE fuel_detected SET status = 'dismissed' WHERE id = ?", (int(det_id),))
        db.commit()
        return cur.rowcount > 0
    except sqlite3.Error:
        return False
    finally:
        db.close()


def latest_fuel_pct(vehicle_id: Optional[int] = None) -> Optional[float]:
    """Most recent tank % the car reported — for the Rifornimenti page's live tank state."""
    try:
        db = _get()
        r = db.execute(
            "SELECT fuel_level_pct FROM positions WHERE vehicle_id = COALESCE(?, vehicle_id) "
            "AND fuel_level_pct IS NOT NULL ORDER BY recorded_at DESC LIMIT 1",
            (vehicle_id if vehicle_id is not None else _current_vehicle_id(),)).fetchone()
        return r["fuel_level_pct"] if r else None
    except sqlite3.Error:
        return None


def upsert_vehicle(vin: str, car_type: str) -> None:
    """Pre-populate vehicles table from setup wizard (before first poller run)."""
    db = _conn_rw()
    db.execute(
        "INSERT OR IGNORE INTO vehicles (vin, car_type) VALUES (?,?)",
        (vin, car_type),
    )
    db.execute("UPDATE vehicles SET car_type=? WHERE vin=?", (car_type, vin))
    db.commit()


def get_vehicle():
    db = _get()
    # `ORDER BY id` is load-bearing, not decoration: `vin` is UNIQUE, so SQLite has a covering
    # index over (vin, rowid) and an unordered `LIMIT 1` is free to scan THAT instead of the
    # table — handing back whichever car sorts first by VIN. With one car it can't show; with
    # two it picks by VIN spelling. Scope to the selected car and pin the order.
    v = db.execute("SELECT * FROM vehicles WHERE id = COALESCE(?, id) ORDER BY id LIMIT 1",
                   (_current_vehicle_id(),)).fetchone()
    s = {r["key"]: r["value"] for r in db.execute("SELECT * FROM settings").fetchall()}
    return dict(v) if v else None, s


def clear_optimistic_status() -> None:
    """Remove the in-memory optimistic overlay (called when API does not confirm the command)."""
    global _opt_overrides, _opt_expiry
    _opt_overrides = {}
    _opt_expiry = 0.0


def extend_optimistic_status() -> None:
    """Re-arm the optimistic overlay's TTL while a command is still being verified.
    The post-command verification can poll the cloud for up to ~30s waiting for the
    car's state to propagate; without this the overlay would expire mid-wait and the
    UI would briefly flash the stale pre-command state (GitHub #34)."""
    global _opt_expiry
    if _opt_overrides:
        _opt_expiry = time.time() + _OPT_TTL


def write_optimistic_status(overrides: dict) -> None:
    """Copy the latest position row, apply field overrides, insert as new row.
       Also caches overrides in memory so get_latest_status() can re-apply them
       even if the poller overwrites the DB row before the UI refresh fires.
    """
    global _opt_overrides, _opt_expiry
    db = _conn_rw()
    # Clone the CURRENT vehicle's latest row (scoped) — an unscoped "latest" could clone another
    # car's position and insert the optimistic override under the wrong vehicle_id. No-op single-car.
    row = db.execute("SELECT * FROM positions WHERE vehicle_id = COALESCE(?, vehicle_id) ORDER BY id DESC LIMIT 1",
                     (_current_vehicle_id(),)).fetchone()
    if not row:
        return
    d = dict(row)
    d.pop("id")
    d["recorded_at"] = datetime.now(timezone.utc).isoformat()
    d.update(overrides)
    cols = ", ".join(d.keys())
    placeholders = ", ".join("?" for _ in d)
    db.execute(f"INSERT INTO positions ({cols}) VALUES ({placeholders})", list(d.values()))
    db.commit()
    _opt_overrides = dict(overrides)
    _opt_expiry = time.time() + _OPT_TTL


# ── GPS sign on the web write path (GitHub #158 — same root cause as #30/#43) ───────────
# The cloud sends the coordinates twice: signals 2/3 are SIGNED, 3724/3725 (and 2190/2191) are
# unsigned magnitudes. The poller resolves this properly (client._resolve_coord), but this
# module keeps its own copy of the parse for the after-a-command / Refresh-button write — and
# that copy read ONLY the unsigned pair. So a west-of-Greenwich car had its history stored
# correctly by the poller and then, on the very next Refresh, the NEWEST row filed at the
# mirrored longitude: Andreexylus' Lisbon B10 (-9.14) jumped to +9.14, i.e. the sea off
# Sardinia, which is what the Overview map shows. Everything east of Greenwich was unaffected,
# which is why it survived this long.
#
# The signed pair arrives in the very same dict get_fresh_signals() already returns, so it costs
# nothing to prefer it. When a poll omits it, re-apply the sign the poller persisted (#43) —
# the web is a separate process and can't see the poller's in-memory sign, but it can read the
# setting the poller writes. Unknown sign → magnitude as-is, i.e. exactly today's behaviour.
_COORD_SIGNALS = {"lat": ("3", ("3725", "2190")), "lon": ("2", ("3724", "2191"))}


def _coord_from_signals(signals: dict, axis: str) -> float:
    """One GPS axis from a raw signal dict: signed signal first, else magnitude × remembered sign."""
    def _f(raw) -> float:
        if raw in (None, ""):
            return 0.0
        try:
            return float(raw)
        except (TypeError, ValueError):
            return 0.0

    signed_id, unsigned_ids = _COORD_SIGNALS[axis]
    s = _f(signals.get(signed_id))
    if s != 0.0:
        return s                                   # authoritative — carries its own sign
    u = next((v for v in (_f(signals.get(i)) for i in unsigned_ids) if v != 0.0), 0.0)
    if u == 0.0:
        return 0.0
    try:
        sign = float(get_setting(f"gps_{axis}_sign", "0") or 0)
    except (TypeError, ValueError):
        sign = 0.0
    return abs(u) * (-1.0 if sign < 0 else 1.0)


def save_fresh_signals(signals: dict) -> None:
    """Write a fresh position row from raw API signals (called after a command)."""
    db = _conn_rw()
    # See get_vehicle(): an unordered LIMIT 1 rides the UNIQUE(vin) covering index and can name
    # the wrong car. This one WRITES a position row, so the wrong id would file live telemetry
    # under the other vehicle.
    vehicle_id = _current_vehicle_id()
    if vehicle_id is None:
        return

    def sig(key, default=0):  return int(signals.get(key) or default)
    def sigf(key, default=0.0): return float(signals.get(key) or default)

    def _is_charging() -> bool:
        """Charging only happens while PARKED, so the car must be stationary (gear P,
        speed ~0); plus the cable plugged in (1149) AND a real charge current (1178). The
        motion gate is essential: during regen the pack current is strongly negative (same
        sign as charging) and 1149 reads 1 spuriously, so without it driving is mistaken
        for charging. Signal 1939 (AC fan mode) is not used."""
        if int(signals.get("1010") or 0) != 0:   # gear R/N/D → moving
            return False
        try:
            if float(signals.get("1319") or 0) > 2.0:   # speed > 2 km/h → moving
                return False
        except (TypeError, ValueError):
            pass
        # 0 = unplugged, 5 = the drive-time cable code the REEVs emit while moving (never a
        # connection). Kept identical to the poller's _is_charging so both readers of 1149 agree.
        if int(signals.get("1149") or 0) in (0, 5):
            return False
        cur = signals.get("1178"); volt = signals.get("1177"); rem = signals.get("1200")
        try:    cur = float(cur) if cur is not None else None
        except (TypeError, ValueError): cur = None
        try:    volt = float(volt) if volt is not None else None
        except (TypeError, ValueError): volt = None
        power = abs(cur * volt) / 1000.0 if (cur is not None and volt is not None and abs(cur) >= 3.0) else None
        if cur is not None:
            if abs(cur) < 3.0:
                return False
            return rem is not None or (power is not None and power >= 1.0)
        if power is not None:
            return power >= 1.0 and rem is not None
        return int(signals.get("1149") or 0) == 2

    gear_map = {0: "P", 1: "R", 2: "N", 3: "D"}
    # Windows: flag OR position % (the T03 reports only the %, the B10 only the flag) — same shared
    # logic as the Vehicle page so the Overview tile / Commands grid agree with it (#62). use_pct is
    # gated by the capability profile, exactly as _parse_vehicle_status does.
    _wvin = (get_vehicle()[0] or {}).get("vin")
    _wstates = capability_profile.window_open_states(
        signals, bool(_wvin) and capability_profile.is_shown(_wvin, "windows_pct"))
    windows_open = int(any(_wstates))
    windows_open_count = sum(1 for w in _wstates if w)

    # Plug from signal 1149 (charge connection status), gated by motion. Signal 47
    # (acInputSlowCharge) latches at 1 for ~5 min after an AC charge on the B10 and does
    # NOT clear on unplug, so it cannot drive session-close; 1149 drops to 0 immediately.
    # 1149 reads 1 spuriously during regen at speed → suppress while moving (mirrors
    # _is_charging). 47 is only a fallback when 1149 is absent. See poller/client._is_plugged_in.
    def _is_plugged() -> bool:
        if int(signals.get("1010") or 0) != 0:          # gear R/N/D → moving
            return False
        try:
            if float(signals.get("1319") or 0) > 2.0:   # speed > 2 km/h → moving
                return False
        except (TypeError, ValueError):
            pass
        conn = signals.get("1149")
        if conn is None:
            return int(signals.get("47") or 0) == 1     # legacy fallback when 1149 absent
        try:
            # 3 is the third connected state the REEVs cycle THROUGH mid-charge (1→2→3→2, parked,
            # current ~0). The poller learned that in v2.8.4 — reading 3 as unplugged closed and
            # reopened the session on every flicker and shredded one slow AC charge into empty
            # fragments (beta #12/#13) — but this copy never got it and still disagreed with
            # poller/client._is_plugged_in. 5 stays out: that one is the drive-time cable code.
            return int(conn) in (1, 2, 3)
        except (TypeError, ValueError):
            return False
    plug_connected = _is_plugged()

    db.execute(
        """INSERT INTO positions (
            vehicle_id, recorded_at,
            latitude, longitude, speed_kmh, odometer_km,
            soc, range_km, gear, charging,
            battery_min_temp, climate_target_temp, inside_temp,
            is_locked, climate_on, plug_connected,
            climate_cooling, climate_heating, climate_defrost,
            trunk_open, windows_open, sunshade_open,
            remaining_charge_min, charge_voltage_v, charge_current_a, charge_completed, security_active,
            windows_open_count,
            door_driver_open, door_passenger_open, door_rear_left_open, door_rear_right_open,
            window_fl_open, window_rl_open, ac_port_mode,
            fan_level, recirculation, climate_mode,
            fuel_level_pct, fuel_range_km, combined_range_km
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            vehicle_id,
            datetime.now(timezone.utc).isoformat(),
            _coord_from_signals(signals, "lat"),   # signed pair first (#158) — never the bare
            _coord_from_signals(signals, "lon"),   # unsigned magnitude, or west cars land at sea
            sigf("1319"), sigf("1318"),
            sigf("100003") or sigf("1204"),
            sigf("3260"),
            gear_map.get(sig("1010"), "P"),
            int(_is_charging()),
            sigf("1182"), sigf("2183"), sigf("1349"),
            sig("1298"), sig("1938"), int(plug_connected),
            int(sig("2669") == 2), int(sig("2681") == 2), int(sig("1945") == 2),
            sig("1281"), windows_open, sig("1724"),
            sig("1200") or None,
            sigf("1177") or None,
            sigf("1178") or None,
            int(int(signals.get("3736") or 0) != 0),
            int(int(signals.get("1255") or 0) != 0),
            windows_open_count,
            1 if sig("1277") else 0, 1 if sig("1278") else 0,
            1 if sig("1279") else 0, 1 if sig("1280") else 0,
            1 if _wstates[0] else 0, 1 if _wstates[2] else 0,
            int(signals.get("47") or 0),     # ac_port_mode — same as the poller; without it this
                                             # web-side write left NULL, fragmenting V2L sessions (#)
            sig("1941") or None,             # fan_level (1941 acAirVolume 1-7; 0 → NULL = no data)
            int(sig("1943") == 1),           # recirculation (1=recirc/in, 0=fresh/out)
            int(signals["3713"]) if signals.get("3713") is not None else None,  # climate_mode (3713)
            # REEV dual-energy (mirror the poller's save_position): fuel level % (3235) MUST be None on a
            # BEV — sigf() would coerce absent → 0.0 and wrongly trip the "has fuel" guard at 0%.
            float(signals["3235"]) if signals.get("3235") is not None else None,
            sigf("3259") or None, sigf("3261") or None,   # fuel range (3259) + combined range (3261)
        ),
    )
    db.commit()


def get_latest_status() -> Optional[dict]:
    db = _get()
    row = db.execute(
        "SELECT * FROM positions WHERE vehicle_id = COALESCE(?, vehicle_id) ORDER BY id DESC LIMIT 1",
        (_current_vehicle_id(),)
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    # Apply in-memory optimistic overrides if still within TTL
    if time.time() < _opt_expiry and _opt_overrides:
        d.update(_opt_overrides)
    # GPS fallback: a poll can come back with no fix → (0,0). Don't let that blank the Overview
    # map (or reset Navigation's start point) — fall back to the last position that had a real
    # fix and flag it stale, so the last known location keeps showing. Only a true (0,0)/null is
    # treated as "no fix" (a car genuinely on the prime meridian at lon 0 is kept).
    _lat, _lon = d.get("latitude"), d.get("longitude")
    if _lat is None or _lon is None or (abs(_lat) < 1e-6 and abs(_lon) < 1e-6):
        last = db.execute(
            "SELECT latitude, longitude FROM positions "
            "WHERE vehicle_id = COALESCE(?, vehicle_id) "
            "AND latitude IS NOT NULL AND longitude IS NOT NULL "
            "AND NOT (ABS(latitude) < 1e-6 AND ABS(longitude) < 1e-6) "
            "ORDER BY id DESC LIMIT 1", (_current_vehicle_id(),)).fetchone()
        if last:
            d["latitude"], d["longitude"] = last["latitude"], last["longitude"]
            d["position_stale"] = True
    # Charge power: positions stores current/voltage, not a power column. Compute it
    # (|I×V|), only when the charge current is meaningful (>=3A). Signal 49 is NOT a
    # power (it's the left-mirror-heating flag) and must never be used here.
    cur_a = d.get("charge_current_a")
    volt_v = d.get("charge_voltage_v")
    if cur_a is not None and volt_v is not None and abs(cur_a) >= 3.0:
        d["charge_power_kw"] = round(abs(cur_a * volt_v) / 1000.0, 2)
    else:
        d["charge_power_kw"] = 0.0
    # "Ventilating" = the REAL vent mode (signal 3713 climate_mode == 4), gated on A/C being on
    # (modes persist when off). The old derive-by-absence wrongly lit up for plain A/C-on / AUTO
    # (mode 0 = A/C on but not yet cooling) — confirmed on-car 2026-06-21.
    d["climate_venting"] = bool(d.get("climate_on")) and d.get("climate_mode") == 4
    # REEV only: drop the car's "charge complete" flag entirely — on a range-extender it marks a
    # charge in PROGRESS, not a finished one (see the note beside this file's constants). The raw
    # value is kept under another name so a diagnostics bundle still shows what the car said.
    # Pure EVs are untouched.
    if get_setting("is_reev", "0") == "1":
        d["charge_completed_raw"] = 1 if d.get("charge_completed") else 0
        d["charge_completed"] = 0
    # How long ago
    try:
        ts = datetime.fromisoformat(d["recorded_at"])
        now = datetime.now(timezone.utc)
        delta = int((now - ts).total_seconds())
        # Raw seconds for the templates, which render it in the reader's language via ago() — this
        # module has no translator. `last_seen` below stays as it is: English, and the only string
        # on the Overview that never spoke anyone else's language. It survives for any consumer
        # that still reads it; nothing on screen does.
        d["last_seen_s"] = delta
        if delta < 60:
            d["last_seen"] = f"{delta}s ago"
        elif delta < 3600:
            d["last_seen"] = f"{delta // 60}m ago"
        else:
            d["last_seen"] = f"{delta // 3600}h ago"
    except Exception:
        d["last_seen"] = "unknown"
    _data_age(d)
    # OTA / software-update status (the poller scans the account message inbox for an update notice).
    d["ota"] = get_ota_status()
    return d


# How far the data must fall BEHIND THE ROW before the Overview says so. Comfortably above every
# poll cadence (10s driving, 60s charging), so a slow-but-genuine update is never called stale.
DATA_AGE_STALE_S = 300


def _data_age(d: dict) -> None:
    """Age of the DATA, as opposed to the age of the row (#178 @riri19).

    `last_seen` is now − when Mate wrote the row: it is always a few seconds, because Mate polls
    on a timer and the cloud always answers. When the car can't reach the cloud, the cloud re-serves
    the last frame it received — so a fresh row can carry half-hour-old contents, and the Overview
    looks healthy while the car is out of touch. `frame_ts` is the car's own clock on that frame, so
    now − frame_ts is how old what you're reading really is.

    It is shown only when it says something, and TWO conditions gate it.

    First, the data must have fallen behind THE ROW, not merely be old in absolute terms. The two
    ages usually move together — if Mate itself hasn't polled for nine minutes, then "9 min ago" and
    "data 9m old" are the same fact printed twice, which is the duplicate-number defect we've been
    told about before. What's worth saying is the DIVERGENCE: Mate keeps getting answers while the
    car behind them has stopped moving.

    Second, the last frame must have had the car DRIVING or CHARGING. A car asleep in a garage
    overnight legitimately has hours-old data, and announcing that every morning is the "light that
    cries wolf every night" we turned down in #130. Parked and unplugged, Mate stays quiet.
    """
    d["data_age"] = None
    d["data_age_s"] = None
    ts = d.get("frame_ts")
    if not ts:
        return                       # car doesn't report its own clock → nothing honest to say
    try:
        age = int((datetime.now(timezone.utc) - datetime.fromtimestamp(int(ts) / 1000, timezone.utc))
                  .total_seconds())
    except Exception:  # noqa: BLE001
        return
    if age < 0:                      # car clock ahead of the host — not a staleness signal
        return
    d["data_age_s"] = age
    moving = bool(d.get("charging")) or (d.get("gear") == "D") or float(d.get("speed_kmh") or 0) > 0
    behind = age - int(d.get("last_seen_s") or 0)      # how far the DATA trails the ROW
    if behind < DATA_AGE_STALE_S or not moving:
        return
    d["data_age"] = (f"{age // 60}m" if age < 3600 else
                     f"{age // 3600}h {(age % 3600) // 60}m" if age < 86400 else
                     f"{age // 86400}d")


def get_ota_status() -> dict:
    """OTA / software-update status the poller stored (from scanning the account inbox). Returns
    {available:bool, title:str|None, time:str|None (localized "dd/mm HH:MM")}. False until the
    poller has run a check; only ever True when an update notice is actually present."""
    available = get_setting("ota_available", "") == "1"
    title = get_setting("ota_title", "") or None
    when = None
    raw = get_setting("ota_time", "")
    if raw:
        try:
            dt = datetime.fromtimestamp(int(raw) / 1000, tz=timezone.utc)
            when = (_local_dt(dt.isoformat()) or dt).strftime("%d/%m %H:%M")
        except (TypeError, ValueError, OSError):
            when = None
    return {"available": available, "title": title, "time": when}


def delete_trip(trip_id: int) -> bool:
    """Permanently remove a trip and its GPS track. Returns True if a trip was deleted.
    Day/month/lifetime trip totals recompute from the DB, so they update automatically."""
    db = _conn_rw()
    # Deleting a merged trip removes the whole group (the parent + every child) and their tracks.
    ids = [trip_id] + [r["id"] for r in db.execute(
        "SELECT id FROM trips WHERE merged_into_id=?", (trip_id,)).fetchall()]
    ph = ",".join("?" * len(ids))
    cur = db.execute(f"DELETE FROM trips WHERE id IN ({ph})", ids)
    db.execute(f"DELETE FROM trip_positions WHERE trip_id IN ({ph})", ids)
    db.commit()
    return cur.rowcount > 0


# ── Phase 2: per-trip EC (driving) energy enrichment ─────────────────────────
# The cloud getEC endpoint gives the official DRIVING-energy split (Guida/AC/Altro) for a trip's
# exact window. We enrich NEW trips (after the feature's cutoff) and, when enabled, make EC the
# trip's energy — backing up the SoC value so it's fully reversible. Old trips stay SoC.
def _trip_epoch(s):
    """A stored trip timestamp (UTC ISO, possibly naive) → epoch seconds, or None."""
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return int(d.timestamp())
    except Exception:
        return None


def trip_epoch_window(trip: dict):
    """(begin_ts, end_ts) for a trip dict."""
    return _trip_epoch(trip.get("started_at")), _trip_epoch(trip.get("ended_at"))


def trip_ec_window(trip: dict, pad_s: int = 120):
    """Window for the getEC QUERY.

    getEC stamps a driving session's whole energy at ONE instant — the cloud anchor ≈ the real
    Ready-on (power-on). A query [begin, end] returns that energy only when begin ≤ anchor ≤ end. So:
      START = on_lo, the LAST ready=0 sample before the session (the car was provably OFF there →
              guaranteed ≤ the anchor, at ANY poll cadence). NOT sess["on"] (the first ready=1 poll):
              that can sit up to a poll interval (~30 s cold) AFTER the anchor → getEC None and the
              trip wrongly drops to SoC (#117 — verified same trip: one account caught it at
              on=08:33:09, another missed at 08:33:13, a 4 s knife-edge; on_lo=08:32:59 catches both).
              No magic pad. FALLBACK (no ready data): T0 − pad_s clamped to the previous trip midpoint.
      END   = T1, with NO padding — the energy is at the START anchor, so any end past it works; T1 is
              always past the anchor, and T1 + pad would risk the FUTURE (None) / the next trip.

    CAVEAT — the cloud's SESSION ≠ Mate's TRIP. The session runs from READY (power-on) until the car
    is switched OFF, so it can span SEVERAL Mate trips + long idle in Park (verified 22/06: trips
    133+134, the car never powered off between them → ONE session anchored at the first start; the
    second trip's window, being AFTER that anchor, returns None). Consequences: the FIRST drive after
    Ready catches the anchor and gets the WHOLE session (which may include pre-drive climate / idle /
    later drives → can over-read); a LATER drive in the same no-power-off run sits past the anchor →
    getEC returns None → the trip stays on the SoC estimate. The bigger the Ready→D gap (sitting in
    Ready with climate before shifting to D), the more likely a later trip misses it. Upstream (cloud
    session definition, not fixable here); ec_enrich._ec_implausible catches the absurd over-reads.
    Returns (begin_ts, end_ts) or (None, None)."""
    b, e = trip_epoch_window(trip)
    if not b or not e:
        return (None, None)
    # PRIMARY: begin = on_lo (last ready=0 before the session) — provably ≤ the cloud anchor at any
    # cadence, so getEC always catches it. NOT sess["on"] (first ready=1 poll), which can land a poll
    # interval AFTER the anchor → None (#117). end stays T1 (always past the start anchor).
    sess = ready_session(trip)
    if sess and sess.get("on_lo") is not None:
        return (int(sess["on_lo"]), int(e))
    # FALLBACK (no ready data, or no off-sample before the session): T0 − pad, clamped to prev midpoint.
    db = _get()
    begin = b - pad_s
    prev = db.execute(
        "SELECT MAX(ended_at) AS m FROM trips WHERE vehicle_id = COALESCE(?, vehicle_id) "
        "AND merged_into_id IS NULL "
        "AND ended_at IS NOT NULL AND ended_at < ?", (_current_vehicle_id(), trip.get("started_at"))).fetchone()
    if prev and prev["m"]:
        pe = _trip_epoch(prev["m"])
        if pe:
            begin = max(begin, (pe + b) // 2)
    return (int(begin), int(e))


_READY_DEBOUNCE_S = 90        # ignore ready=0 dips shorter than this — signal blips seen in the log
_READY_LOOKBACK_S = 6 * 3600  # how far around the trip to scan positions for the session bounds


def ready_session(trip: dict):
    """Reconstruct the car's power-on session (READY/ON3, PID 1258) that brackets this trip, from the
    per-poll `positions.ready` log. The cloud's getEC session runs from Ready-ON to power-OFF and can
    span SEVERAL Mate trips + idle (verified 22/06: trips 133+134 = one session) → this is the REAL
    getEC window AND tells us whether a trip shares its session with others.

    Returns {on, off, n_trips, trip_ids} (epoch seconds) or None when no ready data covers the trip
    (old trips before the signal existed → caller falls back to the T0−2min window). Brief ready=0
    dips shorter than _READY_DEBOUNCE_S are treated as still-on (blips)."""
    t0, t1 = _trip_epoch(trip.get("started_at")), _trip_epoch(trip.get("ended_at"))
    if not t0 or not t1:
        return None
    db = _get()
    lo = datetime.fromtimestamp(t0 - _READY_LOOKBACK_S, timezone.utc).isoformat()
    hi = datetime.fromtimestamp(t1 + _READY_LOOKBACK_S, timezone.utc).isoformat()
    rows = db.execute(
        "SELECT recorded_at, ready FROM positions WHERE vehicle_id = COALESCE(?, vehicle_id) "
        "AND recorded_at >= ? AND recorded_at <= ? "
        "ORDER BY recorded_at", (_current_vehicle_id(), lo, hi)).fetchall()
    samples, last = [], None
    for r in rows:
        e = _trip_epoch(r["recorded_at"])
        if e is None:
            continue
        rd = r["ready"]
        rd = (last if last is not None else 0) if rd is None else rd  # carry-forward unknown
        last = rd
        samples.append((e, rd))
    if not any(rd for _, rd in samples):
        return None                          # no ready=1 anywhere → no session info
    # Build ready=1 runs, then merge runs separated by a ready=0 gap shorter than the debounce.
    runs, cur = [], None
    for e, rd in samples:
        if rd == 1:
            cur = [e, e] if cur is None else [cur[0], e]
        elif cur is not None:
            runs.append(cur); cur = None
    if cur is not None:
        runs.append(cur)
    merged = []
    for run in runs:
        if merged and run[0] - merged[-1][1] < _READY_DEBOUNCE_S:
            merged[-1][1] = run[1]
        else:
            merged.append(list(run))
    # The session = the run that brackets the trip (small slack: the gear-P trip-end lags ready-off
    # by ~1 min, and ready-on can sit a poll after T0).
    sess = next(((s, e) for s, e in merged
                 if s - _READY_DEBOUNCE_S <= t0 and t1 <= e + _READY_DEBOUNCE_S), None)
    if sess is None:                         # fallback: any run overlapping the trip
        sess = next(((s, e) for s, e in merged if not (e < t0 or s > t1)), None)
    if sess is None:
        return None
    on, off = sess
    # on_lo = last ready=0 sample BEFORE the run = lower bracket of the real Ready-on. The true
    # power-on (= getEC anchor) sits between on_lo and `on` (≤ one poll interval), so on_lo is
    # provably ≤ the anchor → the safe getEC begin (see trip_ec_window). None only if the run starts
    # at the scan edge with no preceding off-sample (caller then uses its fallback).
    on_lo = max((ts for ts, rd in samples if ts < on and rd == 0), default=None)
    # Count finalized, non-merged trips whose span falls inside the session.
    olo = datetime.fromtimestamp(on - _READY_DEBOUNCE_S, timezone.utc).isoformat()
    ohi = datetime.fromtimestamp(off + _READY_DEBOUNCE_S, timezone.utc).isoformat()
    trs = db.execute(
        "SELECT id, started_at, ended_at FROM trips WHERE vehicle_id = COALESCE(?, vehicle_id) "
        "AND merged_into_id IS NULL "
        "AND ended_at IS NOT NULL AND ended_at >= ? AND started_at <= ? ORDER BY started_at",
        (_current_vehicle_id(), olo, ohi)).fetchall()
    ids = []
    for tr in trs:
        ts0, ts1 = _trip_epoch(tr["started_at"]), _trip_epoch(tr["ended_at"])
        if ts0 and ts1 and ts0 >= on - _READY_DEBOUNCE_S and ts1 <= off + _READY_DEBOUNCE_S:
            ids.append(tr["id"])
    return {"on": int(on), "off": int(off),
            "on_lo": int(on_lo) if on_lo is not None else None,
            "n_trips": len(ids), "trip_ids": ids}


def get_trips_needing_ec(cutoff_iso: str, limit: int = 5, min_age_s: int = 600,
                         giveup_age_s: int = 6 * 3600) -> list[dict]:
    """Finalized, non-merged trips started on/after `cutoff_iso` whose cloud EC isn't STABLE yet,
    within the re-fetchable window: ended between `giveup_age_s` and `min_age_s` ago. The cloud
    aggregates a fresh trip's EC with a lag and writes it incrementally, so we keep re-reading
    (store_trip_ec overwrites with the latest) until two equal reads lock it (ec_stable=1) or it
    ages out. Returns ec_kwh too so the sweep can compare to the previous read. Skips zero-distance."""
    now = datetime.now(timezone.utc)
    not_after = (now - timedelta(seconds=min_age_s)).isoformat()      # ended_at <= this (old enough)
    not_before = (now - timedelta(seconds=giveup_age_s)).isoformat()  # ended_at >= this (not too old)
    db = _conn_rw()
    rows = db.execute(
        """SELECT id, started_at, ended_at, distance_km, ec_kwh,
                  efficiency_kwh_100km, efficiency_soc, start_soc, end_soc FROM trips
           WHERE vehicle_id = COALESCE(?, vehicle_id) AND merged_into_id IS NULL AND ended_at IS NOT NULL
             AND started_at >= ? AND ended_at <= ? AND ended_at >= ?
             AND COALESCE(ec_stable, 0) = 0 AND COALESCE(ec_tried, 0) < 80 AND distance_km > 0
             AND COALESCE(reconstructed, 0) = 0
           ORDER BY started_at DESC LIMIT ?""",
        (_current_vehicle_id(), cutoff_iso, not_after, not_before, int(limit))).fetchall()
    return [dict(r) for r in rows]


def store_trip_ec(trip_id: int, ec: Optional[dict], distance_km, apply_energy: bool,
                  stable: bool = False) -> None:
    """Record an EC enrichment attempt. Always bumps ec_tried. With data: store the split + total
    (overwriting any earlier partial read), back up the SoC efficiency once, and (if apply_energy)
    override efficiency_kwh_100km with the EC-derived value. `stable=True` locks the trip
    (ec_stable=1) so the sweep stops re-fetching it."""
    db = _conn_rw()
    if not ec:
        db.execute("UPDATE trips SET ec_tried = COALESCE(ec_tried, 0) + 1 WHERE id=?", (trip_id,))
        db.commit()
        return
    drv, ac, oth, tot = ec.get("driving_kwh"), ec.get("ac_kwh"), ec.get("other_kwh"), ec.get("total_kwh")
    db.execute(
        """UPDATE trips SET ec_tried = COALESCE(ec_tried, 0) + 1,
               ec_kwh=?, ec_driving=?, ec_ac=?, ec_other=?, ec_stable=?
           WHERE id=?""",
        (tot, drv, ac, oth, 1 if stable else 0, trip_id))
    # Override the trip's energy/efficiency only once the EC is STABLE — a fresh trip's cloud value
    # is written incrementally, so applying an early partial read would show a wrong figure. Back up
    # the SoC efficiency at the same moment so the override stays exactly reversible.
    if apply_energy and stable and tot and distance_km and distance_km > 0:
        # REEV: never let getEC (electric energy spread over the FULL distance) become the trip's
        # efficiency when the range-extender ran — that's exactly the diluted ~0.5 figure we suppress
        # (beta #10). The AND-NOT self-gates to REEV engine-on trips; BEV/pure-EV trips override as before.
        db.execute(
            """UPDATE trips SET efficiency_soc = COALESCE(efficiency_soc, efficiency_kwh_100km),
                   efficiency_kwh_100km=? WHERE id=?
               AND NOT (fuel_start_pct IS NOT NULL AND fuel_end_pct IS NOT NULL
                        AND fuel_start_pct - fuel_end_pct > ?)""",
            (round(tot / distance_km * 100, 1), trip_id, _REEV_FUEL_MIN_DROP))
    db.commit()


def apply_ec_trip_energy() -> int:
    """Flag ON: make EC the energy for every trip that has EC data (backing up SoC first)."""
    db = _conn_rw()
    cur = db.execute(
        """UPDATE trips SET efficiency_soc = COALESCE(efficiency_soc, efficiency_kwh_100km),
               efficiency_kwh_100km = ROUND(ec_kwh / distance_km * 100, 1)
           WHERE ec_kwh IS NOT NULL AND ec_stable = 1 AND distance_km > 0
             AND NOT (fuel_start_pct IS NOT NULL AND fuel_end_pct IS NOT NULL
                      AND fuel_start_pct - fuel_end_pct > ?)""", (_REEV_FUEL_MIN_DROP,))
    db.commit()
    return cur.rowcount


def revert_ec_trip_energy() -> int:
    """Flag OFF: restore the original SoC efficiency for every overridden trip."""
    db = _conn_rw()
    cur = db.execute(
        "UPDATE trips SET efficiency_kwh_100km = efficiency_soc WHERE efficiency_soc IS NOT NULL")
    db.commit()
    return cur.rowcount


def revert_trip_ec(trip_id: int) -> bool:
    """Undo ONE trip's getEC conversion ('Revert to estimate' button): restore the SoC efficiency
    backed up at apply time, drop the EC split, and clear the lock so the trip shows the estimate
    again (and the Convert button comes back). ec_tried is parked at the sweep's give-up threshold
    (see get_trips_needing_ec: `ec_tried < 80`) so the background sweep won't silently re-convert a
    trip the user explicitly reverted — a manual Convert still works (convert_trip ignores ec_tried).
    Only touches trips that were actually converted (efficiency_soc set). Returns True if reverted."""
    db = _conn_rw()
    cur = db.execute(
        """UPDATE trips
              SET efficiency_kwh_100km = COALESCE(efficiency_soc, efficiency_kwh_100km),
                  ec_kwh = NULL, ec_driving = NULL, ec_ac = NULL, ec_other = NULL,
                  ec_stable = 0, ec_tried = 80
            WHERE id = ? AND efficiency_soc IS NOT NULL""",
        (trip_id,))
    db.commit()
    return cur.rowcount > 0


def delete_charge(charge_id: int) -> bool:
    """Permanently remove a charge session. Returns True if one was deleted. Day/month/lifetime
    charge totals recompute from the DB automatically. The shared per-poll positions log is untouched."""
    db = _conn_rw()
    cur = db.execute("DELETE FROM charges WHERE id=?", (charge_id,))
    db.commit()
    return cur.rowcount > 0


# ── Command responsiveness log (car↔cloud reachability proxy) ────────────────
# A remote command is the ONLY moment Mate talks to the car in real time — polls just read
# the cloud's CACHED state, so they succeed even when the car has weak coverage. Logging each
# command's outcome therefore measures how responsive the car itself is (a proxy for the
# cellular coverage where it's parked) — which is exactly what a "cloud OK but car didn't
# confirm" timeout is telling us. This is why one user can see timeouts while everyone else is fine.
def _ensure_command_log(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS command_log ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, "
        "action TEXT, outcome TEXT NOT NULL, latency_ms INTEGER)")


def log_command(action: str, outcome: str, latency_ms: Optional[int] = None) -> None:
    """Record one remote-command outcome (confirmed|timeout_car|cloud_unreachable|rejected).
    Best-effort: never raises into the command path. Keeps ~90 days."""
    try:
        db = _conn_rw()
        _ensure_command_log(db)
        db.execute("INSERT INTO command_log (ts, action, outcome, latency_ms) VALUES (?,?,?,?)",
                   (datetime.now(timezone.utc).isoformat(), action, outcome, latency_ms))
        db.execute("DELETE FROM command_log WHERE ts < ?",
                   ((datetime.now(timezone.utc) - timedelta(days=90)).isoformat(),))
        db.commit()
    except Exception:
        pass


def command_responsiveness(last_n: int = 24, min_samples: int = 3) -> dict:
    """How reliably the car answers commands — a proxy for its cellular coverage. Window is by
    COUNT (the LAST `last_n` commands), NOT by time: it stays visible between command sessions
    and recovers to green within ~last_n good commands (old timeouts scroll out). Only
    'confirmed' vs 'timeout_car' count (a cloud/network or auth failure isn't the car's fault).
    ALWAYS returns a dict so the badge stays visible — state='unknown' until min_samples commands."""
    rows = []
    try:
        db = _conn_rw()
        _ensure_command_log(db)
        rows = db.execute(
            "SELECT outcome, latency_ms FROM command_log "
            "WHERE outcome IN ('confirmed','timeout_car') ORDER BY id DESC LIMIT ?",
            (last_n,)).fetchall()
    except Exception:
        rows = []
    total = len(rows)
    if total < min_samples:
        return {"state": "unknown", "confirmed": 0, "timeouts": 0, "total": total,
                "rate": None, "last_n": last_n, "avg_latency_ms": None}
    confirmed = sum(1 for r in rows if r["outcome"] == "confirmed")
    lat = [r["latency_ms"] for r in rows
           if r["outcome"] == "confirmed" and r["latency_ms"] is not None]
    rate = confirmed / total
    state = ("responsive" if rate >= 0.8 else
             "intermittent" if rate >= 0.4 else "unresponsive")
    return {"state": state, "confirmed": confirmed, "timeouts": total - confirmed,
            "total": total, "rate": round(rate, 2), "last_n": last_n,
            "avg_latency_ms": int(sum(lat) / len(lat)) if lat else None}


# ── Manual trip merge (reversible) ──────────────────────────────────────────────
# A merged trip is a parent + child trips (merged_into_id = parent.id), joined by the user when
# a journey was split by a SHORT, NON-charging stop. Nothing is deleted or overwritten — the group
# stats are computed on the fly, so "unmerge" restores the originals exactly.
TRIP_MERGE_GAP_DEFAULT = 5    # minutes — a stop under this is plausibly ONE continuous drive split by
                              # a brief pause (lights/gate/quick drop-off). A 15-30 min stop is a real
                              # destination = two separate trips → never auto-suggested for merge. The
                              # merge UI slider still opens up to TRIP_MERGE_GAP_MAX for manual merges.
TRIP_MERGE_GAP_MIN = 5
TRIP_MERGE_GAP_MAX = 90


def _gap_minutes(end_iso, start_iso):
    """Minutes from end_iso to start_iso (raw stored UTC ISO). None if unparseable."""
    try:
        return (datetime.fromisoformat(start_iso) - datetime.fromisoformat(end_iso)).total_seconds() / 60.0
    except (TypeError, ValueError):
        return None


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


_FROZEN_MIN_SPEED_KMH = 8.0   # below this a real stop (red light / traffic) legitimately repeats
                              # identical soc/position — never flag it, only above-floor "cruising"
_FROZEN_POS_EPS_KM = 0.03     # ~30m — GPS-precision-level "hasn't moved", well under what even a
                              # slow-cruising car covers over one real polling interval
_FROZEN_SOC_EPS = 0.1         # % — SoC ticks are quantized; under this counts as "unchanged"
_FROZEN_SPEED_EPS_KMH = 0.5
_FROZEN_MIN_RUN_S = 60        # shorter runs are more likely 1-2 coincidental polls than a genuine
                              # stuck cloud cache — restore them rather than risk dropping real data


def _telemetry_frozen(prev: dict, cur: dict) -> bool:
    """True when `cur` repeats `prev`'s speed, SoC AND position while claiming a real driving speed —
    physically impossible for a moving car, and the substance-level twin of the write-time stale-frame
    guard (poller/recorder.py #128): that guard catches an identical RAW cloud timestamp, but if the
    cloud re-serves a cached snapshot wrapped in a FRESH timestamp each poll, the payload underneath —
    speed, SoC, GPS — stays frozen while the timestamp moves, and #128's identity check misses it."""
    if cur.get("speed_kmh") is None or prev.get("speed_kmh") is None:
        return False
    if (cur["speed_kmh"] or 0) < _FROZEN_MIN_SPEED_KMH:
        return False
    if abs(cur["speed_kmh"] - prev["speed_kmh"]) >= _FROZEN_SPEED_EPS_KMH:
        return False
    if cur.get("soc") is None or prev.get("soc") is None or abs(cur["soc"] - prev["soc"]) >= _FROZEN_SOC_EPS:
        return False
    if cur.get("latitude") is None or prev.get("latitude") is None:
        return False
    return _haversine_km(prev["latitude"], prev["longitude"], cur["latitude"], cur["longitude"]) < _FROZEN_POS_EPS_KM


def _filter_frozen_telemetry(positions: list[dict]) -> list[dict]:
    """Drop a run of trip_positions samples where the cloud kept re-serving a CACHED vehicle snapshot —
    speed/SoC/GPS frozen — while its own wrapper timestamp still advanced each poll (see
    _telemetry_frozen). Every sample here is DRIVING by construction (trip_positions only records
    driving polls), so a run this way above walking pace is never legitimate. Runs AFTER the fact, so
    it also cleans up trips already recorded with a live cloud hiccup. Keeps the point right before
    the freeze as the last-known-good anchor and the resume point after it; the resulting recorded_at
    gap is exactly what the trip-profile chart already renders as a break. A run shorter than
    _FROZEN_MIN_RUN_S is left alone: too brief to trust over the risk of discarding real data."""
    n = len(positions)
    if n < 3:
        return positions
    dup = [False] * n
    for i in range(1, n):
        dup[i] = _telemetry_frozen(positions[i - 1], positions[i])
    drop = [False] * n
    i = 1
    while i < n:
        if not dup[i]:
            i += 1
            continue
        j = i
        while j < n and dup[j]:
            j += 1
        span = _gap_minutes(positions[i - 1].get("recorded_at"), positions[j - 1].get("recorded_at"))
        if span is None or span * 60 >= _FROZEN_MIN_RUN_S:
            for k in range(i, j):
                drop[k] = True
        i = j
    return [p for p, d in zip(positions, drop) if not d]


def _interpolate_elevation(positions: list[dict]) -> list[dict]:
    """Fill elevation_m gaps BETWEEN two known samples by linear interpolation over elapsed time.
    Legitimate because altitude changes physically continuously (unlike SoC/speed, which can have
    genuine jumps) — the same technique route-elevation profiles use to draw a smooth line from sparse
    samples. A leading/trailing gap (no known value on ONE side) is left None — never extrapolated
    beyond what was actually measured. Mutates and returns `positions`."""
    known = [i for i, p in enumerate(positions) if p.get("elevation_m") is not None]
    if len(known) < 2:
        return positions
    epochs = [_trip_epoch(p.get("recorded_at")) for p in positions]
    for a, b in zip(known, known[1:]):
        ea, eb = epochs[a], epochs[b]
        if ea is None or eb is None or eb <= ea:
            continue
        va, vb = positions[a]["elevation_m"], positions[b]["elevation_m"]
        for i in range(a + 1, b):
            if epochs[i] is None:
                continue
            frac = (epochs[i] - ea) / (eb - ea)
            positions[i]["elevation_m"] = va + (vb - va) * frac
    return positions


def _children_by_parent(db) -> dict:
    """All merged child trips grouped by parent id (one query)."""
    out: dict = {}
    for r in db.execute("SELECT * FROM trips WHERE vehicle_id = COALESCE(?, vehicle_id) AND merged_into_id IS NOT NULL",
                        (_current_vehicle_id(),)).fetchall():
        out.setdefault(r["merged_into_id"], []).append(dict(r))
    return out


def _segment_ids(db, trip_id: int) -> list:
    """Every trip id in the merge-group containing trip_id (parent + children); [trip_id] if none."""
    row = db.execute("SELECT id, merged_into_id FROM trips WHERE id=? AND vehicle_id = COALESCE(?, vehicle_id)",
                     (trip_id, _current_vehicle_id())).fetchone()
    if not row:
        return [trip_id]
    parent = row["merged_into_id"] or row["id"]
    return [parent] + [r["id"] for r in
            db.execute("SELECT id FROM trips WHERE vehicle_id = COALESCE(?, vehicle_id) AND merged_into_id=?",
                       (_current_vehicle_id(), parent)).fetchall()]


def _trip_group_stats(parent: dict, children: list) -> dict:
    """Parent dict enriched with the combined stats of [parent + children] (earliest start →
    latest end). Pure display math — stored rows are untouched. The merge guard guarantees no
    charge in any gap, so the SoC delta (energy/efficiency) stays valid."""
    d = dict(parent)
    d["merged_count"] = 1
    d["is_merged"] = False
    if not children:
        return d
    segs = sorted([parent, *children], key=lambda t: t.get("started_at") or "")
    first, last = segs[0], segs[-1]
    d["started_at"], d["start_soc"] = first.get("started_at"), first.get("start_soc")
    d["start_odometer_km"] = first.get("start_odometer_km")
    d["start_lat"], d["start_lon"] = first.get("start_lat"), first.get("start_lon")
    d["ended_at"], d["end_soc"] = last.get("ended_at"), last.get("end_soc")
    d["end_odometer_km"] = last.get("end_odometer_km")
    d["end_lat"], d["end_lon"] = last.get("end_lat"), last.get("end_lon")
    so, eo = first.get("start_odometer_km"), last.get("end_odometer_km")
    if so is not None and eo is not None and eo >= so and so > 0:
        d["distance_km"] = round(eo - so, 2)
    else:
        d["distance_km"] = round(sum((s.get("distance_km") or 0) for s in segs), 2)
    d["duration_min"] = round(sum((s.get("duration_min") or 0) for s in segs), 1)   # DRIVING only
    d["regen_kwh"] = round(sum((s.get("regen_kwh") or 0) for s in segs), 3)
    # Elevation is per-segment like regen_kwh, but None here means "not enriched yet" (not "zero") —
    # summing None-as-0 would show a misleading "+0 m" while some segments still await the Open-Meteo
    # sweep. Only aggregate once EVERY segment has a value; the outside temperature is the mean of the
    # segments that do have one (a merged group can span more than one weather hour).
    if all(s.get("elevation_gain_m") is not None for s in segs):
        d["elevation_gain_m"] = round(sum(s["elevation_gain_m"] for s in segs))
        d["elevation_loss_m"] = round(sum(s["elevation_loss_m"] for s in segs))
    else:
        d["elevation_gain_m"] = None
        d["elevation_loss_m"] = None
    # Temperature is start-point/end-point (not aggregated): the group's start temp is the FIRST
    # segment's start, its end temp the LAST segment's end (segs is sorted by started_at).
    d["outside_temp_start_c"] = first.get("outside_temp_start_c")
    d["outside_temp_end_c"] = last.get("outside_temp_end_c")
    ssoc, esoc, dist = d["start_soc"], d["end_soc"], d.get("distance_km") or 0
    # REEV: if the range-extender ran anywhere in the group (fuel dropped from first-start to last-end),
    # net SoC ≠ traction energy, so a combined electric kWh/100km is meaningless — leave it blank (beta
    # #10); the fuel figure is shown instead. Self-gates to REEV engine-on groups (BEV fuel is NULL).
    _fs, _fe = first.get("fuel_start_pct"), last.get("fuel_end_pct")
    _reev_engine = (_fs is not None and _fe is not None and (_fs - _fe) > _REEV_FUEL_MIN_DROP)
    if _reev_engine:
        d["efficiency_kwh_100km"] = None
    elif ssoc is not None and esoc is not None and dist > 0:
        energy = max((ssoc - esoc) / 100.0 * get_battery_capacity_kwh(), 0)
        d["efficiency_kwh_100km"] = round(energy / dist * 100, 1) if energy > 0 else None
    # If the group was converted to the official cloud EC (stored on the parent over the COMBINED
    # distance, e.g. convert-on-merge), prefer it over the SoC estimate so the headline matches the
    # breakdown card. (Skipped for a REEV engine-on group — same reason as above.)
    if not _reev_engine and d.get("ec_stable") and d.get("ec_kwh") and dist > 0:
        d["efficiency_kwh_100km"] = round(d["ec_kwh"] / dist * 100, 1)
    d["merged_count"] = len(segs)
    d["is_merged"] = True
    d["segment_ids"] = [s["id"] for s in segs]
    return d


def get_mergeable_pairs(gap_min: int = TRIP_MERGE_GAP_DEFAULT) -> list:
    """Eligible adjacent top-level trip pairs for the merge UI: B starts within gap_min of A's
    (group) end AND B's start SoC is not higher than A's end SoC (a SoC rise = a charge in the
    gap → never mergeable). Returns [{a_id, b_id, gap_min}]."""
    db = _get()
    kids = _children_by_parent(db)
    tops = [dict(r) for r in db.execute(
        "SELECT * FROM trips WHERE vehicle_id = COALESCE(?, vehicle_id) AND merged_into_id IS NULL "
        "AND ended_at IS NOT NULL "
        "ORDER BY started_at", (_current_vehicle_id(),)).fetchall()]
    groups = [_trip_group_stats(t, kids.get(t["id"], [])) for t in tops]
    pairs = []
    for a, b in zip(groups, groups[1:]):
        gap = _gap_minutes(a.get("ended_at"), b.get("started_at"))
        if gap is None or gap < 0 or gap >= gap_min:
            continue
        if (a.get("end_soc") is not None and b.get("start_soc") is not None
                and b["start_soc"] > a["end_soc"]):
            continue   # SoC rose → charged in the gap
        pairs.append({"a_id": a["id"], "b_id": b["id"], "gap_min": round(gap)})
    return pairs


def get_merge_candidates(gap_min: int = TRIP_MERGE_GAP_DEFAULT) -> list[dict]:
    """Mergeable pairs (get_mergeable_pairs) hydrated with full trip_row.html-ready data —
    the Viaggi 🔗 button's dedicated candidates view. Previously these surfaced as inline
    connectors between adjacent rows in the full year/month/day accordion; the calendar
    only ever renders one day at a time, so there's no "whole page" left to scan for
    them — this view lists just the (typically few) actual candidates instead, unrelated
    to whichever month is currently browsed. Most-recent-first."""
    pairs = get_mergeable_pairs(gap_min)
    if not pairs:
        return []
    trips_by_id = {t["id"]: t for t in _localized_trips(get_trips(limit=1_000_000))}
    out = []
    for p in pairs:
        a, b = trips_by_id.get(p["a_id"]), trips_by_id.get(p["b_id"])
        if a and b:
            out.append({"a": a, "b": b, "gap_min": p["gap_min"]})
    out.sort(key=lambda p: p["b"]["started_at"], reverse=True)
    return out


def merge_trips(parent_id: int, child_id: int, gap_min: int = TRIP_MERGE_GAP_DEFAULT) -> dict:
    """Merge child into parent (the earlier of the two becomes the parent). Re-validates the
    eligibility server-side. Reversible: only sets merged_into_id, nothing is overwritten."""
    db = _conn_rw()
    a = db.execute("SELECT * FROM trips WHERE id=? AND merged_into_id IS NULL", (parent_id,)).fetchone()
    b = db.execute("SELECT * FROM trips WHERE id=? AND merged_into_id IS NULL", (child_id,)).fetchone()
    if not a or not b:
        return {"ok": False, "error": "not_found_or_already_merged"}
    a, b = dict(a), dict(b)
    if (a.get("started_at") or "") > (b.get("started_at") or ""):
        a, b = b, a                                   # parent = earlier trip
    kids = _children_by_parent(db)
    a_grp = _trip_group_stats(a, kids.get(a["id"], []))
    gap = _gap_minutes(a_grp.get("ended_at"), b.get("started_at"))
    if gap is None or gap < 0:
        return {"ok": False, "error": "gap_too_large"}
    if gap >= gap_min:
        # Normally a stop ≥ gap_min is a separate trip. EXCEPTION: if the two trips share ONE power-on
        # (Ready) session — the car was never switched off between them — the cloud bundles them into
        # one driving session anyway, so allow the merge at ANY gap (the only way to get the official
        # combined figure). Detected from the real positions.ready log.
        sess = ready_session(a_grp)
        if not (sess and b["id"] in sess.get("trip_ids", [])):
            return {"ok": False, "error": "gap_too_large"}
    if (a_grp.get("end_soc") is not None and b.get("start_soc") is not None
            and b["start_soc"] > a_grp["end_soc"]):
        return {"ok": False, "error": "soc_rose_charge_in_gap"}
    # absorb B and any of B's own children into A (flatten the chain so all point to A)
    db.execute("UPDATE trips SET merged_into_id=? WHERE id=? OR merged_into_id=?",
               (a["id"], b["id"], b["id"]))
    db.commit()
    return {"ok": True, "parent_id": a["id"]}


def unmerge_trip(parent_id: int) -> dict:
    """Split a merged group back into its original trips — clears merged_into_id on every child.
    All rows were untouched, so they reappear exactly as before."""
    db = _conn_rw()
    cur = db.execute("UPDATE trips SET merged_into_id=NULL WHERE merged_into_id=?", (parent_id,))
    # The parent may hold the COMBINED cloud EC (from a convert-on-merge); once split it no longer
    # matches the standalone trip → drop it and restore the SoC efficiency (the user can re-convert
    # the standalone trip). Only touches a parent that actually carries an EC override.
    db.execute(
        "UPDATE trips SET efficiency_kwh_100km=COALESCE(efficiency_soc, efficiency_kwh_100km), "
        "efficiency_soc=NULL, ec_kwh=NULL, ec_driving=NULL, ec_ac=NULL, ec_other=NULL, ec_stable=0 "
        "WHERE id=? AND ec_kwh IS NOT NULL", (parent_id,))
    db.commit()
    return {"ok": True, "restored": cur.rowcount}


def preview_merge(parent_id: int, child_id: int) -> Optional[dict]:
    """Group stats the merge WOULD produce (for the confirm dialog), without committing."""
    db = _get()
    a = db.execute("SELECT * FROM trips WHERE id=? AND vehicle_id = COALESCE(?, vehicle_id)",
                   (parent_id, _current_vehicle_id())).fetchone()
    b = db.execute("SELECT * FROM trips WHERE id=? AND vehicle_id = COALESCE(?, vehicle_id)",
                   (child_id, _current_vehicle_id())).fetchone()
    if not a or not b:
        return None
    a, b = dict(a), dict(b)
    if (a.get("started_at") or "") > (b.get("started_at") or ""):
        a, b = b, a
    kids = _children_by_parent(db)
    children = kids.get(a["id"], []) + [b] + kids.get(b["id"], [])
    g = _trip_group_stats(a, children)
    drive = g.get("duration_min") or 0
    elapsed = _gap_minutes(g.get("started_at"), g.get("ended_at"))
    g["stop_min"] = round(max(elapsed - drive, 0)) if elapsed is not None else None
    g["started_at"] = _local_iso(g.get("started_at"))
    g["ended_at"] = _local_iso(g.get("ended_at"))
    return g


def get_merge_preview_route(a_id: int, b_id: int, max_points: int = 120) -> list[dict]:
    """Downsampled union GPS track of the two trips' groups — for the merge-preview thumbnail."""
    db = _get()
    ids = list(dict.fromkeys(_segment_ids(db, a_id) + _segment_ids(db, b_id)))
    ph = ",".join("?" * len(ids))
    rows = db.execute(
        f"SELECT latitude, longitude FROM trip_positions WHERE trip_id IN ({ph}) "
        "AND latitude IS NOT NULL AND longitude IS NOT NULL ORDER BY recorded_at, id", ids).fetchall()
    pts = [dict(r) for r in rows]
    if len(pts) <= max_points:
        return pts
    step = len(pts) / max_points
    out = [pts[int(i * step)] for i in range(max_points)]
    out[-1] = pts[-1]
    return out


def get_trips(limit: int = 500) -> list[dict]:
    db = _get()
    kids = _children_by_parent(db)
    rows = db.execute(
        """SELECT * FROM trips
           WHERE vehicle_id = COALESCE(?, vehicle_id) AND ended_at IS NOT NULL AND merged_into_id IS NULL
           ORDER BY started_at DESC
           LIMIT ?""",
        (_current_vehicle_id(), limit),
    ).fetchall()
    out = []
    for r in rows:
        kids_r = kids.get(r["id"], [])
        td = _trip_group_stats(dict(r), kids_r)
        # REEV Phase C — per-trip fuel so the list can flag engine-on trips (⛽) at a glance. Same
        # generator-on basis as the detail page; the positions walk runs only for trips that actually
        # burned fuel (a REEV drives mostly electric), so the list stays cheap.
        _fs, _fe = td.get("fuel_start_pct"), td.get("fuel_end_pct")
        _eng = None
        if _fs is not None and _fe is not None and (_fs - _fe) > _REEV_FUEL_MIN_DROP:
            _seg = [r["id"]] + [k["id"] for k in kids_r]
            _b = db.execute(
                f"SELECT MIN(started_at) s, MAX(ended_at) e FROM trips WHERE id IN ({','.join('?' * len(_seg))})",
                _seg).fetchone()
            _eng = _reev_engine_on(db, r["vehicle_id"], _b["s"], _b["e"])
        td.update(_reev_trip_fuel(_fs, _fe, td.get("distance_km"), _eng))
        out.append(td)
    return out


def reev_fuel_summary() -> Optional[dict]:
    """REEV — the range-extender's REAL fuel appetite, from the engine-on trips (on-board): total
    litres burned, generator-on driving km, and the L/100km WHILE the generator drove the car. This is
    the number that matters to a REEV owner — unlike the cloud's period average (fuel over ALL km), which
    a mostly-electric REEV dilutes to near zero, and unlike spreading the litres over the whole trip. The
    average uses fuel-while-driving over distance-while-driving (see _reev_engine_on); `total_l` stays the
    full litres that left the tank. None when the engine never ran (or no fuel data)."""
    db = _get()
    try:
        rows = db.execute(
            "SELECT id, vehicle_id, started_at, ended_at, distance_km, fuel_start_pct, fuel_end_pct "
            "FROM trips WHERE vehicle_id = COALESCE(?, vehicle_id) AND ended_at IS NOT NULL "
            "AND fuel_start_pct IS NOT NULL AND fuel_end_pct IS NOT NULL "
            "AND fuel_start_pct - fuel_end_pct > ?",
            (_current_vehicle_id(), _REEV_FUEL_MIN_DROP)).fetchall()
    except sqlite3.Error:
        return None
    total_l, engine_km, engine_l, n = 0.0, 0.0, 0.0, 0
    for r in rows:
        total_l += (r["fuel_start_pct"] - r["fuel_end_pct"]) / 100.0 * _REEV_TANK_L
        eng = _reev_engine_on(db, r["vehicle_id"], r["started_at"], r["ended_at"])
        if eng:
            engine_km += eng["engine_km"]
            engine_l += eng["engine_fuel_pct"] / 100.0 * _REEV_TANK_L
        else:  # positions pruned → fall back to the whole-trip distance + full drop
            engine_km += r["distance_km"] or 0
            engine_l += (r["fuel_start_pct"] - r["fuel_end_pct"]) / 100.0 * _REEV_TANK_L
        n += 1
    if not n:
        return None
    return {
        "engine_trips": n,
        "total_l": round(total_l, 1),
        "engine_km": round(engine_km, 1),
        "avg_l_100km": round(engine_l / engine_km * 100, 1) if engine_km > 0.5 else None,
    }


def reev_total_consumption() -> Optional[dict]:
    """REEV — what the driving actually COST, over every trip, in the two things you pay for.

    Mate's efficiency figure deliberately goes blank on a trip where the range-extender ran: a
    SoC drop stops measuring how efficiently the battery drove you once the generator has been
    refilling the pack underneath. That is right for the question "how efficient was this", and
    it leaves the other question — "what did this cost me" — unanswered on exactly the long
    trips that cost the most. Two REEV owners arrived at that hole independently, from different
    cars, in the same week (@michapr, who proposed this, and @gm27271).

    Cost does not care where the electrons came from. Fuel burned is fuel you bought; the NET
    drop in the battery is grid energy you bought. Whatever the generator moved from the tank
    into the pack is already counted once, in the litres — so subtracting it from the battery
    side is not losing it, it is refusing to bill it twice. No trip is excluded.

    Verified against physics rather than argued: on one 541 km range-extender day in @gm27271's
    signal bundle, this SoC-based figure came to 11.5 kWh while integrating the pack's own volts
    × amps over the same day gave 11.7 — two independent methods, 2% apart. (The same bundle also
    shows why the cloud's own kWh cannot answer this: the generator put 44 kWh back into that
    pack over a week, and the cloud counts none of it.)

    None until there is at least half a kilometre to divide by."""
    db = _get()
    try:
        row = db.execute(
            """SELECT SUM(distance_km) AS km,
                      SUM((start_soc - end_soc) / 100.0 * ?) AS kwh,
                      -- Only tank drops. A trip that ENDS fuller than it started is a refuel,
                      -- and a refuel is a purchase, not negative consumption: counted signed, one
                      -- fill-up erases weeks of real burning. Found by running this over a real
                      -- range-extender history, where a single 6% → 76% stop turned 22 litres
                      -- burned into MINUS 23. Same guard reev_fuel_summary already applies.
                      SUM(CASE WHEN fuel_start_pct IS NOT NULL AND fuel_end_pct IS NOT NULL
                                AND fuel_start_pct > fuel_end_pct
                               THEN (fuel_start_pct - fuel_end_pct) / 100.0 * ? END) AS litres
                 FROM trips
                WHERE vehicle_id = COALESCE(?, vehicle_id) AND ended_at IS NOT NULL""",
            (get_battery_capacity_kwh(), _REEV_TANK_L, _current_vehicle_id())).fetchone()
    except sqlite3.Error:
        return None
    km = (row["km"] if row else None) or 0
    if km < 0.5:
        return None
    # The battery side can come out NEGATIVE over a period: a generator that hands the pack more
    # than the driving took out leaves you with stored energy you paid for in petrol, already
    # billed in the litres beside it. Reporting a negative kWh/100km would read as a defect, so
    # the electric side floors at zero and the fuel figure carries that period on its own.
    kwh = max((row["kwh"] or 0), 0.0)
    litres = row["litres"] or 0
    return {
        "total_km": round(km, 1),
        "total_kwh": round(kwh, 1),
        "total_fuel_l": round(litres, 1),
        "kwh_100km": round(kwh / km * 100, 1),
        "fuel_l_100km": round(litres / km * 100, 1),
    }


def reev_actual_spend() -> Optional[dict]:
    """REEV — what was actually BOUGHT, beside the figure derived from the car's own gauges.

    reev_total_consumption above works out both sides from percentages: the pack's SoC drop
    against a nominal capacity, the tank's level against a nominal 50 litres. That is the right
    tool where nothing better exists — it needs no prices, no typing, and it works trip by trip.
    But on a lifetime total nothing better is not the situation Mate is in: every charge it
    recorded already carries its energy AND its cost, and every refuel the owner entered already
    carries litres off a receipt. Measured beats derived, and it beats it in three separate ways.

    It reads the AC side. _billed_kwh returns the wallbox's own kWh for a home charge, which is
    what the meter charged you; the SoC-derived figure is the DC energy that reached the battery,
    smaller by the 10-15% lost in conversion (see the answer to #134 — the gap is physics, not a
    bug). A cost card built on the DC side understates the bill, always in the same direction.

    It needs no assumption about the car. No nominal pack capacity, so battery ageing cannot
    skew it; no linear fuel float, so a tank that reads optimistically near the bottom cannot.

    It sees what never became a trip. Vampire drain, preconditioning, cabin heating on the
    driveway: electricity bought and paid for that appears in no trip's SoC delta.

    What it cannot do is the other's job. It measures PURCHASES over a period, not consumption
    over those kilometres — charge the car tonight and drive it next week and the two land in
    different places — and it only knows the refuels somebody bothered to enter. Hence both,
    side by side, saying plainly which is which. None when nothing has been bought yet."""
    db = _get()
    try:
        charges = [dict(r) for r in db.execute(
            "SELECT energy_added_kwh, ac_energy_kwh, location_type, cost FROM charges "
            "WHERE vehicle_id = COALESCE(?, vehicle_id) AND ended_at IS NOT NULL",
            (_current_vehicle_id(),)).fetchall()]
    except sqlite3.Error:
        charges = []
    kwh = sum(_billed_kwh(c) for c in charges)
    elec_cost = sum(c["cost"] for c in charges if c.get("cost"))
    litres = fuel_cost = 0.0
    try:
        _ensure_fuel_purchases(db)
        r = db.execute(
            "SELECT COALESCE(SUM(liters), 0) AS l, COALESCE(SUM(total_cost), 0) AS c "
            "FROM fuel_purchases WHERE vehicle_id = COALESCE(?, vehicle_id)",
            (_current_vehicle_id(),)).fetchone()
        litres, fuel_cost = (r["l"] or 0), (r["c"] or 0)
    except sqlite3.Error:
        pass
    if kwh <= 0 and litres <= 0:
        return None
    return {
        "kwh": round(kwh, 1),
        "litres": round(litres, 1),
        "cost": round(elec_cost + fuel_cost, 2) if (elec_cost or fuel_cost) else None,
        "charges": len(charges),
        # Told plainly rather than hidden: a total that is missing every refuel the owner never
        # typed in is not "what you spent", it is "what Mate was told about".
        "has_fuel_entries": litres > 0,
    }


def _trip_blended_rate_fn():
    """Blended €/kWh-over-time lookup, built once from ALL priced charges (same basis as
    get_trip_detail's own per-trip rate) — shared by the Trips calendar and search so every
    view prices a trip identically."""
    cost_bp: dict = {}
    seen_ch: dict = {}
    for c in _get().execute(
            "SELECT vehicle_id, ended_at, start_soc, end_soc, cost, ac_energy_kwh, location_type, "
            "energy_added_kwh FROM charges WHERE vehicle_id = COALESCE(?, vehicle_id) "
            "AND ended_at IS NOT NULL AND cost IS NOT NULL "
            "AND energy_added_kwh > 0 ORDER BY vehicle_id, ended_at", (_current_vehicle_id(),)).fetchall():
        seen_ch.setdefault(c["vehicle_id"], []).append(dict(c))
        cost_bp.setdefault(c["vehicle_id"], []).append(
            (c["ended_at"], _wac_blend(seen_ch[c["vehicle_id"]])))

    def _rate_at(vehicle_id, ts_utc):
        rate = None
        for ended_at, wac in cost_bp.get(vehicle_id, ()):   # ascending → last ≤ ts wins
            if ended_at <= ts_utc:
                rate = wac
            else:
                break
        return rate
    return _rate_at


def _localized_trips(trips: list[dict]) -> list[dict]:
    """Per-trip localization + derived cost shared by the Trips calendar and search: the
    ec_pending flag, local start/end times, and cost (efficiency × distance × the blended
    €/kWh in effect AT the trip's time — same basis as get_trip_detail). Adds a private
    `_dt` (aware, local-tz datetime) for the caller's OWN day/date bucketing or filtering."""
    rate_at = _trip_blended_rate_fn()
    ec_on = get_setting("ec_trip_energy_enabled", "1") == "1"
    ec_cutoff = get_setting("ec_trip_since", "")
    now_ts = datetime.now(timezone.utc).timestamp()
    out = []
    for t in trips:
        if not t.get("started_at"):
            continue
        dt = _local_dt(t["started_at"])
        if dt is None:
            continue
        raw_start = t["started_at"]
        ee = _trip_epoch(t.get("ended_at")) if t.get("ended_at") else None
        t["ec_pending"] = bool(
            ec_on and not t.get("ec_stable") and ec_cutoff
            and t["started_at"] >= ec_cutoff
            and ee and (now_ts - ee) < 6 * 3600)
        t["started_at"] = dt.isoformat()
        t["ended_at"] = _local_iso(t.get("ended_at"))
        km = t.get("distance_km") or 0
        eff = t.get("efficiency_kwh_100km")
        energy = (eff * km / 100) if (eff and km) else 0
        rate = rate_at(t.get("vehicle_id"), raw_start) if energy else None
        t["cost"] = (energy * rate) if (energy and rate) else 0
        t["_dt"] = dt
        out.append(t)
    return out


def _totals_node() -> dict:
    return {"count": 0, "km": 0.0, "regen": 0.0, "cost": 0.0, "_eff_wsum": 0.0, "_eff_wdist": 0.0}


def _totals_add(node: dict, trip: dict) -> None:
    """Fold one trip into a totals node. Efficiency is a DISTANCE-WEIGHTED mean, never a plain
    average of the per-trip figures — a 2 km hop and a 200 km drive must not count the same."""
    km = trip.get("distance_km") or 0
    eff = trip.get("efficiency_kwh_100km")
    node["count"] += 1
    node["km"] = round(node["km"] + km, 2)
    node["regen"] = round(node["regen"] + (trip.get("regen_kwh") or 0), 3)
    node["cost"] = round(node["cost"] + (trip.get("cost") or 0), 2)
    if eff and km > 0:
        node["_eff_wsum"] += km * eff
        node["_eff_wdist"] += km


def _totals_seal(node: dict) -> dict:
    """Turn the running weights into avg_eff and drop them. Call once per node."""
    node["avg_eff"] = round(node["_eff_wsum"] / node["_eff_wdist"], 1) if node["_eff_wdist"] > 0 else None
    del node["_eff_wsum"]
    del node["_eff_wdist"]
    return node


def trips_totals(trips: list[dict]) -> dict:
    """Totals for an arbitrary set of trips — the day drawer's header (#175). Deliberately built on
    the SAME three helpers the month calendar uses: the day line and the month line sit centimetres
    apart on screen, so a second implementation of the weighted mean would eventually disagree with
    the first and the page would contradict itself."""
    node = _totals_node()
    for t in trips:
        _totals_add(node, t)
    return _totals_seal(node)


def get_trips_calendar_month(year: int, month: int) -> dict:
    """Per-day totals for the Viaggi calendar's Month view: session count, distance, regen
    and derived cost for each day of `year`/`month` (local time), plus the month's own
    total. Mirrors get_charges_calendar_month; the day's actual trips are fetched lazily
    (see get_trips_calendar_day) only when a cell is clicked."""
    trips = _localized_trips(get_trips(limit=1_000_000))
    days: dict[int, dict] = {}
    total = _totals_node()
    for t in trips:
        dt = t["_dt"]
        if dt.year != year or dt.month != month:
            continue
        d = days.setdefault(dt.day, _totals_node())
        for node in (d, total):
            _totals_add(node, t)
    for node in list(days.values()) + [total]:
        _totals_seal(node)
    return {"year": year, "month": month, "days": days, "total": total}


def get_trips_calendar_day(year: int, month: int, day: int) -> list[dict]:
    """The trip_row.html-ready trips for ONE calendar day — backs the Month view's day
    drawer, most-recent-first."""
    trips = _localized_trips(get_trips(limit=1_000_000))
    trips = [t for t in trips if t["_dt"].year == year and t["_dt"].month == month and t["_dt"].day == day]
    trips.sort(key=lambda t: t["started_at"], reverse=True)
    return trips


def search_trips(text: str = "", date_from: str = "", date_to: str = "",
                  km_min: "float | None" = None, km_max: "float | None" = None,
                  eff_min: "float | None" = None, eff_max: "float | None" = None,
                  duration_min: "float | None" = None, duration_max: "float | None" = None,
                  drive_mode: str = "") -> list[dict]:
    """Flat, most-recent-first list of trips matching ALL given filters — the Viaggi search
    bar. `text` matches the user note (substring, case-insensitive); `drive_mode` is
    comfort/normal/sport (#107); the km/efficiency/duration filters are inclusive ranges;
    `date_from`/`date_to` are inclusive "YYYY-MM-DD" LOCAL calendar dates."""
    trips = _localized_trips(get_trips(limit=1_000_000))
    q = (text or "").strip().lower()
    dm = (drive_mode or "").strip().lower()
    try:
        d_from = date.fromisoformat(date_from) if date_from else None
    except ValueError:
        d_from = None
    try:
        d_to = date.fromisoformat(date_to) if date_to else None
    except ValueError:
        d_to = None
    out = []
    for t in trips:
        if q and q not in (t.get("note") or "").lower():
            continue
        if dm and (t.get("drive_mode") or "").lower() != dm:
            continue
        km = t.get("distance_km") or 0
        if km_min is not None and km < km_min:
            continue
        if km_max is not None and km > km_max:
            continue
        eff = t.get("efficiency_kwh_100km")
        if eff_min is not None and (eff is None or eff < eff_min):
            continue
        if eff_max is not None and (eff is None or eff > eff_max):
            continue
        dur = t.get("duration_min") or 0
        if duration_min is not None and dur < duration_min:
            continue
        if duration_max is not None and dur > duration_max:
            continue
        day_ = t["_dt"].date()
        if d_from and day_ < d_from:
            continue
        if d_to and day_ > d_to:
            continue
        out.append(t)
    out.sort(key=lambda t: t["started_at"], reverse=True)
    return out


def get_trip_years() -> list[int]:
    """Distinct years (local time, most recent first) with at least one trip — populates
    the Viaggi calendar's year-jump pills with only years the user actually has data for."""
    years = set()
    for t in get_trips(limit=1_000_000):
        dt = _local_dt(t.get("started_at"))
        if dt:
            years.add(dt.year)
    return sorted(years, reverse=True)


def get_trip_local_date(trip_id: int) -> "date | None":
    """The local calendar date a trip falls on, or None if it doesn't exist — used to open
    the Viaggi calendar on the right month when following a ?highlight=<id> link."""
    row = _get().execute("SELECT started_at FROM trips WHERE id=?", (trip_id,)).fetchone()
    if not row or not row["started_at"]:
        return None
    dt = _local_dt(row["started_at"])
    return dt.date() if dt else None


def get_trips_grouped() -> list[dict]:
    """Return trips nested as year → month → day for the sidebar tree view."""
    trips = get_trips()
    from collections import OrderedDict

    def _node(label):
        return {"label": label, "km": 0, "count": 0, "regen": 0.0, "cost": 0.0,
                "_eff_wsum": 0.0, "_eff_wdist": 0.0, "avg_eff": None}

    def _add(node, km, eff, regen, cost):
        node["km"]    = round(node["km"] + km, 2)
        node["count"] += 1
        node["regen"] = round(node["regen"] + (regen or 0), 3)
        node["cost"]  = round(node["cost"] + (cost or 0), 2)
        if eff and km > 0:
            node["_eff_wsum"]  += km * eff
            node["_eff_wdist"] += km

    def _finalize(node):
        if node["_eff_wdist"] > 0:
            node["avg_eff"] = round(node["_eff_wsum"] / node["_eff_wdist"], 1)

    lang = get_language()
    # Provisional-SoC marker per trip (same rule as get_trip_detail) so the list shows which trips are
    # still waiting for the official cloud value. Settings read once, not per trip.
    _ec_on = get_setting("ec_trip_energy_enabled", "1") == "1"
    _ec_cutoff = get_setting("ec_trip_since", "")
    _now_ts = datetime.now(timezone.utc).timestamp()
    # Cost per group = Σ per-trip cost, each at the battery's blended €/kWh AT the trip's time (#53,
    # same basis as get_trip_detail). The blend only moves when a PRICED charge ends, so build that
    # (ended_at → blended price) timeline ONCE per vehicle instead of calling blended_price_at per trip.
    _cost_bp: dict = {}
    _seen_ch: dict = {}
    for _c in _get().execute(
            "SELECT vehicle_id, ended_at, start_soc, end_soc, cost, ac_energy_kwh, location_type, "
            "energy_added_kwh FROM charges WHERE vehicle_id = COALESCE(?, vehicle_id) "
            "AND ended_at IS NOT NULL AND cost IS NOT NULL "
            "AND energy_added_kwh > 0 ORDER BY vehicle_id, ended_at", (_current_vehicle_id(),)).fetchall():
        _seen_ch.setdefault(_c["vehicle_id"], []).append(dict(_c))
        _cost_bp.setdefault(_c["vehicle_id"], []).append(
            (_c["ended_at"], _wac_blend(_seen_ch[_c["vehicle_id"]])))

    def _rate_at(vehicle_id, ts_utc):
        """Blended €/kWh in effect at ts_utc = the last breakpoint whose charge ended at/before it."""
        rate = None
        for ended_at, wac in _cost_bp.get(vehicle_id, ()):   # ascending → last ≤ ts wins
            if ended_at <= ts_utc:
                rate = wac
            else:
                break
        return rate

    years: dict = OrderedDict()
    for t in trips:
        if not t.get("started_at"):
            continue
        dt = _local_dt(t["started_at"])
        if dt is None:
            continue
        # ec_pending + cost rate must use the RAW (UTC) started_at — capture before the local rewrite.
        _raw_start = t["started_at"]
        _ee = _trip_epoch(t.get("ended_at")) if t.get("ended_at") else None
        t["ec_pending"] = bool(
            _ec_on and not t.get("ec_stable") and _ec_cutoff
            and t["started_at"] >= _ec_cutoff
            and _ee and (_now_ts - _ee) < 6 * 3600)
        # Rewrite to local-time ISO so the template (started_at[11:16]) shows local
        t["started_at"] = dt.isoformat()
        t["ended_at"] = _local_iso(t.get("ended_at"))

        yr  = dt.strftime("%Y")
        mo  = i18n.fmt_month_year(lang, dt)
        day = i18n.fmt_day_month_year(lang, dt)

        years.setdefault(yr, {**_node(yr), "months": OrderedDict()})
        years[yr]["months"].setdefault(mo, {**_node(mo), "days": OrderedDict()})
        years[yr]["months"][mo]["days"].setdefault(day, {**_node(day), "trips": []})

        years[yr]["months"][mo]["days"][day]["trips"].append(t)

        km  = t.get("distance_km") or 0
        eff = t.get("efficiency_kwh_100km")
        regen = t.get("regen_kwh") or 0
        energy = (eff * km / 100) if (eff and km) else 0
        rate = _rate_at(t.get("vehicle_id"), _raw_start) if energy else None
        cost = (energy * rate) if (energy and rate) else 0
        for node in [years[yr], years[yr]["months"][mo], years[yr]["months"][mo]["days"][day]]:
            _add(node, km, eff, regen, cost)

    # Compute weighted avg efficiency for every node
    for yr_node in years.values():
        _finalize(yr_node)
        for mo_node in yr_node["months"].values():
            _finalize(mo_node)
            for day_node in mo_node["days"].values():
                _finalize(day_node)

    return list(years.values())


def get_trips_summary() -> dict:
    """Grand totals for the trips dashboard hero (no extra polling — pure SQL).

    Values are returned RAW, with no rounding — the template decides how to
    display them. avg_eff is a weighted mean (an inherently fractional ratio)."""
    db = _get()
    r = db.execute(
        """SELECT SUM(CASE WHEN merged_into_id IS NULL THEN 1 ELSE 0 END) AS n,
                  COALESCE(SUM(distance_km), 0)              AS km,
                  COALESCE(SUM(regen_kwh), 0)                AS regen,
                  SUM(distance_km * efficiency_kwh_100km)    AS eff_wsum,
                  SUM(CASE WHEN efficiency_kwh_100km IS NOT NULL
                           THEN distance_km END)             AS eff_wdist
           FROM trips WHERE vehicle_id = COALESCE(?, vehicle_id) AND ended_at IS NOT NULL""",
        (_current_vehicle_id(),)
    ).fetchone()
    return {
        "count":    r["n"],
        "km":       r["km"] or 0,
        "regen":    r["regen"] or 0,
        "avg_eff":  (r["eff_wsum"] / r["eff_wdist"]) if r["eff_wdist"] else None,
    }


def get_first_trip_date() -> Optional[str]:
    """Earliest trip date (YYYY-MM-DD, local) — the lower bound for the 'all-time' EC window on the
    Trips page. None if there are no trips yet."""
    db = _get()
    r = db.execute("SELECT MIN(started_at) AS m FROM trips WHERE vehicle_id = COALESCE(?, vehicle_id) "
                   "AND started_at IS NOT NULL", (_current_vehicle_id(),)).fetchone()
    if not r or not r["m"]:
        return None
    return (_local_iso(r["m"]) or r["m"])[:10]


def get_first_trip_ts() -> Optional[int]:
    """Epoch seconds of the earliest recorded trip's start — the lower bound of Mate's LOCAL trip
    coverage. Cloud getEC windows can reach back to the car's first day (long before Mate was
    installed), so callers pairing local trip totals with a getEC total use this to detect when
    the two do NOT cover the same span (GitHub #105). None if there are no trips yet."""
    db = _get()
    r = db.execute("SELECT MIN(started_at) AS m FROM trips WHERE vehicle_id = COALESCE(?, vehicle_id) "
                   "AND started_at IS NOT NULL", (_current_vehicle_id(),)).fetchone()
    dt = _local_dt(r["m"]) if r else None
    return int(dt.timestamp()) if dt else None


def _wac_blend(charges) -> Optional[float]:
    """Weighted-average-cost blended €/kWh of the battery after a chronological list of PRICED
    charges (GitHub #53). Pure (no DB) so it's simulation/unit-testable: each item is a dict with
    start_soc, end_soc, cost, ac_energy_kwh, location_type, energy_added_kwh.

    Model: the battery is ONE reservoir at a blended price; only a charge moves the price,
    consumption never does → replay the charges, anchoring the mix on each charge's SoC. Capacity
    CANCELS out (SoC ratios), so this is capacity-free and robust to SoH error. Update per charge:

        p' = (start_soc·p + (end_soc − start_soc)·rate) / end_soc

    where rate = charge cost ÷ its billed energy (_billed_kwh: wallbox AC for HOME, else battery DC —
    same basis as the per-charge € and the #51 trip-rate fix). Bootstrap: the first priced charge
    sets p to its own rate (the pre-existing energy is valued at the first thing we can measure).
    Unconfirmed charges (cost=NULL) are simply ABSENT from this list → carry-forward, i.e. the blend
    is unchanged across them — Mate's framework rule "no cost until confirmed, HOME excluded"."""
    p = None
    for c in charges:
        ss, es = c.get("start_soc"), c.get("end_soc")
        if ss is None or es is None or es <= 0 or es <= ss:
            continue                         # need a real SoC rise to weight the mix
        basis = _billed_kwh(c)
        cost = c.get("cost")
        if cost is None or not basis or basis <= 0:
            continue                         # unpriced → must not move the blend
        rate = cost / basis
        if rate <= 0:
            continue
        p = rate if p is None else (ss * p + (es - ss) * rate) / es
    return p


def blended_price_at(vehicle_id: int, ts: str) -> Optional[float]:
    """Blended €/kWh of the battery (WAC, #53) for `vehicle_id` at instant `ts` — the price in
    effect for a trip starting then, set by every PRICED charge that ended at/before `ts`. None until
    the first priced charge (early trips stay uncosted, as today). Recomputed from history each call
    (no stored state) → self-corrects the moment a charge's cost is assigned/edited."""
    db = _get()
    rows = db.execute(
        "SELECT start_soc, end_soc, cost, ac_energy_kwh, location_type, energy_added_kwh "
        "FROM charges WHERE vehicle_id = ? AND ended_at IS NOT NULL AND ended_at <= ? "
        "  AND cost IS NOT NULL AND energy_added_kwh > 0 ORDER BY ended_at",
        (vehicle_id, ts),
    ).fetchall()
    return _wac_blend([dict(r) for r in rows])


def _fuel_wac_blend(purchases) -> Optional[float]:
    """Weighted-average-cost blended €/L of the tank after a chronological list of refuels — the FUEL
    twin of _wac_blend (#53). Pure (no DB) so it's simulation/unit-testable. Each item is a dict with
    fuel_before_pct (tank % just before this refuel), liters (added), price_per_l (€/L paid).

    Same reservoir model as the battery: the tank is ONE blend, only a refuel moves the price and
    driving never does. Each refuel mixes the RESIDUAL (fuel_before_pct, at the running blend) with the
    ADDED litres (as a % of the tank, liters/50·100, at the paid rate):

        p' = (fs·p + add_pct·rate) / (fs + add_pct)

    Litres CANCEL (fuel-% ratios) so it's tank-size-free. Bootstrap: the first refuel sets the blend to
    its own €/L (pre-existing fuel is valued at the first thing we can price). A refuel whose residual
    is unknown (fuel_before_pct=None — e.g. no car data before it) can't weight the mix → it only
    bootstraps if it's the first, else carries the blend forward unchanged."""
    p = None
    for pur in purchases:
        rate = pur.get("price_per_l")
        liters = pur.get("liters")
        if rate is None or rate <= 0 or not liters or liters <= 0:
            continue                         # unpriced / empty → can't price, must not move the blend
        fs = pur.get("fuel_before_pct")
        if fs is None or fs < 0:
            if p is None:
                p = rate                     # first refuel, unknown residual → bootstrap to its rate
            continue                         # else carry-forward (an unknown residual can't weight)
        add_pct = liters / _REEV_TANK_L * 100.0
        p = rate if p is None else (fs * p + add_pct * rate) / (fs + add_pct)
    return p


def fuel_blended_price_at(vehicle_id: int, ts: str) -> Optional[float]:
    """Blended €/L of the tank (fuel WAC) for `vehicle_id` at instant `ts` — the price in effect for an
    engine-on trip starting then, set by every refuel logged at/before `ts`. None until the first
    refuel (engine trips before it stay uncosted, like the battery before its first priced charge).
    Recomputed from history each call (no stored state) → self-corrects when a refuel is added/edited.
    The FUEL twin of blended_price_at."""
    db = _conn_rw()
    try:
        _ensure_fuel_purchases(db)
        rows = db.execute(
            "SELECT fuel_before_pct, liters, price_per_l FROM fuel_purchases "
            "WHERE (vehicle_id = ? OR vehicle_id IS NULL) AND ts <= ? ORDER BY ts, id",
            (vehicle_id, ts)).fetchall()
        return _fuel_wac_blend([dict(r) for r in rows])
    finally:
        db.close()


def get_trip_detail(trip_id: int) -> Optional[dict]:
    db = _get()
    row = db.execute("SELECT * FROM trips WHERE id = ? AND vehicle_id = COALESCE(?, vehicle_id)",
                     (trip_id, _current_vehicle_id())).fetchone()
    if not row:
        return None
    # A merged child resolves to (and shows) its parent group.
    parent_id = row["merged_into_id"] or row["id"]
    trip = db.execute("SELECT * FROM trips WHERE id = ? AND vehicle_id = COALESCE(?, vehicle_id)",
                      (parent_id, _current_vehicle_id())).fetchone()
    children = _children_by_parent(db).get(parent_id, [])
    seg_ids = _segment_ids(db, parent_id)
    ph = ",".join("?" * len(seg_ids))
    positions = db.execute(
        "SELECT recorded_at, latitude, longitude, speed_kmh, soc, elevation_m FROM trip_positions "
        f"WHERE trip_id IN ({ph}) ORDER BY recorded_at, id",
        seg_ids,
    ).fetchall()
    # Drop cloud-cached/frozen stretches (see _filter_frozen_telemetry) BEFORE anything else reads
    # `positions` — so the chart, the map track and the speed stats below all agree on what actually
    # happened, instead of the chart alone showing a gap while the stats stay skewed.
    positions = _filter_frozen_telemetry([dict(p) for p in positions])
    # Whether the chart has a real point to draw a Quota line from — checked BEFORE interpolation
    # (which needs one to fill from). A trip enriched before per-point storage existed has
    # elevation_gain_m/loss_m set but every trip_positions.elevation_m still NULL: the aggregate being
    # present must not hide the recalculate button in that case (see template).
    elevation_profile_available = any(p.get("elevation_m") is not None for p in positions)
    # elevation_m is only fetched for the DOWNSAMPLED subset the sweep queried Open-Meteo for — fill
    # the rest by interpolation so the chart draws a smooth line, not one broken at every un-sampled point.
    positions = _interpolate_elevation(positions)
    trip_d = _trip_group_stats(dict(trip), children)
    trip_d["elevation_profile_available"] = elevation_profile_available
    if trip_d.get("is_merged"):
        elapsed = _gap_minutes(trip_d.get("started_at"), trip_d.get("ended_at"))
        trip_d["stop_min"] = (round(max(elapsed - (trip_d.get("duration_min") or 0), 0))
                              if elapsed is not None else None)
    trip_d["started_at"] = _local_iso(trip_d.get("started_at"))
    trip_d["ended_at"] = _local_iso(trip_d.get("ended_at"))

    # #107: per-trip user note + manual driving tags — read from the parent row (the detail page
    # always shows the parent, so the note/tags saved against it are the ones edited here).
    _tp = dict(trip)
    trip_d["note"] = _tp.get("note")
    trip_d["drive_mode"] = _tp.get("drive_mode")
    trip_d["one_pedal"] = _tp.get("one_pedal")

    # Speed stats derived from the GPS track (speed_kmh per point).
    speeds = [p["speed_kmh"] for p in positions if p["speed_kmh"] is not None]
    trip_d["max_speed_kmh"] = round(max(speeds)) if speeds else None
    # Average over moving points only (>1 km/h) so long idle stretches don't skew it.
    moving = [s for s in speeds if s > 1]
    trip_d["avg_speed_kmh"] = round(sum(moving) / len(moving)) if moving else None

    # ── #18: total energy consumed + trip cost ──────────────────────────────────
    # Energy consumed = efficiency × distance / 100 (consistent with the stored efficiency).
    eff = trip_d.get("efficiency_kwh_100km")
    dist = trip_d.get("distance_km") or 0
    trip_d["energy_kwh"] = round(eff * dist / 100, 2) if (eff and dist) else None

    # REEV Phase C — per-trip fuel consumption from the fuel-tank % drop (signal 3235). L/100 km is over
    # the generator-on DRIVING distance (across every merged segment), not the whole trip → matches the car.
    _fs, _fe = _tp.get("fuel_start_pct"), _tp.get("fuel_end_pct")
    trip_d["fuel_start_pct"], trip_d["fuel_end_pct"] = _fs, _fe
    _fbounds = db.execute(f"SELECT MIN(started_at) s, MAX(ended_at) e FROM trips WHERE id IN ({ph})",
                          seg_ids).fetchone()
    _feng = _reev_engine_on(db, trip["vehicle_id"], _fbounds["s"], _fbounds["e"])
    trip_d.update(_reev_trip_fuel(_fs, _fe, dist, _feng))
    # REEV Phase D — the electric counterpart, from the metered getEC (driverEC) not ΔSoC. Shown
    # research-only next to the fuel so REEV testers can validate it against the car's own dashboard
    # before we ever promote it to the headline efficiency (see _reev_trip_elec).
    trip_d.update(_reev_trip_elec(_tp.get("ec_driving"), dist, trip_d.get("engine_ran")))
    # REEV — fuel COST of this engine-on trip: litres burned × the tank's BLENDED €/L at the trip's
    # start (fuel WAC, the twin of the battery's blended_price_at / #53). None until the user logs a
    # refuel. It's an allocation of what that fuel cost, not a price measured at the pump.
    trip_d["fuel_cost"] = None
    trip_d["fuel_price_per_l"] = None
    if trip_d.get("fuel_used_l"):
        _fp = fuel_blended_price_at(trip["vehicle_id"], trip["started_at"])
        if _fp and _fp > 0:
            trip_d["fuel_price_per_l"] = round(_fp, 3)
            trip_d["fuel_cost"] = round(trip_d["fuel_used_l"] * _fp, 2)
    # Cost = trip energy × the battery's BLENDED €/kWh at the trip's start (weighted-average-cost,
    # GitHub #53). Replaces the old "rate of the single last charge", which over-billed every trip
    # after an expensive top-up (a small public charge made all the cheaper home energy bill at the
    # premium rate). The blend mixes every PRICED charge by the energy it added (blended_price_at /
    # _wac_blend); unconfirmed charges don't move it (Mate's "no cost until confirmed, HOME excluded").
    # Stores the number only — the `money` filter applies the currency. Final trip cost → 2 decimals.
    trip_d["cost"] = None
    trip_d["cost_per_kwh"] = None
    if trip_d["energy_kwh"]:
        rate = blended_price_at(trip["vehicle_id"], trip["started_at"])
        if rate and rate > 0:
            trip_d["cost_per_kwh"] = round(rate, 4)
            trip_d["cost"] = round(trip_d["energy_kwh"] * rate, 2)

    # Provisional-SoC marker: a getEC-candidate trip (feature on, started on/after the cutoff) whose
    # official cloud value hasn't locked yet is showing the SoC ESTIMATE for energy/efficiency/cost.
    # Flag it so the UI can label it "provisional — waiting for cloud" instead of looking like a final
    # (and slightly imprecise) number. Only while still inside the enrichment retry window (~6h); older
    # trips the cloud never enriched stay plain SoC with no "waiting" claim.
    trip_d["ec_pending"] = False
    try:
        if get_setting("ec_trip_energy_enabled", "1") == "1" and not trip_d.get("ec_stable"):
            cutoff = get_setting("ec_trip_since", "")
            sa, ea = trip["started_at"], trip["ended_at"]
            ee = _trip_epoch(ea) if ea else None
            if cutoff and sa and sa >= cutoff and ee and \
                    (datetime.now(timezone.utc).timestamp() - ee) < 6 * 3600:
                trip_d["ec_pending"] = True
    except Exception:  # noqa: BLE001
        pass

    return {
        **trip_d,
        "positions": positions,
    }


def _downsample(pts: list[dict], max_points: int) -> list[dict]:
    """Evenly reduce ``pts`` to at most ``max_points``, always keeping the last point."""
    if len(pts) <= max_points:
        return pts
    step = len(pts) / max_points
    sampled = [pts[int(i * step)] for i in range(max_points)]
    sampled[-1] = pts[-1]  # always keep the real end point
    return sampled


def get_trip_route(trip_id: int, max_points: int = 80) -> list[dict]:
    """Lat/lon track for a single trip, downsampled to at most ``max_points``
    points — used to draw the lightweight route thumbnail in the trips list."""
    db = _get()
    ids = _segment_ids(db, trip_id)
    ph = ",".join("?" * len(ids))
    rows = db.execute(
        "SELECT latitude, longitude FROM trip_positions "
        f"WHERE trip_id IN ({ph}) AND latitude IS NOT NULL AND longitude IS NOT NULL "
        "ORDER BY recorded_at, id",
        ids,
    ).fetchall()
    return _downsample([dict(r) for r in rows], max_points)


_SIMILAR_TRIP_GEOHASH_PRECISION = 7      # ~150m cell — matches trips.start_geohash/end_geohash
_SIMILAR_TRIP_STEP_KM = 0.1              # resample the route every 100m
_SIMILAR_TRIP_OVERLAP_THRESHOLD = 0.7    # ≥70% cell overlap → same road, not just same endpoints


def _route_geohash_cells(db, trip_id: int, step_km: float = _SIMILAR_TRIP_STEP_KM,
                          precision: int = _SIMILAR_TRIP_GEOHASH_PRECISION) -> set:
    """Geohash cells covering a trip's route, sampled every step_km ALONG THE PATH — not
    just at however-often the poller happened to record a point (polling cadence varies
    with speed/signal, so raw points alone would over-sample a slow crawl through town and
    under-sample a fast highway stretch, skewing the overlap check below). Empty for a trip
    with no GPS trace (e.g. reconstructed from an offline SoC/odometer jump, see
    poller/db.py create_reconstructed_trip) — which is exactly right: with nothing to
    compare, it can never clear the overlap threshold in get_similar_trips, so it's
    silently excluded from "confirmed same route" results without its own special case."""
    import geohash
    ids = _segment_ids(db, trip_id)
    ph = ",".join("?" * len(ids))
    rows = db.execute(
        "SELECT latitude, longitude FROM trip_positions "
        f"WHERE trip_id IN ({ph}) AND latitude IS NOT NULL AND longitude IS NOT NULL "
        "ORDER BY recorded_at, id", ids).fetchall()
    pts = [(r["latitude"], r["longitude"]) for r in rows]
    if not pts:
        return set()
    cells = {geohash.encode(pts[0][0], pts[0][1], precision)}
    cum = 0.0
    next_sample = step_km
    for (lat1, lon1), (lat2, lon2) in zip(pts, pts[1:]):
        seg = _haversine_km(lat1, lon1, lat2, lon2)
        if seg <= 0:
            continue
        seg_end = cum + seg
        while next_sample <= seg_end:
            frac = (next_sample - cum) / seg
            ilat = lat1 + (lat2 - lat1) * frac
            ilon = lon1 + (lon2 - lon1) * frac
            cells.add(geohash.encode(ilat, ilon, precision))
            next_sample += step_km
        cum = seg_end
    cells.add(geohash.encode(pts[-1][0], pts[-1][1], precision))
    return cells


def _jaccard(a: set, b: set) -> float:
    """Intersection-over-union of two cell sets — 0.0 when either is empty (rather than a
    ZeroDivisionError on an empty union), so a trip with no GPS trace at all simply never
    matches instead of needing its own guard at every call site."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def get_similar_trips(trip_id: int, overlap_threshold: float = _SIMILAR_TRIP_OVERLAP_THRESHOLD) -> list[dict]:
    """Other trips on the SAME route as `trip_id` — same direction only (start≈start,
    end≈end); a return trip is deliberately a SEPARATE group by default, since consumption
    and traffic often differ by direction (e.g. one leg uphill, the other downhill). Two
    stages: a geohash bucket on start/end (trips.start_geohash/end_geohash, ± the 8
    neighbor cells so a route right at a cell boundary isn't missed) narrows candidates to
    roughly the right corner of the map — cheap and indexable; then the ACTUAL path is
    compared via resampled-geohash overlap (_route_geohash_cells/_jaccard), because
    matching endpoints alone would also match a same-start/end trip that took a different
    road (a real risk with only a start/end + total-distance-tolerance check). Reconstructed
    trips (no GPS trace) can never clear the overlap threshold, so they're excluded from
    results without a special case. Live, on-demand computation (button-triggered) — pure
    local math on already-stored data, no network call, so unlike geocoding this is safe to
    run on every click without any usage-policy concern. Sorted oldest-first, for reading
    the efficiency trend over time."""
    import geohash
    db = _get()
    row = db.execute("SELECT * FROM trips WHERE id=? AND vehicle_id = COALESCE(?, vehicle_id)",
                     (trip_id, _current_vehicle_id())).fetchone()
    if not row:
        return []
    row = dict(row)
    parent_id = row["merged_into_id"] or row["id"]
    if parent_id != row["id"]:
        row = dict(db.execute("SELECT * FROM trips WHERE id=? AND vehicle_id = COALESCE(?, vehicle_id)",
                              (parent_id, _current_vehicle_id())).fetchone())
    if row.get("start_lat") is None or row.get("end_lat") is None:
        return []

    my_cells = _route_geohash_cells(db, parent_id)
    if not my_cells:
        return []   # no GPS trace on THIS trip → nothing to validate a match against

    start_gh = row.get("start_geohash") or geohash.encode(row["start_lat"], row["start_lon"])
    end_gh = row.get("end_geohash") or geohash.encode(row["end_lat"], row["end_lon"])
    start_cells = {start_gh} | geohash.neighbors(start_gh)
    end_cells = {end_gh} | geohash.neighbors(end_gh)
    ph_s = ",".join("?" * len(start_cells))
    ph_e = ",".join("?" * len(end_cells))
    candidates = db.execute(
        f"SELECT * FROM trips WHERE vehicle_id = COALESCE(?, vehicle_id) AND merged_into_id IS NULL "
        f"AND id != ? AND ended_at IS NOT NULL "
        f"AND start_geohash IN ({ph_s}) AND end_geohash IN ({ph_e})",
        [_current_vehicle_id(), parent_id] + list(start_cells) + list(end_cells)
    ).fetchall()

    out = []
    for c in candidates:
        c = dict(c)
        overlap = _jaccard(my_cells, _route_geohash_cells(db, c["id"]))
        if overlap < overlap_threshold:
            continue
        c["overlap_pct"] = round(overlap * 100)
        c["started_at"] = _local_iso(c.get("started_at"))
        c["ended_at"] = _local_iso(c.get("ended_at"))
        seg_ids = _segment_ids(db, c["id"])
        ph_seg = ",".join("?" * len(seg_ids))
        speeds = [r["speed_kmh"] for r in db.execute(
            f"SELECT speed_kmh FROM trip_positions WHERE trip_id IN ({ph_seg}) "
            "AND speed_kmh IS NOT NULL", seg_ids).fetchall()]
        moving = [s for s in speeds if s > 1]   # idle stretches shouldn't skew the average
        c["avg_speed_kmh"] = round(sum(moving) / len(moving)) if moving else None
        out.append(c)
    out.sort(key=lambda t: t.get("started_at") or "")
    return out


def get_trips_needing_elevation(limit: int = 4) -> list[dict]:
    """Finalized trips (any segment — elevation is per-segment like regen_kwh, see _trip_group_stats)
    not yet enriched, under the retry ceiling, with a real GPS track and non-trivial distance (skip
    parking-lot-only hops). A trip that only got a temperature (elevation missed) keeps elev_done=0,
    so it stays selected until the elevation lands or the ceiling is hit."""
    db = _get()
    rows = db.execute(
        """SELECT id FROM trips
           WHERE ended_at IS NOT NULL AND COALESCE(elev_done, 0) = 0
             AND COALESCE(elev_tried, 0) < 3 AND COALESCE(distance_km, 0) > 0.3
             AND EXISTS (SELECT 1 FROM trip_positions WHERE trip_id = trips.id)
           ORDER BY started_at DESC LIMIT ?""",
        (int(limit),),
    ).fetchall()
    return [dict(r) for r in rows]


def get_trip_points_for_elevation(trip_id: int, max_points: int = 60) -> list[dict]:
    """Lat/lon (+time) track of THIS trip segment only (enrichment runs per segment), downsampled to
    at most ``max_points`` to keep the Open-Meteo batch small. Frozen-telemetry duplicates are dropped
    BEFORE downsampling — left in, a long freeze wastes several slots on the same repeated coordinate
    (index-based downsampling has no notion of "already sampled here"), coarsening the profile over the
    rest of the trip for no benefit. recorded_at is included so the temperature lookup can use the
    segment's own time window (first→last point)."""
    db = _get()
    rows = db.execute(
        "SELECT id, recorded_at, latitude, longitude, speed_kmh, soc FROM trip_positions "
        "WHERE trip_id = ? AND latitude IS NOT NULL AND longitude IS NOT NULL "
        "ORDER BY recorded_at, id",
        (trip_id,),
    ).fetchall()
    points = _filter_frozen_telemetry([dict(r) for r in rows])
    return _downsample(points, max_points)


def store_point_elevations(elevations_by_id: dict) -> None:
    """Persist per-point altitude (metres) keyed by trip_positions.id — the sparse subset the chart's
    _interpolate_elevation fills the gaps between. `{}`/None is a no-op."""
    if not elevations_by_id:
        return
    db = _conn_rw()
    db.executemany("UPDATE trip_positions SET elevation_m=? WHERE id=?",
                   [(v, k) for k, v in elevations_by_id.items()])
    db.commit()


def store_trip_elevation(trip_id: int, gain, loss,
                         outside_temp_start_c=None, outside_temp_end_c=None) -> None:
    """Record an enrichment attempt. Always bumps elev_tried; with a gain/loss result also stores it
    and marks elev_done=1 so the sweep stops re-fetching. The start/end outside temperatures, when
    present, are written in the same statement (best-effort, independent of the elevation result)."""
    db = _conn_rw()
    sets = ["elev_tried = COALESCE(elev_tried, 0) + 1"]
    params: list = []
    if gain is not None and loss is not None:
        sets += ["elevation_gain_m=?", "elevation_loss_m=?", "elev_done=1"]
        params += [gain, loss]
    if outside_temp_start_c is not None:
        sets.append("outside_temp_start_c=?")
        params.append(outside_temp_start_c)
    if outside_temp_end_c is not None:
        sets.append("outside_temp_end_c=?")
        params.append(outside_temp_end_c)
    params.append(trip_id)
    db.execute(f"UPDATE trips SET {', '.join(sets)} WHERE id=?", params)
    db.commit()


def get_charges(limit: int = 50) -> list[dict]:
    db = _get()
    rows = db.execute(
        "SELECT * FROM charges WHERE vehicle_id = COALESCE(?, vehicle_id) AND ended_at IS NOT NULL ORDER BY started_at DESC LIMIT ?",
        (_current_vehicle_id(), limit),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["started_at"] = _local_iso(d.get("started_at"))
        d["ended_at"] = _local_iso(d.get("ended_at"))
        out.append(d)
    return out


def get_last_charge_end() -> Optional[datetime]:
    """End time of the most recently COMPLETED charge (local-tz aware), or None if no
    charge has ever finished. Used to bound the "since last charge" getEC window."""
    db = _get()
    row = db.execute(
        "SELECT ended_at FROM charges WHERE vehicle_id = COALESCE(?, vehicle_id) "
        "AND ended_at IS NOT NULL ORDER BY ended_at DESC LIMIT 1",
        (_current_vehicle_id(),)
    ).fetchone()
    return _local_dt(row["ended_at"]) if row else None


def get_trip_totals_between(begin_ts: int, end_ts: int) -> dict:
    """Distance/duration/count of LOCAL trips started within [begin_ts, end_ts] (epoch seconds) —
    paired by the caller with a live getEC total for the SAME window, to show distance + average
    kWh/100km alongside the official split (mirrors the car's own "since last charge" screen, which
    shows Distanza/Durata/Media next to the same Guida/AC/Altro breakdown)."""
    b = datetime.fromtimestamp(begin_ts, tz=timezone.utc).isoformat()
    e = datetime.fromtimestamp(end_ts, tz=timezone.utc).isoformat()
    db = _get()
    row = db.execute(
        """SELECT COUNT(*) AS trip_count,
                  ROUND(SUM(distance_km), 2) AS distance_km,
                  ROUND(SUM(duration_min), 0) AS duration_min
           FROM trips WHERE vehicle_id = COALESCE(?, vehicle_id) AND ended_at IS NOT NULL
             AND started_at >= ? AND started_at <= ?""",
        (_current_vehicle_id(), b, e),
    ).fetchone()
    return dict(row) if row else {}


def get_charge_power_curve(charge_id: int) -> dict:
    """Per-sample charging power for one session, for the expandable power chart.
    Power = |pack_voltage(1177) x pack_current(1178)| / 1000 — the same value as the
    HA `sensor.leapmotor_charging_power`. NOT rounded to 1 decimal (that flattens the
    curve); kept at 3 decimals so the real variation shows. Samples come from the
    general `positions` log (may be pruned over time → empty for very old sessions)."""
    db = _get()
    ch = db.execute("SELECT started_at, ended_at FROM charges WHERE id = ? AND vehicle_id = COALESCE(?, vehicle_id)",
                    (charge_id, _current_vehicle_id())).fetchone()
    if not ch:
        return {"labels": [], "power": [], "soc": []}
    start, end = ch["started_at"], ch["ended_at"]
    if end:
        # Cap the upper bound at the next charge's start so an orphan/overlapping charge
        # (whose ended_at bled past a later charge — see close_orphan_charges) cannot absorb
        # the next charge's power samples into its curve. That leak would inflate BOTH the
        # AC-vs-DC wallbox comparison AND the HOME cost (which bills the AC energy derived from
        # this curve) — GitHub #24. Mirrors _charge_active_window / compute_cost. For a normal
        # charge the next charge starts after ended_at → no cap, identical behaviour.
        lo, hi, excl = _power_window_bounds(db, start, end)
        rows = db.execute(
            "SELECT recorded_at, charge_voltage_v, charge_current_a, soc FROM positions "
            "WHERE vehicle_id = COALESCE(?, vehicle_id) AND charging = 1 AND recorded_at >= ? AND recorded_at "
            + ("<" if excl else "<=")
            + " ? ORDER BY recorded_at",
            (_current_vehicle_id(), lo, hi),
        ).fetchall()
    else:  # charge still in progress — open upper bound
        rows = db.execute(
            "SELECT recorded_at, charge_voltage_v, charge_current_a, soc FROM positions "
            "WHERE vehicle_id = COALESCE(?, vehicle_id) AND charging = 1 AND recorded_at >= ? ORDER BY recorded_at",
            (_current_vehicle_id(), start),
        ).fetchall()
    labels, power, soc, times = [], [], [], []
    for r in rows:
        v = r["charge_voltage_v"] or 0
        a = r["charge_current_a"] or 0
        labels.append((_local_iso(r["recorded_at"]) or "")[11:16])  # HH:MM local
        power.append(round(abs(v * a) / 1000.0, 3))
        soc.append(r["soc"])
        times.append(r["recorded_at"])  # raw UTC ISO — used to align external (wallbox) history
    return {"labels": labels, "power": power, "soc": soc, "times": times}


def latest_charge_id_with_power() -> int | None:
    """Most recent charge that still has per-sample data (for the Wallbox page chart)."""
    db = _get()
    row = db.execute(
        "SELECT c.id FROM charges c WHERE c.vehicle_id = COALESCE(?, c.vehicle_id) AND EXISTS ("
        "  SELECT 1 FROM positions p WHERE p.vehicle_id = c.vehicle_id AND p.charging = 1"
        "  AND p.recorded_at >= c.started_at"
        "  AND (c.ended_at IS NULL OR p.recorded_at <= c.ended_at)"
        ") ORDER BY c.started_at DESC LIMIT 1",
        (_current_vehicle_id(),)
    ).fetchone()
    return row["id"] if row else None


def charges_with_power(limit: int = 30) -> list[dict]:
    """Recent HOME charges (= the wallbox) that still have a power curve — raw
    {id, started_at, energy_added_kwh}. Only HOME charges are relevant to the
    wallbox comparison: public/away charges (and unconfirmed NULL ones) are excluded,
    which also avoids attributing another car's wallbox session to this car."""
    db = _get()
    rows = db.execute(
        "SELECT c.id, c.started_at, c.energy_added_kwh FROM charges c "
        "WHERE c.vehicle_id = COALESCE(?, c.vehicle_id) AND c.location_type = 'HOME' AND EXISTS ("
        "  SELECT 1 FROM positions p WHERE p.vehicle_id = c.vehicle_id AND p.charging = 1"
        "  AND p.recorded_at >= c.started_at"
        "  AND (c.ended_at IS NULL OR p.recorded_at <= c.ended_at)"
        ") ORDER BY c.started_at DESC LIMIT ?",
        (_current_vehicle_id(), limit),
    ).fetchall()
    return [dict(r) for r in rows]


def _wallbox_home_charges_raw() -> list[dict]:
    """All-time HOME charges that still have a power curve (same EXISTS gate as
    charges_with_power, but selecting BOTH energy columns and unbounded — the Wallbox
    calendar's month totals and year-jump need the full history, not just the newest 30)."""
    db = _get()
    rows = db.execute(
        "SELECT c.id, c.started_at, c.energy_added_kwh, c.ac_energy_kwh FROM charges c "
        "WHERE c.vehicle_id = COALESCE(?, c.vehicle_id) AND c.location_type = 'HOME' AND EXISTS ("
        "  SELECT 1 FROM positions p WHERE p.vehicle_id = c.vehicle_id AND p.charging = 1"
        "  AND p.recorded_at >= c.started_at"
        "  AND (c.ended_at IS NULL OR p.recorded_at <= c.ended_at)"
        ") ORDER BY c.started_at DESC",
        (_current_vehicle_id(),)).fetchall()
    return [dict(r) for r in rows]


def get_wallbox_calendar_month(year: int, month: int) -> dict:
    """Per-day AC(wallbox)/DC(battery) totals for the Wallbox calendar's Month view — uses
    the ALREADY-STORED charge columns (ac_energy_kwh/energy_added_kwh), not the per-session
    HA-history integration main.py's _session_energy does. That stays lazy, computed only
    for the one day the user opens (see get_wallbox_calendar_day) instead of for every
    session up front — each call is a live Home Assistant history fetch."""
    charges = _wallbox_home_charges_raw()
    days: dict[int, dict] = {}
    total = {"count": 0, "ac": 0.0, "dc": 0.0}
    for c in charges:
        dt = _local_dt(c["started_at"])
        if dt is None or dt.year != year or dt.month != month:
            continue
        d = days.setdefault(dt.day, {"count": 0, "ac": 0.0, "dc": 0.0})
        ac = c.get("ac_energy_kwh") or 0
        dc = c.get("energy_added_kwh") or 0
        for node in (d, total):
            node["count"] += 1
            node["ac"] = round(node["ac"] + ac, 2)
            node["dc"] = round(node["dc"] + dc, 2)
    for node in list(days.values()) + [total]:
        node["eff"] = round(100 * node["dc"] / node["ac"], 1) if node["ac"] else None
    return {"year": year, "month": month, "days": days, "total": total}


def get_wallbox_calendar_day(year: int, month: int, day: int) -> list[dict]:
    """{id, time} for each HOME-with-power charge on ONE calendar day, most-recent-first —
    the day-drawer computes each session's precise AC/DC comparison (_session_energy,
    main.py) lazily per row, not all up front like the old accordion did."""
    out = []
    for c in _wallbox_home_charges_raw():
        dt = _local_dt(c["started_at"])
        if dt and dt.year == year and dt.month == month and dt.day == day:
            out.append({"id": c["id"], "time": dt.strftime("%H:%M"), "_sort": c["started_at"]})
    out.sort(key=lambda s: s["_sort"], reverse=True)
    for s in out:
        del s["_sort"]
    return out


def get_wallbox_years() -> list[int]:
    """Distinct years (local time, most recent first) with at least one HOME charge that
    has a power curve — populates the Wallbox calendar's year-jump pills."""
    years = {dt.year for dt in (_local_dt(c["started_at"]) for c in _wallbox_home_charges_raw()) if dt}
    return sorted(years, reverse=True)


def is_home_charge(charge_id: int) -> bool:
    """True only when the charge is tagged HOME (= the wallbox)."""
    db = _get()
    row = db.execute("SELECT location_type FROM charges WHERE id = ? AND vehicle_id = COALESCE(?, vehicle_id)",
                     (charge_id, _current_vehicle_id())).fetchone()
    return bool(row) and row["location_type"] == "HOME"


def unconfirmed_charges_count() -> int:
    """How many FINISHED charges still have no type set (location_type NULL) → need
    confirming. In-progress charges (ended_at NULL) are excluded: they can't be
    confirmed until they end, otherwise the banner would never clear while charging."""
    db = _get()
    row = db.execute(
        "SELECT COUNT(*) n FROM charges WHERE vehicle_id = COALESCE(?, vehicle_id) "
        "AND location_type IS NULL AND ended_at IS NOT NULL",
        (_current_vehicle_id(),)
    ).fetchone()
    return row["n"] if row else 0


def latest_home_charge_cost():
    """Cost of the most recent home charge (= the wallbox) — from Mate's own charge
    records, so the Wallbox page reuses it instead of a separate HA cost sensor."""
    db = _get()
    row = db.execute(
        "SELECT cost FROM charges WHERE vehicle_id = COALESCE(?, vehicle_id) "
        "AND location_type = 'HOME' AND cost IS NOT NULL "
        "ORDER BY started_at DESC LIMIT 1",
        (_current_vehicle_id(),)
    ).fetchone()
    return row["cost"] if row else None


def get_stats_grouped() -> list[dict]:
    """Trip stats nested as year → month → day (aggregated, no individual trips)."""
    from collections import OrderedDict
    db = _get()
    rows = db.execute("""
        SELECT
            strftime('%Y', started_at)    AS year,
            strftime('%Y-%m', started_at) AS month_key,
            date(started_at)              AS day_key,
            COUNT(*)                      AS trip_count,
            ROUND(SUM(distance_km), 2)    AS total_km,
            ROUND(SUM(distance_km * COALESCE(efficiency_kwh_100km, 0) / 100), 2) AS total_kwh,
            ROUND(
                SUM(distance_km * COALESCE(efficiency_kwh_100km, 0) / 100) /
                NULLIF(SUM(CASE WHEN efficiency_kwh_100km IS NOT NULL
                               THEN distance_km END), 0) * 100, 1
            ) AS avg_efficiency,
            ROUND(SUM(regen_kwh), 2) AS total_regen_kwh
        FROM trips
        WHERE vehicle_id = COALESCE(?, vehicle_id) AND ended_at IS NOT NULL
        GROUP BY year, month_key, day_key
        ORDER BY started_at DESC
    """, (_current_vehicle_id(),)).fetchall()

    lang = get_language()
    years: dict = OrderedDict()
    for r in rows:
        d = dict(r)
        yr, mo_key, day_key = d["year"], d["month_key"], d["day_key"]

        # Localize labels in Python (SQLite %B/%b not supported; strftime is English-only)
        try:
            mo_dt  = datetime.strptime(mo_key, "%Y-%m")
            mo_label = i18n.fmt_month_year(lang, mo_dt)
            day_dt   = datetime.strptime(day_key, "%Y-%m-%d")
            d["day_label"] = i18n.fmt_day_month_year(lang, day_dt)
        except Exception:
            mo_label = mo_key
            d["day_label"] = day_key

        if yr not in years:
            years[yr] = {"label": yr, "trip_count": 0, "total_km": 0.0,
                         "total_kwh": 0.0, "total_regen_kwh": 0.0,
                         "_ws": 0.0, "_wd": 0.0,
                         "avg_efficiency": None, "months": OrderedDict()}
        if mo_key not in years[yr]["months"]:
            years[yr]["months"][mo_key] = {"label": mo_label, "trip_count": 0,
                                           "total_km": 0.0, "total_kwh": 0.0,
                                           "total_regen_kwh": 0.0,
                                           "_ws": 0.0, "_wd": 0.0,
                                           "avg_efficiency": None, "days": []}

        years[yr]["months"][mo_key]["days"].append(d)

        km  = d.get("total_km") or 0
        eff = d.get("avg_efficiency")
        for node in (years[yr], years[yr]["months"][mo_key]):
            node["trip_count"]      += d["trip_count"]
            node["total_km"]         = round(node["total_km"] + km, 2)
            node["total_kwh"]        = round(node["total_kwh"] + (d.get("total_kwh") or 0), 2)
            node["total_regen_kwh"]  = round(node["total_regen_kwh"] + (d.get("total_regen_kwh") or 0), 2)
            if eff and km > 0:
                node["_ws"] += km * eff
                node["_wd"] += km

    for yr_node in years.values():
        if yr_node["_wd"] > 0:
            yr_node["avg_efficiency"] = round(yr_node["_ws"] / yr_node["_wd"], 1)
        for mo_node in yr_node["months"].values():
            if mo_node["_wd"] > 0:
                mo_node["avg_efficiency"] = round(mo_node["_ws"] / mo_node["_wd"], 1)
            mo_node["trips"] = []

    # Attach individual trips (chronological ASC) to each month for per-trip charts
    db2 = _get()
    trip_rows = db2.execute(
        """SELECT id, started_at, distance_km, efficiency_kwh_100km, regen_kwh
           FROM trips WHERE vehicle_id = COALESCE(?, vehicle_id) AND ended_at IS NOT NULL
           ORDER BY started_at ASC""",
        (_current_vehicle_id(),)
    ).fetchall()
    for r in trip_rows:
        t = dict(r)
        if not t.get("started_at"):
            continue
        dt = _local_dt(t["started_at"])
        if dt is None:
            continue
        yr, mo_key = dt.strftime("%Y"), dt.strftime("%Y-%m")
        t["label"] = dt.strftime("%d/%m %H:%M")
        if yr in years and mo_key in years[yr]["months"]:
            years[yr]["months"][mo_key]["trips"].append(t)

    return list(years.values())


def get_monthly_stats() -> list[dict]:
    db = _get()
    rows = db.execute(
        """SELECT
               strftime('%Y-%m', started_at) AS month,
               COUNT(*)                       AS trip_count,
               ROUND(SUM(distance_km), 2)     AS total_km,
               ROUND(SUM(CASE WHEN efficiency_kwh_100km IS NOT NULL
                              THEN distance_km END), 2) AS km_with_eff,
               ROUND(SUM(distance_km * COALESCE(efficiency_kwh_100km,0) / 100), 2) AS total_kwh,
               ROUND(AVG(efficiency_kwh_100km), 1) AS avg_efficiency
           FROM trips
           WHERE vehicle_id = COALESCE(?, vehicle_id) AND ended_at IS NOT NULL
           GROUP BY month
           ORDER BY month DESC
           LIMIT 12""",
        (_current_vehicle_id(),)
    ).fetchall()
    return [dict(r) for r in rows]


def _iso_to_utc(x):
    """Normalize any ISO timestamp to a UTC (+00:00) string so it compares correctly against
    positions.recorded_at (stored in UTC). get_charges() hands us LOCAL-offset timestamps, and a raw
    string compare of differently-offset ISO values is wrong — so always convert to UTC first."""
    if not x:
        return x
    import datetime
    try:
        dt = datetime.datetime.fromisoformat(x)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc).isoformat()
    except Exception:
        return x


def get_position_near(ts: "str | None", tolerance_min: int = 20) -> "dict | None":
    """The single positions row closest to timestamp `ts` (within tolerance_min minutes
    either side) — for reading outside_temp/battery_min_temp at an arbitrary INSTANT (a
    trip's or charge's own start/end), which nothing needed before: every existing
    telemetry query is either "latest" (status cards) or a min/max aggregate over a whole
    charging window (_charge_temp_odo), never "nearest to one point in time." None when
    `ts` is missing/unparseable, or nothing falls within the tolerance window (e.g. the
    sample was already pruned by the positions_retention_days setting)."""
    utc = _iso_to_utc(ts)
    if not utc:
        return None
    target = _trip_epoch(utc)
    if target is None:
        return None
    try:
        center = datetime.fromisoformat(utc)
    except Exception:
        return None
    lo = (center - timedelta(minutes=tolerance_min)).isoformat()
    hi = (center + timedelta(minutes=tolerance_min)).isoformat()
    rows = _get().execute(
        "SELECT * FROM positions WHERE vehicle_id = COALESCE(?, vehicle_id) "
        "AND recorded_at >= ? AND recorded_at <= ? ORDER BY recorded_at",
        (_current_vehicle_id(), lo, hi)).fetchall()
    if not rows:
        return None
    best = min(rows, key=lambda r: abs((_trip_epoch(r["recorded_at"]) or 0) - target))
    return dict(best)


def generate_trip_auto_note(trip_id: int, provider: str = "", api_key: "str | None" = None,
                             only_if_note_empty: bool = False) -> "str | None":
    """Builds the start/end address+time+temperature summary and writes it straight into
    the trip's `note` field (the ONE note field — no separate read-only line to keep in
    sync). Reverse-geocoding is a live network call (web/geocode.py, no caching, and
    Nominatim's usage policy forbids bulk lookups) — safe for the 🧭 button (one trip) and
    for the automatic call at trip-close (poller/recorder.py, one NEW trip at a time), but
    never a historical backfill sweep. `only_if_note_empty` is the automatic-at-close
    guard: a manual note the user already typed is never clobbered by this running just
    after; the manual button always overwrites (the UI confirms with the user first when
    there's something to lose — see trip_detail.html's hx-confirm). Temperature reuses the
    trip's own outside_temp_start_c/end_c (Open-Meteo, already collected by
    elevation_enrich) — None when that enrichment hasn't run yet (may still be the case
    right at trip-close; regenerating later via the button picks it up once it has)."""
    import geocode
    import units
    row = _get().execute("SELECT * FROM trips WHERE id=? AND vehicle_id = COALESCE(?, vehicle_id)",
                         (trip_id, _current_vehicle_id())).fetchone()
    if not row:
        return None
    row = dict(row)
    if only_if_note_empty and (row.get("note") or "").strip():
        return row.get("note")

    def _addr(lat, lon):
        # geocode.reverse_geocode's own keyed-provider path swallows failures and falls back to
        # Nominatim, but that final call (urllib underneath) can still raise on a timeout/DNS
        # blip — one endpoint's network hiccup must not blank the other endpoint's time+temp.
        if lat is None or lon is None:
            return None
        try:
            return geocode.reverse_geocode(lat, lon, provider, api_key)
        except Exception:  # noqa: BLE001
            return None

    start_addr = _addr(row.get("start_lat"), row.get("start_lon"))
    end_addr = _addr(row.get("end_lat"), row.get("end_lon"))
    start_dt = _local_dt(row.get("started_at"))
    end_dt = _local_dt(row.get("ended_at"))

    def _line(marker: str, addr, dt, temp_c) -> "str | None":
        if not dt:
            return None
        bits = ([addr] if addr else []) + [dt.strftime("%H:%M")]
        if temp_c is not None:
            bits.append(f"🌡️ {units.temp(temp_c)}")
        text = " · ".join(bits)
        return f"{marker} {text}" if marker else text

    lines = [_line("", start_addr, start_dt, row.get("outside_temp_start_c")),
             _line("→", end_addr, end_dt, row.get("outside_temp_end_c"))]
    text = (" ".join(l for l in lines if l) or None)
    if text:
        text = text.strip()[:1000]
    db = _conn_rw()
    db.execute("UPDATE trips SET note=? WHERE id=?", (text, trip_id))
    db.commit()
    return text


def generate_charge_auto_note(charge_id: int, provider: str = "", api_key: "str | None" = None,
                               only_if_note_empty: bool = False) -> "str | None":
    """Builds the station-address+start/end time+temperature summary and writes it
    straight into the charge's `note` field (the ONE note field — no separate read-only
    line to keep in sync). The address reuses find_station_candidates — the SAME OSM/OCM
    lookup the 📍 label/🔗 link already run — matched to the charge's own resolved name
    when possible; skipped for HOME charges (no station to address, and the user already
    knows their own home). Both temperatures come from the car's own telemetry
    (positions.outside_temp / battery_min_temp) nearest each endpoint's timestamp
    (get_position_near) — charges have no Open-Meteo weather enrichment like trips do
    (elevation_enrich), so 🌡️ here can be blank on cars that don't report an
    ambient-temperature signal at all. Live network calls (geocoding, station lookup) —
    safe for the 🧭 button (one charge) and for the automatic call at charge-close
    (poller/recorder.py, one NEW charge at a time), never a historical backfill sweep.
    `only_if_note_empty` is the automatic-at-close guard: a manual note the user already
    typed is never clobbered by this running just after; the manual button always
    overwrites (the UI confirms with the user first when there's something to lose — see
    charge_card.html's hx-confirm)."""
    import charger_locator
    import units
    row = _get().execute("SELECT * FROM charges WHERE id=? AND vehicle_id = COALESCE(?, vehicle_id)",
                         (charge_id, _current_vehicle_id())).fetchone()
    if not row:
        return None
    row = dict(row)
    if only_if_note_empty and (row.get("note") or "").strip():
        return row.get("note")
    address = None
    if (row.get("location_type") != "HOME"
            and row.get("latitude") is not None and row.get("longitude") is not None):
        options, _ok = charger_locator.find_station_candidates(row["latitude"], row["longitude"])
        match = next((o for o in options if o.get("name") == row.get("location_name")), None)
        address = (match or {}).get("address")
        if not address:
            # The name-matched option (or none, if the saved name came from a source no
            # longer offered) can still lack a street address even though a DIFFERENT
            # source at the very same physical site has one — charger_locator keeps
            # differently-named sources as separate options on purpose (its own docstring:
            # lets the manual relocate button offer a real choice), so an address one
            # network reports isn't automatically inherited by another's option. Borrow the
            # nearest option that has one — options are already distance-sorted, and it's
            # the same charging site either way.
            address = next((o["address"] for o in options if o.get("address")), None)
    start_dt = _local_dt(row.get("started_at"))
    end_dt = _local_dt(row.get("ended_at"))
    p_start = get_position_near(row.get("started_at"))
    p_end = get_position_near(row.get("ended_at"))

    def _temps(pos) -> str:
        if not pos:
            return ""
        bits = []
        if pos.get("outside_temp") is not None:
            bits.append(f"🌡️ {units.temp(pos['outside_temp'])}")
        if pos.get("battery_min_temp") is not None:
            bits.append(f"🔋 {units.temp(pos['battery_min_temp'])}")
        return " · ".join(bits)

    def _line(marker: str, addr, dt, pos) -> "str | None":
        if not dt:
            return None
        bits = ([addr] if addr else []) + [dt.strftime("%H:%M")]
        t = _temps(pos)
        if t:
            bits.append(t)
        text = " · ".join(bits)
        return f"{marker} {text}" if marker else text

    lines = [_line("", address, start_dt, p_start),
             _line("→", None, end_dt, p_end)]
    text = (" ".join(l for l in lines if l) or None)
    if text:
        text = text.strip()[:1000]
    db = _conn_rw()
    db.execute("UPDATE charges SET note=? WHERE id=?", (text, charge_id))
    db.commit()
    return text


def _charge_active_window(db, started_at, ended_at):
    """First & last sample with REAL charging power (positions.charging=1, which is set only when power
    flows — NOT on plug-in) inside the session window. Returns (start_utc_iso, end_utc_iso), or
    (None, None) when there are no power samples (e.g. pruned/old charges). Bounds are normalized to UTC
    because positions.recorded_at is UTC while the charge timestamps may arrive localized."""
    if not started_at:
        return None, None
    # Cap at the next charge's start so an orphan/overlapping charge (whose ended_at can
    # bleed past a later charge — see the poller's close_orphan_charges) cannot inherit the
    # next charge's last power sample as its own window end.
    lo, hi, excl = _power_window_bounds(db, started_at, ended_at)
    row = db.execute(
        "SELECT MIN(recorded_at) AS s, MAX(recorded_at) AS e FROM positions "
        "WHERE vehicle_id = COALESCE(?, vehicle_id) AND charging = 1 AND recorded_at >= ? AND recorded_at "
        + ("<" if excl else "<=") + " ?",
        (_current_vehicle_id(), lo, hi),
    ).fetchone()
    return (row["s"], row["e"]) if (row and row["s"]) else (None, None)


def _charge_window_display(db, raw_start, raw_end) -> dict:
    """For the charges list: surface the REAL charging window (first→last power) only when it differs
    from the plug-in→unplug session window by more than a threshold — i.e. a delayed/scheduled charge
    or a long idle tail. For a normal charge the two coincide → {differs: False} (no extra clutter).
    Returns {differs: False} or {differs: True, real_start, real_end} (HH:MM, local)."""
    rs, re = _charge_active_window(db, raw_start, raw_end)
    if not rs:
        return {"differs": False}
    import datetime

    def _p(x):
        try:
            return datetime.datetime.fromisoformat(x)
        except Exception:
            return None

    s0, e0, rs0, re0 = _p(raw_start), _p(raw_end), _p(rs), _p(re)
    THRESH = 300  # seconds — below this the windows are "the same" (just poll granularity)
    differs = bool((s0 and rs0 and (rs0 - s0).total_seconds() > THRESH)
                   or (e0 and re0 and (e0 - re0).total_seconds() > THRESH))
    if not differs:
        return {"differs": False}
    return {"differs": True,
            "real_start": (_local_iso(rs) or "")[11:16],
            "real_end": (_local_iso(re) or "")[11:16]}


def _billed_kwh(c) -> float:
    """The energy figure SHOWN (and billed) for a charge: the wallbox-measured AC kWh for
    HOME charges that have a wallbox reading (what you actually pay for, conversion losses
    included), else the battery DC (SoC) energy. Single source of truth so the per-charge
    card, the period totals and get_charge_stats all agree. Mirrors the SQL CASE in
    get_charge_stats and the card's `show_wb` condition (charges.html)."""
    ac = c.get("ac_energy_kwh")
    if c.get("location_type") == "HOME" and ac and ac > 0:
        return ac
    return c.get("energy_added_kwh") or 0


def _filter_by_station(charges: list[dict], station: str) -> list[dict]:
    """Narrow a charge list to the one physical station a "lat,lon" key (3-decimal rounded,
    from get_charging_stations()) identifies. Shared by the accordion, the calendar and
    search — a malformed key yields [], never a crash or the unfiltered set."""
    try:
        lat_r, lon_r = (round(float(v), 3) for v in station.split(","))
    except (ValueError, AttributeError):
        return []
    return [c for c in charges if c.get("latitude") is not None and c.get("longitude") is not None
            and round(c["latitude"], 3) == lat_r and round(c["longitude"], 3) == lon_r]


def get_charge_years(station: str | None = None) -> list[int]:
    """Distinct years (local time, most recent first) with at least one charge — populates
    the Ricariche calendar's year-jump pills with only years the user actually has data for."""
    charges = get_charges(limit=1_000_000)
    if station:
        charges = _filter_by_station(charges, station)
    years = {dt.year for dt in (_local_dt(c.get("started_at")) for c in charges) if dt}
    return sorted(years, reverse=True)


def get_charges_grouped(station: str | None = None) -> list[dict]:
    """Return charges nested as year → month → day. `station`, when given, is a
    "lat,lon" key from get_charging_stations() (same rounding) — narrows the tree to
    just the sessions charged at that one station, for the /charges?station= filtered view."""
    # #67 (rossiadobe): the grouped Charges page must show the FULL history — a default
    # limit would silently hide older charges (his CSV-imported ones before the newest 50
    # vanished, the list "stopped at October 2025"). The page is a collapsed accordion, so
    # loading everything is fine — same unbounded read the CSV export and monthly report use.
    charges = get_charges(limit=1_000_000)
    if station:
        charges = _filter_by_station(charges, station)
    from collections import OrderedDict
    db = _get()

    def _node(label):
        return {"label": label, "count": 0, "kwh": 0.0, "cost": 0.0, "has_cost": False, "months": OrderedDict()}

    def _day_node(label):
        return {"label": label, "count": 0, "kwh": 0.0, "cost": 0.0, "has_cost": False, "charges": []}

    lang = get_language()
    years: dict = OrderedDict()
    for c in charges:
        if not c.get("started_at"):
            continue
        dt = _local_dt(c["started_at"])
        if dt is None:
            continue
        # Real charging window (first→last power) vs the plug-in→unplug session — compute on the RAW
        # UTC timestamps BEFORE we localize them below.
        c["active_window"] = _charge_window_display(db, c.get("started_at"), c.get("ended_at"))
        c["started_at"] = dt.isoformat()
        c["ended_at"] = _local_iso(c.get("ended_at"))

        yr  = dt.strftime("%Y")
        mo  = i18n.fmt_month_year(lang, dt)
        day = i18n.fmt_day_month_year(lang, dt)

        years.setdefault(yr, _node(yr))
        years[yr]["months"].setdefault(mo, {**_node(mo), "days": OrderedDict()})
        years[yr]["months"][mo]["days"].setdefault(day, _day_node(day))

        years[yr]["months"][mo]["days"][day]["charges"].append(c)

        kwh  = _billed_kwh(c)   # wallbox AC for HOME (billed); DC otherwise — matches the card
        cost = c.get("cost") or 0
        for node in [years[yr], years[yr]["months"][mo], years[yr]["months"][mo]["days"][day]]:
            node["kwh"]   = round(node["kwh"] + kwh, 2)
            node["count"] += 1
            if c.get("cost") is not None:
                node["cost"]     = round(node["cost"] + cost, 2)
                node["has_cost"] = True

    return list(years.values())


def _localized_charges(charges: list[dict]) -> list[dict]:
    """Per-charge localization shared by the Charges calendar and search: local start/end
    times + the real-charging-window display, same convention get_charges_grouped applies
    inline — so charge_card.html renders identically wherever it's included. Adds a private
    `_dt` (aware, local-tz datetime) for the caller's OWN day/date bucketing or filtering;
    never rendered, so its presence in the dict is harmless to charge_card.html."""
    db = _get()
    out = []
    for c in charges:
        if not c.get("started_at"):
            continue
        dt = _local_dt(c["started_at"])
        if dt is None:
            continue
        c["active_window"] = _charge_window_display(db, c.get("started_at"), c.get("ended_at"))
        c["started_at"] = dt.isoformat()
        c["ended_at"] = _local_iso(c.get("ended_at"))
        c["_dt"] = dt
        out.append(c)
    return out


def get_charges_calendar_month(year: int, month: int, station: str | None = None) -> dict:
    """Per-day totals for the Ricariche calendar's Month view: how many sessions, kWh and
    cost landed on each day of `year`/`month` (local time, same billed-kWh convention as
    get_charges_grouped) plus the month's own total — the grid only needs counts, the
    day's actual charges are fetched lazily (see get_charges_calendar_day) when a cell is
    clicked, so a month never ships more than ~31 small numbers to the template."""
    charges = _localized_charges(get_charges(limit=1_000_000))
    if station:
        charges = _filter_by_station(charges, station)
    days: dict[int, dict] = {}
    total = {"count": 0, "kwh": 0.0, "cost": 0.0, "has_cost": False}
    for c in charges:
        dt = c["_dt"]
        if dt.year != year or dt.month != month:
            continue
        d = days.setdefault(dt.day, {"count": 0, "kwh": 0.0, "cost": 0.0, "has_cost": False})
        kwh = _billed_kwh(c)
        for node in (d, total):
            node["kwh"] = round(node["kwh"] + kwh, 2)
            node["count"] += 1
            if c.get("cost") is not None:
                node["cost"] = round(node["cost"] + (c["cost"] or 0), 2)
                node["has_cost"] = True
    return {"year": year, "month": month, "days": days, "total": total}


def get_charges_calendar_day(year: int, month: int, day: int, station: str | None = None) -> list[dict]:
    """The charge_card.html-ready charges for ONE calendar day — backs the Month view's
    day drawer, most-recent-first."""
    charges = _localized_charges(get_charges(limit=1_000_000))
    if station:
        charges = _filter_by_station(charges, station)
    charges = [c for c in charges
               if c["_dt"].year == year and c["_dt"].month == month and c["_dt"].day == day]
    charges.sort(key=lambda c: c["started_at"], reverse=True)
    return charges


def search_charges(text: str = "", charge_type: str = "",
                    cost_min: float | None = None, cost_max: float | None = None,
                    kwh_min: float | None = None, kwh_max: float | None = None,
                    date_from: str = "", date_to: str = "",
                    station: str | None = None) -> list[dict]:
    """Flat, most-recent-first list of charges matching ALL given filters — the Ricariche
    search bar. `text` matches the station name OR the user note (substring, case-
    insensitive); `charge_type` is a location_type key (AC/FAST/HPC/HOME/FREE/MANUAL);
    the kWh/cost filters compare against the SAME billed figure the card shows
    (_billed_kwh); `date_from`/`date_to` are inclusive "YYYY-MM-DD" LOCAL calendar dates.
    Loads the full history like get_charges_grouped (#67 — no default limit may hide
    older charges) and filters in Python — same convention as the calendar/accordion,
    no SQL date-math needed since _local_dt already localizes the timezone."""
    charges = _localized_charges(get_charges(limit=1_000_000))
    if station:
        charges = _filter_by_station(charges, station)
    q = (text or "").strip().lower()
    ctype = (charge_type or "").strip().upper()
    try:
        d_from = date.fromisoformat(date_from) if date_from else None
    except ValueError:
        d_from = None
    try:
        d_to = date.fromisoformat(date_to) if date_to else None
    except ValueError:
        d_to = None
    out = []
    for c in charges:
        if q and q not in (c.get("location_name") or "").lower() \
             and q not in (c.get("note") or "").lower():
            continue
        if ctype and (c.get("location_type") or "") != ctype:
            continue
        kwh = _billed_kwh(c)
        if kwh_min is not None and kwh < kwh_min:
            continue
        if kwh_max is not None and kwh > kwh_max:
            continue
        cost = c.get("cost")
        if cost_min is not None and (cost is None or cost < cost_min):
            continue
        if cost_max is not None and (cost is None or cost > cost_max):
            continue
        day = c["_dt"].date()
        if d_from and day < d_from:
            continue
        if d_to and day > d_to:
            continue
        out.append(c)
    out.sort(key=lambda c: c["started_at"], reverse=True)
    return out


def get_stats_summary() -> dict:
    db = _get()
    trips = db.execute(
        """SELECT
               COUNT(*)                                                       AS trip_count,
               ROUND(SUM(distance_km), 2)                                    AS total_km,
               ROUND(SUM(distance_km * COALESCE(efficiency_kwh_100km,0)/100), 2) AS total_kwh_used,
               ROUND(SUM(duration_min), 0)                                   AS total_drive_min,
               -- distance-weighted = total energy / total distance (#42): a simple AVG
               -- over-weights short trips and disagreed with both the Trips-page header
               -- and this page's own "energy used ÷ distance". Matches get_trips_summary.
               ROUND(SUM(distance_km * efficiency_kwh_100km) /
                     NULLIF(SUM(CASE WHEN efficiency_kwh_100km IS NOT NULL
                                     THEN distance_km END), 0), 1)           AS avg_efficiency,
               -- "Best" must come from a real trip, not a 3 km downhill coast or a glitch frame
               -- (#86): a min-distance floor keeps this metric representative of the car.
               ROUND(MIN(CASE WHEN efficiency_kwh_100km > 0 AND distance_km >= 15
                              THEN efficiency_kwh_100km END), 1) AS best_efficiency,
               ROUND(SUM(regen_kwh), 2)                                      AS total_regen_kwh,
               ROUND(AVG(regen_kwh), 2)                                      AS avg_regen_kwh
           FROM trips WHERE vehicle_id = COALESCE(?, vehicle_id) AND ended_at IS NOT NULL""",
        (_current_vehicle_id(),)
    ).fetchone()
    charges = db.execute(
        """SELECT
               COUNT(*)                         AS charge_count,
               ROUND(SUM(energy_added_kwh), 2)  AS total_kwh_charged,
               ROUND(SUM(cost), 2)              AS total_cost
           FROM charges WHERE vehicle_id = COALESCE(?, vehicle_id) AND ended_at IS NOT NULL""",
        (_current_vehicle_id(),)
    ).fetchone()
    t = dict(trips) if trips else {}
    c = dict(charges) if charges else {}
    total_kwh = t.get("total_kwh_used") or 0
    total_regen = t.get("total_regen_kwh") or 0
    t["regen_pct"] = round(total_regen / total_kwh * 100, 1) if total_kwh > 0 else None
    return {**t, **c}


def get_charge_stats() -> dict:
    db = _get()
    row = db.execute(
        """SELECT
               COUNT(*)                            AS session_count,
               -- billed energy: wallbox AC for HOME w/ a reading, else battery DC (mirrors _billed_kwh)
               ROUND(SUM(CASE WHEN location_type='HOME' AND ac_energy_kwh IS NOT NULL AND ac_energy_kwh > 0
                              THEN ac_energy_kwh ELSE energy_added_kwh END), 2)  AS total_kwh,
               ROUND(AVG(duration_min / 60.0), 1) AS avg_duration_h,
               ROUND(SUM(cost), 2)                AS total_cost,
               ROUND(AVG(end_soc - start_soc), 1) AS avg_soc_delta,
               ROUND(MAX(max_power_kw), 2)        AS peak_power_kw
           FROM charges
           WHERE vehicle_id = COALESCE(?, vehicle_id) AND ended_at IS NOT NULL""",
        (_current_vehicle_id(),)
    ).fetchone()
    return dict(row) if row else {}


def get_ac_dc_stats() -> dict:
    """Count + energy of AC vs DC charge sessions. DC = charge_type 'DC', or (when not
    set) a measured peak power above 11 kW (AC tops out at ~11 kW; DC is faster)."""
    db = _get()
    rows = db.execute(
        "SELECT charge_type, max_power_kw, energy_added_kwh FROM charges "
        "WHERE vehicle_id = COALESCE(?, vehicle_id) AND ended_at IS NOT NULL",
        (_current_vehicle_id(),)
    ).fetchall()
    ac = {"count": 0, "kwh": 0.0}
    dc = {"count": 0, "kwh": 0.0}
    for r in rows:
        ct = r["charge_type"]
        is_dc = ct == "DC" or (ct is None and (r["max_power_kw"] or 0) > 11)
        b = dc if is_dc else ac
        b["count"] += 1
        b["kwh"] += r["energy_added_kwh"] or 0
    ac["kwh"] = round(ac["kwh"], 2)
    dc["kwh"] = round(dc["kwh"], 2)
    return {"ac": ac, "dc": dc, "total": ac["count"] + dc["count"]}


# ── Monthly report (driving + charging + cost, one month) ──────────────────────

def _month_shift(month_key: str, delta: int) -> str:
    """'YYYY-MM' shifted by `delta` calendar months (delta may be negative)."""
    y, m = int(month_key[:4]), int(month_key[5:7])
    idx = y * 12 + (m - 1) + delta
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def _report_bucket() -> dict:
    return {
        "trip_count": 0, "total_km": 0.0, "total_kwh_used": 0.0,
        "regen_kwh": 0.0, "drive_min": 0.0,
        "_eff_wsum": 0.0, "_eff_wdist": 0.0, "avg_efficiency": None,
        "charge_count": 0, "charge_kwh": 0.0, "charge_cost": 0.0, "has_cost": False,
        "unconfirmed": 0,
        "home":   {"count": 0, "kwh": 0.0, "cost": 0.0},
        "public": {"count": 0, "kwh": 0.0, "cost": 0.0},
        "_days": {},   # day-of-month -> {"km": float, "cost": float}
    }


def _collect_monthly_buckets() -> dict:
    """Bucket every trip and charge into its LOCAL 'YYYY-MM'. One pass, reused for the
    selected month, the previous month (deltas) and the month list (navigation). Trips come
    from get_trips() (merged-aware, same as the Trips page); charges carry the frozen per-row
    cost and the billed-kWh basis (_billed_kwh) so the report's € matches the Charges page."""
    buckets: dict = {}

    for tr in get_trips(limit=1_000_000):
        dt = _local_dt(tr.get("started_at"))
        if dt is None:
            continue
        b = buckets.setdefault(dt.strftime("%Y-%m"), _report_bucket())
        km  = tr.get("distance_km") or 0
        eff = tr.get("efficiency_kwh_100km")
        b["trip_count"]     += 1
        b["total_km"]       += km
        b["total_kwh_used"] += km * (eff or 0) / 100.0
        b["regen_kwh"]      += tr.get("regen_kwh") or 0
        b["drive_min"]      += tr.get("duration_min") or 0
        if eff and km > 0:
            b["_eff_wsum"]  += km * eff
            b["_eff_wdist"] += km
        b["_days"].setdefault(dt.day, {"km": 0.0, "cost": 0.0})["km"] += km

    for c in get_charges(limit=1_000_000):
        dt = _local_dt(c.get("started_at"))
        if dt is None:
            continue
        b = buckets.setdefault(dt.strftime("%Y-%m"), _report_bucket())
        kwh  = _billed_kwh(c)
        cost = c.get("cost")
        lt   = c.get("location_type")
        b["charge_count"] += 1
        b["charge_kwh"]   += kwh
        if cost is not None:
            b["charge_cost"] += cost
            b["has_cost"]     = True
        grp = b["home"] if lt == "HOME" else (b["public"] if lt else None)
        if grp is not None:
            grp["count"] += 1
            grp["kwh"]   += kwh
            if cost is not None:
                grp["cost"] += cost
        else:
            b["unconfirmed"] += 1   # untyped charge: counted in totals, left out of the split
        if cost is not None:
            b["_days"].setdefault(dt.day, {"km": 0.0, "cost": 0.0})["cost"] += cost

    for b in buckets.values():
        if b["_eff_wdist"] > 0:
            b["avg_efficiency"] = round(b["_eff_wsum"] / b["_eff_wdist"], 1)
        for k in ("total_km", "total_kwh_used", "regen_kwh", "charge_kwh", "charge_cost"):
            b[k] = round(b[k], 2)
        b["drive_min"] = int(round(b["drive_min"]))
        for g in ("home", "public"):
            b[g]["kwh"]  = round(b[g]["kwh"], 2)
            b[g]["cost"] = round(b[g]["cost"], 2)
    return buckets


def get_monthly_report(month: Optional[str] = None) -> dict:
    """One-month digest combining driving, charging and cost, with deltas vs the previous
    calendar month and the list of months that have data (for the ◀ ▶ / dropdown nav).
    `month` = local 'YYYY-MM'; defaults to the most recent month with any data."""
    import calendar
    buckets = _collect_monthly_buckets()
    if not buckets:
        return {"has_data": False, "month": None, "months": []}

    months_desc = sorted(buckets.keys(), reverse=True)
    if not month or month not in buckets:
        month = months_desc[0]

    lang = get_language()
    def _label(mk):
        return i18n.fmt_month_year(lang, datetime.strptime(mk, "%Y-%m"))

    cur      = buckets[month]
    prev_key = _month_shift(month, -1)
    prev     = buckets.get(prev_key)

    older = [m for m in months_desc if m < month]   # desc → nearest past is first
    newer = [m for m in months_desc if m > month]   # desc → nearest future is last

    def _delta(now, was):
        if not was:                                 # None or 0 → no meaningful %
            return {"diff": round(now, 2), "pct": None}
        return {"diff": round(now - was, 2), "pct": int(round((now - was) / was * 100))}

    deltas = None
    if prev:
        eff_d = None
        if cur["avg_efficiency"] is not None and prev["avg_efficiency"] is not None:
            eff_d = _delta(cur["avg_efficiency"], prev["avg_efficiency"])
        deltas = {
            "km":         _delta(cur["total_km"], prev["total_km"]),
            "kwh_used":   _delta(cur["total_kwh_used"], prev["total_kwh_used"]),
            "cost":       _delta(cur["charge_cost"], prev["charge_cost"]),
            "charge_kwh": _delta(cur["charge_kwh"], prev["charge_kwh"]),
            "efficiency": eff_d,
        }

    avg_price = (round(cur["charge_cost"] / cur["charge_kwh"], 3)
                 if cur["charge_kwh"] > 0 and cur["has_cost"] else None)

    ndays = calendar.monthrange(int(month[:4]), int(month[5:7]))[1]
    daily = [{"day": d,
              "km":   cur["_days"].get(d, {}).get("km", 0.0),
              "cost": cur["_days"].get(d, {}).get("cost", 0.0)}
             for d in range(1, ndays + 1)]

    return {
        "has_data": True,
        "month": month, "label": _label(month),
        "prev_month": older[0] if older else None,
        "next_month": newer[-1] if newer else None,
        "months": [{"key": m, "label": _label(m)} for m in months_desc],
        "cur": cur, "prev": prev, "prev_label": _label(prev_key) if prev else None,
        "deltas": deltas, "avg_price": avg_price, "daily": daily,
    }


# ── Battery health (SoH) ───────────────────────────────────────────────────────

def get_battery_capacity_kwh() -> float:
    """Configured (nominal) usable battery capacity, set per-model at first run and
    overridable in Settings. Used as the 100%-SoC reference for the health estimate."""
    try:
        return float(get_setting("battery_capacity_kwh", "65.0"))
    except (TypeError, ValueError):
        return 65.0


_SCAN_MAX_KW = 250.0  # implied charge rate above this → spurious-SoC glitch, not a real charge


def scan_missed_charges(threshold: float = 2.0, apply: bool = False) -> list[dict]:
    """Find charges that happened while the car was asleep/offline BEFORE live
    reconstruction existed (or while the poller was down) and were never logged — a
    SoC that ROSE while parked, not covered by any existing charge (GitHub #35, from
    the #29 follow-up). Returns candidate dicts; with apply=True also inserts them as
    reconstructed charges (charge_type 'AC', cost NULL until the user confirms the type,
    exactly like the live reconstruction path).

    Idempotent: an applied candidate's window is then covered by its own charge row, so
    a re-run's overlap check skips it — running it twice creates no duplicates.

    Guards against false positives (which a one-shot silent migration could not afford,
    hence this is preview-then-confirm): parked at both ends (charging=0, speed<=1), the
    odometer UNCHANGED across the whole run (so regen while driving offline can't look
    like a charge), and no overlap with any existing charge window."""
    db = _conn_rw() if apply else _get()
    # See get_vehicle(): an unordered LIMIT 1 rides the UNIQUE(vin) covering index and can name
    # the wrong car — and with apply=True this INSERTS charges, so it would file reconstructed
    # sessions against the other vehicle.
    vehicle_id = _current_vehicle_id()
    if vehicle_id is None:
        return []
    rows = db.execute(
        "SELECT recorded_at, soc, charging, speed_kmh, odometer_km, latitude, longitude "
        "FROM positions WHERE vehicle_id=? AND soc IS NOT NULL ORDER BY recorded_at, id",
        (vehicle_id,)).fetchall()
    charges = db.execute(
        "SELECT started_at, ended_at FROM charges WHERE vehicle_id=?", (vehicle_id,)).fetchall()
    cap = get_battery_capacity_kwh()

    def _parked(r):
        return (r["charging"] or 0) == 0 and (r["speed_kmh"] or 0) <= 1

    def _odo_same(a, b):
        oa, ob = a["odometer_km"], b["odometer_km"]
        return oa is None or ob is None or abs(ob - oa) < 0.5

    def _overlaps(start, end):
        for c in charges:
            cs, ce = c["started_at"], (c["ended_at"] or "9999")   # NULL end = open-ended
            if start <= ce and cs <= end:                          # inclusive interval overlap
                return True
        return False

    candidates, i, n = [], 0, len(rows)
    while i < n - 1:
        a, b = rows[i], rows[i + 1]
        if not (b["soc"] - a["soc"] > 0 and _parked(a) and _parked(b) and _odo_same(a, b)):
            i += 1
            continue
        # Extend the run while SoC keeps rising, parked, and the odometer never moves —
        # so one charge seen across several stale polls becomes ONE candidate, not many.
        run_start, run_end, j = a, b, i + 1
        while j < n - 1:
            c, d = rows[j], rows[j + 1]
            if d["soc"] - c["soc"] > 0 and _parked(c) and _parked(d) and _odo_same(run_start, d):
                run_end, j = d, j + 1
            else:
                break
        rise = run_end["soc"] - run_start["soc"]
        if rise >= threshold and run_start["soc"] >= 1.0 and not _overlaps(run_start["recorded_at"], run_end["recorded_at"]):
            try:
                dur = round((datetime.fromisoformat(run_end["recorded_at"])
                             - datetime.fromisoformat(run_start["recorded_at"])).total_seconds() / 60, 1)
            except (TypeError, ValueError):
                dur = None
            # Plausibility: a spurious SoC=0/low reading makes a "charge" of impossible power (a full
            # pack in seconds). Skip runs whose implied rate exceeds any real charger; keep when the
            # duration is unknown (start_soc>=1 already filters the zero-start glitch).
            implied_kw = (rise / 100.0 * cap) / (dur / 60.0) if dur and dur > 0 else None
            if implied_kw is not None and implied_kw > _SCAN_MAX_KW:
                i = j + 1
                continue
            candidates.append({
                "started_at": run_start["recorded_at"], "ended_at": run_end["recorded_at"],
                "start_soc": run_start["soc"], "end_soc": run_end["soc"],
                "energy_kwh": round(max(rise / 100.0 * cap, 0), 3), "duration_min": dur,
                "latitude": run_end["latitude"], "longitude": run_end["longitude"],
            })
        i = j + 1

    if apply and candidates:
        for c in candidates:
            db.execute(
                """INSERT INTO charges
                   (vehicle_id, started_at, ended_at, start_soc, end_soc, energy_added_kwh,
                    duration_min, latitude, longitude, charge_type, reconstructed)
                   VALUES (?,?,?,?,?,?,?,?,?,?,1)""",
                (vehicle_id, c["started_at"], c["ended_at"], c["start_soc"], c["end_soc"],
                 c["energy_kwh"], c["duration_min"], c["latitude"], c["longitude"], "AC"))
        db.commit()
    return candidates


def _integrate_charge_energy_kwh(db, start: str, end: str | None) -> float:
    """Real DC energy delivered into the pack during a charge = ∫|V·I|dt over the
    logged samples (trapezoidal). V/I come from signals 1177/1178 in `positions`, the
    same source as the power-curve chart and the Wallbox DC comparison. This is a
    MEASURED energy, independent of SoC — so dividing it by the SoC delta gives an
    estimate of usable pack capacity that actually tracks battery ageing (unlike the
    stored energy_added_kwh, which is SoC × nominal capacity and would be circular)."""
    if end:
        # Cap at the next charge's start (same leak guard as get_charge_power_curve / compute_cost)
        # so an overlapping orphan charge can't inflate the integrated DC energy / SoH estimate.
        lo, hi, excl = _power_window_bounds(db, start, end)
        rows = db.execute(
            "SELECT recorded_at, charge_voltage_v, charge_current_a FROM positions "
            "WHERE vehicle_id = COALESCE(?, vehicle_id) AND charging = 1 AND recorded_at >= ? AND recorded_at "
            + ("<" if excl else "<=")
            + " ? ORDER BY recorded_at",
            (_current_vehicle_id(), lo, hi),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT recorded_at, charge_voltage_v, charge_current_a FROM positions "
            "WHERE vehicle_id = COALESCE(?, vehicle_id) AND charging = 1 AND recorded_at >= ? ORDER BY recorded_at",
            (_current_vehicle_id(), start),
        ).fetchall()
    energy = 0.0
    prev_t = None
    prev_p = 0.0
    for r in rows:
        try:
            t = datetime.fromisoformat(str(r["recorded_at"]).replace(" ", "T").rstrip("Z"))
        except Exception:
            continue
        p = abs((r["charge_voltage_v"] or 0) * (r["charge_current_a"] or 0)) / 1000.0
        if prev_t is not None:
            dt_h = (t - prev_t).total_seconds() / 3600.0
            # Guard against gaps (deep-sleep / pruning): ignore intervals over 15 min.
            if 0 < dt_h <= 0.25:
                energy += (p + prev_p) / 2.0 * dt_h
        prev_t, prev_p = t, p
    return energy


_AC_CHARGE_TYPES = ('AC', 'HOME', 'FREE')   # types where DC fast-rate is impossible


def _charge_has_soc_jump(db, start: str, end: str | None,
                         max_rate_per_min: float = 0.8) -> bool:
    """True if any two consecutive charging samples in the session show a SoC rise rate
    faster than max_rate_per_min %/min — a BMS recalibration snap, not real energy.
    At AC rates (≤ 22 kW on 67 kWh), the physical max is ~0.55%/min; a threshold of 0.8
    leaves margin for fast 3-phase AC while still catching BMS jumps (e.g. +2.5%/min).
    Only call this for AC charge types — DC fast-charging can legitimately reach 3-4%/min."""
    clause = "recorded_at >= ? AND recorded_at <= ?" if end else "recorded_at >= ?"
    params = (start, end) if end else (start,)
    rows = db.execute(
        f"SELECT recorded_at, soc FROM positions WHERE vehicle_id = COALESCE(?, vehicle_id) AND {clause} "
        "AND charging = 1 "
        "AND soc IS NOT NULL ORDER BY recorded_at",
        (_current_vehicle_id(), *params),
    ).fetchall()
    prev_soc, prev_t = None, None
    for r in rows:
        soc = r["soc"]
        try:
            t = datetime.fromisoformat(str(r["recorded_at"]).replace(" ", "T").rstrip("Z"))
        except Exception:
            prev_soc, prev_t = soc, None
            continue
        if prev_soc is not None and prev_t is not None:
            dt_min = (t - prev_t).total_seconds() / 60.0
            if 0 < dt_min <= 15.0 and (soc - prev_soc) / dt_min > max_rate_per_min:
                return True
        prev_soc, prev_t = soc, t
    return False


def _charge_has_active_use(db, start: str, end: str | None) -> bool:
    """True if any position sample during the charge window had cabin HVAC running
    (climate_cooling=1 or climate_heating=1 — not just climate_on, which also fires during
    battery thermal management and is too broad). A running cabin compressor/heater is a
    reliable proxy for 'user was in the car consuming power', which distorts the energy/SoC
    ratio used for the SoH estimate."""
    clause = "recorded_at >= ? AND recorded_at <= ?" if end else "recorded_at >= ?"
    params = (start, end) if end else (start,)
    row = db.execute(
        f"SELECT 1 FROM positions WHERE vehicle_id = COALESCE(?, vehicle_id) AND {clause} "
        "AND (climate_cooling = 1 OR climate_heating = 1) LIMIT 1",
        (_current_vehicle_id(), *params),
    ).fetchone()
    return row is not None


def _charge_temp_odo(db, start: str, end: str | None):
    """Coldest battery temperature (°C) and the odometer (km) seen WHILE CHARGING in a session,
    from the positions log. The min temp is the conservative basis for the cold-charge gate; the
    odometer gives the per-distance (cycle-ageing) axis of the SoH trend."""
    if end:
        rows = db.execute(
            "SELECT battery_min_temp, odometer_km FROM positions "
            "WHERE vehicle_id = COALESCE(?, vehicle_id) AND charging = 1 "
            "AND recorded_at >= ? AND recorded_at <= ? ORDER BY recorded_at",
            (_current_vehicle_id(), start, end)).fetchall()
    else:
        rows = db.execute(
            "SELECT battery_min_temp, odometer_km FROM positions "
            "WHERE vehicle_id = COALESCE(?, vehicle_id) AND charging = 1 "
            "AND recorded_at >= ? ORDER BY recorded_at", (_current_vehicle_id(), start)).fetchall()
    temps = [r["battery_min_temp"] for r in rows if r["battery_min_temp"] is not None]
    odos = [r["odometer_km"] for r in rows if r["odometer_km"] is not None]
    return (min(temps) if temps else None), (max(odos) if odos else None)


def get_battery_health(min_soc_delta: float = 12.0, temp_min_c: float | None = None,
                       min_start_soc: float = 15.0) -> dict:
    """Estimate usable battery capacity / state-of-health over time from charge sessions. For
    each charge with a meaningful SoC rise we integrate the measured DC energy and divide by the
    SoC delta → estimated full-pack capacity.

    Three LFP-specific refinements keep the trend honest — two guard the *ends* of the SoC scale,
    where the flat LFP voltage curve makes the BMS SoC least reliable:
    - **Cold charges are shown but excluded** from the headline/trend. A cold LFP pack delivers
      less and its BMS SoC drifts, so a winter session reads low — that's temperature, not ageing.
      Charges whose min battery temp is below `temp_min_c` (Settings `soh_temp_min_c`, default 15°C)
      get `excluded: True` and don't feed the figure, but stay in `points` for the chart.
    - **Charges ending near 100% weigh most** (the *top* guard): the BMS recalibrates SoC near full,
      so their SoC delta — and therefore the estimate — is the most trustworthy.
    - **Charges STARTING below `min_start_soc` (default 15%) are shown but excluded** (the *bottom*
      guard). Near-empty, each 1% holds less energy than capacity/100, so the BMS over-reports the
      SoC rise → `energy / ΔSoC` under-estimates capacity and the point plunges as an isolated
      outlier (reported by riri19, #125). Same treatment as cold: on the chart, out of the figure.

    Single sessions are noisy, so the headline is a weighted mean over the most recent valid ones.
    Charges with no stored telemetry (pruned) are skipped entirely."""
    db = _get()
    # SoH is measured-vs-as-new, so the denominator is the ORIGINAL spec capacity, not
    # the energy-calc capacity the user may have overridden — otherwise adopting a
    # measured (already-aged) value would reset SoH to ~100% and hide the ageing.
    # battery_capacity_nominal_kwh is snapshotted the first time the user overrides.
    try:
        nominal = float(get_setting("battery_capacity_nominal_kwh", "") or get_battery_capacity_kwh())
    except (TypeError, ValueError):
        nominal = get_battery_capacity_kwh()
    if temp_min_c is None:
        try:
            temp_min_c = float(get_setting("soh_temp_min_c", "15") or 15)
        except (TypeError, ValueError):
            temp_min_c = 15.0
    rows = db.execute(
        "SELECT id, started_at, ended_at, start_soc, end_soc, charge_type "
        "FROM charges WHERE vehicle_id = COALESCE(?, vehicle_id) AND ended_at IS NOT NULL "
        "AND start_soc IS NOT NULL "
        "AND end_soc IS NOT NULL ORDER BY started_at",
        (_current_vehicle_id(),)
    ).fetchall()
    points = []
    for r in rows:
        delta = (r["end_soc"] or 0) - (r["start_soc"] or 0)
        if delta < min_soc_delta:                      # tiny top-ups → huge relative error
            continue
        energy = _integrate_charge_energy_kwh(db, r["started_at"], r["ended_at"])
        if energy <= 0.1:                              # no usable telemetry (pruned / AC-only meter)
            continue
        est = energy / (delta / 100.0)
        # Drop physically implausible estimates (sampling gaps, bad V/I spikes).
        if not (nominal * 0.5 <= est <= nominal * 1.15):
            continue
        temp, odo = _charge_temp_odo(db, r["started_at"], r["ended_at"])
        cold = temp is not None and temp < temp_min_c
        # Bottom guard: a charge that STARTED near-empty over-reports its SoC rise → capacity
        # under-estimate (the isolated low outlier riri19 saw). Excluded like cold; still charted.
        low_start = r["start_soc"] is not None and r["start_soc"] < min_start_soc
        soc_jump = (not cold and r["charge_type"] in _AC_CHARGE_TYPES
                    and _charge_has_soc_jump(db, r["started_at"], r["ended_at"]))
        active_use = (not cold and not soc_jump
                      and _charge_has_active_use(db, r["started_at"], r["ended_at"]))
        excluded = cold or soc_jump or active_use or low_start
        exclude_reason = ("cold" if cold else "soc_jump" if soc_jump
                          else "active_use" if active_use else "low_start" if low_start else None)
        dt = _local_dt(r["started_at"])
        points.append({
            "charge_id": r["id"],
            "date": dt.strftime("%Y-%m-%d") if dt else (r["started_at"] or "")[:10],
            "ts": dt.isoformat() if dt else r["started_at"],
            "capacity_kwh": round(est, 1),
            "soh_pct": round(est / nominal * 100, 1) if nominal else None,
            "soc_delta": round(delta, 1),
            "end_soc": round(r["end_soc"], 1) if r["end_soc"] is not None else None,
            "energy_kwh": round(energy, 2),
            "temp_c": round(temp, 1) if temp is not None else None,
            "odometer_km": round(odo) if odo is not None else None,
            "charge_type": r["charge_type"],
            "excluded": excluded,
            "exclude_reason": exclude_reason,
        })
    valid = [p for p in points if not p["excluded"]]

    # Weight a session by how close it ended to a full (BMS-recalibrated) 100% — that's where the
    # LFP SoC is trustworthy, so its SoC delta (and the estimate) carries the least error.
    def _w(p):
        es = p.get("end_soc")
        return 1.0 if es is None else max(0.25, min(1.0, (es - 50.0) / 50.0))

    tail = valid[-5:]                                  # weighted mean of the recent valid estimates
    if tail:
        wsum = sum(_w(p) for p in tail)
        latest_cap = round(sum(p["capacity_kwh"] * _w(p) for p in tail) / wsum, 1)
        latest_soh = round(latest_cap / nominal * 100, 1) if nominal else None
    else:
        latest_cap = latest_soh = None
    return {
        "nominal_kwh": round(nominal, 1),
        "points": points,
        "sample_count": len(valid),
        "excluded_count": len(points) - len(valid),
        "cold_count": sum(1 for p in points if p.get("exclude_reason") == "cold"),
        "active_use_count": sum(1 for p in points if p.get("exclude_reason") == "active_use"),
        "soc_jump_count": sum(1 for p in points if p.get("exclude_reason") == "soc_jump"),
        "low_start_count": sum(1 for p in points if p.get("exclude_reason") == "low_start"),
        "temp_min_c": round(temp_min_c, 1),
        "min_start_soc": round(min_start_soc, 1),
        "latest_capacity_kwh": latest_cap,
        "latest_soh_pct": latest_soh,
    }


# SoC arrives as preciseSoc (signal 100003) with 0.1% resolution, and a ±0.1% parked BMS
# jitter is real (both up- and down-ticks observed while parked, odometer flat). Worst case
# each window endpoint is one quantum off, so a window's drop carries up to ±0.2% of pure
# measurement error — which the %/day extrapolation multiplies by 24/hours (#41).
SOC_QUANTUM = 0.1
_DROP_ERR = 2 * SOC_QUANTUM
# The intrinsic noise floor: a parked drop below 2 sensor quanta is jitter, not drain. The user's
# `vampire_min_drop_pct` is a DISPLAY threshold layered on top — raising it thins the charted bars,
# but it must never make a car that DOES lose charge look like it has no parked data at all (#63).
# So we always collect windows down to this floor and tag which ones clear the user's threshold,
# letting the page tell "no parked data yet" apart from "data exists, just below your threshold".
_VAMPIRE_NOISE_FLOOR = 0.2


_VAMPIRE_ACTIVE_USE_RATE = 15.0  # %/day above this is active use (A/C, meeting, etc.), not standby


def get_vampire_drain(min_hours: float = 1.0, min_drop_pct: float = 0.2,
                      lookback_days: int = 90, limit: int = 60) -> dict:
    """Vampire drain = SoC lost while the car is OFF (Ready/ON3 = 0) and NOT charging — measured
    exactly from power-OFF to the next power-ON (precise, via positions.ready; falls back to the old
    speed<1 "parked" test only for trips logged before the ready signal existed). This INCLUDES
    off-state remote heating/cooling (it ran while the car was off) and EXCLUDES on-state idle
    (Ready+P with climate, which belongs to the driving session). Scans the per-poll
    `positions` log, groups consecutive OFF samples (charging=0, not moving) into windows
    bounded by any charging or driving — driving is detected by speed OR a rise in odometer between
    idle samples, so a drive that happened during a reporting gap can't be mistaken for drain. Each
    kept window reports its SoC drop, a normalised %/day rate, the rate's quantization error band
    (`rate_err`) and whether the rate is trustworthy (`reliable`: a drop of at least 4 quanta AND
    an error band within ±1 %/day — short windows extrapolate a single sensor step into several
    %/day, see #41). Windows shorter than `min_hours` or with a drop below `min_drop_pct` (sensor
    jitter) are not charted, but every park >= `min_hours` — zero-drop ones included — feeds the
    time-weighted `typical_pct_per_day` headline. Pure read over data Mate already records every
    poll — no extra polling, no user input."""
    db = _get()
    # Collect down to the intrinsic noise floor regardless of the user's display threshold, so a
    # raised `min_drop_pct` thins the chart without hiding that drain exists at all (#63).
    floor = min(min_drop_pct, _VAMPIRE_NOISE_FLOOR)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    rows = db.execute(
        "SELECT recorded_at, soc, charging, speed_kmh, odometer_km, ac_port_mode, ready FROM positions "
        "WHERE vehicle_id = COALESCE(?, vehicle_id) AND soc IS NOT NULL AND recorded_at >= ? ORDER BY recorded_at",
        (_current_vehicle_id(), cutoff),
    ).fetchall()

    windows = []
    agg = {"drop": 0.0, "hours": 0.0}

    def _flush(w, ongoing=False, close=None):
        if not w:
            return
        soc_end, t_end = w["soc_last"], w["t_last"]
        # The park ended at a wake into driving/charging: the first fresh reading reveals the SoC
        # that actually drained DURING deep sleep — while asleep the car stops reporting and the
        # cloud serves a FROZEN SoC, so the parked samples sit flat and a slow loss is invisible
        # until wake (and is otherwise lost if the car is driven right away: the parked window
        # closes at the frozen value and the drop falls in the gap before the trip's start SoC).
        # Close the window at that fresh value + time so the drain is captured — but only when it's
        # a DROP (a rise = BMS recalibration / charge → keep the parked value, never invent drain).
        if close is not None and close["soc"] is not None and close["soc"] < (soc_end or 0):
            soc_end, t_end = close["soc"], close["recorded_at"]
        t0, t1 = _local_dt(w["t0"]), _local_dt(t_end)
        if t0 is None or t1 is None:
            return
        hours = (t1 - t0).total_seconds() / 3600.0
        drop = (w["soc0"] or 0) - (soc_end or 0)
        pct_per_day = drop / hours * 24 if hours else 0
        # OFF-state high-rate windows are flagged (amber) as likely remote heating/cooling, but — unlike
        # the old speed-based logic — they are NOT excluded: drain while the car is OFF is OFF drain by
        # the Ready-OFF→Ready-ON definition (the in-card note says off-climate is included).
        active_use = pct_per_day > _VAMPIRE_ACTIVE_USE_RATE
        if hours >= min_hours:
            # Headline aggregate: every OFF stretch long enough to measure counts, including zero-drop
            # ones (a "drain happened"-only sample reads high — selection bias). SoC up-ticks are BMS
            # jitter → clamp to 0.
            agg["hours"] += hours
            agg["drop"] += max(drop, 0.0)
        # Compare the rounded drop: raw float drops sit a hair off the threshold
        # (56.8 − 56.4 = 0.3999…), so identical physical drops would randomly pass/fail.
        drop_r = round(drop, 1)
        if hours >= min_hours and drop_r >= floor - 1e-9:
            err = _DROP_ERR / hours * 24
            windows.append({
                "start": t0.isoformat(), "end": t1.isoformat(),
                "hours": round(hours, 1),
                "soc_start": round(w["soc0"], 1), "soc_end": round(soc_end, 1),
                "drop_pct": drop_r,
                "pct_per_day": round(pct_per_day, 1),
                "rate_err": round(err, 1),
                # Two INDEPENDENT reasons an estimate can be untrustworthy, and the chart used to
                # blame the wrong one. #160: a 45.9-hour park with a 0.2% drop was labelled "short
                # stop" — the duration test had passed with ten times the margin (err 0.10 against
                # a limit of 1.0); what failed was the drop, two sensor steps where four are
                # needed. Saying "short" about a two-day park is simply false, and it sends the
                # user looking for a problem in the wrong place.
                "reliable": drop_r >= 2 * _DROP_ERR - 1e-9 and err <= 1.0,
                # Which test failed, so the label can say so. 'rate' = the window is too short and
                # a single sensor step extrapolates into several %/day; 'drop' = the window is long
                # enough but the battery barely moved, so drain cannot be told from rounding.
                "low_conf": (None if (drop_r >= 2 * _DROP_ERR - 1e-9 and err <= 1.0)
                             else "rate" if err > 1.0 else "drop"),
                "ongoing": ongoing,
                "active_use": active_use,
                # Clears the user's display threshold → charted as a bar; otherwise it's a real
                # parked window kept only to power the "below your threshold" hint + headline.
                "_charted": drop_r >= min_drop_pct - 1e-9,
            })

    cur = None
    for r in rows:
        # A V2L / bidirectional-discharge sample (ac_port_mode==2) is NOT standby: the car is parked
        # but actively powering an external load, so that SoC loss is V2L output, not vampire drain.
        # Treat it like charging — it BOUNDS the parked window and its drop is never read as drain.
        v2l = r["ac_port_mode"] == 2
        # OFF window = car powered down (Ready/ON3 = 0), not charging, not V2L. Falls back to the old
        # speed<1 test only when the ready signal is absent (trips before it was logged). The drain now
        # spans exactly Ready-OFF → next Ready-ON: on-state idle (Ready+P with climate) is NOT counted,
        # while OFF-state remote heating/cooling IS (per the in-card note).
        rd = r["ready"]
        idle = (not r["charging"]) and (not v2l) and (rd == 0 if rd is not None else (r["speed_kmh"] or 0) < 1)
        odo = r["odometer_km"]
        # a rise in odometer since the window's last idle sample → a drive happened (even if its
        # samples were missed) → the park ended there.
        if (cur is not None and odo is not None and cur["odo_last"] is not None
                and odo - cur["odo_last"] > 0.5):
            _flush(cur)
            cur = None
        if not idle:                        # driving / charging / V2L now → park ended
            # Close at the wake's fresh SoC only on a DRIVING transition (the odometer-rise guard
            # above already split off any drive that happened in a gap, so a same-odometer drive
            # sample here is a genuine wake-after-park → its SoC is real standby drain). A CHARGING
            # or V2L transition is left as-is: the pre-charge gap is ambiguous (could be a drive to
            # the charger), and a V2L drop is bidirectional-discharge output (not standby) — so we
            # never infer drain from either.
            _flush(cur, close=(None if (r["charging"] or v2l) else r))
            cur = None
            continue
        if cur is None:                     # start a new parked window
            cur = {"t0": r["recorded_at"], "soc0": r["soc"],
                   "t_last": r["recorded_at"], "soc_last": r["soc"], "odo_last": odo}
        else:                               # extend the current parked window
            cur["t_last"] = r["recorded_at"]
            cur["soc_last"] = r["soc"]
            if odo is not None:
                cur["odo_last"] = odo
    _flush(cur, ongoing=True)               # the trailing park is still open

    windows = windows[-limit:]
    # Split the kept (>= noise floor) windows into the ones charted at the user's display
    # threshold and the rest. `measurable` = real parked drain that exists regardless of the
    # slider; `below_threshold` powers the "data exists, just below your X% threshold" hint so a
    # raised slider never reads as "no parked data at all" (#63).
    charted = [w for w in windows if w.pop("_charted")]
    measurable = len(windows)
    active_use_count = sum(1 for w in charted if w.get("active_use"))
    # Time-weighted typical (total SoC lost / total parked time): quantization noise cancels
    # across windows instead of every short park voting like a long one, and slow drain below
    # the per-window display threshold still surfaces. Gated on `measurable` (not the charted
    # count) so the headline survives a raised display threshold; None while nothing clears the
    # noise floor, so young installs keep the no-data state.
    typical = round(agg["drop"] / agg["hours"] * 24, 1) if measurable and agg["hours"] else None
    return {"windows": charted, "count": len(charted),
            "measurable_count": measurable, "below_threshold": measurable - len(charted),
            "active_use_count": active_use_count,
            "min_drop_pct": round(min_drop_pct, 1),
            "typical_pct_per_day": typical, "lookback_days": lookback_days}


# ── V2L (vehicle-to-load) discharge sessions ───────────────────────────────────
# Reconstructed ON-READ from the per-poll `positions` log (ac_port_mode + battery current/voltage)
# — same "pure read, no extra table" approach as get_vampire_drain. A session = a run of samples
# with ac_port_mode==2 (V2L mode active, signal 47). Reported power is NET of the idle baseline
# captured just before the session, so the car's own awake overhead (~300 W) is not attributed to
# the external load. Battery current (charge_current_a / signal 1178) is SIGNED: positive = discharge.

def get_v2l_sessions(lookback_days: int = 90, limit: int = 50, vehicle_id: int | None = None) -> dict:
    db = _get()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    if vehicle_id is not None:   # use idx_positions_vehicle(vehicle_id, recorded_at) → fast range scan
        rows = db.execute(
            "SELECT recorded_at, soc, charge_current_a, charge_voltage_v, ac_port_mode FROM positions "
            "WHERE vehicle_id = ? AND recorded_at >= ? ORDER BY recorded_at", (vehicle_id, cutoff),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT recorded_at, soc, charge_current_a, charge_voltage_v, ac_port_mode FROM positions "
            "WHERE vehicle_id = COALESCE(?, vehicle_id) AND recorded_at >= ? ORDER BY recorded_at",
            (_current_vehicle_id(), cutoff),
        ).fetchall()

    def _close(c, ongoing=False):
        s = c["samples"]
        # Integrate net power over time (left-rectangle per gap). Gaps outside (0, 1h] are skipped so
        # a sleep/offline hole between two V2L samples can never invent energy.
        energy_wh, peak_w = 0.0, 0.0
        for k in range(len(s)):
            peak_w = max(peak_w, s[k][1])
            if k:
                dt_h = (s[k][0] - s[k - 1][0]).total_seconds() / 3600.0
                if 0 < dt_h <= 1.0:
                    energy_wh += s[k - 1][1] * dt_h
        soc_used = round((c["soc0"] or 0) - (c["soc_last"] or 0), 1)
        return {
            "start": c["t0"].isoformat(), "end": c["t_last"].isoformat(),
            "duration_min": round((c["t_last"] - c["t0"]).total_seconds() / 60.0, 1),
            "energy_wh": round(energy_wh, 1),
            "peak_w": round(peak_w),
            "current_w": round(s[-1][1]) if s else 0,    # latest sample's net power (instantaneous)
            "baseline_w": round(c["i0"] * (c["v_ref"] or 0.0)),
            "soc_used_pct": soc_used if soc_used > 0 else 0.0,
            "ongoing": ongoing,
        }

    sessions, cur, baseline_a = [], None, 0.0   # baseline_a = last non-V2L (awake idle) discharge current
    for r in rows:
        mode = r["ac_port_mode"]
        if mode is None:
            continue   # web-side live writes can leave ac_port_mode NULL — skip so they neither SPLIT a
                       # session (NULL != 2 would close it) NOR corrupt baseline_a with their own current
        i = float(r["charge_current_a"] or 0.0)
        v = float(r["charge_voltage_v"] or 0.0)
        if mode != 2:                            # not in V2L → close any open session, refresh baseline
            if cur is not None:
                sessions.append(_close(cur)); cur = None
            if i > 0:                            # positive = discharge → the awake idle overhead (I0)
                baseline_a = i
            continue
        t = _local_dt(r["recorded_at"])
        if t is None:
            continue
        if cur is None:                          # V2L just started → open a session, freeze its baseline
            cur = {"t0": t, "t_last": t, "i0": max(0.0, baseline_a), "v_ref": v,
                   "soc0": r["soc"], "soc_last": r["soc"], "samples": []}
        cur["samples"].append((t, max(0.0, i - cur["i0"]) * v))    # NET power, clamped at 0
        cur["t_last"], cur["soc_last"] = t, r["soc"]

    if cur is not None:
        sessions.append(_close(cur, ongoing=True))

    sessions = sessions[-limit:]
    return {"sessions": sessions, "count": len(sessions),
            "total_energy_wh": round(sum(s["energy_wh"] for s in sessions), 1),
            "lookback_days": lookback_days}


def get_v2l_status(lookback_days: int = 7) -> dict:
    """Compact V2L summary for the Overview card — ALWAYS shown (we don't gate on model; the data
    decides). Idle until a V2L session appears, then live net power. `ever_used` separates
    idle-with-history from never-used; `power_max_w` (3500 W) scales the UI bar. Vehicle-scoped + a
    short lookback so the Overview's 10 s htmx auto-refresh stays a cheap indexed range scan."""
    try:
        veh, _ = get_vehicle()
        vehicle_id = veh.get("id") if veh else None
    except Exception:  # noqa: BLE001
        vehicle_id = None
    recent = get_v2l_sessions(lookback_days=lookback_days, limit=1, vehicle_id=vehicle_id)["sessions"]
    last = recent[-1] if recent else None
    active = bool(last and last.get("ongoing"))
    dur_min = int(round(last["duration_min"])) if last else 0
    return {
        "has_data": True,                          # always visible — never hide a feature on a guess
        "ever_used": last is not None,
        "active": active,
        "power_w": last["current_w"] if active else 0,
        "energy_wh": last["energy_wh"] if last else 0.0,
        "peak_w": last["peak_w"] if last else 0,
        "end": last["end"] if last else None,
        "duration": f"{dur_min // 60:02d}:{dur_min % 60:02d}",   # session length, hh:mm
        "power_max_w": 3500,
    }


def get_v2l_total_kwh() -> float:
    """All-time total energy DRAWN via V2L (sum of every reconstructed session), in kWh — for the
    Statistics 'total summary' card. Reconstructed from the positions log (no table), vehicle-scoped."""
    try:
        veh, _ = get_vehicle()
        vid = veh.get("id") if veh else None
    except Exception:  # noqa: BLE001
        vid = None
    wh = get_v2l_sessions(lookback_days=36500, limit=1_000_000, vehicle_id=vid)["total_energy_wh"]
    return round((wh or 0) / 1000.0, 2)


# ── Global map (all tracks + frequent places) ──────────────────────────────────

def _rows_to_segments(rows, max_points: int) -> list[list[list[float]]]:
    """Group ordered (trip_id, lat, lon) rows into one polyline per trip (never joined across
    trips), then proportionally downsample to ~max_points total while keeping each trip's real
    first/last point. Shared by the global map (get_all_track) and the report's month map."""
    segments: list[list[list[float]]] = []
    cur_id, cur = None, []
    for r in rows:
        if r["trip_id"] != cur_id:
            if len(cur) >= 2:
                segments.append(cur)
            cur, cur_id = [], r["trip_id"]
        cur.append([round(r["latitude"], 5), round(r["longitude"], 5)])
    if len(cur) >= 2:
        segments.append(cur)

    total = sum(len(s) for s in segments)
    if total <= max_points or total == 0:
        return segments
    # Proportional per-trip downsample, keeping each segment's real endpoints.
    step = total / max_points
    out = []
    for s in segments:
        keep = max(2, int(len(s) / step))
        if keep >= len(s):
            out.append(s)
            continue
        st = len(s) / keep
        ds = [s[int(i * st)] for i in range(keep)]
        ds[-1] = s[-1]
        out.append(ds)
    return out


def get_all_track(max_points: int = 12000) -> list[list[list[float]]]:
    """Every trip's GPS track as a list of polylines (one [lat, lon] list per trip),
    so the global map draws the actual driven roads as connected lines instead of
    loose dots. Points are NEVER joined across trips. Downsampled to roughly
    ``max_points`` total while always keeping each trip's first and last point, so the
    lines stay continuous even when zoomed in."""
    db = _get()
    rows = db.execute(
        "SELECT trip_id, latitude, longitude FROM trip_positions "
        "WHERE trip_id IN (SELECT id FROM trips WHERE vehicle_id = COALESCE(?, vehicle_id)) "
        "AND latitude IS NOT NULL AND longitude IS NOT NULL ORDER BY trip_id, id",
        (_current_vehicle_id(),)
    ).fetchall()
    return _rows_to_segments(rows, max_points)


def get_month_track(month: str, max_points: int = 8000) -> list[list[list[float]]]:
    """GPS polylines for every trip STARTED in the given local 'YYYY-MM' — the report's month
    map. Same shape/downsampling as get_all_track, scoped to one month's trips (parent and
    merged-child trips alike, so every road driven that month is drawn)."""
    if not month:
        return []
    db = _get()
    ids = []
    for r in db.execute("SELECT id, started_at FROM trips WHERE vehicle_id = COALESCE(?, vehicle_id) "
                        "AND started_at IS NOT NULL", (_current_vehicle_id(),)).fetchall():
        dt = _local_dt(r["started_at"])
        if dt is not None and dt.strftime("%Y-%m") == month:
            ids.append(r["id"])
    if not ids:
        return []
    ph = ",".join("?" * len(ids))
    rows = db.execute(
        "SELECT trip_id, latitude, longitude FROM trip_positions "
        f"WHERE trip_id IN ({ph}) AND latitude IS NOT NULL AND longitude IS NOT NULL "
        "ORDER BY trip_id, id", ids
    ).fetchall()
    return _rows_to_segments(rows, max_points)


def get_frequent_places(min_visits: int = 2, top_n: int = 15) -> list[dict]:
    """Cluster trip start/end points into recurring places (Home, Work, …) by snapping
    coordinates to a ~110 m grid (3 decimals) and counting visits. Returns the busiest
    clusters with an averaged centre and a visit count — no reverse geocoding, so it
    stays offline and cheap."""
    db = _get()
    rows = db.execute(
        "SELECT start_lat, start_lon, end_lat, end_lon FROM trips "
        "WHERE vehicle_id = COALESCE(?, vehicle_id)",
        (_current_vehicle_id(),)
    ).fetchall()
    buckets: dict[tuple, dict] = {}
    for r in rows:
        for lat, lon in ((r["start_lat"], r["start_lon"]), (r["end_lat"], r["end_lon"])):
            if lat is None or lon is None:
                continue
            key = (round(lat, 3), round(lon, 3))
            b = buckets.setdefault(key, {"lat": 0.0, "lon": 0.0, "visits": 0})
            b["lat"] += lat
            b["lon"] += lon
            b["visits"] += 1
    places = [
        {"latitude": round(b["lat"] / b["visits"], 6),
         "longitude": round(b["lon"] / b["visits"], 6),
         "visits": b["visits"]}
        for b in buckets.values() if b["visits"] >= min_visits
    ]
    places.sort(key=lambda p: p["visits"], reverse=True)
    return places[:top_n]


# Mirrors poller/db.py's _WB_HOME_* constants/_learned_wallbox_location: location_type == "HOME"
# is only ever set by the user or by the (off-by-default) wallbox_auto_home setting, so on a
# typical install it's NULL on every home charge and can't be relied on to exclude them here.
# The wallbox's own learned position — median of the charges where it measured real energy,
# same signal v2.8.1 uses to gate wallbox-counter attribution — is true on a default install.
_WB_HOME_RADIUS_KM = 1.0
_WB_HOME_MIN_KWH = 2.0
_WB_HOME_MIN_SAMPLES = 2


def _learned_wallbox_location(vehicle_id):
    """Median lat/lon of charges where the wallbox measured > _WB_HOME_MIN_KWH (rules out standby
    creep). None until _WB_HOME_MIN_SAMPLES such charges exist — a fresh install has no signal yet."""
    db = _get()
    rows = db.execute(
        "SELECT latitude, longitude FROM charges "
        "WHERE vehicle_id = COALESCE(?, vehicle_id) AND latitude IS NOT NULL AND longitude IS NOT NULL "
        "AND COALESCE(ac_energy_kwh, 0) > ?",
        (vehicle_id, _WB_HOME_MIN_KWH)).fetchall()
    if len(rows) < _WB_HOME_MIN_SAMPLES:
        return None
    lats = sorted(r["latitude"] for r in rows)
    lons = sorted(r["longitude"] for r in rows)
    return lats[len(lats) // 2], lons[len(lons) // 2]


def _is_home_charge(c: dict, home) -> bool:
    """True for a charge that belongs to the home-wallbox bubble, not the station map: an explicit
    HOME tag (works whenever it's set), or — the default-install case — a charge within
    _WB_HOME_RADIUS_KM of the learned wallbox location."""
    if c.get("location_type") == "HOME":
        return True
    if home is None:
        return False
    lat, lon = c.get("latitude"), c.get("longitude")
    if lat is None or lon is None:
        return False
    return _haversine_km(lat, lon, home[0], home[1]) <= _WB_HOME_RADIUS_KM


def get_charging_stations(min_sessions: int = 1, top_n: Optional[int] = 15, recent_n: int = 6) -> list[dict]:
    """Cluster completed charges into physical charging stations for the map's concentration
    bubbles — same ~110 m grid (3-decimal rounding) as get_frequent_places, so a station
    resolves to one bubble even though each session's own GPS fix jitters slightly. Each
    cluster carries its most-common resolved name (set by charger_locator's OSM/OCM sweep)
    and its most recent sessions (for the map popup). `key` is "lat,lon" rounded to the SAME
    3 decimals used to bucket, so /charges?station=<key> re-selects the identical cluster.
    top_n mirrors get_frequent_places (15) so a driver with many charge spots doesn't get a
    marker — and a JSON blob — per stop. min_sessions does NOT: a place visited once isn't a
    "frequent place", but a station used once IS the interesting datum here (the charger you
    stopped at on a trip), and dropping singletons would leave a driver who charged at six
    different chargers on one holiday looking at an empty map. top_n alone bounds the payload;
    ties on `sessions` keep get_charges' recency order, so the cap takes the newest one-offs.

    Note: the 3-decimal grid, inherited from get_frequent_places, can round two GPS fixes
    ~2 m apart into different buckets and split one physical station into two markers with
    split totals — a real (not just cosmetic) split here, since totals are billed amounts.
    Deferred: a true proximity merge is more work than this fix warrants.

    HOME charges are excluded — a home wallbox isn't a "colonnina" and, being by far the most
    visited spot for most drivers, would otherwise dominate the concentration map as one giant
    unnamed bubble (HOME charges never resolve a location_name — see _LOCATION_CANDIDATES_WHERE).
    Home charging already has its own bubble via get_frequent_places."""
    charges = get_charges(limit=1_000_000)
    home = _learned_wallbox_location(_current_vehicle_id())
    buckets: dict[tuple, dict] = {}
    for c in charges:
        lat, lon = c.get("latitude"), c.get("longitude")
        if not lat or not lon or _is_home_charge(c, home):
            continue
        key = (round(lat, 3), round(lon, 3))
        b = buckets.setdefault(key, {"lat": 0.0, "lon": 0.0, "n": 0, "kwh": 0.0,
                                      "cost": 0.0, "has_cost": False, "names": {}, "charges": []})
        b["lat"] += lat
        b["lon"] += lon
        b["n"] += 1
        b["kwh"] += _billed_kwh(c)
        if c.get("cost") is not None:
            b["cost"] += c["cost"]
            b["has_cost"] = True
        if c.get("location_name"):
            b["names"][c["location_name"]] = b["names"].get(c["location_name"], 0) + 1
        b["charges"].append(c)

    stations = []
    for (lat_r, lon_r), b in buckets.items():
        if b["n"] < min_sessions:
            continue
        b["charges"].sort(key=lambda c: c.get("started_at") or "", reverse=True)
        stations.append({
            "key": f"{lat_r:.3f},{lon_r:.3f}",
            "latitude": round(b["lat"] / b["n"], 6),
            "longitude": round(b["lon"] / b["n"], 6),
            "name": max(b["names"], key=b["names"].get) if b["names"] else None,
            "sessions": b["n"],
            "kwh": round(b["kwh"], 2),
            "cost": round(b["cost"], 2) if b["has_cost"] else None,
            "recent": [
                {"id": c["id"], "started_at": c["started_at"], "kwh": round(_billed_kwh(c), 2),
                 "cost": c.get("cost"), "charge_type": c.get("charge_type")}
                for c in b["charges"][:recent_n]
            ],
        })
    stations.sort(key=lambda s: s["sessions"], reverse=True)
    return stations if top_n is None else stations[:top_n]
