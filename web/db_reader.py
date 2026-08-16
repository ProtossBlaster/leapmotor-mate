"""Read-only DB queries for the web layer."""
import json
import math
import sqlite3
import statistics
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


TZ_PINNED_KEY = "timezone_pinned_v1"     # one-shot: Auto turned into an explicit, recorded zone


def detected_tz_name() -> str:
    """The container's zone as an IANA NAME — what "Automatic" silently resolves to. 'UTC' when
    there is nothing to read, which is what a bare Docker container actually runs on."""
    try:
        known = available_timezones()
    except Exception:  # noqa: BLE001
        known = set()
    env = (os.environ.get("TZ") or "").strip()
    if env and (not known or env in known):
        return env
    try:                                    # Debian/Alpine write the name here
        name = Path("/etc/timezone").read_text(encoding="utf-8").strip()
        if name and (not known or name in known):
            return name
    except Exception:  # noqa: BLE001
        pass
    try:                                    # …otherwise /etc/localtime points into the tz database
        p = Path("/etc/localtime").resolve()
        parts = p.parts
        if "zoneinfo" in parts:
            name = "/".join(parts[parts.index("zoneinfo") + 1:])
            if name and (not known or name in known):
                return name
    except Exception:  # noqa: BLE001
        pass
    return "UTC"


def pin_auto_timezone() -> str:
    """One-shot: turn "Automatic" into the zone it was already resolving to, and RECORD it.

    Auto was never wrong so much as UNRECORDED. `_local_tz` fell back to the container's zone while
    the setting stayed '', so a charge you typed or imported was anchored to a clock nobody had
    named — and `repair_manual_charge_timezones` (rightly) refuses to run without a chosen zone, so
    it could never put those rows back either. If the container ran on neither UTC nor your real
    zone, the offset was baked in and unrecoverable. Writing the resolved name down closes that:
    every write from here on anchors to a zone that is known, and can be re-anchored if it changes.

    Nothing moves and nobody's times change — the zone stored is exactly the one already in use.
    What DOES change is that the setting stops following the container: on Home Assistant, altering
    HA's zone no longer silently re-interprets what you typed. That is the point, and it is in the
    CHANGELOG. Runs once, guarded by a flag, and never overrides an explicit choice."""
    if get_setting("timezone", "").strip() or get_setting(TZ_PINNED_KEY, "") == "1":
        set_setting(TZ_PINNED_KEY, "1")
        return ""
    name = detected_tz_name()
    set_timezone(name)
    set_setting(TZ_PINNED_KEY, "1")
    return name


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


# AC, DC and HPC are acronyms and mean the same in every language Mate speaks — those stay.
# ⚠️ The comment that used to sit here said ALL of these were "intentionally language-neutral", and
# that was the wrong half of the rule: **Home, FREE and Manual are ordinary English words**. On a
# Polish interface the charge badge read "Home" while the monthly report, using its own key, read
# "Dom" — the app contradicting itself on one screen (@konrad300, #210).
#
# 🔑 Two of the three words already existed, translated by native speakers, so they are REUSED:
# `report_home` is the very key whose "Dom" exposed the mismatch, and `charge_free` was already
# there. Only "Manual" had to be added.
_CHARGE_TYPE_LABEL_KEY = {"HOME": "report_home", "FREE": "charge_free", "MANUAL": "charge_manual"}
CHARGE_TYPES = {
    "HOME": {"label": "Home", "icon": "🏠", "color": "#22c55e"},
    "AC":   {"label": "AC",   "icon": "🔌", "color": "#60a5fa"},
    "FAST": {"label": "DC",   "icon": "⚡", "color": "#fb923c"},
    "HPC":  {"label": "HPC",  "icon": "🚀", "color": "#e879f9"},
    "FREE": {"label": "FREE", "icon": "🆓", "color": "#a3e635"},
    "MANUAL": {"label": "Manual", "icon": "✎", "color": "#94a3b8"},
}

def charge_types_localised() -> dict:
    """CHARGE_TYPES with the three English WORDS in the reader's language (#210).

    Translated HERE, at the single source, rather than in the nine routes that inject
    `charge_types` into a template: a fix applied per-route is a fix one route forgets.

    🔴 Returns a COPY. Writing the label into the module dict would translate it once, for whoever
    loaded a page first, and leave it that way for every other language on the same process.
    """
    from i18n import get_t
    t = get_t(get_language())
    out = {}
    for code, meta in CHARGE_TYPES.items():
        key = _CHARGE_TYPE_LABEL_KEY.get(code)
        label = t(key) if key else meta["label"]
        out[code] = {**meta, "label": label or meta["label"]}
    return out


PRICE_KEYS = {
    "HOME": "price_home_kwh",
    "AC":   "price_ac_kwh",
    "FAST": "price_fast_kwh",
    "HPC":  "price_hpc_kwh",
}

# REEV Phase C — the minimum fuel-% drop over a trip that counts as the engine having run (the 3235
# signal steps at 0.1% ≈ 50 mL; 0.2% guards the single-tick noise). A real range-extender drive drops
# several %.
_REEV_FUEL_MIN_DROP = 0.2   # % — the floor for signal 3235, whose own step is 0.1 %
# …and the floor for signal 3263, which counts in millilitres. 5 mL is a hundredth of the smallest
# step the percentage can express, and well under any real generator burn: it exists only so a
# 1 mL wobble in the counter does not become a "trip that used fuel".
_REEV_FUEL_MIN_L = 0.005

# The WHERE clause for "this trip may have burned fuel" — EITHER signal is enough. It exists because
# v3.6.6 fixed the litres in `_reev_trip_fuel` and left this filter behind in two aggregates, which
# dropped the row before the fine signal could be read: @michapr's all-time total stayed at 5.9 L
# against 9.64 measured off his own car, on the very release that was about this. The floors belong
# to the reader, which knows which signal it is looking at; a query's job is only to avoid loading
# the trips that cannot possibly qualify.
_REEV_FUEL_ANY_DROP_SQL = (
    "((fuel_start_l IS NOT NULL AND fuel_end_l IS NOT NULL AND fuel_start_l > fuel_end_l)"
    " OR (fuel_start_pct IS NOT NULL AND fuel_end_pct IS NOT NULL"
    "     AND fuel_start_pct - fuel_end_pct > ?))")

# Tank size, per model — the FALLBACK for turning a percentage into litres. Prefer the car's own
# litre count (signal 3263, positions.fuel_liters / trips.fuel_start_l|fuel_end_l): where that is
# present nothing here is used at all.
#
# It used to be one number, 50 L, "C10/B10 REEV both 50 L, confirmed" — confirmed from spec sheets,
# never measured. Signal 3263 measures it, and the two models differ: dividing 3263 by 3235 across
# seven bundles from three owners gives 47.5 L on a C10 and 50.0 L on a B10, each constant to
# ±0.05 L. So every litre Mate ever showed a C10 owner was 5.3 % too big. Decoded by @gm27271
# (beta #10).
#
# ⚠️ This is what the CAR calls a full tank, not what the tank holds. 3263 is the percentage scaled
# by this constant, so the two stop together — 100.0 % and 47 500 mL in the same frame, and never a
# millilitre more across 22 459 readings. @pdifeo's C10 took 10.51 L off the pump into 9.204 L of
# nominal room (beta #21, 03/08/26), so the physical tank holds at least 48.81 L. A fill that tops
# the gauge out is therefore a LOWER bound, and `_fill_is_capped` is what says so.
_REEV_TANK_L_BY_MODEL = {"C10": 47.5, "B10": 50.0}
_REEV_TANK_L = 50.0        # last resort: an unknown range-extender model


def reev_tank_l(car_type: Optional[str] = None) -> float:
    """Assumed tank litres for `car_type` (default: the current vehicle's). Only ever reached when
    the car has not reported its own litres."""
    if car_type is None:
        try:
            v, _ = get_vehicle()
            car_type = (v or {}).get("car_type") or ""
        except Exception:      # noqa: BLE001 — a fallback must never be the thing that raises
            car_type = ""
    return _REEV_TANK_L_BY_MODEL.get((car_type or "").strip().upper()[:3], _REEV_TANK_L)

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
    caller can fall back to the whole-trip distance.

    🔑 **Which fuel column decides is a question of UNITS, and it changes the answer by a third.**
    The test is per-sample — odometer up AND fuel down in the SAME row — so the resolution of the
    fuel signal sets how many rows survive. The car reports its tank twice: 3235 as a percentage
    moving in steps of 0.1, and 3263 in MILLILITRES, fifty times finer. Read on the percentage, a
    row where the car drove but the coarse gauge had not yet ticked is dropped, and there are a lot
    of them. Measured on @pdifeo's beta #28 bundle against photographs of his own dashboard, which
    states the petrol distance per drive:

        the car said   60.2 km      on the percentage   35.0 km  (−42 %)
                                    on the millilitres  54.0 km  (−10 %)

    The millilitres were in the same row the whole time: `positions.fuel_liters`, written since
    v2.14.1. ⚠️ Second time this file has made this mistake — `_reev_trip_fuel` had its noise floor
    on the percentage while reading the millilitres (beta #22/#23).

    ⛔ One column for the whole walk, never a mix: half the kilometres counted on a 47.5 mL grid and
    half on a 1 mL one is a total that means nothing.

    ⛔ **The rule that looks better and is not.** "Anchor on the last fuel CHANGE and credit every
    kilometre since" scores −2 % if you reconstruct the trail from the raw signal log — and +54 % on
    the real one, because it hands the generator the whole electric middle of a drive that burned a
    little at each end. The raw log records a signal only WHEN IT CHANGES, so rebuilding from it
    gives a timeline 3× sparser than `positions`, which gets a row every poll (~11 s while driving).
    Any rule tuned on that reconstruction is tuned on an artefact. Model the poll grid, not the
    signal log.

    ⛔ And there is **no "generator running" signal** in the cloud. 1277 looks exactly like one — 0
    across a pure-electric drive, 1 across a petrol one — and across all 13 days of the bundle it
    turns out to fire in one-minute bursts at the start and end of drives, with 0.001 L burned
    inside them against 24.9 L outside."""
    if not (vehicle_id and started_at and ended_at):
        return None
    try:
        rows = db.execute(
            "SELECT odometer_km, fuel_level_pct, fuel_liters FROM positions "
            "WHERE vehicle_id = ? AND recorded_at BETWEEN ? AND ? ORDER BY recorded_at, id",
            (vehicle_id, started_at, ended_at)).fetchall()
    except sqlite3.Error:
        return None
    pts = [(r["odometer_km"], r["fuel_level_pct"], r["fuel_liters"]) for r in rows
           if r["odometer_km"] is not None and r["fuel_level_pct"] is not None]
    if len(pts) < 2:
        return None
    fine = all(p[2] is not None for p in pts)     # the whole trail carries the car's own millilitres
    engine_km = engine_fuel_pct = 0.0
    for a, b in zip(pts, pts[1:]):
        dkm = b[0] - a[0]
        drop = (a[2] - b[2]) if fine else (a[1] - b[1])
        if dkm > 0 and drop > 0:             # moving AND burning → generator driving the car
            engine_km += dkm
            engine_fuel_pct += a[1] - b[1]   # always the percentage: reev_fuel_summary reads it
    if engine_km <= 0.5:
        return None
    return {"engine_km": round(engine_km, 1), "engine_fuel_pct": round(engine_fuel_pct, 2)}


def _reev_trip_fuel(fuel_start_pct, fuel_end_pct, distance_km, engine=None,
                    fuel_start_l=None, fuel_end_l=None, tank_l=None) -> dict:
    """REEV Phase C — per-trip fuel from the tank-% drop. There's no 'engine on' PID: the range-extender
    ran iff the fuel level dropped more than the signal-noise floor. `engine` (from _reev_engine_on) is
    the generator's driving footprint; when present the L/100 km is fuel-burned-while-driving over
    distance-while-driving — matching the car — instead of spreading the litres over the WHOLE trip
    (which under-reports on a mixed EV+generator drive). Falls back to the whole-trip distance when the
    per-position trail isn't available (old, pruned trips). Returns {fuel_used_l, fuel_l_100km,
    engine_ran, engine_km}; all inert when there's no fuel data (BEV) or the drive was pure-electric.

    The litres come from the car's OWN counter (fuel_start_l/fuel_end_l, signal 3263) when the trip
    has them; the tank-% × assumed-capacity path below is the fallback for a BEV, an unknown model,
    or any trip recorded before v2.14.1. `tank_l` overrides the assumed capacity (per model)."""
    out = {"fuel_used_l": None, "fuel_l_100km": None, "engine_ran": False, "engine_km": None}
    if fuel_start_pct is None or fuel_end_pct is None:
        return out
    drop = fuel_start_pct - fuel_end_pct
    cap = tank_l if tank_l else reev_tank_l()
    measured = (fuel_start_l - fuel_end_l) if (fuel_start_l is not None and fuel_end_l is not None) else None
    # The noise floor belongs to whichever signal is actually being read. 3235 (%) moves in steps of
    # 0.1 — 50 mL of a 50 L tank — so 0.2 % is the right guard for it. 3263 counts in MILLILITRES,
    # fifty times finer, and gating IT on the percentage threw away every trip that burned under
    # ~100 mL: on a range-extender, which runs mostly electric with the generator cutting in and
    # out, those are not the exception but the norm. Measured on two owners' bundles (beta #23
    # @michapr: 9.64 L by the car's own counter against 5.9 reported — 3.7 L lost in sub-threshold
    # trips; beta #22 @pdifeo: ~2.1 L over 35 km reported as 0.3). The tank constants were right
    # all along; the guard was on the wrong signal, and it ran BEFORE the fine one was even read.
    if measured is not None and measured > _REEV_FUEL_MIN_L:
        out["fuel_used_l"] = round(measured, 3)
    elif measured is None and drop > _REEV_FUEL_MIN_DROP:
        out["fuel_used_l"] = round(drop / 100.0 * cap, 2)
    else:
        return out                      # nothing burned, or too little to tell from noise
    out["engine_ran"] = True
    # engine_km is still measured and still shown — it says how far the generator actually drove —
    # but it is NO LONGER the denominator. The L/100 km is over the WHOLE distance, which is what
    # the car itself reports (getPlugIn's oc100km) and therefore what the owner sees in the official
    # app. Dividing by the generator-on distance answers "how thirsty is the generator"; the app
    # answers "what did this drive cost in petrol", and two different answers under one label is
    # how a correct figure gets reported as a bug (@michapr, beta #23 — 15.9 here against the 2.2
    # his own arithmetic and his app both gave). Silvio's call, and it reverses the basis
    # reev_fuel_summary was built on.
    if engine and engine.get("engine_km", 0) > 0.5:
        out["engine_km"] = engine["engine_km"]
    if distance_km and distance_km > 0.5:
        out["fuel_l_100km"] = round(out["fuel_used_l"] / distance_km * 100, 1)
    return out


def _reev_trip_elec(ec_kwh, distance_km, engine_ran) -> dict:
    """REEV Phase D (beta #10 step 2) — the ELECTRIC side of an engine-on trip, from the cloud's METERED
    getEC, NOT from ΔSoC. On a series hybrid the generator recharges the pack mid-drive, so the net SoC
    change isn't the motor's appetite (that's the diluted ~0.5 the SoC path yields and we suppress);
    getEC counts real consumption, generator-proof. Over the FULL distance — the electric motor drives the
    whole trip, so (unlike fuel) there's no generator-on sub-distance to normalise over. Inert on a BEV /
    pure-electric / not-yet-enriched trip (returns None, None → the UI shows a 'getEC pending' hint).

    ⚠️ `ec_kwh`, the TOTAL that left the battery — not `ec_driving`, the motor's share of it. Silvio's
    rule, 05/08/26: *«la quota guida non dovremmo mai prenderla in considerazione, sempre l'energia
    totale, quello che facciamo anche per le EV»*. It used to show the driving share, and that is the
    number the cost is NOT billed on: `reev_trip_electric_cost` draws down the paid stock by `ec_kwh`.
    So a trip card said "ELECTRIC USED 1.7 kWh" over a cost worked out on 2.0 (@michapr, beta #11) —
    two electric figures on one card, and the one on show was not the one paid. A BEV has always been
    billed on the total; a range-extender now matches it."""
    out = {"reev_elec_kwh": None, "reev_elec_kwh_100km": None}
    if engine_ran and ec_kwh and distance_km and distance_km > 0:
        out["reev_elec_kwh"] = round(ec_kwh, 2)
        out["reev_elec_kwh_100km"] = round(ec_kwh / distance_km * 100, 1)
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


ACTIVE_VEHICLE_SETTING = "active_vehicle_vin"


def _current_vehicle_id():
    """The vehicle every read is scoped to: the one picked in the sidebar, else the first.

    🔑 One statement does both jobs, and that matters because it runs around sixty times per page
    render. `vin <> <chosen>` scores 0 for the picked car and 1 for every other, so the choice
    sorts first; ties — nobody has chosen, or the choice names a car that is no longer on the
    account — fall through to `id`, which is exactly the old first-vehicle behaviour. So the picker
    cannot strand the interface on a car that is not there, and an install with one car is
    untouched: filtering by its single id matches every row it has.

    Returns None only before the first vehicle is registered; `vehicle_id = COALESCE(?, vehicle_id)`
    then matches everything, so a fresh or minimal database behaves as it always did.
    """
    try:
        row = _get().execute(
            "SELECT id FROM vehicles ORDER BY vin <> COALESCE("
            "  (SELECT value FROM settings WHERE key = ?), ''), id LIMIT 1",
            (ACTIVE_VEHICLE_SETTING,)).fetchone()
    except sqlite3.OperationalError:      # a partial/minimal DB with no vehicles table → don't scope
        return None
    return row["id"] if row else None


def get_vehicles() -> list[dict]:
    """Every registered car, oldest first — what the picker lists. Empty before setup."""
    try:
        return [dict(r) for r in _get().execute(
            "SELECT * FROM vehicles ORDER BY id").fetchall()]
    except sqlite3.OperationalError:
        return []


# The C10 RWD default Mate itself wrote until v3.11.1, and what replaced it. 69.9 is the nameplate
# figure read as usable; @ghuaywen-ai's own charges (#246) showed the battery taking 100.8% of what
# his charger delivered on it — more energy in than out. Only installs still sitting on the exact
# old default are offered the correction: 4% high on every kWh, €/kWh and consumption figure they
# print. A number the owner typed is theirs. → [[pack-capacity-declared-is-gross-not-net]]
_SUPERSEDED_PACKS = {("C10", 69.9): 67.0}


def superseded_pack_kwh() -> "float | None":
    """The corrected pack for the SELECTED car if it is still on a default Mate has since
    disproved, else None. A suggestion for Settings to show — never a migration: a capacity is
    calibratable, some owners have measured theirs, and overwriting that is how a figure becomes
    impossible to trust."""
    try:
        v, _ = get_vehicle()
        cap = get_battery_capacity_kwh()
    except sqlite3.Error:
        return None
    if not v or cap is None:
        return None
    return _SUPERSEDED_PACKS.get(((v.get("car_type") or "").strip().upper(), round(cap, 1)))


_SETUP_STAMP_PREFIX = "vehicle_setup_done_"


def mark_vehicle_configured(vin: str = "") -> None:
    """Record that a human has answered for a car — which is what turns the "never set up" strip
    off. Named `vin`, or the SELECTED car when it is not given.

    Until v3.14.0 the wizard was the only writer of this mark, so a car the strip complained about
    could be corrected properly, from Settings, and go on being accused (@cookingeek, the first
    install with two real cars). Choosing that car's pack IS the answer to "nobody chose this car's
    pack", wherever it is chosen.

    🔴 One car, never install-wide: a third car nobody has looked at must keep its strip. Same rule
    as the capacity itself, where writing the first car's value while looking at the second was an
    ~80% error on everything derived from a percentage (#186)."""
    key = (vin or "").strip().lower()
    if not key:
        v, _ = get_vehicle()
        key = ((v or {}).get("vin") or "").strip().lower()
    if key:
        set_setting(f"{_SETUP_STAMP_PREFIX}{key}", "1")


def unconfigured_vehicles() -> list[dict]:
    """The cars nobody was ever asked about — a second car that walked in from the poller on an
    install where the login was already done, so the wizard never ran for it and it took its
    MODEL's default pack. On a C10 that default is the RWD: an AWD is then 20% off and a REEV
    2.4 times off, and every kWh, €/kWh and consumption figure of that car follows the wrong
    number in silence. Whoever shows this must offer the wizard, which is where a pack and a PIN
    are chosen.

    🔴 Silent by design in two cases, both of them "we do not know" rather than "all good":

      * before `vehicle_setup_backfilled` — the poller has not run its one-time stamping yet (a
        page rendered between the update and the poller's first start), so no stamp on a car
        means nothing at all;
      * before `setup_complete` — a fresh install is not an install with an unconfigured car.

    → the same trap as signal-absent-is-not-signal-zero, on a settings key."""
    try:
        if get_setting("vehicle_setup_backfilled", "") != "1":
            return []
        if get_setting("setup_complete", "") != "1":
            return []
        rows = _get().execute(
            "SELECT key FROM settings WHERE key LIKE ? AND value = '1'",
            (_SETUP_STAMP_PREFIX + "%",)).fetchall()
    except sqlite3.Error:      # minimal schema (no settings table): claim nothing
        return []
    seen = {r["key"][len(_SETUP_STAMP_PREFIX):] for r in rows}
    return [v for v in get_vehicles()
            if (v.get("vin") or "").strip() and (v["vin"]).strip().lower() not in seen]


def is_reev_car() -> bool:
    """Whether the SELECTED car is a range-extender.

    It was one flag for the install, written the first time any car reported a fuel tank. With a
    range-extender and a plain electric car on one account that put the REEV pages on both — and,
    worse on the official build, withheld the battery-derived figures from the very car they are
    correct for.

    The per-car key wins when it exists. Absence is not "no": a car the poller has not reached
    since the update has no key yet, and falls back to the account flag — which is exactly what it
    read before, so a half-updated install behaves as it used to rather than flipping to BEV and
    hiding a real range-extender's pages.
    """
    try:
        row = _get().execute("SELECT vin FROM vehicles WHERE id = COALESCE(?, id) ORDER BY id "
                             "LIMIT 1", (_current_vehicle_id(),)).fetchone()
    except sqlite3.Error:
        row = None
    if row and row["vin"]:
        per_car = get_setting(f"is_reev_{str(row['vin']).lower()}", "")
        if per_car != "":
            return per_car == "1"
    return get_setting("is_reev", "0") == "1"


def set_active_vehicle(vin: str) -> bool:
    """Point every scoped read at `vin`. A VIN we do not have is refused rather than stored, so a
    stale bookmark or a hand-made request cannot blank the interface. Returns whether it changed."""
    try:
        if not _get().execute("SELECT 1 FROM vehicles WHERE vin = ?", (vin,)).fetchone():
            return False
    except sqlite3.OperationalError:
        return False
    set_setting(ACTIVE_VEHICLE_SETTING, vin)
    return True


# How many polls a car has to have answered before "it has never sent this" is a statement about
# the CAR rather than about how recently Mate was installed. 50 polls ≈ 25 minutes parked — cheap,
# and it means a fresh install shows every row until there is enough evidence to hide one.
_ABSENT_SENSOR_MIN_POLLS = 50
_ABSENT_SENSOR_WINDOW = 500      # how many recent polls the question is asked over

# The temperature sensors that can legitimately not exist on a car, and the column each lives in.
_OPTIONAL_TEMPS = {"inside_temp": "inside_temp",
                   "battery_temp": "battery_min_temp",
                   "ac_target": "climate_target_temp"}


def never_reported_temps() -> set:
    """Which temperature sensors THIS car has never once reported (#144).

    Silvio, 08/08: *«se non è presente un sensore per la T03 dobbiamo nasconderlo, e non farlo più
    vedere»*. A row that reads "—" for ever is still a row promising a number that will never come.

    🔑 Hidden on the DATA, not on the model. "This car has never sent it in 88 000 polls" is a
    measurement; "T03s don't have it" would be a guess about every T03 from one owner's car, and
    these two are marked `core` precisely so no model list can silence them.
    → [[a-feature-switch-must-gate-the-data]]

    ⚠️ The poll floor is the whole safety of it: with three polls behind a fresh install, "never
    seen" means nothing at all, and hiding on it would blank rows that were about to work.

    ⚠️ Bounded to the most recent polls, and that is a performance decision with a correctness
    consequence worth stating: this runs on every render of the status card, and a full scan of
    `positions` means 100 000 rows per page on a month-old install. These signals arrive on every
    poll or on none — nothing sends a cabin temperature once a week — so the recent window answers
    the same question at a fixed cost, and a car whose sensor STARTS working is un-hidden within a
    few hours instead of never.
    """
    try:
        cols = ", ".join(f"SUM({c} IS NOT NULL) AS {k}" for k, c in _OPTIONAL_TEMPS.items())
        row = _get().execute(
            f"SELECT COUNT(*) AS n, {cols} FROM (SELECT {', '.join(_OPTIONAL_TEMPS.values())} "
            "  FROM positions WHERE vehicle_id = COALESCE(?, vehicle_id) "
            "  ORDER BY id DESC LIMIT ?)",
            (_current_vehicle_id(), _ABSENT_SENSOR_WINDOW)).fetchone()
    except sqlite3.Error:
        return set()
    if not row or int(row["n"] or 0) < _ABSENT_SENSOR_MIN_POLLS:
        return set()
    return {k for k in _OPTIONAL_TEMPS if not int(row[k] or 0)}


def get_charge_schedule_window() -> dict:
    """The charging plan of the car ON SCREEN (#186). The poller reads each car's own plan from the
    cloud; one shared key meant the car polled last overwrote the other, and the Scheduling page
    showed one plan under both. Falls back to the shared keys, which is what installs have today."""
    vin = ""
    try:
        row = _get().execute("SELECT vin FROM vehicles WHERE id = COALESCE(?, id) ORDER BY id "
                             "LIMIT 1", (_current_vehicle_id(),)).fetchone()
        vin = (row["vin"] if row and row["vin"] else "").lower()
    except sqlite3.Error:
        pass

    def _one(name, default=""):
        own = get_setting(f"charge_sched_{name}_{vin}", "") if vin else ""
        return own or get_setting(f"charge_sched_{name}", default)

    return {"enabled": _one("enabled", "0") == "1",
            "start": (_one("start") or "").strip(),
            "end": (_one("end") or "").strip()}


def _selected_or_first(db) -> Optional[int]:
    """The car a WRITE belongs to: the one on screen, else the only/first one (#186).

    🔴 Three writes used `SELECT id FROM vehicles ORDER BY id LIMIT 1` — the FIRST car, always. On
    two cars that meant a hand-typed charge, a fuel purchase and a battery-capacity change all
    landing on the car you were not looking at. `set_vehicle_capacity_current` even said so in its
    own docstring — *"the multi-car step will resolve 'current' to the selected vehicle instead of
    the first"* — a note to a future that had arrived.

    The fallback is not decoration: `_current_vehicle_id()` is None on a database with no vehicles
    yet, and a manual charge typed during setup must still land somewhere."""
    vid = _current_vehicle_id()
    if vid is not None:
        return vid
    row = db.execute("SELECT id FROM vehicles ORDER BY id LIMIT 1").fetchone()
    return row["id"] if row else None


def _conn_rw() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


# How close a CSV line has to be to a charge already in the database to be the SAME session rather
# than a new one (#237, `import_charge_row`). A file that came out of Mate's own export matches to
# the microsecond; one typed by hand from a receipt does not, and a minute is well inside the gap
# between two real charging sessions. The energy has to agree too — either test alone is a
# coincidence waiting to happen, both together are not.
_IMPORT_MATCH_DAYS = 60.0 / 86400.0      # one minute, in the days julianday() speaks
_IMPORT_MATCH_KWH = 0.05


def get_setting(key: str, default: str = "") -> str:
    db = _get()
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def get_account_user() -> str:
    """The Leapmotor account this instance polls with — the login, not the car.

    Asked for in beta #13 by @ebagnoli, who runs several Mate instances on several Leapmotor
    accounts: the Settings card named the model and the VIN but never the account, so from
    inside Mate there was no way to tell one instance from another.

    The wizard stores it in settings; a dev/add-on install passes it in the environment and
    the setting stays empty — hence the fallback, which is the SAME precedence the command
    client uses for the credentials. Empty string when neither is set (fresh install), and
    the caller decides what to do with that (the Settings card hides the row)."""
    return get_setting("leapmotor_user") or os.environ.get("LEAPMOTOR_USER", "")


# The settings that silently change how Mate BEHAVES — nine sliders in five forms. #230: @adoewa's
# `charge_detect_min_a` sat at 14.5 A against a default of 2.0, above the 11-12 A a home AC charge
# moves the pack at, so no charge was ever recorded; he says he had set 2, and that the poll cadence
# had moved too. Each was a range slider in a form that saved on `change` — a stray drag while
# scrolling a phone wrote the value with no confirmation and no trace. The forms now need a Save
# press; this is the other half, so "it changed by itself" stops being unanswerable.
#
# ⚠️ Deliberately NOT everything: language, currency, prices and the rest are visible in the UI and
# change nothing about what gets recorded. A trail of everything is a trail nobody reads. And never
# a secret — the values are stored verbatim.
AUDITED_SETTINGS = (
    "poll_parked", "poll_driving", "charge_detect_min_a", "charge_reconstruct_min_pct",
    "vampire_min_drop_pct", "vampire_min_hours", "charge_dc_min_kw", "soh_temp_min_c",
    "map_station_min_sessions", "positions_retention_days",
)


def _ensure_settings_audit(db) -> None:
    db.execute("CREATE TABLE IF NOT EXISTS settings_audit ("
               "id INTEGER PRIMARY KEY AUTOINCREMENT, changed_at TEXT NOT NULL, key TEXT NOT NULL,"
               " old_value TEXT, new_value TEXT)")


def get_settings_audit(limit: int = 40) -> list:
    """The recent changes to the behaviour settings, newest first."""
    # ⚠️ Read-only, deliberately: a reader must never open the write connection just to CREATE a
    # table it might read. The table appears on the first audited write; until then "no such table"
    # is the honest answer and is caught below. The first version called `_conn_rw()` here and the
    # suite raised "SQLite objects created in a thread can only be used in that same thread".
    try:
        db = _get()
        return [dict(r) for r in db.execute(
            "SELECT changed_at, key, old_value, new_value FROM settings_audit"
            " ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
    except sqlite3.Error:
        return []


def set_setting(key: str, value: str) -> None:
    db = _conn_rw()
    if key in AUDITED_SETTINGS:
        try:
            _ensure_settings_audit(db)
            row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            old = row[0] if row else None
            # Saving a form re-writes every field in it, so only real movement is recorded —
            # otherwise the trail fills with noise and the one line that matters is buried.
            if str(old) != str(value):
                db.execute("INSERT INTO settings_audit (changed_at, key, old_value, new_value)"
                           " VALUES (?,?,?,?)",
                           (datetime.now(timezone.utc).isoformat(), key, old, str(value)))
        except sqlite3.Error:
            pass                  # a trail that fails must never stop the setting being saved
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, str(value)))
    db.commit()
    _lang_memo[0] = None          # cheap and unconditional: any write re-reads the language once


def set_vehicle_capacity(vin: str, kwh: float) -> None:
    """Capacity onto ONE named car's row. The wizard uses it: it configures every car it found, and
    "current" has no meaning there — nothing is selected yet."""
    db = _conn_rw()
    db.execute("UPDATE vehicles SET capacity_kwh = ? WHERE lower(vin) = ?",
               (float(kwh), str(vin).lower()))
    db.commit()


def set_vehicle_capacity_current(kwh: float, nominal: float = None) -> None:
    """Mirror a capacity override onto the CURRENT vehicle's own row (vehicles.capacity_kwh), so the
    poller's per-vehicle energy math honours it — the poller reads the vehicle column, not the global
    setting, so writing only the global would leave the override ignored. Single-car today = the only
    row. Multi-car: 'current' IS the selected vehicle — writing the first car's capacity while
    looking at the second was an ~80% error on everything derived from a percentage (#186)."""
    db = _conn_rw()
    vid = _selected_or_first(db)
    if vid is None:
        return
    db.execute("UPDATE vehicles SET capacity_kwh = ? WHERE id = ?", (float(kwh), vid))
    if nominal is not None:
        db.execute("UPDATE vehicles SET capacity_nominal_kwh = ? WHERE id = ?", (float(nominal), vid))
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
        return _get().execute(
            "SELECT COUNT(*) c FROM raw_signals_log WHERE vehicle_id = COALESCE(?, vehicle_id)",
            (_current_vehicle_id(),)).fetchone()["c"]
    except Exception:  # noqa: BLE001
        return 0


def latest_raw_signals() -> dict:
    """Latest value per raw signal id from the research capture — {sig_key: value}. Lets the REEV
    dashboard render from the last stored signals when a live cloud fetch isn't available (a replayed
    tester bundle, or a transient hiccup). Empty when nothing was captured (the normal build)."""
    try:
        rows = _get().execute(
            "SELECT sig_key, value FROM raw_signals_log WHERE id IN ("
            "  SELECT MAX(id) FROM raw_signals_log"
            "   WHERE vehicle_id = COALESCE(?, vehicle_id) GROUP BY sig_key)",
            (_current_vehicle_id(),)).fetchall()
        return {r["sig_key"]: r["value"] for r in rows}
    except Exception:  # noqa: BLE001
        return {}


def get_raw_signal_rows():
    """All captured raw-signal rows (ts, sig_key, value), oldest first — for the export."""
    try:
        rows = _get().execute(
            "SELECT ts, sig_key, value FROM raw_signals_log "
            "WHERE vehicle_id = COALESCE(?, vehicle_id) ORDER BY ts ASC",
            (_current_vehicle_id(),)).fetchall()
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


def abrp_cars_without_token() -> list[str]:
    """Cars that would send NOTHING to ABRP — the ones with no token of their own, once there is
    more than one car. With a single car the install-wide token still covers it, so the list is
    empty and nothing is claimed. Shown in Settings because otherwise the silence is invisible:
    the second car simply never appears on ABRP and nobody says why."""
    vehicles = get_vehicles()
    if len(vehicles) < 2:
        return []
    out = []
    for v in vehicles:
        vin = (v.get("vin") or "")
        if not get_secret(f"abrp_token_{vin.lower()}", ""):
            out.append(v.get("car_type") or vin[-6:])
    return out


def abrp_token_in_use() -> bool:
    """Whether ABRP would actually send anything, which is not the same as "a token exists".

    Mirrors the poller's rule in `Database.get_abrp_token`: a per-car token counts always, the
    install-wide one only while there is a single car. With two cars and nothing but the shared
    token the poller sends NOTHING — so a status dot reading that key alone would report "active"
    over silence, which is the kind of green light that costs an afternoon to disbelieve."""
    vehicles = get_vehicles()
    if any(get_secret(f"abrp_token_{(v.get('vin') or '').lower()}", "") for v in vehicles):
        return True
    return len(vehicles) <= 1 and bool(get_secret("abrp_token", ""))


def get_secret(key: str, default: str = "") -> str:
    """Read a secret setting, decrypting transparently (plaintext passes through)."""
    return crypto.decrypt(get_setting(key, default))


def set_secret(key: str, value: str) -> None:
    """Write a secret setting encrypted at rest (matches the poller's crypto/key)."""
    set_setting(key, crypto.encrypt(value or ""))


def _pin_key(vin: str) -> str:
    return f"leapmotor_pin_{str(vin).lower()}"


def get_operate_pin(vin: str = "") -> str:
    """The four digits that authorise a command ON THIS CAR (#186, @cookingeek).

    It was one PIN for the install. Eight steps of multi-car work made the battery capacity, the
    range-extender flag, the declared abilities, the model and the Home Assistant entities into facts
    about a *car* — and left the PIN behind. With two cars whose PINs differ, every command to the
    second one would have failed, and **only the commands**: reads carry no PIN, so the pages would
    have looked perfectly healthy while the buttons quietly did nothing.

    🔑 The cloud checks it per VIN — `/operPwd/verify` takes `operatePassword` *and* `vin`. That does
    not prove two cars must differ; it proves the API is built so they can.

    ⚠️ The fallback is the whole safety of it. No per-car PIN → the install-wide one, which is what
    every install alive today has: one car, or two cars sharing a PIN, see no change at all.
    """
    if not vin:
        row = None
        try:
            row = _get().execute("SELECT vin FROM vehicles WHERE id = COALESCE(?, id) ORDER BY id "
                                 "LIMIT 1", (_current_vehicle_id(),)).fetchone()
        except sqlite3.Error:
            pass
        vin = (row["vin"] if row and row["vin"] else "")
    if vin:
        own = get_secret(_pin_key(vin), "")
        if own:
            return own
    return get_secret("leapmotor_pin", "") or os.environ.get("LEAPMOTOR_PIN", "")


def set_operate_pin(pin: str, vin: str) -> None:
    """Give one car its own PIN — or clear it, which returns that car to the install-wide one."""
    set_secret(_pin_key(vin), pin or "")


def boost_selected_car(seconds: float = 60.0) -> None:
    """Poll the car you are LOOKING AT quickly for a while, after a command from the page (#186).

    Per car, like everything else the poller schedules: one shared flag meant a command sent to the
    car in the garage also woke the one on the motorway — small, but it spends that car's cloud
    budget on a state nobody asked about. With no car registered yet it falls back to the shared key,
    which is what a fresh install has."""
    import time as _t
    vin = ""
    try:
        row = _get().execute("SELECT vin FROM vehicles WHERE id = COALESCE(?, id) ORDER BY id "
                             "LIMIT 1", (_current_vehicle_id(),)).fetchone()
        vin = (row["vin"] if row and row["vin"] else "")
    except sqlite3.Error:
        pass
    key = f"boost_until_{vin.lower()}" if vin else "boost_until"
    set_setting(key, str(_t.time() + seconds))


def per_car_pin_keys() -> list:
    """The per-car PIN settings that exist, so the boot decryption check can name them (#227)."""
    try:
        return [r["key"] for r in _get().execute(
            "SELECT key FROM settings WHERE key LIKE 'leapmotor_pin\\_%' ESCAPE '\\'").fetchall()]
    except sqlite3.Error:
        return []


# The secrets kept encrypted at rest. Mirrors poller/db.py's SECRET_KEYS — the same eight settings
# seen from the other process.
SECRET_KEYS = ("leapmotor_pass", "leapmotor_pin", "abrp_token", "mqtt_pass",
               "geocoder_key", "ha_token", "ocm_key", "tomtom_key")


_decryption_reported = False


def check_decryption() -> list:
    """Say — once, at boot, and in words — that the stored secrets belong to a key we no longer
    have. Returns the names of the unreadable ones.

    The poller has had this since the encryption landed. The web did not, and the web is the screen
    people are looking at: @Ng-EY (#227) restored a database whose secrets belonged to a
    `secret.key` a clean Docker restart had replaced, and got **101 identical generic warnings**
    with no hint of what to do. The instruction he needed already existed — in the other process's
    log, which he had no reason to open.
    """
    lost = []
    try:
        # …and the per-car PINs, which are not a fixed list: a secret missing from this check goes
        # missing in silence, which is the very thing #227 was about.
        for key in list(SECRET_KEYS) + per_car_pin_keys():
            if not crypto.can_decrypt(get_setting(key)):
                lost.append(key)
    except sqlite3.Error:
        return []
    global _decryption_reported
    if lost and not _decryption_reported:
        # Once per PROCESS, not once per call: `uvicorn.run("main:app")` imports web/main.py a
        # second time in the same process (as `main`, after `__main__`), so every module-level line
        # in it fires twice — that is riri19's doubled diagnostics. The flag lives HERE because
        # `db_reader` is cached in sys.modules and is therefore the one object both copies share.
        _decryption_reported = True
        # This module has no logger of its own — it is a reader, and every other message it could
        # want to emit belongs to its caller. One import here beats a module-level logger that
        # nothing else would ever use.
        import logging
        logging.getLogger("mate.web").error(
            "Cannot decrypt %d stored secret(s) (%s): wrong or missing /data/secret.key. "
            "Restore the key together with the database, or re-enter these in Settings. "
            "Trips and charges are not encrypted and are unaffected.",
            len(lost), ", ".join(lost))
    return lost


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


# The UI language is read for EVERY number displayed (units.decimal_point / main._nice put the
# decimal separator where the language wants it), and a settings row costs ~0.5 ms to fetch — on a
# page with 200 figures that would be 100 ms of pure lookup. It changes about once in the life of
# an install, so it is memoised and the memo is dropped by set_setting when anything is written.
_lang_memo: list = [None]


def get_language() -> str:
    if _lang_memo[0] is None:
        _lang_memo[0] = get_setting("language", "en")
    return _lang_memo[0]


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


def ready_automation_key(vin: str = "") -> str:
    """Where THIS car's ready-automation config lives. Same shape as the ABRP token: per-VIN, with
    the install-wide key still covering a single-car install and covering NOBODY once there are two
    — turning on the climate of one car on another car's orders is not a fallback, it is the wrong
    car acting."""
    v = (vin or _selected_vin() or "").lower()
    return f"ready_automation_{v}" if v else "ready_automation"


def _selected_vin() -> str:
    try:
        row = _get().execute("SELECT vin FROM vehicles WHERE id = COALESCE(?, id) ORDER BY id "
                             "LIMIT 1", (_current_vehicle_id(),)).fetchone()
    except sqlite3.Error:
        return ""
    return (row["vin"] if row and row["vin"] else "")


def _ready_automation_raw(vin: str = "") -> str:
    own = get_setting(ready_automation_key(vin), "")
    if own:
        return own
    try:
        one_car = len(get_vehicles()) <= 1
    except sqlite3.Error:
        one_car = True
    return get_setting("ready_automation", "") if one_car else ""


def get_ready_automation_config() -> dict:
    """Sanitised config for the Prepara Veicolo page's automation section — of the SELECTED car."""
    try:
        raw = json.loads(_ready_automation_raw() or "{}")
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
    # On the SELECTED car: with two cars a single blob meant one climate answering for both.
    set_setting(ready_automation_key(), json.dumps(cfg))


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
                                         # across the gap (same guard as _charge_energy_below_soc)
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
                       manual_cost: Optional[float] = None,
                       gross_kwh: Optional[float] = None) -> dict:
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
        # A public charger has no meter Mate can read, so the owner may type what its display said
        # (#222 @ghuaywen-ai). It plays exactly the role the wallbox counter plays at home: it
        # PRICES the charge — you pay for what left the charger, conversion losses included — and
        # nothing else. It never becomes the energy Mate reports or totals (_billed_kwh is
        # untouched), because that one is measured and this one is typed.
        gross = gross_kwh if gross_kwh is not None else (charge["gross_kwh"] if "gross_kwh" in charge.keys() else None)
        if location_type == "HOME" and meter and meter > 0:
            billed = meter
        elif gross and gross > 0:
            billed = gross
        else:
            billed = None
        cost = compute_cost(charge, ac_kwh=billed)   # returns 0.0 when the charge is marked free

    # gross_kwh only joins the UPDATE when it is actually being set: a re-tag must not rewrite a
    # column it is not changing, and the write then never assumes a column a caller's table may not
    # have (the cost-floor tests build a minimal charges table by hand).
    # ...and only where the column exists. The poller owns that migration, so between an update and
    # its next start a typed figure would hit an UPDATE naming a column that is not there — a 500 on
    # the form instead of a stored number. Not storing it for those few seconds is the lesser harm.
    if gross_kwh is not None and _charges_have_gross(db):
        db.execute("UPDATE charges SET location_type=?, cost=?, is_free=?, gross_kwh=? WHERE id=?",
                   (location_type, cost, free, gross_kwh, charge_id))
    else:
        db.execute("UPDATE charges SET location_type=?, cost=?, is_free=? WHERE id=?",
                   (location_type, cost, free, charge_id))
    db.commit()
    return dict(db.execute("SELECT * FROM charges WHERE id=?", (charge_id,)).fetchone())


def set_charge_gross_kwh(charge_id: int, gross_kwh: Optional[float]) -> dict:
    """#222 — store the kWh the charger's own display said it delivered, and NOTHING else about the
    charge. Its own type is handed back to update_charge_type unchanged, so the cost is recomputed on
    the same rule as everywhere else; a charge nobody has typed yet is left alone (the field is only
    offered on a typed charge, and an untyped one must not become typed by a side effect).

    `None` means the box came back empty — read it back and write nothing. That is not politeness:
    the field opens empty every time, so an accidental open followed by Enter has to be a no-op
    rather than an erasure. Zero is the deliberate way to take a wrong number back — every reader
    tests `gross_kwh > 0`, so a stored zero reads as never-typed and the cost falls straight back to
    the measured basis."""
    db = _conn_rw()
    row = db.execute("SELECT * FROM charges WHERE id=?", (charge_id,)).fetchone()
    if not row or not row["location_type"]:
        return dict(row) if row else {}
    if gross_kwh is None:
        return dict(row)
    return update_charge_type(charge_id, row["location_type"], gross_kwh=gross_kwh)


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


def import_charge_row(row: dict) -> str:
    """One CSV line into the database — FILLING IN the session it matches, or adding it (#237).

    Until now the importer only ever inserted. Re-importing a file therefore doubled the archive
    silently: 152 charges became 304, and the money with them. Nobody had reported it because
    nobody had a reason to import twice — until the odometer column gave them one. Someone who has
    already typed in a year of history must be able to add the kilometres to it without deleting
    anything first; asking them to wipe 152 rows by hand, in the right order, to repair a defect of
    ours was never a serious answer.

    A match is the same instant (within a minute — a file that came out of Mate's own export
    round-trips exactly, one typed by hand may not) AND the same energy to within 0.05 kWh. Both,
    because either alone is a coincidence waiting to happen.

    ⚠️ Declared, and deliberately narrow: **a matched row updates the odometer and nothing else.**
    Not the cost, not the type, not the SoC. Mate may have computed those from a real charging
    curve, and a re-import that quietly overwrote them would be a fresh way to lose data — the very
    thing this function exists to avoid. Correcting a price is what the charge's own edit form is
    for.

    Returns 'filled', 'added' or 'unchanged'.
    """
    started_at, energy = row.get("started_at"), row.get("energy_kwh")
    odo = row.get("odometer_km")
    db = _conn_rw()
    try:
        try:
            match = db.execute(
                "SELECT id FROM charges "
                " WHERE vehicle_id = COALESCE(?, vehicle_id) "
                "   AND ABS(julianday(started_at) - julianday(?)) <= ? "
                "   AND energy_added_kwh IS NOT NULL AND ABS(energy_added_kwh - ?) <= ? "
                " ORDER BY ABS(julianday(started_at) - julianday(?)) LIMIT 1",
                (_current_vehicle_id(), started_at, _IMPORT_MATCH_DAYS,
                 energy, _IMPORT_MATCH_KWH, started_at)).fetchone()
        except sqlite3.Error:
            match = None
        if match is not None:
            if odo is None or not _charges_have_odometer(db):
                return "unchanged"
            db.execute("UPDATE charges SET odometer_km = ? WHERE id = ?", (odo, match["id"]))
            db.commit()
            return "filled"
    finally:
        db.close()
    add_manual_charge(started_at, energy, row.get("cost"), row.get("charge_type", "AC"),
                      ended_at=row.get("ended_at"), start_soc=row.get("start_soc"),
                      end_soc=row.get("end_soc"), odometer_km=odo)
    return "added"


def add_manual_charge(started_at: str, energy_kwh: float, cost: Optional[float] = None,
                      charge_type: str = "AC", ended_at: Optional[str] = None,
                      start_soc: Optional[float] = None, end_soc: Optional[float] = None,
                      odometer_km: Optional[float] = None) -> int:
    """Insert a user-entered historical charge — e.g. sessions from before Mate was installed —
    so the lifetime totals / monthly report reflect them (#87). Date + energy are the essentials;
    cost, AC/DC and — optionally — start/end SoC can be given (the latter drives the card's SoC-gain
    tile, requested by @rossiadobe on #67). It stays SoH-safe either way: a manual charge has no power
    curve, so get_battery_health integrates ~0 energy and skips it regardless of SoC. location_type=
    'MANUAL' keeps the automatic costers from overwriting the cost the user typed, and manual_entry=1
    is what says the row was TYPED (#188) — the location_type alone can't, since it doubles as the
    cost basis a user picks on a real charge."""
    db = _conn_rw()
    try:
        vehicle_id = _selected_or_first(db)
        ct = "DC" if str(charge_type).upper() in ("DC", "FAST", "HPC") else "AC"
        # #237 — the odometer only joins the INSERT where the column exists: the migration lives in
        # the poller and the web never alters the database (see `_charges_have_odometer`). Zero is
        # not stored, for the same reason the poller refuses it: an odometer of 0 would place the
        # session at the factory gate rather than say nothing.
        odo = float(odometer_km) if odometer_km else None
        if odo is not None and _charges_have_odometer(db):
            cur = db.execute(
                "INSERT INTO charges (vehicle_id, started_at, ended_at, energy_added_kwh, "
                "duration_min, charge_type, location_type, cost, start_soc, end_soc, "
                "odometer_km, reconstructed, manual_entry) "
                "VALUES (?, ?, ?, ?, ?, ?, 'MANUAL', ?, ?, ?, ?, 0, 1)",
                (vehicle_id, started_at, ended_at or started_at, energy_kwh,
                 _span_minutes(started_at, ended_at), ct, cost, start_soc, end_soc, odo))
            db.commit()
            return cur.lastrowid  # type: ignore[return-value]
        cur = db.execute(
            "INSERT INTO charges (vehicle_id, started_at, ended_at, energy_added_kwh, duration_min, "
            "charge_type, location_type, cost, start_soc, end_soc, reconstructed, manual_entry) "
            "VALUES (?, ?, ?, ?, ?, ?, 'MANUAL', ?, ?, ?, 0, 1)",
            (vehicle_id, started_at, ended_at or started_at, energy_kwh,
             _span_minutes(started_at, ended_at), ct, cost, start_soc, end_soc))
        db.commit()
        # lastrowid is Optional only for a cursor that last ran something other than an INSERT;
        # this one just inserted into a table with an INTEGER PRIMARY KEY, so it is the new id.
        return cur.lastrowid  # type: ignore[return-value]
    finally:
        db.close()


def _span_minutes(started_at: Optional[str], ended_at: Optional[str]) -> Optional[float]:
    """Minutes between two stored timestamps, or None when there is no real span. A typed-in charge
    that carries an end time deserves the same ⏱ duration a measured one shows — without this the
    card prints the two times and then nothing between them (#188)."""
    if not started_at or not ended_at or started_at == ended_at:
        return None
    try:
        a = datetime.fromisoformat(str(started_at).replace(" ", "T").replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(ended_at).replace(" ", "T").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    mins = (b - a).total_seconds() / 60.0
    return round(mins, 1) if mins > 0 else None


def update_manual_charge(charge_id: int, started_at: str, energy_kwh: float,
                         cost: Optional[float] = None, charge_type: str = "AC",
                         ended_at: Optional[str] = None, start_soc: Optional[float] = None,
                         end_soc: Optional[float] = None,
                         odometer_km: Optional[float] = None) -> bool:
    """Rewrite a charge the user typed in (#188) — @adoewa imported his whole history from a
    spreadsheet and could then change only its note, its AC/DC tag and its cost, while the times and
    the SoC (which the add form never even asked for) were frozen for good.

    Guarded on manual_entry=1, and that guard is the point: on a MEASURED charge these fields are
    readings, and handing them to a form would let a typo overwrite what the car reported. Returns
    False — changing nothing — when the id isn't a typed-in charge."""
    db = _conn_rw()
    try:
        ct = "DC" if str(charge_type).upper() in ("DC", "FAST", "HPC") else "AC"
        # #237 — the odometer is written only where the column exists, and clearing it is a real
        # answer: someone who realises they typed the wrong reading must be able to take it back
        # out, not be stuck with a wrong kilometre for ever.
        if _charges_have_odometer(db):
            cur = db.execute(
                "UPDATE charges SET started_at=?, ended_at=?, energy_added_kwh=?, duration_min=?, "
                "charge_type=?, cost=?, start_soc=?, end_soc=?, odometer_km=? "
                "WHERE id=? AND manual_entry=1",
                (started_at, ended_at or started_at, energy_kwh,
                 _span_minutes(started_at, ended_at), ct, cost, start_soc, end_soc,
                 (float(odometer_km) if odometer_km else None), charge_id))
            db.commit()
            return cur.rowcount > 0
        cur = db.execute(
            "UPDATE charges SET started_at=?, ended_at=?, energy_added_kwh=?, duration_min=?, "
            "charge_type=?, cost=?, start_soc=?, end_soc=? "
            "WHERE id=? AND manual_entry=1",
            (started_at, ended_at or started_at, energy_kwh, _span_minutes(started_at, ended_at),
             ct, cost, start_soc, end_soc, charge_id))
        db.commit()
        return cur.rowcount > 0
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
    many rows moved. ONLY touches rows marked manual_entry=1 — the poller's are already UTC.

    🔴 That predicate used to be location_type='MANUAL', and it was too wide by exactly one meaning:
    the same value is what someone picks on the badge to say "I'll type the price myself", on a REAL
    charge. Measured, and therefore selected here. A first pass leaves it alone (its timestamp carries
    a zone, so the wall-clock conversion is a no-op) — but on a later zone CHANGE it lands in the
    re-anchoring branch and the car's own timestamp is rewritten. Measured on a test install while
    building #188: a charge the car recorded at 07:54 UTC, tagged MANUAL for its price, came out at
    13:54 after a move from Europe/Rome to America/New_York.

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
            "SELECT id, started_at, ended_at FROM charges WHERE manual_entry = 1").fetchall()
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
        vehicle_id = _selected_or_first(db)
        fb = fuel_before_pct if fuel_before_pct is not None else _fuel_before_pct(db, vehicle_id, ts)
        cur = db.execute(
            "INSERT INTO fuel_purchases (vehicle_id, ts, liters, price_per_l, total_cost, "
            "fuel_before_pct, note, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (vehicle_id, ts, liters, round(ppl, 4), round(tot, 2), fb, note,
             datetime.now(timezone.utc).isoformat()))
        db.commit()
        # lastrowid: Optional only for a cursor that last ran a non-INSERT — see add_manual_charge.
        return cur.lastrowid  # type: ignore[return-value]
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
_FUEL_DETECT_SETTLE_MIN = 15      # a further rise within this of the last one is the SAME fill-up.
                                  # @pdifeo's gauge took 28 s to climb from 70.2 % to full, so this
                                  # is thirty times the measured settle — and still nowhere near
                                  # any believable gap between two real visits to a pump.


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
            "SELECT recorded_at, fuel_level_pct, fuel_liters FROM positions "
            "WHERE vehicle_id = ? AND fuel_level_pct IS NOT NULL AND recorded_at >= ? "
            "ORDER BY recorded_at", (vid, mark or "")).fetchall()
        tank = reev_tank_l()
        if len(rows) < 2:
            return 0
        found = 0
        run = None      # the fill-up currently being followed; see below
        for i in range(len(rows) - 1):
            before, after = rows[i]["fuel_level_pct"], rows[i + 1]["fuel_level_pct"]
            rising = after > before

            # ── extend the fill-up in progress ───────────────────────────────────
            # A float gauge does not jump to the final level, it CLIMBS there. Measured on
            # @pdifeo's C10 (beta #17, 30/07/2026): 70.2 → 78.0 → 87.0 → 98.1 → 100.0 % in four
            # steps over twenty-eight seconds, every one of them reported. Counting the steps
            # instead of the fill turned one tank into THREE refuels — and no floor can fix that:
            # raise it and you still get three, lower it and you get four.
            #
            # So once a fill is open, absorb every further rise near it, HOWEVER SMALL. The tail
            # is not a rounding detail: his last step is +1.9 points, under the floor, and
            # dropping it books 13.213 L against a real 14.110.
            # NB: measured to the reading being ABSORBED (i+1), not to rows[i] — rows[i] is the run's
            # own last reading, so that distance is always zero and the window would never bite.
            if run is not None and rising and \
                    _minutes_between(run["ts"], rows[i + 1]["recorded_at"]) <= _FUEL_DETECT_SETTLE_MIN:
                run.update(after=after, ts=rows[i + 1]["recorded_at"],
                           l_after=rows[i + 1]["fuel_liters"])
                continue
            if run is not None:
                found += _flush_fuel_run(db, vid, run, tank)
                run = None

            # ── or open a new one ────────────────────────────────────────────────
            if after - before < _FUEL_DETECT_MIN_RISE_PCT:
                continue
            # Confirm the rise held: the next reading must not have dropped back to the old level.
            # (The last pair in the log has nothing after it — leave it for the next scan, when it
            # will have.)
            nxt = rows[i + 2]["fuel_level_pct"] if i + 2 < len(rows) else None
            if nxt is None or nxt < before + _FUEL_DETECT_MIN_RISE_PCT / 2:
                continue
            run = {"ts_from": rows[i]["recorded_at"], "ts": rows[i + 1]["recorded_at"],
                   "before": before, "after": after,
                   "l_before": rows[i]["fuel_liters"], "l_after": rows[i + 1]["fuel_liters"]}
        if run is not None:
            found += _flush_fuel_run(db, vid, run, tank)
        # Stop one pair short: the final reading may yet be the "before" of a rise still arriving.
        db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('fuel_scan_watermark', ?)",
                   (rows[-2]["recorded_at"],))
        db.commit()
        return found
    except sqlite3.Error:
        return 0
    finally:
        db.close()


def _minutes_between(a: str, b: str) -> float:
    """Minutes from `a` to `b`. A value that can't be parsed reads as forever, so a run is closed
    rather than extended across a timestamp nobody understands."""
    try:
        return abs((datetime.fromisoformat(str(b)) - datetime.fromisoformat(str(a))).total_seconds()) / 60
    except (ValueError, TypeError):
        return float("inf")


def _flush_fuel_run(db: sqlite3.Connection, vid: int, run: dict, tank: float) -> int:
    """Record one followed fill-up as a single pending detection. Returns 1 if it was new."""
    ts_from, ts = run["ts_from"], run["ts"]
    lo = (_iso_shift(ts_from, -_FUEL_DETECT_DEDUP_H), _iso_shift(ts, _FUEL_DETECT_DEDUP_H))
    if db.execute("SELECT 1 FROM fuel_purchases WHERE (vehicle_id = ? OR vehicle_id IS NULL) "
                  "AND ts BETWEEN ? AND ? LIMIT 1", (vid, lo[0], lo[1])).fetchone():
        return 0                                       # already logged by hand — same refuel
    if db.execute("SELECT 1 FROM fuel_detected WHERE vehicle_id = ? AND ts = ? LIMIT 1",
                  (vid, ts)).fetchone():
        return 0                                       # already known (pending or dismissed)
    # Litres: the car counts them itself (3263) — when both ends of the fill carry that, the figure
    # is MEASURED and the "≈" in front of it on the card stops being an apology. @gm27271's own fill
    # read 34.416 L against a pump ticket of 33.84. Percentage × assumed tank stays as the fallback
    # for rows written before v2.14.1.
    #
    # ⚠️ Unless the fill topped the gauge out, and this is where that was got wrong. @pdifeo's
    # 33.390 → 47.500 (beta #17) was read here as "47.5 confirms the C10 tank size on a second car":
    # 47 500 is the counter's CEILING, and in the same issue he had already said he stopped at the
    # pump's first click with room left. His next fill settled it (beta #21) — the pump gave
    # 10.51 L, the car reported 9.204, and both fills land on 47.500 exactly. The litres stay as
    # measured (they are the best we have); `_fill_is_capped` marks the row so the card can say
    # "≥" instead of "≈" and send the owner to the receipt.
    l_before, l_after = run["l_before"], run["l_after"]
    liters = ((l_after - l_before) if (l_before is not None and l_after is not None
                                       and l_after > l_before)
              else (run["after"] - run["before"]) / 100.0 * tank)
    db.execute(
        "INSERT INTO fuel_detected (vehicle_id, ts, ts_from, liters, fuel_before_pct, "
        "fuel_after_pct, status, created_at) VALUES (?,?,?,?,?,?,'pending',?)",
        (vid, ts, ts_from, round(liters, 2), run["before"], run["after"],
         datetime.now(timezone.utc).isoformat()))
    return 1


def _iso_shift(ts: str, hours: float) -> str:
    """`ts` moved by `hours`, as a stored-format ISO string — for the dedup window around a rise."""
    try:
        return (datetime.fromisoformat(str(ts)) + timedelta(hours=hours)).isoformat()
    except (ValueError, TypeError):
        return str(ts)


def _fill_is_capped(fuel_after_pct) -> bool:
    """True when a fill ended with the gauge reading FULL — so its litres are a lower bound.

    The car's litre counter is its own percentage scaled by a per-model constant, and both stop at
    the top in the same frame (100.0 % / 47 500 mL on a C10). Fuel that goes in above that is simply
    not counted: @pdifeo's pump gave 10.51 L into 9.204 L of nominal room (beta #21). Read off the
    PERCENTAGE, not the litres, so this holds on any model without knowing its ceiling — and so it
    also covers the rows written before v2.14.1, which carry no litres at all.

    Derived on read rather than stored: `fuel_after_pct` is already on every row, so the detections
    sitting pending right now start telling the truth as soon as this ships, with no migration."""
    return fuel_after_pct is not None and fuel_after_pct >= 100


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
        return [{**dict(r), "capped": _fill_is_capped(r["fuel_after_pct"])} for r in rows]
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


def latest_fuel_liters(vehicle_id: Optional[int] = None) -> Optional[float]:
    """Most recent litres the car itself counted (signal 3263) — None before v2.14.1 and on a BEV,
    where the caller falls back to the tank % times the model's capacity."""
    try:
        db = _get()
        r = db.execute(
            "SELECT fuel_liters FROM positions WHERE vehicle_id = COALESCE(?, vehicle_id) "
            "AND fuel_liters IS NOT NULL ORDER BY recorded_at DESC LIMIT 1",
            (vehicle_id if vehicle_id is not None else _current_vehicle_id(),)).fetchone()
        return r["fuel_liters"] if r else None
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

# Twin of poller/client._MERIDIAN_NEAR_DEG (#232) — the two processes must not disagree about
# where the car is, so test_gps_sign_survives_a_bad_poll.py asserts they stay equal.
_MERIDIAN_NEAR_DEG = 1.0


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
    try:
        sign = float(get_setting(f"gps_{axis}_sign", "0") or 0)
    except (TypeError, ValueError):
        sign = 0.0
    known = -1.0 if sign < 0 else (1.0 if sign > 0 else 0.0)     # 0.0 = never learned one

    s = _f(signals.get(signed_id))
    if s != 0.0:
        # Authoritative — it carries its own sign. But a frame that LOST that sign is
        # indistinguishable from a genuine one (#232), and this copy runs once, on a button: it
        # cannot count polls the way the poller does, so it never overrules a hemisphere it
        # already knows unless the car is at the line. The poller stays the only process that
        # learns; when it does, the setting changes and this follows.
        # One-directional, like the poller: a dropped sign can only ever arrive POSITIVE, since the
        # signals it gets confused with are magnitudes and have no minus to lose. So the only
        # reading worth doubting is a positive one, far from the line, on a car we remember as
        # west/south — every other combination proves itself and passes straight through.
        if known == -1.0 and s > _MERIDIAN_NEAR_DEG:
            return -s
        return s
    u = next((v for v in (_f(signals.get(i)) for i in unsigned_ids) if v != 0.0), 0.0)
    if u == 0.0:
        return 0.0
    return abs(u) * (known or 1.0)


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
        # connection). 4 (charge postponed to the programmed window) is deliberately NOT here: see
        # the poller's _is_charging — excluding it would drop a scheduled charge whole if the car
        # keeps reporting 4 once its window opens. Kept identical so both readers of 1149 agree.
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
            # 4 is the cable connected with the charge DEFERRED to its programmed window — the
            # state that blanked the cable on the Overview (#243), since it fell into "unplugged"
            # by exclusion. Charging is judged separately, above: 4 is plugged and NOT charging.
            return int(conn) in (1, 2, 3, 4)
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
    if is_reev_car():
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


# Two different questions used to share one number. They are not the same question.
#
# _READY_BLIP_S — how short a ready=0 gap has to be before we call it a signal glitch rather than
# the driver switching the car off. It was 90 s, on the strength of "signal blips seen in the log".
# Measured afterwards on eight bundles from three owners (beta #19): of 123 distinct READY
# switch-offs in three weeks, only THREE are under 90 s — 38.8 s, 72.0 s and 89.8 s — and the 72 s
# one is @michapr's real power-off, which Mate swallowed and then told him the car had never been
# switched off. The blips the number defended against amount to that single 38.8 s event, so 60 s
# still absorbs it and stops eating everything else.
_READY_BLIP_S = 60
# _READY_MATCH_SLACK_S — how far a session may miss the trip it belongs to and still be matched to
# it. Nothing to do with blips: it absorbs the ~1 minute the gear-P trip-end lags behind ready-off
# (PARKED_CONFIRM = 6 polls). Lowering it with the other one would have left it exactly equal to
# the lag it exists to cover.
_READY_MATCH_SLACK_S = 90
# How long a READY value may be carried forward over polls that didn't report one. The floor is
# above the widest parked cadence the settings allow (10–600 s), so even the slowest poller keeps
# a value across a missed reading; the 3× term keeps that margin at three polls when the user has
# widened the interval. Beyond it the value expires — see ready_session for what that buys.
_READY_CARRY_MIN_S = 900


def _parked_poll_seconds() -> int:
    """The user's parked poll interval, clamped to the same 10–600 s the settings form allows so a
    hand-edited row can't stretch the carry window without limit."""
    try:
        return max(10, min(int(float(get_setting("poll_parked", "30") or 30)), 600))
    except (TypeError, ValueError):
        return 30
_READY_LOOKBACK_S = 6 * 3600  # how far around the trip to scan positions for the session bounds


def ready_session(trip: dict):
    """Reconstruct the car's power-on session (READY/ON3, PID 1258) that brackets this trip, from the
    per-poll `positions.ready` log. The cloud's getEC session runs from Ready-ON to power-OFF and can
    span SEVERAL Mate trips + idle (verified 22/06: trips 133+134 = one session) → this is the REAL
    getEC window AND tells us whether a trip shares its session with others.

    Returns {on, off, n_trips, trip_ids} (epoch seconds) or None when no ready data covers the trip
    (old trips before the signal existed → caller falls back to the T0−2min window). Brief ready=0
    dips shorter than _READY_BLIP_S are treated as still-on (blips)."""
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
    # Carry a known value forward across polls that didn't report one — but only for a while. The
    # carry-forward is meant to bridge a missed poll or two; on a car that reports READY rarely it
    # was instead becoming the only source of truth, and one ready=1 kept meaning "still on" for
    # hours, straight across a real power-off. Measured: on a BEV, 89.8% of position rows carry a
    # READY value and 99.9% of consecutive samples are ONE poll apart, so expiry never fires there;
    # on michapr's REEV (beta #19) the signal arrives in ~0.8% of frames and effectively never as a
    # zero, so the carry ran for hours and two separate drives were reported as one power-on —
    # which told him to MERGE trips that must stay apart.
    #
    # Past the window the sample becomes None, not 0: "we no longer know" is the truth, and claiming
    # "off" would be the same overreach in the other direction. None ends the ready=1 run (anything
    # that isn't 1 does) without being picked up as an observed zero by the on_lo bracket below.
    carry_max = max(_READY_CARRY_MIN_S, 3 * _parked_poll_seconds())
    samples, last, last_e = [], None, None
    for r in rows:
        e = _trip_epoch(r["recorded_at"])
        if e is None:
            continue
        rd = r["ready"]
        if rd is None:
            rd = last if (last is not None and last_e is not None
                          and e - last_e <= carry_max) else None
        else:
            last, last_e = rd, e
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
        if merged and run[0] - merged[-1][1] < _READY_BLIP_S:
            merged[-1][1] = run[1]
        else:
            merged.append(list(run))
    # The session = the run that brackets the trip (small slack: the gear-P trip-end lags ready-off
    # by ~1 min, and ready-on can sit a poll after T0).
    sess = next(((s, e) for s, e in merged
                 if s - _READY_MATCH_SLACK_S <= t0 and t1 <= e + _READY_MATCH_SLACK_S), None)
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
    olo = datetime.fromtimestamp(on - _READY_MATCH_SLACK_S, timezone.utc).isoformat()
    ohi = datetime.fromtimestamp(off + _READY_MATCH_SLACK_S, timezone.utc).isoformat()
    trs = db.execute(
        "SELECT id, started_at, ended_at FROM trips WHERE vehicle_id = COALESCE(?, vehicle_id) "
        "AND merged_into_id IS NULL "
        "AND ended_at IS NOT NULL AND ended_at >= ? AND started_at <= ? ORDER BY started_at",
        (_current_vehicle_id(), olo, ohi)).fetchall()
    ids = []
    for tr in trs:
        ts0, ts1 = _trip_epoch(tr["started_at"]), _trip_epoch(tr["ended_at"])
        if ts0 and ts1 and ts0 >= on - _READY_MATCH_SLACK_S and ts1 <= off + _READY_MATCH_SLACK_S:
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
        "action TEXT, outcome TEXT NOT NULL, latency_ms INTEGER, vin TEXT)")
    # The car was never recorded at all, so with two of them one badge answered for both: the
    # timeouts of the car in the garage pulled down the score of the one parked outside, and the
    # figure stayed plausible.
    cols = {r[1] for r in db.execute("PRAGMA table_info(command_log)").fetchall()}
    if "vin" not in cols:
        db.execute("ALTER TABLE command_log ADD COLUMN vin TEXT")
    _recover_anonymous_commands(db)


_COMMAND_LOG_BACKFILL_KEY = "command_log_vin_backfilled"


def _recover_anonymous_commands(db: sqlite3.Connection) -> None:
    """Give the pre-v3.13.0 commands back to the car they were sent to — once, if it is knowable.

    Adding the vin column left every earlier row without a car, and the badge counts only rows that
    carry one: a working install lost up to 90 days of history in a single upgrade and printed a
    dash until three fresh commands rebuilt it.

    It reads the STATE, not the moment the column appeared. Hanging the recovery off that ALTER
    would only ever reach installs still on 3.12.0 — the ones that already took 3.13.0 have the
    column since that upgrade, and their dash would never lift.

    The count of cars decides. With ONE car there is nothing to guess: every command ever sent went
    to it. With two or more the rows belong to nobody and stay NULL — attributing them to the oldest
    car, or the selected one, or any car at all, would judge one car's coverage on another's diary,
    which is the defect v3.13.0 just closed. With none registered yet nothing is decided: the
    question is asked again next time rather than answered at random."""
    try:
        done = db.execute("SELECT value FROM settings WHERE key = ?",
                          (_COMMAND_LOG_BACKFILL_KEY,)).fetchone()
        if done and done[0] == "1":
            return
        cars = db.execute("SELECT vin FROM vehicles WHERE vin IS NOT NULL AND vin != ''").fetchall()
    except sqlite3.Error:
        return          # minimal schema, no settings/vehicles: skip the recovery, never raise
    if not cars:
        return
    if len(cars) == 1:
        db.execute("UPDATE command_log SET vin = ? WHERE vin IS NULL", (cars[0][0],))
    # `_conn_rw()` hands out a fresh connection every call and nobody closes it, so an uncommitted
    # write dies with it: without this the badge was right exactly once — the same connection could
    # see its own pending write — and every later page load found the work undone.
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, '1')",
               (_COMMAND_LOG_BACKFILL_KEY,))
    db.commit()


def log_command(action: str, outcome: str, latency_ms: Optional[int] = None,
                vin: str = "") -> None:
    """Record one remote-command outcome (confirmed|timeout_car|cloud_unreachable|rejected) FOR ONE
    CAR. Best-effort: never raises into the command path. Keeps ~90 days.

    An empty vin writes an anonymous row rather than refusing: the command must never fail because
    of its own diary. Anonymous rows are then left out of the badge — see command_responsiveness."""
    try:
        db = _conn_rw()
        _ensure_command_log(db)
        db.execute(
            "INSERT INTO command_log (ts, action, outcome, latency_ms, vin) VALUES (?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), action, outcome, latency_ms,
             (vin or "").strip() or None))
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
        # Only THIS car's commands, and only the rows that carry a car: an anonymous row (written
        # before the column existed, or by a path with no VIN) belongs to nobody, and counting it
        # would judge one car on another's coverage.
        rows = db.execute(
            "SELECT outcome, latency_ms FROM command_log "
            "WHERE outcome IN ('confirmed','timeout_car') AND vin IS NOT NULL "
            "AND lower(vin) = lower(?) ORDER BY id DESC LIMIT ?",
            (_selected_vin(), last_n)).fetchall()
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


def _charges_have_merge(db) -> bool:
    """Whether the charges table carries the merge column yet.

    Same rule as _charges_have_gross, and the same reason: the migration lives in the POLLER, the
    web serves the same file and never alters it, so between an update and the poller's next start
    the column is simply absent — and a query naming it is a 500 on the Charges page, not a missing
    figure. Asked per call, never cached: the poller can add it while the web is running."""
    try:
        return any(r[1] == "merged_into_id" for r in db.execute("PRAGMA table_info(charges)"))
    except sqlite3.Error:
        return False


def _charge_children_by_parent(db) -> dict:
    """All merged child charges grouped by parent id (one query). Twin of _children_by_parent.
    Empty on a database the poller has not migrated yet — nothing is merged there by definition."""
    if not _charges_have_merge(db):
        return {}
    out: dict = {}
    for r in db.execute("SELECT * FROM charges WHERE vehicle_id = COALESCE(?, vehicle_id) "
                        "AND merged_into_id IS NOT NULL", (_current_vehicle_id(),)).fetchall():
        out.setdefault(r["merged_into_id"], []).append(dict(r))
    return out


def _charge_group_stats(parent: dict, children: list) -> dict:
    """Parent dict enriched with the combined figures of [parent + children] — one plug-in the car
    reported as several rows, read back as the single session it was.

    Pure display math: the stored rows are never touched, which is what makes the merge reversible.
    The merge guards promise no other charge and no driving inside the gaps, so the group's SoC span
    is the real one — that is the figure the state-of-health divides by, and the reason a split
    charge lies to it (partial energy over partial ΔSoC).

    ⚠️ `duration_min` is END MINUS START, so the pause inside the group counts. That is the OPPOSITE
    of _trip_group_stats, which SUMS its segments — and deliberately so: a merged trip's duration is
    time spent driving, while a charge's is the window the user sees on the page ("18:00 → 18:38").
    It does inflate the average duration on the Charges page, which already mixes reconstructed
    sessions into that same average; this rides on that open defect rather than adding a second one
    in silence.

    ⚠️ Nothing is rounded here. Summing three pieces and rounding would invent a precision the
    figures do not have, and Mate shows measured values at full precision.
    """
    d = dict(parent)
    d["merged_count"] = 1 + len(children)
    d["is_merged"] = bool(children)
    if not children:
        return d
    pieces = sorted([parent, *children], key=lambda c: c.get("started_at") or "")
    first, last = pieces[0], pieces[-1]
    d["started_at"], d["start_soc"] = first.get("started_at"), first.get("start_soc")
    d["ended_at"], d["end_soc"] = last.get("ended_at"), last.get("end_soc")
    # A figure NOBODY reported stays missing: gross_kwh is typed by the owner, and summing None as 0
    # would turn "never entered" into a perfectly credible zero. → [[signal-absent-is-not-signal-zero]]
    for f in ("energy_added_kwh", "cost", "ac_energy_kwh", "gross_kwh", "wb_stuck_kwh"):
        vals = [c[f] for c in pieces if c.get(f) is not None]
        d[f] = sum(vals) if vals else None
    peaks = [c["max_power_kw"] for c in pieces if c.get("max_power_kw") is not None]
    d["max_power_kw"] = max(peaks) if peaks else None
    # The type follows the piece that carried the most energy: a DC stop inside an AC night must not
    # make the group read as AC, nor the other way round.
    d["charge_type"] = max(pieces, key=lambda c: c.get("energy_added_kwh") or 0).get("charge_type")
    d["duration_min"] = _minutes_between(d["started_at"], d["ended_at"])
    d["child_ids"] = [c["id"] for c in children]
    return d


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
    # Fuel spans the group exactly like SoC does — first segment's start, last segment's end (beta
    # #20, @michapr). It used to be left on the parent row alone, and merge_trips makes the EARLIER
    # trip the parent: merging a short electric hop with the long generator-on drive that followed it
    # took the hop's flat tank as the whole group's, so the litres vanished and the trip's cost fell
    # from 7.53 € to 0.50 € — the petrol simply stopped being counted. Taken from the first and last
    # segment that actually HAS a reading rather than blindly first/last, since a segment can carry
    # none (a BEV, or a trip recorded before the signal was read).
    for _key, _pick in (("fuel_start_pct", segs), ("fuel_start_l", segs),
                        ("fuel_end_pct", segs[::-1]), ("fuel_end_l", segs[::-1])):
        d[_key] = next((s[_key] for s in _pick if s.get(_key) is not None), None)
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


def get_merge_candidates(gap_min: int = TRIP_MERGE_GAP_DEFAULT, day=None) -> list[dict]:
    """Mergeable pairs (get_mergeable_pairs) hydrated with full trip_row.html-ready data —
    the day drawer's 🔗 view. Previously these surfaced as inline connectors between adjacent
    rows in the full year/month/day accordion, then as one flat all-history list when the
    calendar replaced it; that list carried no date at all, so 22 pairs came back as bare
    clock times and two of them (17:52 and 17:53, weeks apart) sat four rows from each other
    — #204 @riri19. The drawer already prints the date as its heading, so the pairs moved
    back under it.

    `day` (a date) scopes them to ONE calendar day, which is what the drawer asks for; None
    keeps every pair. A pair is anchored to the EARLIER trip's local day, so one straddling
    midnight appears on the day the merged trip would start — the merged trip takes the
    parent's date, so that's the day it will end up on. Measured on 302 real trips: 22 pairs
    at the default gap, 160 at the widest, none straddling midnight — the anchor decides a
    case that so far only exists in theory, but it has to decide it. Most-recent-first."""
    pairs = get_mergeable_pairs(gap_min)
    if not pairs:
        return []
    trips_by_id = {t["id"]: t for t in _localized_trips(get_trips(limit=1_000_000))}
    out = []
    for p in pairs:
        a, b = trips_by_id.get(p["a_id"]), trips_by_id.get(p["b_id"])
        if not (a and b):
            continue
        # `_dt` is the same localized field get_trips_calendar_day buckets on, so the pairs and
        # the list under them can never disagree about which day a trip belongs to — near
        # midnight that's the whole ballgame. (started_at is localized by now too, so slicing it
        # would land on the same day; _dt is just the date itself instead of a string prefix.)
        if day is not None and a["_dt"].date() != day:
            continue
        out.append({"a": a, "b": b, "gap_min": p["gap_min"]})
    out.sort(key=lambda p: p["b"]["started_at"], reverse=True)
    return out


def get_merge_chains(gap_min: int = TRIP_MERGE_GAP_DEFAULT, day=None) -> list[list[dict]]:
    """The same proposals as get_merge_candidates, but as CHAINS: each trip once, with the
    connector to the next one on it. `[[{"trip": …, "link": {"b_id", "gap_min"} | None}, …], …]`.

    Pairs were what the view drew, and pairs overlap: a trip between two others is the second of
    one and the first of the next, so it was drawn twice. On @riri19's own fortnight (#249) at the
    90-minute stop he had chosen: 23 pairs, **46 cards for 34 trips**, 12 of them repeated — and
    his 14 August became ten cards under a heading reading "6 trips".

    Nothing changes underneath: a merge is still between two adjacent trips, and each connector
    carries exactly its own two ids. Chains keep the pairs' order (most recent first); inside a
    chain the trips run in the order they were driven, which is the order the pair drew them in.
    """
    pairs = get_merge_candidates(gap_min, day=day)
    if not pairs:
        return []
    # `pairs` is most-recent-first; walking it oldest-first makes the chains build forwards, and
    # `next_of` says which trip follows which. A trip can start at most one chain and continue at
    # most one, because the pairs come from adjacent trips in a single ordered pass.
    ordered = list(reversed(pairs))
    next_of = {p["a"]["id"]: p for p in ordered}
    continues = {p["b"]["id"] for p in ordered}
    chains = []
    for p in ordered:
        if p["a"]["id"] in continues:
            continue                                   # not a head — some other pair leads here
        chain, cur = [], p
        while cur is not None:
            chain.append({"trip": cur["a"],
                          "link": {"b_id": cur["b"]["id"], "gap_min": cur["gap_min"]}})
            nxt = next_of.get(cur["b"]["id"])
            if nxt is None:
                chain.append({"trip": cur["b"], "link": None})
            cur = nxt
        chains.append(chain)
    chains.reverse()                                   # back to most-recent-first
    return chains


def merge_trips(parent_id: int, child_id: int, gap_min: int = TRIP_MERGE_GAP_DEFAULT) -> dict:
    """Merge child into parent (the earlier of the two becomes the parent). Re-validates the
    eligibility server-side. Reversible: only sets merged_into_id, nothing is overwritten."""
    db = _conn_rw()
    a = db.execute("SELECT * FROM trips WHERE id=? AND merged_into_id IS NULL", (parent_id,)).fetchone()
    b = db.execute("SELECT * FROM trips WHERE id=? AND merged_into_id IS NULL", (child_id,)).fetchone()
    if not a or not b:
        return {"ok": False, "error": "not_found_or_already_merged"}
    # Two ids, and nothing checked they were the same CAR (#186): merging one car's drive into the
    # other's puts kilometres and energy inside a trip that never made them — and a merge writes
    # only the marker, so it looks perfectly tidy. → [[merged-trip-child-keeps-its-own-data]]
    if a["vehicle_id"] != b["vehicle_id"]:
        return {"ok": False, "error": "different_car"}
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


# How far apart two rows may sit and still be one plug-in.
#
# Measured on a real 29-pair history rather than picked: the gaps are bimodal. The splits sit at
# 1.0, 1.0, 1.2, 1.5 and 3.0 minutes — four of them inside the one night that came back as six rows
# (#56 to #61) — while genuinely separate charges start at 30 minutes and up. Exactly one pair lands
# between, at 17.6 minutes, and that is the kind of call the person who was there should make.
#
# So 30 covers every split ever measured with room to spare, and it is not what protects anyway:
# the charge-in-the-gap and drove-in-the-gap guards below are. A tighter window would only refuse
# honest merges after a long pause.
CHARGE_MERGE_GAP_DEFAULT = 30
# A real pause leaves the SoC where it was, give or take the tenth of a point the car rounds to.
# A fall bigger than this means the car went somewhere between the two rows.
_CHARGE_MERGE_SOC_TOLERANCE = 2.0


def merge_charges(parent_id: int, child_id: int, gap_min: int = CHARGE_MERGE_GAP_DEFAULT) -> dict:
    """Join two charge rows the car split by declaring the cable gone on a pause.

    Reversible: only merged_into_id is written, so unmerge brings the originals back untouched.
    That is the whole reason this exists instead of a grace window in the poller — a window would
    have to GUESS how long a real pause lasts, and a closed charge is never recomputed, so a wrong
    guess would be silent and permanent. Here the user decides, and can undo.

    Which makes the guards the important part: a merge moves kilowatt-hours and euros into a row
    that did not make them, and leaves everything looking perfectly tidy. Refused when the two rows
    are not the same car, when either is still running or already merged, when they are further
    apart than gap_min, when ANOTHER charge sits between them, or when the car drove in the gap —
    a trip overlapping it, or a SoC that FELL (the mirror of the trips rule, where a SoC that ROSE
    means a charge in the gap).
    """
    db = _conn_rw()
    a = db.execute("SELECT * FROM charges WHERE id=? AND merged_into_id IS NULL "
                   "AND ended_at IS NOT NULL", (parent_id,)).fetchone()
    b = db.execute("SELECT * FROM charges WHERE id=? AND merged_into_id IS NULL "
                   "AND ended_at IS NOT NULL", (child_id,)).fetchone()
    if not a or not b:
        return {"ok": False, "error": "not_found_or_already_merged"}
    if a["vehicle_id"] != b["vehicle_id"]:
        return {"ok": False, "error": "different_car"}
    a, b = dict(a), dict(b)
    if (a.get("started_at") or "") > (b.get("started_at") or ""):
        a, b = b, a                                   # parent = the earlier row
    kids = _charge_children_by_parent(db)
    a_grp = _charge_group_stats(a, kids.get(a["id"], []))
    gap = _gap_minutes(a_grp.get("ended_at"), b.get("started_at"))
    if gap is None or gap < 0 or gap >= gap_min:
        return {"ok": False, "error": "gap_too_large"}
    group_ids = [a["id"], b["id"], *(c["id"] for c in kids.get(a["id"], [])),
                 *(c["id"] for c in kids.get(b["id"], []))]
    holes = ",".join("?" * len(group_ids))
    other = db.execute(
        f"SELECT 1 FROM charges WHERE vehicle_id=? AND ended_at IS NOT NULL "
        f"AND started_at > ? AND started_at < ? AND id NOT IN ({holes}) LIMIT 1",
        (a["vehicle_id"], a_grp["ended_at"], b["started_at"], *group_ids)).fetchone()
    if other:
        return {"ok": False, "error": "charge_in_gap"}
    drove = db.execute(
        "SELECT 1 FROM trips WHERE vehicle_id=? AND started_at <= ? "
        "AND COALESCE(ended_at, started_at) >= ? LIMIT 1",
        (a["vehicle_id"], b["started_at"], a_grp["ended_at"])).fetchone()
    if drove:
        return {"ok": False, "error": "drove_in_gap"}
    if (a_grp.get("end_soc") is not None and b.get("start_soc") is not None
            and b["start_soc"] < a_grp["end_soc"] - _CHARGE_MERGE_SOC_TOLERANCE):
        return {"ok": False, "error": "drove_in_gap"}
    # absorb B and any of B's own children into A, so every piece points at the parent
    db.execute("UPDATE charges SET merged_into_id=? WHERE id=? OR merged_into_id=?",
               (a["id"], b["id"], b["id"]))
    db.commit()
    return {"ok": True, "parent_id": a["id"]}


def unmerge_charges(parent_id: int) -> dict:
    """Split a merged charge back into the rows the car reported. Nothing was ever overwritten,
    so they come back exactly as they were — including the split figures."""
    db = _conn_rw()
    cur = db.execute("UPDATE charges SET merged_into_id=NULL WHERE merged_into_id=?", (parent_id,))
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
    # Built ONCE for the whole list — the fuel twin of the electric rate timeline. Per trip it would
    # replay every refuel from the beginning, which is quadratic down a long list.
    _fuel_rate_at = _trip_fuel_rate_fn()
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
        td.update(_reev_trip_fuel(_fs, _fe, td.get("distance_km"), _eng,
                                  td.get("fuel_start_l"), td.get("fuel_end_l")))
        # …and what those litres COST, the same allocation the detail page makes: litres × the
        # tank's blended €/L at the trip's start. Without it the trips reached the day and month
        # totals carrying petrol nobody could price, and those totals showed the electric half of a
        # range-extender's bill — 0.08 € on a day that burned 8.3 L (@michapr, beta #11). The rate
        # timeline is built once per call, not per trip.
        td["fuel_cost"] = None
        if td.get("fuel_used_l"):
            _fp = _fuel_rate_at(r["vehicle_id"], r["started_at"])
            if _fp and _fp > 0:
                td["fuel_cost"] = round(td["fuel_used_l"] * _fp, 2)
        # …and the ELECTRIC counterpart, the same call the detail page makes. Without it the list
        # can only ever show one of the two energies: a generator trip has its efficiency blanked on
        # purpose (finalize_trip), so the ⚡ pill has nothing to print and only the ⛽ line survives —
        # "only one will be shown" (@michapr, beta #11). ec_driving is a stored column, so this
        # costs a dict lookup, not a cloud call.
        td.update(_reev_trip_elec(td.get("ec_kwh"), td.get("distance_km"), td.get("engine_ran")))
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
        # EVERY finished trip, not only the ones whose tank dropped (@michapr, beta #26). The
        # denominator was moved onto the whole distance in v3.6.9, but this query still filtered the
        # rows to generator trips first, so `total_km` could never see an electric kilometre: on his
        # data it read 5.85 L/100 km — litres over the 164 km of his 6 generator trips — where the
        # true figure over all 479 km is 2.00, and his own car's cloud reports 2.9.
        # 🔑 The denominator was corrected; the set of rows it sums over was not.
        rows = db.execute(
            "SELECT id, vehicle_id, started_at, ended_at, distance_km, fuel_start_pct, fuel_end_pct, "
            "fuel_start_l, fuel_end_l "
            "FROM trips WHERE vehicle_id = COALESCE(?, vehicle_id) AND ended_at IS NOT NULL",
            (_current_vehicle_id(),)).fetchall()
    except sqlite3.Error:
        return None
    tank = reev_tank_l()
    # €/L blended over time, built ONCE — the same lookup the trips list and the calendar use, so
    # the cost card cannot disagree with them (beta #25: the card said 116 €, the Trips page 18.54 €
    # for the same 9.6 litres, because the card was summing the PURCHASES).
    _fuel_rate_at = _trip_fuel_rate_fn()
    total_l, engine_km, engine_l, n = 0.0, 0.0, 0.0, 0
    total_cost = 0.0
    total_km = 0.0        # EVERY kilometre driven — the L/100 km denominator, as the car's own is
    for r in rows:
        eng = _reev_engine_on(db, r["vehicle_id"], r["started_at"], r["ended_at"])
        # Through _reev_trip_fuel, like the trips list and the period card. It used to work the
        # litres out again right here — a third copy of a rule that had just been corrected in one
        # place, which is how this total stayed on the old answer after v3.6.6.
        f = _reev_trip_fuel(r["fuel_start_pct"], r["fuel_end_pct"], r["distance_km"], eng,
                            r["fuel_start_l"], r["fuel_end_l"])
        drop_l = f["fuel_used_l"]
        # ⚠️ The distance is added FIRST and unconditionally: a trip driven on the battery burned no
        # petrol but was still driven, and it is exactly what the L/100 km has to be spread over.
        # The `continue` below used to sit above this line, which is the whole defect.
        total_km += r["distance_km"] or 0
        if not drop_l:
            continue                      # nothing burned (or under both floors) — but the km count
        total_l += drop_l
        rate = _fuel_rate_at(r["vehicle_id"], r["started_at"])
        if rate:
            total_cost += drop_l * rate
        if eng:
            engine_km += eng["engine_km"]
            engine_l += eng["engine_fuel_pct"] / 100.0 * tank
        else:  # positions pruned → fall back to the whole-trip distance + full drop
            engine_km += r["distance_km"] or 0
            engine_l += drop_l
        n += 1
    if not n:
        return None
    return {
        "engine_trips": n,
        "total_l": round(total_l, 1),
        "engine_km": round(engine_km, 1),
        # Over ALL the kilometres, not the generator-on ones — same basis as the car's own figure
        # and as every other L/100 km in Mate since beta #23. `engine_km` above still says how far
        # the generator drove; it is reported, not divided by.
        "avg_l_100km": round(total_l / total_km * 100, 1) if total_km > 0.5 else None,
        # …except HERE, and only here: what the generator itself drinks while it is running
        # (@michapr, beta #26 — «certainly an interesting technical metric»). On his history that
        # is 15.2 against the 2.0 above, SEVEN TIMES apart under the same unit on the same page —
        # so whatever shows it has to say *while running* right next to the number.
        # 🔑 `total_l`, not the `engine_l` this loop also has: `engine_l` is engine_fuel_pct against
        # a NOMINAL tank, while `total_l` comes off the car's own millilitre counter wherever the
        # trip carries it. Measured litres over measured distance, or the pair means nothing.
        "engine_l_100km": round(total_l / engine_km * 100, 1) if engine_km > 0.5 else None,
        # What that petrol COST — litres burned × the blended €/L of the tank at the time, which is
        # exactly what the Trips page charges per trip. NOT the sum of the refuels: a tank you paid
        # for is mostly still in the tank (beta #25 — 60 L bought, 9.6 burned, and the card was
        # billing all 60 against 479 km, i.e. 12 €/litre). `reev_actual_spend` keeps summing the
        # purchases and is right to: that card answers "what did you buy".
        "total_cost": round(total_cost, 2) if total_cost else None,
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
                      -- Litres straight off the car's own counter (3263) where the trip carries it;
                      -- the tank-% × capacity below is the fallback for trips predating v2.14.1.
                      SUM(CASE WHEN fuel_start_l IS NOT NULL AND fuel_end_l IS NOT NULL
                                AND fuel_start_l > fuel_end_l
                               THEN fuel_start_l - fuel_end_l
                               WHEN fuel_start_pct IS NOT NULL AND fuel_end_pct IS NOT NULL
                                AND fuel_start_pct > fuel_end_pct
                               THEN (fuel_start_pct - fuel_end_pct) / 100.0 * ? END) AS litres
                 FROM trips
                WHERE vehicle_id = COALESCE(?, vehicle_id) AND ended_at IS NOT NULL""",
            (get_battery_capacity_kwh(), reev_tank_l(), _current_vehicle_id())).fetchone()
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


_BREAKEVEN_THIN_KM = 100.0   # a side resting on less than this is shown, but declared as thin


def reev_breakeven_kwh_price() -> Optional[dict]:
    """REEV — above which €/kWh does charging stop being cheaper than the generator?

    @ebagnoli's question (beta #13): *«dovrebbe apparire da qualche parte il costo al KWh in Euro
    dell'elettricità alla colonnina affinché la ricarica elettrica risulti conveniente rispetto alla
    benzina»*. He charges at home off solar surplus, so his own answer is "always" — the number he
    wants is for standing in front of a public column with a tank in the car.

        break-even €/kWh = (€/L × L/100km WITH THE GENERATOR) ÷ (kWh/100km DRIVING ELECTRIC)

    🔴 The two rates are each measured on their OWN kilometres, and that is the whole difficulty.
    `reev_total_consumption` already publishes a kWh/100km and an L/100km — both divided by the
    WHOLE distance, which is exactly right for "what did the driving cost me" and exactly wrong
    here. Reusing them would drag the petrol rate down by every electric kilometre and answer a
    question nobody asked, under numbers that look like the right ones.
    → [[feedback-two-numbers-one-word]]

    The electric side comes from trips the generator sat out: on a generator trip the pack is being
    refilled underneath, so `ec_kwh` there is not a description of electric driving
    → [[reev-getec-is-battery-not-traction]]. The petrol side is `reev_fuel_summary`'s engine-on
    figure, already measured over the distance the generator actually drove (@michapr, beta #26).
    The price is the blend of HIS OWN refuels — never a pump price we made up.

    Returns None rather than a guess when a half is missing: no generator kilometres, no refuel, no
    electric trips. And when a side rests on less than `_BREAKEVEN_THIN_KM`, the answer still comes
    but carries `thin` — on @ebagnoli's own history the petrol half is **46 km, one trip**, and a
    bare number would hide that. → [[feedback-verified-vs-inferred]]
    """
    if not is_reev_car():
        return None
    fuel = reev_fuel_summary()
    if not fuel or not fuel.get("engine_l_100km") or not fuel.get("engine_km"):
        return None                                  # the generator has never driven: nothing to compare

    db = _get()
    rows = db.execute(
        "SELECT id, started_at, distance_km, ec_kwh, fuel_start_l, fuel_end_l,"
        "       fuel_start_pct, fuel_end_pct FROM trips "
        "WHERE vehicle_id = COALESCE(?, vehicle_id) AND ended_at IS NOT NULL "
        "AND merged_into_id IS NULL AND ec_kwh IS NOT NULL AND distance_km > 0",
        (_current_vehicle_id(),)).fetchall()
    km = kwh = 0.0
    trips = 0
    for r in rows:
        # Engine-on is read off the TANK, the same test _reev_engine_on applies: a level that fell
        # during the trip means the generator ran, whichever column carries it.
        burned = ((r["fuel_start_l"] or 0) - (r["fuel_end_l"] or 0)) if r["fuel_start_l"] is not None else \
                 ((r["fuel_start_pct"] or 0) - (r["fuel_end_pct"] or 0))
        if burned > 0:
            continue                                 # generator trip → its kWh is not electric driving
        km += r["distance_km"] or 0
        kwh += r["ec_kwh"] or 0
        trips += 1
    if km <= 0.5 or kwh <= 0:
        return None

    price = fuel_blended_price_at(_current_vehicle_id() or 1, datetime.now(timezone.utc).isoformat())
    if not price:
        return None                                  # no refuel of his own → no euro to divide

    elec_100 = kwh / km * 100
    fuel_100 = fuel["engine_l_100km"]
    thin = ("fuel" if fuel["engine_km"] < _BREAKEVEN_THIN_KM
            else "elec" if km < _BREAKEVEN_THIN_KM else None)
    return {
        "breakeven_kwh": round(price * fuel_100 / elec_100, 3),
        "elec_kwh_100km": round(elec_100, 1),
        "elec_km": round(km, 1),
        "elec_trips": trips,
        "fuel_l_100km": round(fuel_100, 1),
        "fuel_km": fuel["engine_km"],
        "fuel_price_l": round(price, 3),
        "petrol_100km": round(price * fuel_100, 2),   # what those 100 km cost on petrol
        "thin": thin,
        "thin_km": _BREAKEVEN_THIN_KM,
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
            "SELECT energy_added_kwh, ac_energy_kwh, location_type, cost"
            + (", gross_kwh" if _charges_have_gross(db) else "") + " FROM charges "
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


def _priced_euros(db, vid, where: str = "", params: tuple = ()) -> float:
    """The euros of the closed, PRICED charges matching `where`. Same definition of "priced" as
    everywhere else — `cost IS NOT NULL`, and zero is a price (#218)."""
    try:
        r = db.execute(
            "SELECT COALESCE(SUM(cost), 0) AS e FROM charges "
            "WHERE vehicle_id = COALESCE(?, vehicle_id) AND ended_at IS NOT NULL "
            "AND cost IS NOT NULL AND cost >= 0" + where, (vid, *params)).fetchone()
        return float(r["e"] or 0.0)
    except sqlite3.Error:
        return 0.0


def _km_basis(db, allow_odometer: bool = True) -> Optional[dict]:
    """WHICH kilometres the cost card divides by, and WHICH euros belong to them (#237).

    The rule this whole function exists to enforce, in one line:

        🔑 **the numerator and the denominator must come out of the same stretch of time.**

    Before it, they did not. The euros were summed over the entire archive while the kilometres
    came from the recorded trips alone, so @nico89612 — who typed in 152 charges from before he
    installed Mate — read **4838.43 €/100 km**: months of spending over the 46 km of a single
    afternoon. Nothing about that number was a rounding error; the two halves described different
    years. The same card's OTHER half, the kWh/100 km, was already windowed correctly, which is how
    the split was found: his 19.2 kWh/100 km reproduces to the decimal.

    Two bases, and the one that prices MORE of what he actually spent wins:

    · **trips** — Mate's own reconstructed distance, with the euros starting where the kilometres
      start. A charge that ended before the first recorded trip has no kilometres to be divided by
      and contributes nothing. The RIGHT edge is deliberately left open: a charge made after the
      last trip will get its kilometres tomorrow, and excluding it would make the figure jitter
      day to day for everyone — the same "a full tank raises it until you drive it" property
      Silvio already accepted on the petrol side.

    · **odometer** — the car's own counter, between the first and the last charge carrying one
      (`charges.odometer_km`, stamped by the poller, typed on a manual charge, back-filled once
      from `positions`). Brim-to-brim, exactly as a driver measures fuel: the euros of every charge
      from the first stamped one up to — but NOT including — the last, because the energy of the
      closing charge is still in the battery. Measured on a real B10 the two agree: 6.19 against
      6.20 €/100 km. It is not a redefinition, it is the same answer from the car's own numbers.

    Why "prices more of the spending" rather than a coverage threshold: a threshold is a number
    somebody invented, and every install would sit near it differently. This comparison is a fact
    about the data — and it makes the change INERT for anyone with an ordinary history. On Silvio's
    B10 the trips price 119.74 € of 119.74 and the odometer 104.27, so the trips win and the card
    does not move at all. It only flips for the person the trips cannot speak for: someone whose
    charges reach back further than Mate's kilometres do, which is exactly #237.

    ⚠️ Declared simplification: on a range-extender the odometer basis is not offered. The petrol
    half of this card is measured per TRIP (litres burned × the blended €/L), so re-basing the
    electricity on the odometer would put the two halves back on two windows — the very defect
    being fixed. `allow_odometer` carries that.

    None when nothing can be divided at all.
    """
    vid = _current_vehicle_id()
    total_eur = _priced_euros(db, vid)

    best = None
    try:
        first = db.execute(
            "SELECT started_at, start_soc FROM trips WHERE vehicle_id = COALESCE(?, vehicle_id) "
            "AND ended_at IS NOT NULL ORDER BY started_at LIMIT 1", (vid,)).fetchone()
        last = db.execute(
            "SELECT ended_at, end_soc FROM trips WHERE vehicle_id = COALESCE(?, vehicle_id) "
            "AND ended_at IS NOT NULL ORDER BY ended_at DESC LIMIT 1", (vid,)).fetchone()
        km = (db.execute(
            "SELECT SUM(distance_km) AS k FROM trips WHERE vehicle_id = COALESCE(?, vehicle_id) "
            "AND ended_at IS NOT NULL", (vid,)).fetchone()["k"]) or 0.0
    except sqlite3.Error:
        first = last = None
        km = 0.0
    if first and last and km >= 0.5:
        where, params = " AND ended_at >= ?", (first["started_at"],)
        best = {
            "basis": "trips", "km": float(km), "since": first["started_at"],
            "where": where, "params": params,
            "covers": _priced_euros(db, vid, where, params),
            # The energy balance keeps its OWN, stricter window: it is arithmetic on the SoC at both
            # ends, so a charge must sit entirely inside or it would be billed to kilometres that
            # are not in the divisor (beta #25 documents this at length).
            "bal": (first["started_at"], last["ended_at"], first["start_soc"], last["end_soc"]),
        }

    if allow_odometer and _charges_have_odometer(db):
        try:
            stamped = db.execute(
                "SELECT started_at, odometer_km, start_soc FROM charges "
                "WHERE vehicle_id = COALESCE(?, vehicle_id) AND ended_at IS NOT NULL "
                "AND odometer_km IS NOT NULL AND odometer_km > 0 ORDER BY started_at", (vid,)
            ).fetchall()
        except sqlite3.Error:
            stamped = []
        if len(stamped) >= 2:
            a, b = stamped[0], stamped[-1]
            span = float(b["odometer_km"]) - float(a["odometer_km"])
            # A span that does not move forward is not a measurement: an odometer typed in wrong,
            # or two charges at the same reading with no driving in between. Refused, not absorbed.
            if span >= 0.5:
                where = " AND started_at >= ? AND started_at < ?"
                params = (a["started_at"], b["started_at"])
                cand = {
                    "basis": "odometer", "km": span, "since": a["started_at"],
                    "where": where, "params": params,
                    "covers": _priced_euros(db, vid, where, params),
                    "bal": (a["started_at"], b["started_at"], a["start_soc"], b["start_soc"]),
                }
                # Strictly more, so a tie leaves an existing install exactly where it was.
                if best is None or cand["covers"] > best["covers"]:
                    best = cand
    return best


def cost_per_100km(fuel_l_burned=None) -> Optional[dict]:
    """What 100 km have COST: every euro spent, divided by every kilometre driven.

    The range-extender version of this card was written by @michapr on his own fork (30/07/26) and
    never offered as a pull request. His priced the CONSUMPTION — efficiency × the rate paid per
    kWh — and so did the first version here. Silvio's call, 05/08/26, in one sentence: *«se deve
    essere un costo deve essere totale non parziale»*, and he is right about what that first version
    left out. Measured on his own history for #207, only **71.8%** of a bill lands on trips at all.
    The rest leaves the battery standing still — climate, preconditioning, the on-board charger's
    own losses — and it was money he paid. A card headed "cost" that quietly answers "cost of the
    driving" is a partial figure wearing a total's name.

    So nothing is multiplied by a rate here, and that makes the whole meter-versus-battery argument
    disappear: there is no divisor left to choose. `cost` is what the charge was BILLED — wallbox
    meter included, exactly as `compute_cost` wrote it — and the euros are added, not divided into
    anything. It is also why no €/kWh appears on this card: none is computed.

    ⚠️ Which way it can be wrong, and it is always the SAME way — LOW:

      · a charge with no price contributes kilometres to the divisor and nothing to the numerator;
      · a refuel the owner never entered does the same.

    Both are counted and said out loud, because a floor that admits it is a floor is honest and a
    floor presented as a total is the defect this card exists to avoid. Never silently completed
    with a guess: an unpriced charge is unknown, not free.

    `fuel_l_burned` is what distinguishes the two cars. None means a car with no tank, and then the
    fuel table is not read at all — not even for the stray rows a database keeps from the days its
    owner had the range-extender variant selected.

    None until something has been driven and at least one euro is known."""
    db = _get()
    basis = _km_basis(db, allow_odometer=not fuel_l_burned)
    if basis is None or basis["km"] < 0.5:
        return None
    km, since_trip = basis["km"], basis["since"]

    # "Priced" means here what it means everywhere else in Mate — `cost IS NOT NULL`, and a charge
    # that cost ZERO is priced (#218, free solar). `price_coverage` owns that rule where an AVERAGE
    # is taken; nothing is averaged here, so only its definition is borrowed, not its arithmetic.
    #
    # 🔑 …and it is counted over the window the KILOMETRES cover, which is the whole of #237. The
    # money used to be summed over the entire archive while the divisor came from the trips alone:
    # @nico89612 typed in 152 charges from before Mate was installed and read 4838.43 €/100 km —
    # months of euros over 46 km of one afternoon. `_km_basis` owns the window; here it is only
    # obeyed.
    elec_cost = 0.0
    priced_n = total_n = 0
    since_charge = None
    try:
        for c in db.execute(
                "SELECT cost, ended_at FROM charges "
                "WHERE vehicle_id = COALESCE(?, vehicle_id) AND ended_at IS NOT NULL"
                + basis["where"], (_current_vehicle_id(), *basis["params"])).fetchall():
            total_n += 1
            if c["cost"] is None or c["cost"] < 0:   # negative is nonsense, not a discount
                continue
            priced_n, elec_cost = priced_n + 1, elec_cost + c["cost"]
            if since_charge is None or c["ended_at"] < since_charge:
                since_charge = c["ended_at"]
    except sqlite3.Error:
        pass

    fuel_cost, fuel_n, since_fuel = 0.0, 0, None
    if fuel_l_burned is not None:
        try:
            _ensure_fuel_purchases(db)
            for p in db.execute(
                    "SELECT total_cost, ts FROM fuel_purchases "
                    "WHERE vehicle_id = COALESCE(?, vehicle_id)",
                    (_current_vehicle_id(),)).fetchall():
                if p["total_cost"] is None or p["total_cost"] < 0:
                    continue
                # ⚠️ COUNTED, not added into the figure. Summing the purchases charged a full tank
                # to the kilometres it has not driven yet — beta #25: 60 L bought, 9.6 burned, and
                # the card read 24.18 €/100km against the Trips page's 3.87 for the same petrol,
                # i.e. 12 €/litre. The count still drives the "no refuel entered" warning and the
                # window's start date, both of which are about what Mate was TOLD.
                fuel_n += 1
                if since_fuel is None or p["ts"] < since_fuel:
                    since_fuel = p["ts"]
        except sqlite3.Error:
            pass

    # What the petrol BURNED cost — litres × the blended €/L of the tank at the time, the same
    # figure the Trips page charges per trip. Without a single refuel entered there is no €/L to
    # multiply by, so it stays 0 and `fuel_missing` says the total is a floor.
    if fuel_l_burned:
        fuel_cost = (reev_fuel_summary() or {}).get("total_cost") or 0.0

    if priced_n == 0 and fuel_n == 0:
        return None

    kwh_100km, kwh_charges, kwh_missing = _energy_balance_kwh(db, km, basis)

    per100 = 100.0 / km
    return {
        "elec_100km": round(elec_cost * per100, 2) if priced_n else None,
        "fuel_100km": round(fuel_cost * per100, 2) if fuel_n else None,
        "total_100km": round((elec_cost + fuel_cost) * per100, 2),
        # Nothing priced at all on a side that was used: the total below is missing that side whole.
        "elec_missing": total_n > 0 and priced_n == 0,
        "fuel_missing": bool(fuel_l_burned) and fuel_n == 0,
        # …and the partial case, where SOME charges carry no price. Same direction, smaller.
        "partial": priced_n > 0 and priced_n < total_n,
        "priced_charges": priced_n,
        "total_charges": total_n,
        "fuel_entries": fuel_n,
        # How many kWh those kilometres took — see `_energy_balance_kwh`. None when the balance
        # cannot be trusted, never 0.0. `kwh_missing` counts in-window sessions with no energy
        # figure: an absent reading makes the balance a FLOOR, it does not make it smaller.
        "kwh_100km": kwh_100km,
        "kwh_charges": kwh_charges,
        "kwh_missing": kwh_missing,
        "km": round(km, 1),
        # WHOSE kilometres those are. "trips" = what Mate reconstructed while it was watching;
        # "odometer" = the car's own counter, between the first and the last charge that carries
        # one. The page says it out loud rather than leaving the reader to assume, because the two
        # answer different questions and on a real B10 they are 4.4% apart (1725 km on the dial
        # against 1650 recorded over the same ten weeks).
        "basis": basis["basis"],
        # WHEN this window opens — the earliest row that feeds the figure. Silvio, 05/08: the card
        # has to say that these are Mate's kilometres since Mate started, not the car's odometer.
        # Measured on his own B10 the same day: 4803 km on the dashboard, 1877 recorded here, and
        # the first note read «diviso i 1877 km percorsi» as though the car had done that much.
        # Compared as ISO strings, which is safe because the DB is UTC everywhere by construction.
        #
        # ⚠️ It can no longer reach back BEFORE the kilometres, which is the visible half of #237:
        # a charge outside the window contributes no euros, so it cannot open a window it does not
        # pay into. `since_charge` is now already inside by construction — it is kept in the min()
        # because on the odometer basis the first charge IS the opening row.
        "since": min([s for s in (since_trip, since_charge, since_fuel) if s], default=None),
    }


def _energy_balance_kwh(db, km: float, basis: dict) -> tuple:
    """How many kWh those `km` took, as a closed-system BALANCE — never trip by trip.

    @michapr, BetaTester #25, 06/08/26, worked out on his own history and cross-checked two ways.
    His own reason for not taking the obvious road, which is also Silvio's rule for this card:

        «`reev_total_consumption()`'s `kwh_100km` looks like the obvious answer, but it's trip-only
        by construction […] it would still miss every kWh that left the pack outside of a trip.»

    Measured for #207, only 71.8% of a bill lands on a trip at all. So the window is treated as one
    system and nothing is attributed to a journey:

        consumed = (energy charged INSIDE the window) − (net change in stored energy across it)

    🔑 **The same formula is right on both cars, and that is not a coincidence.** Energy enters the
    pack from the grid and — on a range-extender — from the generator:

        Δstored = charged + generator − consumed   →   consumed = charged + generator − Δstored

    so `charged − Δstored` is what left the pack MINUS the generator's share: the grid-derived half
    alone. On a card that prices fuel separately that is precisely the number wanted — the same
    refusal to bill the tank twice that `reev_total_consumption` documents. On a BEV `generator` is
    zero and it degenerates to plain consumption. No `is_reev` branch exists here, and none is
    needed.

    The window is the one this card sums kilometres over — handed in by `_km_basis`, so the cost
    and the kWh can never again describe two different stretches of time (#237). On the trip basis
    that is the first trip's `started_at` to the last trip's `ended_at`, exactly as before; on the
    odometer basis it is the first stamped charge to the last. A charge counts only if its OWN
    window sits entirely inside — one that ends before the start, or starts after the end, has
    already been absorbed into the SoC at that boundary, and counting it would bill it twice.
    (@michapr's July 9 session, ending 17:33 before a first trip on the 10th, is exactly that case.)

    ⚠️ The estimate enters only through the SMALL term. `Δstored` is SoC × NOMINAL capacity, and an
    LFP's SoC is counted rather than measured — drift ±15%. On his window that term is 3.80 kWh
    against 63.89 charged, 6%, so even a 15% error there moves the answer under 1%. A window that
    ends much fuller or emptier than it started leans on it harder, and there is no way around that
    from the cloud.

    Returns `(kwh_100km, counted, missing)`. `kwh_100km` is None — not 0.0 — whenever the balance
    cannot be trusted: nothing charged inside the window, no SoC at a boundary, or a balance that
    comes out at or below zero. A car that drove 200 km did not use 0 kWh; printing that would be
    a wrong number wearing the confidence of a measurement.
    """
    vid = _current_vehicle_id()
    # The window comes from whoever owns the divisor (#237). Before that it was derived from the
    # trips here, independently — which was right while the euros were the only thing out of step,
    # and would have become a second bug the moment the divisor could be the odometer instead: the
    # cost over one stretch and the kWh over another, side by side under one heading.
    from_ts, to_ts, soc_from, soc_to = basis["bal"]
    if not from_ts or not to_ts or soc_from is None or soc_to is None:
        return None, 0, 0

    # Entirely inside, on both edges. ISO strings compare correctly because the DB is UTC
    # throughout by construction — the same assumption `since` above already relies on.
    try:
        row = db.execute(
            "SELECT SUM(CASE WHEN energy_added_kwh IS NOT NULL THEN energy_added_kwh END) AS kwh, "
            "       SUM(CASE WHEN energy_added_kwh IS NOT NULL THEN 1 ELSE 0 END) AS counted, "
            "       SUM(CASE WHEN energy_added_kwh IS NULL THEN 1 ELSE 0 END) AS missing "
            "  FROM charges "
            " WHERE vehicle_id = COALESCE(?, vehicle_id) AND ended_at IS NOT NULL "
            "   AND started_at >= ? AND ended_at <= ?",
            (vid, from_ts, to_ts)).fetchone()
    except sqlite3.Error:
        return None, 0, 0

    counted, missing = int(row["counted"] or 0), int(row["missing"] or 0)
    if not counted:
        return None, 0, missing

    net = (soc_to - soc_from) / 100.0 * get_battery_capacity_kwh()
    consumed = (row["kwh"] or 0.0) - net
    if consumed <= 0:
        return None, counted, missing
    return round(consumed * 100.0 / km, 1), counted, missing


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
    return {"count": 0, "km": 0.0, "regen": 0.0, "cost": 0.0, "fuel_l": 0.0,
            "_eff_wsum": 0.0, "_eff_wdist": 0.0, "_ec_kwh": 0.0, "_ec_km": 0.0}


def _totals_add(node: dict, trip: dict) -> None:
    """Fold one trip into a totals node. Efficiency is a DISTANCE-WEIGHTED mean, never a plain
    average of the per-trip figures — a 2 km hop and a 200 km drive must not count the same."""
    km = trip.get("distance_km") or 0
    eff = trip.get("efficiency_kwh_100km")
    node["count"] += 1
    node["km"] = round(node["km"] + km, 2)
    node["regen"] = round(node["regen"] + (trip.get("regen_kwh") or 0), 3)
    # `cost_total`, not `cost`: the latter is the ELECTRIC line by design (see get_trip_detail — the
    # petrol is a field of its own so every existing reader keeps the meaning it had). Folding the
    # electric half alone gave @michapr a 28 July of "129 km · 8.3 L · 0.08 €" and a July strip of
    # "416 km · 9.6 L · 9.02 €" (beta #11): his generator drives carry no efficiency, so they have no
    # electric cost at all, and the tank they emptied was in no total. On a BEV `fuel_cost` is None,
    # so `cost_total` IS `cost` and nothing moves.
    node["cost"] = round(node["cost"] + (trip.get("cost") or 0)
                         + (trip.get("fuel_cost") or 0), 2)
    # The petrol half, for a range-extender. Straight off the trip, which get_trips already worked
    # out through _reev_trip_fuel — the one reader — so the day line, the month line and the drawer
    # header cannot drift from the trips they are made of (@michapr, BetaTester #11: "missing the
    # data still here: at the top of calendar and at the top of the trips").
    node["fuel_l"] = round(node["fuel_l"] + (trip.get("fuel_used_l") or 0), 3)
    # The battery's own drop, kept as PERCENT so the pack capacity is read once per node instead of
    # once per trip. Seals into kwh_100km — the electric figure over ALL the kilometres, the same
    # ones the litres above are divided by. `avg_eff` cannot do that job on a range-extender: Mate
    # stores no efficiency for a generator trip, so it covers the battery-driven half alone and the
    # strip printed two "per 100 km" figures on different distances (@michapr, beta #11 and #24).
    # The electric side, from `ec_kwh` — what the car METERED leaving the battery, and what the money
    # beside it is billed on (reev_trip_electric_cost draws the paid stock down by exactly this).
    # ⚠️ Not ΔSoC, which is what this used at first: on a series hybrid the generator refills the pack
    # mid-drive, so the net SoC change is not the motor's appetite, and a consumption pill computed
    # that way sat next to a cost computed another way — a third basis in one row (@michapr, beta #11:
    # "3.2 kWh/100km" shown against the 2.71 his own getEC figures give).
    # ⚠️ And not `ec_driving` either. Silvio's rule, 05/08/26: always the TOTAL energy, exactly as a
    # BEV is treated. The driving share is never the figure to show.
    # ⚠️ Its OWN distance, not `node["km"]`. getEC is not on every trip — it arrives with a later poll
    # and is missing outright on anything Mate recorded before the feature existed. On the real B10
    # here that is 123 trips of 323, 1016 km of 1824: dividing the kWh we do have by every kilometre
    # driven would have printed a consumption not far off HALF the truth, and printed it in confident
    # black and white. A missing signal is not a zero → [[signal-absent-is-not-signal-zero]].
    _ec = trip.get("ec_kwh")
    if _ec and km > 0:
        node["_ec_kwh"] += _ec
        node["_ec_km"] += km
    if eff and km > 0:
        node["_eff_wsum"] += km * eff
        node["_eff_wdist"] += km


def _totals_seal(node: dict) -> dict:
    """Turn the running weights into avg_eff and drop them. Call once per node."""
    node["avg_eff"] = round(node["_eff_wsum"] / node["_eff_wdist"], 1) if node["_eff_wdist"] > 0 else None
    # Over ALL the kilometres, like the car's own figure and every other L/100 km in Mate since
    # BetaTester #23 — not over the ones the generator ran.
    node["fuel_l_100km"] = (round(node["fuel_l"] / node["km"] * 100, 1)
                            if node["fuel_l"] > 0 and node["km"] > 0.5 else None)
    # …and the electric side, over the kilometres getEC actually covers — which is NOT the fuel
    # denominator above. Litres come off a gauge every trip has; getEC is a reading that can be
    # absent, so the two figures on this strip are honest about different distances rather than one
    # of them being quietly diluted. Only produced when something actually came out of the pack; a
    # range-extender day driven entirely on the generator has no electric figure to show rather than
    # a zero.
    # ONE guard, not two: an earlier version had a second `> 0` test saying the same thing, and a
    # mutation that removed either survived every test — there was no behaviour between them.
    _ec, _ec_km = node["_ec_kwh"], node["_ec_km"]
    node["kwh_100km"] = (round(_ec / _ec_km * 100, 1)
                         if _ec > 0 and _ec_km > 0.5 else None)
    # How much of the strip's distance that figure speaks for. The template needs it to say so when
    # the answer is "not all of it" — a consumption over 44% of the kilometres, printed beside a
    # L/100 km over all of them, is two numbers under one word if nothing marks the difference.
    node["kwh_100km_km"] = round(_ec_km, 1) if node["kwh_100km"] is not None else None
    del node["_eff_wsum"]
    del node["_eff_wdist"]
    del node["_ec_kwh"]
    del node["_ec_km"]
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

    where rate = the FULL cost paid ÷ the energy that actually REACHED THE BATTERY
    (`energy_added_kwh`). Bootstrap: the first priced charge sets p to its own rate (the pre-existing
    energy is valued at the first thing we can measure). Unconfirmed charges (cost=NULL) are simply
    ABSENT from this list → carry-forward, i.e. the blend is unchanged across them — Mate's framework
    rule "no cost until confirmed, HOME excluded".

    ⚠️ A charge that cost ZERO is NOT one of those. `cost=0.0` is only ever written deliberately —
    the #120 free mark (own solar), the FREE type, a band priced at 0, a manual 0 — while an unknown
    price is `cost=NULL` (`compute_cost` returns None, never 0, when no tariff applies). The guard
    here used to drop `rate <= 0`, so free energy really sitting in the pack left the blend at the
    last PAID rate: @oenukr charged from his roof and Mate billed his trips more than he had ever
    spent (#218). Zero is a price. Only a NEGATIVE rate is nonsense and still skipped.

    ⚠️ The divisor used to be `_billed_kwh`, which for a HOME charge with a wallbox reading is the
    METER's AC kWh. That priced battery energy at the wall's rate, so the 8-15% the on-board charger
    turns into heat — real money, off your bill — landed on no trip at all and the trip costs summed
    to LESS than what was spent. A trip consumes what is in the pack, so that is what has to be
    divided into. Only home-with-wallbox charges were ever affected: every other charge already had
    the meter and the battery agreeing, because there is no meter (Silvio's call, 31/07/26).
    `_billed_kwh` is a different question and keeps its own answer — the per-charge card, the period
    totals and the €/kWh on the Charges page show what the CHARGER billed (meter, or the figure the
    owner typed from its display). This one divides by what reached the battery, because that is
    what a trip consumes.
    """
    p = None
    for c in charges:
        ss, es = c.get("start_soc"), c.get("end_soc")
        if ss is None or es is None or es <= 0 or es <= ss:
            continue                         # need a real SoC rise to weight the mix
        basis = c.get("energy_added_kwh")
        cost = c.get("cost")
        if cost is None or not basis or basis <= 0:
            continue                         # unpriced → must not move the blend
        rate = cost / basis
        if rate < 0:
            continue                         # below zero is not a price anyone paid → nonsense
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


def current_blended_price() -> Optional[float]:
    """The blend RIGHT NOW — the €/kWh of the energy sitting in the battery at this moment (#200).

    Same number `blended_price_at` gives a trip, read at `now` instead of at the trip's start: the
    rate the next trip will be costed at. Asked for by @riri19, who could see a trip's cost in € but
    not the price behind it, and had to work backwards by hand to check it.

    A helper rather than the call inlined, because the Overview's battery card is rendered from TWO
    routes — the page itself and `/api/status-card`, which replaces it on the live refresh. One of
    them missing this would make the figure vanish a few seconds after the page loads.
    """
    return blended_price_at(_current_vehicle_id(), datetime.now(timezone.utc).isoformat())


def _paid_stock_replay(events, capacity_kwh: float) -> list[dict]:
    """REEV — replay of (priced charges, trip draws) in time order, returning what each draw COST.

    The battery of a range-extender takes energy from TWO sources but money from ONE. The socket
    adds kWh *and* euros. The generator adds kWh only: those kWh were already paid for **in litres**,
    on the very trip that burned them (see `_reev_trip_fuel`), so charging for them again would bill
    the same petrol twice. Hence the generator never appears in this replay — it shows up only
    through its consequence, that a draw can be LARGER than the paid stock left. The excess is free.

    A DEPLETING STOCK, not a blended price. `_wac_blend` gives a €/kWh that charges raise and nothing
    ever consumes, which is right on a BEV (every kWh in the pack has an invoice) and wrong here: a
    REEV owner who charges once a month would pay grid rate for a month of generator kWh. Worse, a
    blend leaves an exponential tail that keeps billing bought energy after it is gone — 28 kWh
    bought, 10 a day: on day three they are finished, full stop, not 0.44 then 0.15 then 0.05.
    First-in-first-out is also the PHYSICAL order: the car empties the pack and only then fires the
    generator, so the accounting order and the car's behaviour are the same order.

    Each event is a dict: {"kind": "charge"|"draw", "id", "kwh", "cost", "start_soc"}. Pure (no DB)
    like `_wac_blend`, so it can be simulated and unit-tested. Returns one row per draw with
    paid_kwh / free_kwh / cost / rate.
    """
    qp = v = 0.0            # kWh bought at the socket still in the pack, and what they cost
    out = []
    for e in events:
        if e.get("kind") == "charge":
            # Re-anchor to the pack's REAL content. Between charges the pack also loses energy that
            # is not a trip draw — vampire drain, preconditioning, climate while parked (beta #18
            # michapr: 1.6% burned standing still). Without this the paid stock would never shrink
            # for those and would leave behind kWh that were paid for but no longer exist.
            ss = e.get("start_soc")
            if ss is not None and capacity_kwh:
                anchor = max(0.0, ss) / 100.0 * capacity_kwh
                if anchor < qp:
                    v = v * (anchor / qp) if qp > 0 else 0.0
                    qp = anchor
            kwh, cost = e.get("kwh") or 0.0, e.get("cost")
            if kwh > 0 and cost is not None and cost > 0:
                qp += kwh
                v += cost
            continue
        draw = max(0.0, e.get("kwh") or 0.0)
        paid = min(draw, qp)
        cost = paid * (v / qp) if qp > 0 else 0.0
        qp -= paid
        v = max(0.0, v - cost)          # float drift must never leave a negative balance behind
        out.append({"id": e.get("id"), "draw_kwh": round(draw, 3),
                    "paid_kwh": round(paid, 3), "free_kwh": round(draw - paid, 3),
                    "cost": round(cost, 4), "rate": round(cost / paid, 4) if paid > 0 else 0.0})
    return out


def reev_trip_electric_cost(vehicle_id: int, trip_id: int) -> Optional[dict]:
    """REEV — what the electricity of ONE trip cost, from the depleting paid stock.

    Replays every priced charge and every trip draw in time order (`_paid_stock_replay`) and returns
    this trip's row: {paid_kwh, free_kwh, cost, rate}. None when the car isn't a range-extender, or
    when this trip has no draw to price.

    The draw is `ec_kwh` — the energy the car itself metered leaving the battery — falling back to
    the SoC drop when the cloud hasn't locked a value. On a REEV that fallback is the weaker of the
    two: mid-drive the generator refills the pack, so the SoC drop is a NET figure and understates
    what actually came out (see the note on `_reev_trip_elec`).

    The stock is measured in the DC kWh that reached the pack (`energy_added_kwh`) against the FULL
    cost paid — it has to be an amount the pack can actually hold, and it is what makes "billed +
    left over == spent" come out exact. `_wac_blend` divides on the same basis since 31/07/26, so
    the two agree and the same charge prices a REEV trip and a BEV trip identically.

    Recomputed from history on each call, no stored counter — same reason as `blended_price_at`:
    correct a charge's price months later and every trip after it re-derives itself.
    """
    if not is_reev_car():
        return None
    cap = get_battery_capacity_kwh()
    db = _get()
    charges = db.execute(
        "SELECT ended_at ts, energy_added_kwh kwh, cost, start_soc FROM charges "
        "WHERE vehicle_id = ? AND ended_at IS NOT NULL AND cost IS NOT NULL AND energy_added_kwh > 0",
        (vehicle_id,)).fetchall()
    trips = db.execute(
        "SELECT id, ended_at ts, ec_kwh, ec_stable, start_soc, end_soc FROM trips "
        "WHERE vehicle_id = ? AND ended_at IS NOT NULL AND merged_into_id IS NULL",
        (vehicle_id,)).fetchall()

    events = [{"kind": "charge", "ts": r["ts"], "kwh": r["kwh"], "cost": r["cost"],
               "start_soc": r["start_soc"]} for r in charges]
    for r in trips:
        if r["ec_kwh"] and r["ec_stable"]:
            draw = r["ec_kwh"]
        elif r["start_soc"] is not None and r["end_soc"] is not None:
            draw = (r["start_soc"] - r["end_soc"]) / 100.0 * cap
        else:
            draw = 0.0
        events.append({"kind": "draw", "ts": r["ts"], "id": r["id"], "kwh": max(0.0, draw)})

    # A charge and a trip can share a timestamp only by accident; when they do, settle the charge
    # first — you cannot spend energy that arrives in the same instant.
    events.sort(key=lambda e: (e["ts"] or "", 0 if e["kind"] == "charge" else 1))
    for row in _paid_stock_replay(events, cap):
        if row["id"] == trip_id:
            return row
    return None


def _fuel_wac_blend(purchases, tank_l: float = _REEV_TANK_L) -> Optional[float]:
    """Weighted-average-cost blended €/L of the tank after a chronological list of refuels — the FUEL
    twin of _wac_blend (#53). Pure (no DB) so it's simulation/unit-testable — hence `tank_l` as an
    argument rather than a lookup. Each item is a dict with fuel_before_pct (tank % just before this
    refuel), liters (added), price_per_l (€/L paid).

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
        add_pct = liters / tank_l * 100.0
        p = rate if p is None else (fs * p + add_pct * rate) / (fs + add_pct)
    return p


def _trip_fuel_rate_fn():
    """Blended €/L-over-time lookup, built ONCE from every refuel — the fuel twin of
    `_trip_blended_rate_fn`, and for the same reason: `fuel_blended_price_at` replays the whole
    history on each call, which is fine for one trip detail and quadratic down a 500-trip list.

    Exists because the day and month totals were showing the ELECTRIC half of a range-extender's
    bill: the trips the calendar folds carried `fuel_used_l` and no price to multiply it by, since
    nobody computed one outside the detail page (@michapr, beta #11 — "8.3 litres ... should be more
    as 0.05€"). Returns (vehicle_id, ts) → €/L, or None before that car's first logged refuel."""
    seen: dict = {}
    time_line: dict = {}
    db = _get()
    try:
        _ensure_fuel_purchases(db)
        rows = db.execute(
            "SELECT vehicle_id, ts, fuel_before_pct, liters, price_per_l FROM fuel_purchases "
            "WHERE vehicle_id = COALESCE(?, vehicle_id) ORDER BY vehicle_id, ts, id",
            (_current_vehicle_id(),)).fetchall()
    except sqlite3.Error:
        rows = []
    tank = reev_tank_l()
    for p in rows:
        vid = p["vehicle_id"]
        seen.setdefault(vid, []).append(dict(p))
        time_line.setdefault(vid, []).append((p["ts"], _fuel_wac_blend(seen[vid], tank)))

    def rate_at(vehicle_id, ts_utc):
        best = None
        for when, price in time_line.get(vehicle_id, ()):      # ascending → last ≤ ts wins
            if ts_utc and when <= ts_utc:
                best = price
            else:
                break
        return best
    return rate_at


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
        return _fuel_wac_blend([dict(r) for r in rows], reev_tank_l())
    finally:
        db.close()


def get_adjacent_trips(trip_id: int) -> dict:
    """{prev_id, next_id} — the top-level trip immediately before/after this one in time, for
    the trip detail page's ←/→ navigation. A merged child resolves to its parent group's
    started_at first (same as get_trip_detail): children have no place of their own in the
    Viaggi list, so stepping through them would land on a page that isn't next in that list."""
    db = _get()
    row = db.execute("SELECT id, merged_into_id FROM trips WHERE id=? AND vehicle_id = COALESCE(?, vehicle_id)",
                     (trip_id, _current_vehicle_id())).fetchone()
    if not row:
        return {"prev_id": None, "next_id": None}
    parent_id = row["merged_into_id"] or row["id"]
    parent = db.execute("SELECT started_at FROM trips WHERE id=? AND vehicle_id = COALESCE(?, vehicle_id)",
                        (parent_id, _current_vehicle_id())).fetchone()
    if not parent:
        return {"prev_id": None, "next_id": None}
    prev_row = db.execute(
        "SELECT id FROM trips WHERE vehicle_id = COALESCE(?, vehicle_id) AND merged_into_id IS NULL "
        "AND started_at < ? ORDER BY started_at DESC LIMIT 1", (_current_vehicle_id(), parent["started_at"])).fetchone()
    next_row = db.execute(
        "SELECT id FROM trips WHERE vehicle_id = COALESCE(?, vehicle_id) AND merged_into_id IS NULL "
        "AND started_at > ? ORDER BY started_at ASC LIMIT 1", (_current_vehicle_id(), parent["started_at"])).fetchone()
    return {"prev_id": prev_row["id"] if prev_row else None,
            "next_id": next_row["id"] if next_row else None}


def _trip_stop_charges(db, vehicle_id, raw_ended_at) -> list[dict]:
    """Charges during the stop right after a trip ends — what the trip's own map marks
    alongside the start/end flags. A charge belongs to THIS trip's stop if it started at/after
    the trip's end and before the next top-level trip started: nowhere else the car could have
    been charging in that window, so no separate GPS-proximity check is needed. `raw_ended_at`
    must be the UN-localized value (trips.started_at is stored the same raw way), or every
    comparison below silently misses by the local UTC offset.

    Home-wallbox charges are excluded — same _is_home_charge test the general Map's station
    cluster already uses, so a trip that simply ends back at the driver's own wallbox doesn't
    get a "charging stop" marker for parking in their own driveway."""
    if not raw_ended_at:
        return []
    nxt = db.execute(
        "SELECT MIN(started_at) AS s FROM trips WHERE vehicle_id = COALESCE(?, vehicle_id) "
        "AND merged_into_id IS NULL AND started_at > ?", (vehicle_id, raw_ended_at)).fetchone()
    hi = nxt["s"] if nxt and nxt["s"] else None
    q = ("SELECT id, latitude, longitude, location_name, location_url, charge_type, cost, "
         "energy_added_kwh, ac_energy_kwh, location_type, started_at, ended_at"
         + (", gross_kwh" if _charges_have_gross(db) else "") + " FROM charges "
         "WHERE vehicle_id = COALESCE(?, vehicle_id) AND ended_at IS NOT NULL "
         + ("AND merged_into_id IS NULL " if _charges_have_merge(db) else "")
         + "AND latitude IS NOT NULL AND longitude IS NOT NULL AND started_at >= ?")
    params = [vehicle_id, raw_ended_at]
    if hi:
        q += " AND started_at < ?"
        params.append(hi)
    q += " ORDER BY started_at"
    home = _learned_wallbox_location(vehicle_id)
    out = []
    for r in db.execute(q, params).fetchall():
        c = dict(r)
        # `not lat or not lon`, the SAME guard get_charging_stations uses — and it is the falsy
        # test on purpose: a charge the car reported with no GPS fix is stored as 0,0, which is
        # NOT NULL and would otherwise earn a marker in the Gulf of Guinea (measured: 5 132 km
        # from the trip it was attached to).
        if not c.get("latitude") or not c.get("longitude") or _is_home_charge(c, home):
            continue
        c["kwh"] = round(_billed_kwh(c), 2)
        c["started_at"] = _local_iso(c["started_at"])
        out.append(c)
    return out


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
    # Same solid/gap-bridge split the global Map and the report's month map use (see
    # _split_track_gaps) — a real signal-loss stretch draws dashed instead of as a solid line
    # implying an actually-driven straight path. Kept SEPARATE from `positions` itself: the
    # SoC/speed chart below indexes `positions` 1:1 by position, and rounding/regrouping it here
    # would break that alignment.
    route_segments = _split_track_gaps(positions)
    trip_d = _trip_group_stats(dict(trip), children)
    trip_d["route_segments"] = route_segments
    trip_d["elevation_profile_available"] = elevation_profile_available
    if trip_d.get("is_merged"):
        elapsed = _gap_minutes(trip_d.get("started_at"), trip_d.get("ended_at"))
        trip_d["stop_min"] = (round(max(elapsed - (trip_d.get("duration_min") or 0), 0))
                              if elapsed is not None else None)
    # The charge(s) at THIS trip's destination — read before ended_at below is overwritten
    # with its localized form, since trips.started_at in the DB is stored raw/UTC like it is.
    trip_d["charges"] = _trip_stop_charges(db, trip["vehicle_id"], trip_d.get("ended_at"))
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

    # NET change in the pack over the trip, signed — and only kept when the pack ended FULLER than it
    # started (beta #11, @michapr + @gm27271). On a range-extender the generator can put back more
    # than the motor took out; the poller computes exactly this at trip close and then discards it,
    # because a SoC-derived consumption is meaningless once the pack is being refilled mid-drive
    # (beta #10) — so the cell those two photographed was never a suppressed value, it was nothing at
    # all. Derived here from the stored SoC pair rather than kept in a column, so every trip already
    # recorded gets it without a migration.
    #
    # NOT the same quantity as energy_kwh above, and that is why it has its own label: energy_kwh is
    # the GROSS energy that left the pack (getEC, when the cloud has it), which stays positive even on
    # a trip that ended with more charge. Printing a minus sign on that one would answer the question
    # with the wrong number. Only the negative case is surfaced: where the net is positive, the
    # consumption figure beside it already says so, and two numbers under one word is its own defect.
    _s0, _s1 = trip_d.get("start_soc"), trip_d.get("end_soc")
    trip_d["battery_net_kwh"] = None
    if _s0 is not None and _s1 is not None and _s1 > _s0:
        trip_d["battery_net_kwh"] = round((_s0 - _s1) / 100.0 * get_battery_capacity_kwh(), 2)

    # REEV Phase C — per-trip fuel consumption from the fuel-tank % drop (signal 3235). L/100 km is over
    # the generator-on DRIVING distance (across every merged segment), not the whole trip → matches the car.
    # From the GROUP, not from `_tp` (the parent row): on a merged trip the parent is only the first
    # segment, and its tank says nothing about what the later segments burned — see beta #20.
    # 🔴 The LITRES have to come from the group too, and until 07/08/26 they did not: this call read
    # the percentages off `trip_d` (the group) and the litres off `_tp` (the parent row), one line
    # apart, against what the comment above says. `_reev_trip_fuel` PREFERS the measured litres
    # whenever they are there, so the parent's won and the group's petrol was thrown away — beta #20
    # coming back through the millilitre path added later in v2.14.1, which the percentage fix of the
    # time never covered. Measured on a merged 30 + 30 km group burning 2.9 L: the trips list said
    # 4.8 L/100km (it has always passed the group) and this page said 2.5 — the same trip, two pages,
    # two answers, exactly the disagreement @michapr opened beta #27 about. Not research-gated, so it
    # reached every range-extender owner who merges two trips. `_trip_group_stats` had the right
    # figures in `trip_d` all along.
    _fs, _fe = trip_d.get("fuel_start_pct"), trip_d.get("fuel_end_pct")
    _fbounds = db.execute(f"SELECT MIN(started_at) s, MAX(ended_at) e FROM trips WHERE id IN ({ph})",
                          seg_ids).fetchone()
    _feng = _reev_engine_on(db, trip["vehicle_id"], _fbounds["s"], _fbounds["e"])
    trip_d.update(_reev_trip_fuel(_fs, _fe, dist, _feng,
                                  trip_d.get("fuel_start_l"), trip_d.get("fuel_end_l")))
    # REEV Phase D — the electric counterpart, from the metered getEC (driverEC) not ΔSoC. Shown
    # research-only next to the fuel so REEV testers can validate it against the car's own dashboard
    # before we ever promote it to the headline efficiency (see _reev_trip_elec).
    trip_d.update(_reev_trip_elec(_tp.get("ec_kwh"), dist, trip_d.get("engine_ran")))
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
    # A rate of ZERO is a rate. `if rate and rate > 0` treated 0.0 as "no price known" and the tile
    # printed "—", which says we don't know — to the one owner who knows exactly: the pack he
    # charged from his own roof cost him nothing, so the trip cost 0.00 and that is what it must
    # say. Same defect as #218 seen from the other end: there free energy left the price too HIGH,
    # here it erases it. `is None` is the only "unknown" (no priced charge yet); negatives can't
    # reach here, `_wac_blend` drops them.
    if trip_d["energy_kwh"]:
        rate = blended_price_at(trip["vehicle_id"], trip["started_at"])
        if rate is not None and rate >= 0:
            trip_d["cost_per_kwh"] = round(rate, 4)
            trip_d["cost"] = round(trip_d["energy_kwh"] * rate, 2)
    # REEV — the blend above cannot answer this car. Its pack also takes kWh from the generator,
    # which are energy but not SPEND: they were already paid for in litres, on the trip that burned
    # them, and a price that only charges can move would bill them again at grid rate (an owner who
    # charges once a month would pay grid rate for a month of petrol-made kWh). Priced instead from
    # the paid stock, which depletes and can run out — see reev_trip_electric_cost. Replaces both
    # numbers rather than adding a third: the electric line of a trip has one right answer.
    trip_d["paid_kwh"] = trip_d["free_kwh"] = None
    _stock = reev_trip_electric_cost(trip["vehicle_id"], trip["id"])
    if _stock is not None and _stock["draw_kwh"] > 0:
        trip_d["paid_kwh"], trip_d["free_kwh"] = _stock["paid_kwh"], _stock["free_kwh"]
        trip_d["cost"] = round(_stock["cost"], 2)
        trip_d["cost_per_kwh"] = _stock["rate"] or None
    # The trip's real bill = electricity drawn from the pack + the petrol burned getting there.
    # Kept as its OWN field: `cost` stays the electric line (every existing reader expects that),
    # and a REEV trip whose electricity cost 0 still cost money — which is why the headline tile
    # showing "—" on a 24,63 € tank of petrol was wrong.
    _parts = [c for c in (trip_d.get("cost"), trip_d.get("fuel_cost")) if c is not None]
    trip_d["cost_total"] = round(sum(_parts), 2) if _parts else None

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
    so it stays selected until the elevation lands or the ceiling is hit.

    ⚠️ DELIBERATELY NOT scoped to the selected car, and it must stay that way. This is a maintenance
    QUEUE, read by elevation_enrich._sweep_now — not a page. Which car the user happens to be
    looking at has nothing to do with which trips still owe Open-Meteo an altitude, and adding the
    usual `vehicle_id = COALESCE(?, vehicle_id)` here would quietly stop enriching the other car's
    trips for as long as it is not the selected one. The defect would be an ABSENCE, so nobody would
    report it.

    A two-car audit flags this function for handing both cars the same trip ids. That is the right
    answer, not a leak: the giveaway is the caller, not the query."""
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
    """The finished charges, most recent first — one row per CHARGE, not per stored row.

    A plug-in the car reported in pieces (it declares the cable gone the instant the current stops)
    comes back as one row once the user has merged them: the children drop out of the list and the
    parent carries the combined figures. `limit` therefore counts charges, which is what the page
    asks for — the last 50 charges, not the last 50 fragments."""
    db = _get()
    rows = db.execute(
        "SELECT * FROM charges WHERE vehicle_id = COALESCE(?, vehicle_id) AND ended_at IS NOT NULL "
        + ("AND merged_into_id IS NULL " if _charges_have_merge(db) else "")
        + "ORDER BY started_at DESC LIMIT ?",
        (_current_vehicle_id(), limit),
    ).fetchall()
    kids = _charge_children_by_parent(db)
    # Compose FIRST, localise after: the group maths works on the stored UTC ISO, and swapping the
    # two would hand the user a correct date at the wrong hour.
    out = [_charge_group_stats(dict(r), kids.get(r["id"], [])) for r in rows]
    # Each row learns about the charge BEFORE it, so the page can offer to join them where the two
    # are close enough to be one plug-in the car reported in pieces. This is an offer, not a
    # permission: merge_charges re-checks every guard, including the ones too expensive to run for
    # a whole page (another charge in the gap, a trip in the gap).
    for i, d in enumerate(out):                       # newest first, so i+1 is the earlier charge
        earlier = out[i + 1] if i + 1 < len(out) else None
        d["prev_charge_id"] = earlier["id"] if earlier else None
        gap = _gap_minutes(earlier.get("ended_at"), d.get("started_at")) if earlier else None
        d["can_merge_prev"] = gap is not None and 0 <= gap < CHARGE_MERGE_GAP_DEFAULT
        d["started_at"] = _local_iso(d.get("started_at"))
        d["ended_at"] = _local_iso(d.get("ended_at"))
    return out


def preview_merge_charges(parent_id: int, child_id: int) -> Optional[dict]:
    """The charge the merge WOULD produce (for the confirm dialog), without committing."""
    db = _get()
    a = db.execute("SELECT * FROM charges WHERE id=? AND vehicle_id = COALESCE(?, vehicle_id)",
                   (parent_id, _current_vehicle_id())).fetchone()
    b = db.execute("SELECT * FROM charges WHERE id=? AND vehicle_id = COALESCE(?, vehicle_id)",
                   (child_id, _current_vehicle_id())).fetchone()
    if not a or not b:
        return None
    a, b = dict(a), dict(b)
    if (a.get("started_at") or "") > (b.get("started_at") or ""):
        a, b = b, a
    kids = _charge_children_by_parent(db)
    g = _charge_group_stats(a, kids.get(a["id"], []) + [b] + kids.get(b["id"], []))
    g["pause_min"] = _gap_minutes(a.get("ended_at"), b.get("started_at"))
    g["started_at"] = _local_iso(g.get("started_at"))
    g["ended_at"] = _local_iso(g.get("ended_at"))
    return g


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


def get_fuel_totals_between(begin_ts: int, end_ts: int) -> dict:
    """REEV — litres burned and generator-on distance for trips started within [begin_ts, end_ts].

    The counterpart of get_trip_totals_between for the petrol half, so a period card can show BOTH
    energies over the window the reader chose (@michapr, beta #11). getPlugIn already gives the two
    figures measured by the car, but on ITS window and no other — the request carries the VIN and
    nothing else — so a chosen period has to be answered from the trips.

    Not a SQL sum: the litres go through _reev_trip_fuel like everywhere else, which prefers the
    car's OWN counter (signal 3263) and only falls back to tank-% × assumed capacity on trips
    recorded before v2.14.1. ⚠️ A long window therefore mixes the two bases — measured litres for
    recent trips, derived for old ones — and no averaging here can undo that.

    The positions walk that finds the generator-on distance runs ONLY for trips whose tank actually
    dropped, so a mostly-electric REEV (and every BEV) costs one indexed query and nothing more.

    ⚠️ MERGED trips are counted, children included, and that is not an oversight — it was one. This
    was the single fuel total carrying `AND merged_into_id IS NULL`, and joining two trips writes
    that column and NOTHING else: the child keeps the tank reading it was recorded with. So the
    filter deleted the child's litres while `get_trip_totals_between` beside it kept the child's
    kilometres, and the card divided a short numerator by a full denominator. On @michapr's B10
    (beta #23, 05/08/26) his 07:56 trip on 28 July was merged into the 2 km one before it and
    carried 3.7 of his 9.6 L — which is precisely the 5.9 the card kept showing him, through four
    rounds of me looking somewhere else. Parent and child hold DISJOINT tank readings, so summing
    both is the whole group and double-counts nothing; every other fuel total already did this."""
    b = datetime.fromtimestamp(begin_ts, tz=timezone.utc).isoformat()
    e = datetime.fromtimestamp(end_ts, tz=timezone.utc).isoformat()
    out = {"fuel_l": 0.0, "engine_km": 0.0, "trip_count": 0}
    db = _get()
    try:
        rows = db.execute(
            "SELECT id, vehicle_id, started_at, ended_at, distance_km, fuel_start_pct, fuel_end_pct,"
            " fuel_start_l, fuel_end_l FROM trips"
            " WHERE vehicle_id = COALESCE(?, vehicle_id) AND ended_at IS NOT NULL"
            "   AND started_at >= ? AND started_at <= ?"
            "   AND " + _REEV_FUEL_ANY_DROP_SQL,
            (_current_vehicle_id(), b, e, _REEV_FUEL_MIN_DROP)).fetchall()
    except sqlite3.Error:
        return out                      # no fuel columns → a BEV, and nothing to add
    for r in rows:
        eng = _reev_engine_on(db, r["vehicle_id"], r["started_at"], r["ended_at"])
        f = _reev_trip_fuel(r["fuel_start_pct"], r["fuel_end_pct"], r["distance_km"], eng,
                            r["fuel_start_l"], r["fuel_end_l"])
        if f["fuel_used_l"]:
            out["fuel_l"] += f["fuel_used_l"]
            out["engine_km"] += f["engine_km"] or 0
            out["trip_count"] += 1
    out["fuel_l"] = round(out["fuel_l"], 2)
    out["engine_km"] = round(out["engine_km"], 1)
    return out


def get_trip_totals_between(begin_ts: int, end_ts: int) -> dict:
    """Distance/duration/count/energy of LOCAL trips started within [begin_ts, end_ts] (epoch
    seconds) — paired by the caller with a live getEC total for the SAME window, to show distance +
    average kWh/100km alongside the official split (mirrors the car's own "since last charge"
    screen, which shows Distanza/Durata/Media next to the same Guida/AC/Altro breakdown).

    `energy_kwh` is Mate's OWN figure for the window — the same per-trip values the Trips page adds
    up, cloud getEC where the cloud had a usable one and the SoC estimate where it didn't. The
    caller uses it as the reference the cloud's period total is checked against (#212). A trip with
    no efficiency counts as 0, which can only make the local total SMALLER — i.e. the check errs
    toward leaving the cloud figure alone."""
    b = datetime.fromtimestamp(begin_ts, tz=timezone.utc).isoformat()
    e = datetime.fromtimestamp(end_ts, tz=timezone.utc).isoformat()
    db = _get()
    row = db.execute(
        """SELECT COUNT(*) AS trip_count,
                  ROUND(SUM(distance_km), 2) AS distance_km,
                  ROUND(SUM(duration_min), 0) AS duration_min,
                  ROUND(SUM(distance_km * COALESCE(efficiency_kwh_100km, 0) / 100.0), 2) AS energy_kwh
           FROM trips WHERE vehicle_id = COALESCE(?, vehicle_id) AND ended_at IS NOT NULL
             AND started_at >= ? AND started_at <= ?""",
        (_current_vehicle_id(), b, e),
    ).fetchone()
    return dict(row) if row else {}


# The cloud's period total is only as complete as the car's uplink was. On a car that often can't
# reach the cloud while driving, whole sessions are simply absent from it — and unlike the per-trip
# path, which notices and falls back to the SoC estimate (ec_enrich._ec_implausible), the period
# cards used to print whatever came back. #212 @riri19: 27.1 kWh against the 36.3 his own trips add
# up to, from a car whose driving polls read a stale frame 59 % of the time (8.9 % on the drive the
# cloud got right, 75.1 % on the one it lost).
#
# The per-trip guard cannot be reused as-is: it fires on physically impossible efficiencies
# (< 5 kWh/100 km), and his 12.3 is perfectly plausible — it is only wrong RELATIVE to what Mate
# itself measured over the same window. So the reference here is the local trip sum.
#
# ⚠️ The threshold is measured on a thin sample: three months of a healthy car gave cloud/local
# 0.895, 1.032 and 0.982, and the broken month gave 0.747. 0.80 sits between them with about the
# same margin on each side. Widen it if a healthy month ever trips it.
_CLOUD_SHORT_RATIO = 0.80
_CLOUD_SHORT_MIN_KM = 20.0     # below this a window is too small for the ratio to mean anything
_CLOUD_SHORT_MIN_KWH = 3.0


def flag_short_cloud_total(eb: dict, tot: dict, dist_km: float) -> dict:
    """Mark (and correct) a cloud period total that is far below Mate's own trips for the window.

    Only the LOW side is guarded. A cloud total ABOVE the local sum is normal — it carries climate
    and standby energy that no trip is charged with — so it is left exactly as it is."""
    local_kwh = tot.get("energy_kwh") or 0
    cloud_kwh = eb.get("total_kwh") or 0
    if dist_km < _CLOUD_SHORT_MIN_KM or local_kwh < _CLOUD_SHORT_MIN_KWH:
        return eb
    if cloud_kwh >= local_kwh * _CLOUD_SHORT_RATIO:
        return eb
    eb = {**eb, "cloud_short": True, "cloud_total_kwh": cloud_kwh, "local_kwh": local_kwh}
    eb["local_avg_kwh100"] = round(local_kwh / dist_km * 100, 1) if dist_km > 0 else None
    return eb


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
        # ⚠️ COMPLETED charges only (Silvio, 06/08/26). While the energy is still flowing the meter
        # lags the car — @Wartopia's live 6 August charge read 2.74 kWh from the wall against 4.03
        # into the battery — so a running session drags every comparison on this page for as long as
        # the cable is in. Measured: 89.3 % became 93.6 % the moment one joined. Its own guards have
        # not run yet either: the ceiling and stuck-counter backstops fire at finalize_charge.
        "SELECT c.id, c.started_at, c.ended_at, c.energy_added_kwh, c.ac_energy_kwh FROM charges c "
        "WHERE c.vehicle_id = COALESCE(?, c.vehicle_id) AND c.location_type = 'HOME' "
        + ("AND c.merged_into_id IS NULL " if _charges_have_merge(db) else "")
        + "AND c.ended_at IS NOT NULL AND EXISTS ("
        "  SELECT 1 FROM positions p WHERE p.vehicle_id = c.vehicle_id AND p.charging = 1"
        "  AND p.recorded_at >= c.started_at"
        "  AND (c.ended_at IS NULL OR p.recorded_at <= c.ended_at)"
        ") ORDER BY c.started_at DESC",
        (_current_vehicle_id(),)).fetchall()
    # One entry per SESSION: two rows here are two Home Assistant history fetches and two
    # attributions for one plug-in, and the window has to reach the real end — stopping at the
    # first piece would leave the rest of the meter's kilowatt-hours outside it.
    kids = _charge_children_by_parent(db)
    return [_charge_group_stats(dict(r), kids.get(r["id"], [])) for r in rows]


def wallbox_session_energy(charge) -> dict:
    """One charge's AC-vs-DC comparison, from the STORED columns.

    The per-session twin of wallbox_ac_dc_totals, and it exists for the same reason: the day drawer
    and the sessions tree used to re-derive this by integrating the wallbox power sensor's Home
    Assistant history, one fetch per session, while the poller had already written the meter's own
    kWh onto the row (#229). Efficiency only when both figures are there — a battery figure with no
    meter figure is not a comparison.
    """
    get = charge.get if hasattr(charge, "get") else charge.__getitem__
    try:
        ac, dc = get("ac_energy_kwh"), get("energy_added_kwh")
    except (KeyError, IndexError):
        return {"ac_kwh": None, "dc_kwh": None, "eff": None}
    ac = ac if (ac and ac > 0) else None
    dc = dc if (dc and dc > 0) else None
    return {"ac_kwh": round(ac, 2) if ac else None,
            "dc_kwh": round(dc, 2) if dc else None,
            "eff": round(100 * dc / ac, 1) if (ac and dc) else None}


def wallbox_ac_dc_totals(charges) -> dict:
    """AC delivered vs DC into the battery over a set of charges, from the STORED columns.

    The one place that arithmetic lives, because there were two and they disagreed on the same
    screen (#229 @Wartopia: 141.5 % on the tiles, 92.5 % on the calendar underneath). The tiles were
    written on 3 June, when `ac_energy_kwh` did not exist yet and the only way to know the wall's
    kWh was to integrate the power sensor's Home Assistant history for every session, on every page
    load. The column arrived on 9 June; nobody moved them onto it.

    ⚠️ A charge counts only when it has BOTH figures. Adding its battery kWh while adding no meter
    kWh is what pushes the ratio above 100 % — the car cannot take more than the wall gave. And it
    is not a corner case: `finalize_charge` deliberately NULLs `ac_energy_kwh` when the counter ran
    away (#46) or stood still (#215), and every HOME charge recorded before the wallbox was
    configured has no meter figure at all.

    `skipped` is returned rather than swallowed: a total built on 3 charges of 10 is a different
    claim from one built on all 10, and the caller may want to say so.
    """
    ac = dc = 0.0
    counted = skipped = 0
    for c in charges:
        a = c.get("ac_energy_kwh") if hasattr(c, "get") else c["ac_energy_kwh"]
        d = c.get("energy_added_kwh") if hasattr(c, "get") else c["energy_added_kwh"]
        if not a or not d or a <= 0 or d <= 0:
            skipped += 1
            continue
        ac += a
        dc += d
        counted += 1
    if not counted:
        return {"ac": None, "dc": None, "eff": None, "counted": 0, "skipped": skipped}
    return {"ac": round(ac, 2), "dc": round(dc, 2),
            "eff": round(100 * dc / ac, 1) if ac else None,
            "counted": counted, "skipped": skipped}


def get_wallbox_calendar_month(year: int, month: int) -> dict:
    """Per-day AC(wallbox)/DC(battery) totals for the Wallbox calendar's Month view — uses
    the ALREADY-STORED charge columns (ac_energy_kwh/energy_added_kwh), not the per-session
    HA-history integration main.py's _session_energy does. That stays lazy, computed only
    for the one day the user opens (see get_wallbox_calendar_day) instead of for every
    session up front — each call is a live Home Assistant history fetch."""
    per_day: dict[int, list] = {}
    month_rows = []
    for c in _wallbox_home_charges_raw():
        dt = _local_dt(c["started_at"])
        if dt is None or dt.year != year or dt.month != month:
            continue
        per_day.setdefault(dt.day, []).append(c)
        month_rows.append(c)
    # Through the SAME helper the lifetime tiles use, so the two figures on this one screen cannot
    # drift apart again — which is exactly how #229 happened. It also fixes the hole this loop had
    # of its own: it added a charge's battery kWh even when its meter kWh was missing, so a month
    # containing a dropped counter (#46/#215) or a pre-wallbox charge read above 100 %.
    days = {d: {**wallbox_ac_dc_totals(rows), "count": len(rows)} for d, rows in per_day.items()}
    total = {**wallbox_ac_dc_totals(month_rows), "count": len(month_rows)}
    return {"year": year, "month": month, "days": days, "total": total}


def get_wallbox_calendar_day(year: int, month: int, day: int) -> list[dict]:
    """{id, time} for each HOME-with-power charge on ONE calendar day, most-recent-first —
    the day-drawer computes each session's precise AC/DC comparison (_session_energy,
    main.py) lazily per row, not all up front like the old accordion did."""
    out = []
    for c in _wallbox_home_charges_raw():
        dt = _local_dt(c["started_at"])
        if dt and dt.year == year and dt.month == month and dt.day == day:
            # The two figures come from the row, not from a Home Assistant history fetch per
            # session (#229). Same numbers as the tiles and the month strip above — one screen,
            # one answer.
            out.append({"id": c["id"], "time": dt.strftime("%H:%M"), "_sort": c["started_at"],
                        **wallbox_session_energy(c)})
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
        # Only whole charges: a merged child is not a charge the user can confirm — the group
        # carries the parent's type, and counting the pieces would ask twice for one answer.
        "SELECT COUNT(*) n FROM charges WHERE vehicle_id = COALESCE(?, vehicle_id) "
        "AND location_type IS NULL AND ended_at IS NOT NULL"
        + (" AND merged_into_id IS NULL" if _charges_have_merge(db) else ""),
        (_current_vehicle_id(),)
    ).fetchone()
    return row["n"] if row else 0


def newest_unconfirmed_charge_id() -> int:
    """The charge the "N to confirm" banner should take the reader to, or 0.

    The banner used to be a dead end: it said a charge needed a type and gave no way to reach it
    — you had to guess which day of the calendar it fell on and click that. That is #240's actual
    complaint, underneath the 500: *"the charging page shows the 1 charge to confirm on top but
    not showing at bottom"*.

    The NEWEST one on purpose: the banner appears right after a charge ends, and the charge the
    reader is looking for is the one they just did. Older ones surface as that one gets typed.
    """
    db = _get()
    row = db.execute(
        "SELECT id FROM charges WHERE vehicle_id = COALESCE(?, vehicle_id) "
        "AND location_type IS NULL AND ended_at IS NOT NULL "
        "ORDER BY started_at DESC LIMIT 1",
        (_current_vehicle_id(),)
    ).fetchone()
    return row["id"] if row else 0


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
    _db = _get()
    row = _db.execute("SELECT * FROM trips WHERE id=? AND vehicle_id = COALESCE(?, vehicle_id)",
                      (trip_id, _current_vehicle_id())).fetchone()
    if not row:
        return None
    # A merged journey is ONE journey to whoever is looking at it: get_trip_detail resolves a child
    # to its parent and composes the group, so the page says A→C while this used to read the parent
    # segment's own row and write A→B — the trip's own summary contradicting the trip (#247,
    # @Ng-EY). Same for the arrival time and the end temperature, which came from B. Resolve the
    # group here exactly as the page does; the stored rows stay untouched (a merge is display math
    # and must stay reversible), and the note is written to the PARENT, which is the row the page
    # reads it back from.
    parent_id = row["merged_into_id"] or row["id"]
    parent = row if parent_id == row["id"] else _db.execute(
        "SELECT * FROM trips WHERE id=? AND vehicle_id = COALESCE(?, vehicle_id)",
        (parent_id, _current_vehicle_id())).fetchone()
    if parent is None:          # child pointing at a parent this vehicle cannot see
        parent, parent_id = row, row["id"]
    row = _trip_group_stats(dict(parent), _children_by_parent(_db).get(parent_id, []))
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
    db.execute("UPDATE trips SET note=? WHERE id=?", (text, parent_id))
    db.commit()
    return text


_FUEL_NOTE_TOLERANCE_MIN = 20     # how far from the refuel a position may sit and still be "there"


def generate_fuel_auto_note(purchase_id: int, provider: str = "", api_key: "str | None" = None,
                            only_if_note_empty: bool = False) -> "str | None":
    """Write where you filled up into the refuel's own note — the 🧭 that trips and charges have.

    Asked for by **@gm27271** (beta discussion #14): *"add GPS coordinates of the gas station
    registered during the refueling timestamp… then users do not need to enter any notes"*. The
    cloud reports no station, so the place has to come from where the CAR was: the position nearest
    the refuel's timestamp, reverse-geocoded through the same provider the trip note uses.

    The tolerance is the honest part. A refuel's timestamp is not always the moment fuel went in —
    on a detected one it is when the NEW level was first seen, which for someone who fills up and
    drives home is the next time the car woke. So a position is only accepted within
    _FUEL_NOTE_TOLERANCE_MIN of it; beyond that the car was demonstrably somewhere else and the
    note is left alone rather than naming the wrong forecourt. Returns the note it wrote, or None.

    `only_if_note_empty` mirrors the charge path: something you typed is never overwritten by an
    automatic call. The button always overwrites — the page asks first when there is something to
    lose."""
    import geocode
    row = _get().execute(
        "SELECT * FROM fuel_purchases WHERE id=? AND (vehicle_id = COALESCE(?, vehicle_id) "
        "OR vehicle_id IS NULL)", (purchase_id, _current_vehicle_id())).fetchone()
    if not row:
        return None
    row = dict(row)
    if only_if_note_empty and (row.get("note") or "").strip():
        return row.get("note")
    pos = get_position_near(row.get("ts"), tolerance_min=_FUEL_NOTE_TOLERANCE_MIN)
    if not pos or pos.get("latitude") is None or pos.get("longitude") is None:
        return None
    try:
        address = geocode.reverse_geocode(pos["latitude"], pos["longitude"], provider, api_key)
    except Exception:  # noqa: BLE001 — a DNS/timeout blip must not lose the refuel
        return None
    if not address:
        return None
    db = _conn_rw()
    try:
        db.execute("UPDATE fuel_purchases SET note=? WHERE id=?", (address, purchase_id))
        db.commit()
    except sqlite3.Error:
        return None
    finally:
        db.close()
    return address


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


def _charges_have_gross(db) -> bool:
    """Whether the charges table carries the #222 column yet.

    The migration lives in the POLLER; the web serves the same database and never alters it. So
    between an update and the poller's next start — and for good on an install whose poller has not
    run — the column is simply absent, and a query that names it raises OperationalError, which is a
    500 on the Charges page. Found on Silvio's own instance hours after v3.6.6 shipped.

    Asked per call, not cached: the poller can add the column while the web is running, and a
    remembered "no" would keep the page degraded until someone restarted it."""
    try:
        return any(r[1] == "gross_kwh" for r in db.execute("PRAGMA table_info(charges)"))
    except sqlite3.Error:
        return False


def charges_have_odometer() -> bool:
    """The same question, asked by a route so a template can hide a field it cannot store."""
    return _charges_have_odometer(_get())


def _charges_have_odometer(db) -> bool:
    """Whether the charges table carries the #237 odometer column yet. Same reasoning, same
    migration-lives-in-the-poller rule, and the same per-call question as `_charges_have_gross` —
    including the consequence Silvio's own instance taught us in v3.6.6: where the column cannot be
    stored, the field is not OFFERED either, so nobody types a number into a form that will drop
    it."""
    try:
        return any(r[1] == "odometer_km" for r in db.execute("PRAGMA table_info(charges)"))
    except sqlite3.Error:
        return False


def _billed_kwh(c) -> float:
    """The energy figure SHOWN (and billed) for a charge — what came OUT of the charger:

        wallbox counter (measured)  →  the charger's own kWh (#222, typed)  →  battery kWh

    Single source of truth so the per-charge card, the period totals, get_charge_stats and the
    Ricariche calendar all agree. Mirrors the SQL CASE in get_charge_stats and the card's `show_wb`
    condition (charges.html). Same order as update_charge_type prices a charge, deliberately: the
    thing that billed you is the thing that delivered.

    ⚠️ The third branch is not a gross figure at all; it is the only number that exists for a charge
    with no meter and nothing typed, and leaving it out would make a month's total drop every time a
    public charge appeared.

    ⚠️ The middle branch means a TYPED number is now part of the energy Mate reports — it was kept
    out on purpose when #222 shipped, so a typo could not inflate a total. Silvio's call, 04/08: the
    Ricariche calendar had started saying "delivered" with the typed figure in it while this one
    still ignored it, and two totals under two words that mean the same thing is worse than one
    total that can be mistyped. It stays out of `_wac_blend`, which divides by the energy that
    actually reached the battery — a trip consumes that, not what the meter saw."""
    ac = c.get("ac_energy_kwh")
    if c.get("location_type") == "HOME" and ac and ac > 0:
        return ac
    g = c.get("gross_kwh")
    if g and g > 0:
        return g
    return c.get("energy_added_kwh") or 0


def price_coverage(cost_total, kwh_priced, priced_n, total_n) -> dict:
    """The €/kWh actually paid — over the PRICED charges ALONE — and how much of the period that
    covers. Single source of truth for the rule, because the obvious shortcut is wrong: a charge
    with no cost (untyped, or a type with no price configured) still has kWh, so dividing the
    spend by the period's TOTAL energy silently reports a price LOWER than the one you pay.
    Measured on a real month before this existed: 0.199 €/kWh on screen against 0.250 real, from
    ONE untagged charge out of ten.

    The flip side is that `avg_price` is then NOT `total_cost ÷ total_kwh` as those two appear on
    screen — divide them and you get a third number. Hence `partial`: the caller must SAY what the
    average was computed over instead of leaving the reader to do that division."""
    avg = (round(cost_total / kwh_priced, 3)
           if cost_total is not None and kwh_priced and kwh_priced > 0 else None)
    priced_n, total_n = priced_n or 0, total_n or 0
    return {"avg_price": avg, "priced_count": priced_n, "total_count": total_n,
            "partial": avg is not None and priced_n < total_n}


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


def _km_since_previous_map() -> dict:
    """{charge id → kilometres since the charge before it} (#237).

    Silvio's own idea, and it falls out of the odometer column for nothing: two consecutive
    readings, subtracted. It is the distance a battery actually covered — the one figure on the
    page a driver can check against their own memory of the week.

    Only where BOTH charges carry a reading, and only forwards. Measured on a real B10 over ten
    weeks: 26 stamped charges, gaps of 0 to 272 km, median 26, **not one negative**. The zeros are
    real and stay silent — ten of them the same afternoon, a car plugged in twice with no driving
    in between. A negative means a mistyped reading, and "-300 km since the last charge" would be
    worse than nothing at all.

    🔴 Computed over the WHOLE history and handed back keyed by id, never over the list being
    rendered. The Charges page shows one day at a time, and search shows whatever matched; pairing
    inside either of those would subtract two charges that are not neighbours and print a
    confident wrong number — the gap across a filtered-out session, called "since the previous
    charge".
    """
    out: dict = {}
    try:
        rows = _get().execute(
            "SELECT id, odometer_km FROM charges "
            "WHERE vehicle_id = COALESCE(?, vehicle_id) AND ended_at IS NOT NULL "
            + ("AND merged_into_id IS NULL " if _charges_have_merge(_get()) else "")
            + "AND odometer_km IS NOT NULL AND odometer_km > 0 ORDER BY started_at",
            (_current_vehicle_id(),)).fetchall()
    except sqlite3.Error:
        return out
    prev = None
    for r in rows:
        if prev is not None and r["odometer_km"] >= prev:
            out[r["id"]] = round(r["odometer_km"] - prev, 1)
        prev = r["odometer_km"]
    return out


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
    never rendered, so its presence in the dict is harmless to charge_card.html.

    🔴 This is where the kilometres-since-the-last-charge land, and finding that out cost a
    round trip through a running container: the figure was first attached in
    `get_charges_grouped`, which reads plausibly enough — and which the Charges page does not
    call. Every card the user actually sees comes through HERE, from the calendar or from search.
    The test was green against a function nobody renders.
    → [[feedback-gate-a-feature-find-every-copy]]"""
    db = _get()
    gaps = _km_since_previous_map()
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
        if c.get("id") in gaps:
            c["km_since_prev"] = gaps[c["id"]]
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
    total = {"count": 0, "kwh": 0.0, "battery_kwh": 0.0, "cost": 0.0, "has_cost": False}
    for c in charges:
        dt = c["_dt"]
        if dt.year != year or dt.month != month:
            continue
        d = days.setdefault(dt.day, {"count": 0, "kwh": 0.0, "battery_kwh": 0.0,
                                     "cost": 0.0, "has_cost": False})
        # `kwh` is the DELIVERED side — the wallbox counter, the charger's own kWh where the owner
        # typed it, the battery figure where neither exists. The month strip says so in words and
        # puts the battery total beside it, because the gap between the two IS the conversion loss:
        # a bare "154.93 kWh" with no label was neither one thing nor the other.
        kwh = _billed_kwh(c)
        batt = c.get("energy_added_kwh") or 0
        for node in (d, total):
            node["kwh"] = round(node["kwh"] + kwh, 2)
            node["battery_kwh"] = round(node["battery_kwh"] + batt, 2)
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
               -- The trip's own efficiency FIRST — it already reflects the owner's choice about
               -- whether the car's getEC figure becomes a trip's energy (Settings), and preferring
               -- ec_kwh over it would silently overrule that setting: measured on a real BEV, the
               -- total moved 338.75 → 349.21 kWh for nobody's benefit. The car's measurement is the
               -- FALLBACK, for a trip that has no efficiency at all, and a trip
               -- with NEITHER contributes nothing — instead of contributing a ZERO, which is what
               -- COALESCE(efficiency,0) used to do. On a range-extender Mate deliberately blanks the
               -- efficiency of every trip the generator ran, so those trips were counted as having
               -- used no electricity at all: @michapr (BetaTester #24) read 37.85 kWh where his own
               -- SUM(ec_kwh) said 41.6, and could not trace the difference to anything on screen.
               -- With ec as the fallback those trips contribute what the CAR measured, and a BEV,
               -- whose trips all carry an efficiency, sees no change at all.
               ROUND(SUM(CASE WHEN efficiency_kwh_100km IS NOT NULL
                                   THEN distance_km * efficiency_kwh_100km / 100.0
                              WHEN ec_kwh IS NOT NULL AND ec_stable = 1 THEN ec_kwh END), 2)
                                                                             AS total_kwh_used,
               -- …and how much of the driving that figure could speak for, so the page can say so
               -- rather than leave the reader to wonder.
               SUM(CASE WHEN efficiency_kwh_100km IS NOT NULL
                          OR (ec_kwh IS NOT NULL AND ec_stable = 1) THEN 1 ELSE 0 END) AS energy_trips,
               -- The DISTANCE behind that count, because the count alone makes a bad gate: two
               -- stray zero-kilometre trips would put "338 of 340" on every BEV for ever, about
               -- driving that never happened. The note is worth showing when real kilometres are
               -- missing from the total, and the count is what it then says.
               ROUND(SUM(CASE WHEN efficiency_kwh_100km IS NOT NULL
                          OR (ec_kwh IS NOT NULL AND ec_stable = 1) THEN distance_km END), 1)
                                                                             AS energy_km,
               -- Reconstructed trips are left OUT of the clock and stay in everything else. Their
               -- duration is the length of the blackout the odometer jump was found across, not of
               -- any driving: a ten-minute errand discovered after a night of no contact carries
               -- nine hours. The kilometres and the energy are real — they are why the trip exists
               -- — so only the time comes out. → [[feedback-two-numbers-one-word]]
               ROUND(SUM(CASE WHEN COALESCE(reconstructed, 0) = 0
                              THEN duration_min END), 0)                     AS total_drive_min,
               SUM(CASE WHEN COALESCE(reconstructed, 0) = 1
                         AND duration_min IS NOT NULL THEN 1 ELSE 0 END)     AS drive_time_excluded,
               -- distance-weighted = total energy / total distance (#42): a simple AVG
               -- over-weights short trips and disagreed with both the Trips-page header
               -- and this page's own "energy used ÷ distance". Matches get_trips_summary.
               ROUND(SUM(distance_km * efficiency_kwh_100km) /
                     NULLIF(SUM(CASE WHEN efficiency_kwh_100km IS NOT NULL
                                     THEN distance_km END), 0), 1)           AS avg_efficiency,
               -- The kilometres that average actually covers. Not the same as the total on a
               -- range-extender, where a generator trip has no efficiency to average: 13.9 kWh/100km
               -- over 272 of 434 km is a different statement from 13.9 over all of them, and the
               -- card used to make the second one.
               ROUND(SUM(CASE WHEN efficiency_kwh_100km IS NOT NULL
                              THEN distance_km END), 1)                      AS avg_efficiency_km,
               -- "Best" must come from a real trip, not a 3 km downhill coast or a glitch frame
               -- (#86): a min-distance floor keeps this metric representative of the car.
               ROUND(MIN(CASE WHEN efficiency_kwh_100km > 0 AND distance_km >= 15
                              THEN efficiency_kwh_100km END), 1) AS best_efficiency,
               ROUND(SUM(regen_kwh), 2)                                      AS total_regen_kwh,
               ROUND(AVG(regen_kwh), 2)                                      AS avg_regen_kwh,
               MIN(started_at)                                               AS _since_trip
           FROM trips WHERE vehicle_id = COALESCE(?, vehicle_id) AND ended_at IS NOT NULL""",
        (_current_vehicle_id(),)
    ).fetchone()
    charges = db.execute(
        """SELECT
               COUNT(*)                         AS charge_count,
               ROUND(SUM(energy_added_kwh), 2)  AS total_kwh_charged,
               ROUND(SUM(cost), 2)              AS total_cost,
               MIN(ended_at)                    AS _since_charge
           FROM charges WHERE vehicle_id = COALESCE(?, vehicle_id) AND ended_at IS NOT NULL""",
        (_current_vehicle_id(),)
    ).fetchone()
    t = dict(trips) if trips else {}
    c = dict(charges) if charges else {}
    total_kwh = t.get("total_kwh_used") or 0
    total_regen = t.get("total_regen_kwh") or 0
    t["regen_pct"] = round(total_regen / total_kwh * 100, 1) if total_kwh > 0 else None
    # When this page's window opens. Every figure on Statistics is Mate's OWN record — not one of
    # them is the car's lifetime counter, and the page never shows that counter at all. Silvio's
    # call, 05/08: say it once at the top rather than defending each card from the misreading.
    # Measured on his B10 the same day: 4803 km on the dashboard against 1877 recorded here.
    out = {**t, **c}
    out["since"] = min([s for s in (out.pop("_since_trip", None),
                                    out.pop("_since_charge", None)) if s], default=None)
    return out


def get_charge_stats() -> dict:
    db = _get()
    # The middle branch only exists where the column does — see _charges_have_gross.
    _g = ("WHEN gross_kwh IS NOT NULL AND gross_kwh > 0 THEN gross_kwh "
          if _charges_have_gross(db) else "")
    row = db.execute(
        f"""SELECT
               COUNT(*)                            AS session_count,
               -- billed energy, in _billed_kwh's own order: the wallbox counter, then the
               -- charger's own kWh where the owner typed it (#222), then the battery
               ROUND(SUM(CASE WHEN location_type='HOME' AND ac_energy_kwh IS NOT NULL AND ac_energy_kwh > 0
                              THEN ac_energy_kwh
                              {_g}
                              ELSE energy_added_kwh END), 2)  AS total_kwh,
               ROUND(AVG(duration_min / 60.0), 1) AS avg_duration_h,
               ROUND(SUM(cost), 2)                AS total_cost,
               -- the SAME billed energy, but only over the charges that HAVE a cost: the €/kWh
               -- divides by this, never by total_kwh (see price_coverage)
               COUNT(cost)                        AS priced_count,
               ROUND(SUM(CASE WHEN cost IS NOT NULL THEN
                              CASE WHEN location_type='HOME' AND ac_energy_kwh IS NOT NULL AND ac_energy_kwh > 0
                                   THEN ac_energy_kwh
                                   {_g}
                                   ELSE energy_added_kwh END END), 2) AS priced_kwh,
               ROUND(AVG(end_soc - start_soc), 1) AS avg_soc_delta,
               ROUND(MAX(max_power_kw), 2)        AS peak_power_kw
           FROM charges
           WHERE vehicle_id = COALESCE(?, vehicle_id) AND ended_at IS NOT NULL""",
        (_current_vehicle_id(),)
    ).fetchone()
    if not row:
        return {}
    d = dict(row)
    # Four of those figures COUNT or AVERAGE charges, and a plug-in the car split into several rows
    # would count several times: the session tally, the €/kWh denominator, and the two averages.
    # They are recomputed over the composed groups. The SUMs above are left alone — a group's
    # pieces sum to the group, so including the children is not just harmless, it is required.
    groups = get_charges(limit=1_000_000)
    d["session_count"] = len(groups)
    d["priced_count"] = sum(1 for g in groups if g.get("cost") is not None)
    # A reconstructed charge has no duration to average: `duration_min` there is the length of the
    # BLACKOUT the SoC jump was found across, not of any charging. On the real database 27 observed
    # charges average 193 min, and the single reconstructed #55 (1091.5 min) pushed the card to
    # 3.8 h — +18% from one row. Left out, and counted so the card can say so; `session_count`
    # above still counts every charge, which is why staying silent here would make two tiles
    # disagree for no visible reason. → [[feedback-two-numbers-one-word]]
    durs = [g["duration_min"] for g in groups
            if g.get("duration_min") is not None and not g.get("reconstructed")]
    d["duration_excluded"] = sum(1 for g in groups
                                 if g.get("duration_min") is not None and g.get("reconstructed"))
    d["avg_duration_h"] = round(sum(durs) / len(durs) / 60.0, 1) if durs else None
    deltas = [g["end_soc"] - g["start_soc"] for g in groups
              if g.get("end_soc") is not None and g.get("start_soc") is not None]
    d["avg_soc_delta"] = round(sum(deltas) / len(deltas), 1) if deltas else None
    d.update(price_coverage(d.get("total_cost"), d.get("priced_kwh"),
                            d.get("priced_count"), d.get("session_count")))
    return d


_OFFLINE_GAPS_SHOWN = 20


def offline_gaps_summary(year: Optional[int] = None, month: Optional[int] = None) -> dict:
    """Kilometres the car covered while the cloud had nothing new to say — measured, and belonging
    to no trip.

    With `year`/`month`, only that month — the Viaggi calendar wants the month it is showing, while
    Statistics wants the running total (that page has no period at all, and its neighbours are all
    since-the-beginning). ⚠️ The month is the one on the clock AT HOME: the timestamps here are UTC
    like every other in the database, and 23:30 UTC on 31 July is already August in Rome. Filtering
    on UTC would put a figure under a calendar grid that disagrees with it.

    They used to be welded onto whichever trip opened next, which put them on the wrong trip and,
    because a trip's start time is the moment the link returned, on the wrong DAY. The silence can
    hold the tail of one drive, a night's parking and the start of another; nothing in the data says
    how it divides, so nothing here guesses. The four figures are what IS known.

    ⚠️ The euro is `None` when Mate has no average price — that average divides over PRICED charges
    alone (`price_coverage`), so an install that never typed a tariff has none. Printing 0,00 € there
    would invent a free kilometre; the caller shows the other three and leaves the money out.

    The window list is capped and the cap is REPORTED: a silent truncation reads as "these are all
    of them" when it is not."""
    empty = {"count": 0, "total_km": 0.0, "total_soc": 0.0, "total_kwh": 0.0,
             "cost": None, "avg_price": None, "windows": [], "shown": 0}
    try:
        rows = _get().execute(
            "SELECT started_at, ended_at, distance_km, soc_start, soc_end, energy_kwh"
            "  FROM offline_gaps WHERE vehicle_id = COALESCE(?, vehicle_id)"
            " ORDER BY started_at", (_current_vehicle_id(),)).fetchall()
    except sqlite3.Error:
        return empty                      # a card must never take the page down
    if year and month:
        def _in_month(r):
            d = _local_dt(r["started_at"])
            return d is not None and d.year == year and d.month == month
        rows = [r for r in rows if _in_month(r)]
    if not rows:
        return empty

    total_km = round(sum(r["distance_km"] or 0 for r in rows), 1)
    # SoC that only ever went DOWN: a rise inside a window is a charge, and counting it as a
    # negative loss would quietly refund energy the car never spent on the road.
    total_soc = round(sum(max((r["soc_start"] or 0) - (r["soc_end"] or 0), 0.0) for r in rows), 1)
    total_kwh = round(sum(r["energy_kwh"] or 0 for r in rows), 2)

    avg_price = None
    try:
        avg_price = get_charge_stats().get("avg_price")
    except Exception:  # noqa: BLE001 — the money is a decoration on top of the measurement
        pass
    cost = round(total_kwh * avg_price, 2) if (avg_price and total_kwh) else None

    windows = [{"start": r["started_at"], "end": r["ended_at"],
                "km": r["distance_km"], "soc": round(max((r["soc_start"] or 0)
                                                         - (r["soc_end"] or 0), 0.0), 1),
                "kwh": r["energy_kwh"]} for r in rows][-_OFFLINE_GAPS_SHOWN:]
    return {"count": len(rows), "total_km": total_km, "total_soc": total_soc,
            "total_kwh": total_kwh, "cost": cost, "avg_price": avg_price,
            "windows": windows, "shown": len(windows)}


def get_ac_dc_stats() -> dict:
    """Count + energy of AC vs DC charge sessions. DC = charge_type 'DC', or (when not
    set) a measured peak power above 11 kW (AC tops out at ~11 kW; DC is faster)."""
    # Read the composed charges, not the stored rows. Excluding merged children from a query here
    # would have counted right and lost their kilowatt-hours — the split pieces would simply stop
    # being AC or DC energy at all. The group carries both: one session, all the energy.
    rows = get_charges(limit=1_000_000)
    ac = {"count": 0, "kwh": 0.0}
    dc = {"count": 0, "kwh": 0.0}
    for r in rows:
        ct = r["charge_type"]
        is_dc = ct == "DC" or (ct is None and (r["max_power_kw"] or 0) > 11)
        b = dc if is_dc else ac
        b["count"] += 1
        # _billed_kwh, like everything else on this page. It used to sum the battery energy while
        # ENERGIA TOTALE right below summed the billed one — two totals on ONE screen that did not
        # add up, off by the whole conversion loss (19.4 kWh on the test data). Older than today's
        # change; it just became impossible to miss once the totals beside it agreed.
        b["kwh"] += _billed_kwh(dict(r))
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
        # priced-only twins: the €/kWh denominator (an unpriced charge brings kWh but no €)
        "charge_count_priced": 0, "charge_kwh_priced": 0.0,
        "unconfirmed": 0,
        # REEV — the petrol side of the month, so a range-extender owner gets a report about the
        # car he drives instead of one about half of it (Silvio's call, beta #11/#22). Litres BURNED
        # come from the trips; litres and € BOUGHT come from the refuels he entered, because a tank
        # is filled on one day and burned over the next fortnight — the two are different questions
        # and summing them would answer neither.
        "fuel_l_burned": 0.0, "fuel_engine_km": 0.0,
        # Electric consumption MEASURED by the car (getEC per trip), so a month with generator
        # driving in it still gets an electric average. avg_efficiency above cannot: on a
        # range-extender trip Mate blanks efficiency_kwh_100km on purpose, and that average then
        # covers only the part of the month driven on the battery alone — without saying so.
        "_ec_kwh": 0.0, "_ec_km": 0.0, "avg_efficiency_measured": None,
        "refuel_count": 0, "refuel_l": 0.0, "refuel_cost": 0.0,
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
        # Same rule as the Statistics card: a reconstructed trip's "duration" is the blackout it
        # was found across, so it never becomes driving time — here too, or the monthly report and
        # the page would print different hours for the same month.
        if not tr.get("reconstructed"):
            b["drive_min"]  += tr.get("duration_min") or 0
        if eff and km > 0:
            b["_eff_wsum"]  += km * eff
            b["_eff_wdist"] += km
        b["_days"].setdefault(dt.day, {"km": 0.0, "cost": 0.0})["km"] += km
        # engine_km, not km: the L/100 km of a range-extender trip is over the distance the
        # generator actually drove, not the whole trip — the same basis reev_fuel_summary uses.
        b["fuel_l_burned"]  += tr.get("fuel_used_l") or 0
        # Only trips the enrichment has actually answered for: a NULL is "not measured yet", and
        # counting its kilometres with zero energy would drag the average down for free.
        _ec = tr.get("ec_driving")
        if _ec is not None and km > 0:
            b["_ec_kwh"] += _ec
            b["_ec_km"]  += km
        b["fuel_engine_km"] += tr.get("engine_km") or 0

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
            b["charge_count_priced"] += 1
            b["charge_kwh_priced"]   += kwh
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

    # REEV refuels — what was BOUGHT that month. Own table, own pass: they are typed in by the
    # owner and exist independently of any trip, so a month can hold a refuel and no engine-on
    # driving (filled on the 31st) or engine-on driving and no refuel (running down the tank).
    # Best-effort: the table only exists once a refuel has been entered.
    try:
        db = _get()
        _ensure_fuel_purchases(db)
        for f in db.execute(
                "SELECT ts, liters, total_cost FROM fuel_purchases "
                "WHERE vehicle_id = COALESCE(?, vehicle_id)", (_current_vehicle_id(),)).fetchall():
            dt = _local_dt(f["ts"])
            if dt is None:
                continue
            b = buckets.setdefault(dt.strftime("%Y-%m"), _report_bucket())
            b["refuel_count"] += 1
            b["refuel_l"]     += f["liters"] or 0
            b["refuel_cost"]  += f["total_cost"] or 0
            if f["total_cost"]:
                b["_days"].setdefault(dt.day, {"km": 0.0, "cost": 0.0})["cost"] += f["total_cost"]
    except sqlite3.Error:
        pass

    for b in buckets.values():
        if b["_eff_wdist"] > 0:
            b["avg_efficiency"] = round(b["_eff_wsum"] / b["_eff_wdist"], 1)
        # The measured twin, over EVERY trip the cloud answered for — generator ones included.
        # ⚠️ getEC counts what LEFT THE BATTERY: on a generator trip the push that goes from the
        # range-extender straight to the wheels never passes through the pack and is invisible
        # here. That makes this the honest "electricity used" and NOT "what the motor consumed" —
        # the litres beside it are the other half of that answer.
        if b["_ec_km"] > 0:
            b["avg_efficiency_measured"] = round(b["_ec_kwh"] / b["_ec_km"] * 100, 1)
        for k in ("total_km", "total_kwh_used", "regen_kwh", "charge_kwh", "charge_cost",
                  "charge_kwh_priced"):
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

    # The current month always exists, even before its first trip. Without this the page silently
    # fell back to the newest month that HAD data: on the 2nd of August, with nothing driven yet,
    # it opened on "July 2026" showing July's totals — right numbers under the wrong month for
    # anyone who doesn't read the header. An empty month is an answer ("nothing yet"), not a
    # missing page. Costs nothing when the month already has data.
    buckets.setdefault(datetime.now(_local_tz()).strftime("%Y-%m"), _report_bucket())

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

    # A month with nothing in it yet has nothing to compare: every delta would read −100 % against
    # the previous month, which describes the calendar rather than the driving.
    month_empty = cur["trip_count"] == 0 and cur["charge_count"] == 0

    deltas = None
    if prev and not month_empty:
        # The arrow beside the consumption tile. That tile shows the car's OWN metered figure for
        # the month (getEC over the month bounds), while this delta was computed from
        # efficiency_kwh_100km — a different quantity, so the arrow could disagree with the number
        # it sits next to. Worse on a range-extender, where that field is deliberately blank on
        # generator trips and the comparison then ran on the electric part of each month alone.
        # avg_efficiency_measured is the same metering as the tile, summed per trip, so both months
        # are weighed on one basis. Falls back when the enrichment has not answered yet.
        eff_d = None
        _cur_e = cur.get("avg_efficiency_measured") or cur["avg_efficiency"]
        _prv_e = prev.get("avg_efficiency_measured") or prev["avg_efficiency"]
        if _cur_e is not None and _prv_e is not None:
            eff_d = _delta(_cur_e, _prv_e)
        deltas = {
            "km":         _delta(cur["total_km"], prev["total_km"]),
            "kwh_used":   _delta(cur["total_kwh_used"], prev["total_kwh_used"]),
            "cost":       _delta(cur["charge_cost"], prev["charge_cost"]),
            "charge_kwh": _delta(cur["charge_kwh"], prev["charge_kwh"]),
            "efficiency": eff_d,
        }

    # Over the priced charges alone — dividing by charge_kwh (which counts the unpriced ones too)
    # under-reported the month's price; one untagged charge out of ten was worth −20%.
    price_cov = price_coverage(cur["charge_cost"] if cur["has_cost"] else None,
                               cur["charge_kwh_priced"], cur["charge_count_priced"],
                               cur["charge_count"])
    avg_price = price_cov["avg_price"]

    ndays = calendar.monthrange(int(month[:4]), int(month[5:7]))[1]
    daily = [{"day": d,
              "km":   cur["_days"].get(d, {}).get("km", 0.0),
              "cost": cur["_days"].get(d, {}).get("cost", 0.0)}
             for d in range(1, ndays + 1)]

    return {
        "has_data": True, "month_empty": month_empty,
        "month": month, "label": _label(month),
        "prev_month": older[0] if older else None,
        "next_month": newer[-1] if newer else None,
        "months": [{"key": m, "label": _label(m)} for m in months_desc],
        "cur": cur, "prev": prev, "prev_label": _label(prev_key) if prev else None,
        "deltas": deltas, "avg_price": avg_price, "price_cov": price_cov, "daily": daily,
    }


# ── Battery health (SoH) ───────────────────────────────────────────────────────

def get_battery_capacity_kwh() -> float:
    """Configured (nominal) usable battery capacity of the SELECTED car, set per-model at first run
    and overridable in Settings. The 100%-SoC reference for the health estimate, the energy balance
    and everything else that turns a percentage into kilowatt-hours.

    🔴 The per-car column has existed since v2.2.0, but only the WRITE side used it: the poller
    computed each car's energies from its own `vehicles.capacity_kwh` while this read — every
    display, every balance — went on returning the one global setting. Single-car that is the same
    number twice. With the picker it is not: choosing a T03 would have shown its 36 kWh pack
    reasoned about as a B10's 65, an 80% overstatement on every figure derived from a percentage.
    Found by a test written for the picker, not by anybody looking at this function.

    The global setting stays the fallback — it is what a database written before v2.2.0 has, and
    what a minimal/schema-less one falls back to."""
    try:
        row = _get().execute(
            "SELECT capacity_kwh FROM vehicles WHERE id = COALESCE(?, id) ORDER BY id LIMIT 1",
            (_current_vehicle_id(),)).fetchone()
        if row and row["capacity_kwh"]:
            return float(row["capacity_kwh"])
    except sqlite3.Error:
        pass
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


_SOH_TOP_CUTOFF_SOC = 95.0    # above this the BMS re-anchors an LFP's counted SoC: points arrive
                              # without matching energy, so they are not part of the capacity sum
_SOH_SOC_BUDGET = 200.0       # how many SoC points the headline pools over (see get_battery_health)


def _charge_energy_below_soc(db, start: str, end: str | None, cap_soc: float):
    """(energy, SoC reached) for the part of a charge BELOW `cap_soc`, or None when there is none.

    ∫|V·I|dt over the logged samples (trapezoidal, signals 1177/1178 — the same source as the
    power-curve chart), stopped where the SoC scale stops being energy. Measured, not derived from
    SoC, so dividing it by the SoC delta tracks battery ageing instead of being circular.
    On an LFP the open-circuit voltage is flat across the middle of the range, so the BMS counts
    coulombs and drifts; near the top the curve finally rises and it re-anchors — adding SoC points
    that no energy paid for. Dividing measured energy by a delta containing them under-states the
    pack, worst on a short top-up where they are most of the delta: @riri19's 94.9 % (#205), and a
    12.9-point charge in Silvio's own history that read 57.7 kWh against 64-67 everywhere else.

    Truncating rather than discarding is the point. A charge to 100 % is the one that re-calibrates
    the pack and belongs in the history; only its last few points are excluded from the arithmetic."""
    if end:
        lo, hi, excl = _power_window_bounds(db, start, end)
        rows = db.execute(
            "SELECT recorded_at, soc, charge_voltage_v, charge_current_a FROM positions "
            "WHERE vehicle_id = COALESCE(?, vehicle_id) AND charging = 1 AND recorded_at >= ? "
            "AND recorded_at " + ("<" if excl else "<=") + " ? ORDER BY recorded_at",
            (_current_vehicle_id(), lo, hi)).fetchall()
    else:
        rows = db.execute(
            "SELECT recorded_at, soc, charge_voltage_v, charge_current_a FROM positions "
            "WHERE vehicle_id = COALESCE(?, vehicle_id) AND charging = 1 AND recorded_at >= ? "
            "ORDER BY recorded_at", (_current_vehicle_id(), start)).fetchall()
    energy, prev_t, prev_p, prev_soc, reached, covered = 0.0, None, 0.0, None, None, 0.0
    for r in rows:
        if r["soc"] is not None and r["soc"] > cap_soc:
            break
        try:
            t = datetime.fromisoformat(str(r["recorded_at"]).replace(" ", "T").rstrip("Z"))
        except Exception:  # noqa: BLE001
            continue
        p = abs((r["charge_voltage_v"] or 0) * (r["charge_current_a"] or 0)) / 1000.0
        if prev_t is not None:
            dt_h = (t - prev_t).total_seconds() / 3600.0
            if 0 < dt_h <= 0.25:                      # same gap guard as the full integral
                energy += (p + prev_p) / 2.0 * dt_h
                # 🔴 …and the SoC that rose across THAT interval, so the two halves of
                # energy ÷ ΔSoC span the same window. They did not: the integral skipped every
                # gap over 15 minutes while the SoC side counted the whole rise, gap included, so
                # each quiet minute pushed the estimate down and the pack read as aged — 67.4 kWh
                # became 54.7 with one hour of silence, with nothing on the page to say why
                # (#241, @riri19). Only rises: a dip is BMS jitter, never negative capacity.
                if r["soc"] is not None and prev_soc is not None and r["soc"] > prev_soc:
                    covered += r["soc"] - prev_soc
        prev_t, prev_p = t, p
        if r["soc"] is not None:
            reached = r["soc"]
            prev_soc = r["soc"]
    # `reached` stays None when not one sample carried a SoC (a partial frame, an older database).
    # The caller then falls back to the charge's own delta: we cannot truncate what we cannot see,
    # and an estimate with a known bias beats making the whole page disappear. `covered` is 0 in
    # that same case, and the caller treats it the same way.
    return (energy, reached, covered) if rows else None


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
    # Whole charges only. This is the reader a split session lies to hardest: it divides measured
    # energy by the SoC delta, and each piece hands it a fraction of the energy over a fraction of
    # the delta — two points estimating a pack the car does not have. Merged, the window spans the
    # whole session again (first start to last end) and the delta is the real one.
    rows = db.execute(
        "SELECT id, started_at, ended_at, start_soc, end_soc, charge_type "
        "FROM charges WHERE vehicle_id = COALESCE(?, vehicle_id) AND ended_at IS NOT NULL "
        + ("AND merged_into_id IS NULL " if _charges_have_merge(db) else "")
        + "AND start_soc IS NOT NULL "
        "AND end_soc IS NOT NULL ORDER BY started_at",
        (_current_vehicle_id(),)
    ).fetchall()
    _kids = _charge_children_by_parent(db)
    rows = [_charge_group_stats(dict(r), _kids.get(r["id"], [])) for r in rows]
    points = []
    for r in rows:
        delta = (r["end_soc"] or 0) - (r["start_soc"] or 0)
        if delta < min_soc_delta:                      # tiny top-ups → huge relative error
            continue
        # Capacity comes from the part of the charge BELOW the re-anchor zone; everything the user
        # SEES about the charge (its real delta, where it ended) is unchanged.
        below = _charge_energy_below_soc(db, r["started_at"], r["ended_at"], _SOH_TOP_CUTOFF_SOC)
        if not below:
            continue
        energy, reached, covered = below
        # The SoC that rose across the intervals whose energy was counted — the denominator that
        # matches this numerator. Falls back to the old whole-charge delta only when no sample
        # carried a SoC at all, which is also when `reached` is None (#241).
        used = covered if covered > 0 else (delta if reached is None
                                            else reached - (r["start_soc"] or 0))
        if used < min_soc_delta:                       # nothing left once the top is set aside
            continue
        if energy <= 0.1:                              # no usable telemetry (pruned / AC-only meter)
            continue
        est = energy / (used / 100.0)
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
            "soc_delta_used": round(used, 1),
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

    # Pool the recent charges instead of averaging their ratios: total energy over total SoC
    # covered. A charge then counts in proportion to how much of the scale it actually spanned —
    # @riri19's own suggestion (#205) — so a 13-point top-up can no longer weigh as much as a
    # 57-point charge, and nothing has to be thrown away to achieve it. The window is measured in
    # SoC POINTS rather than in number of charges, so someone who tops up often and someone who
    # charges deeply stand on the same amount of evidence.
    window, e_sum, d_sum = [], 0.0, 0.0
    for p in reversed(valid):
        window.append(p)
        e_sum += p["energy_kwh"]
        d_sum += p["soc_delta_used"]
        if d_sum >= _SOH_SOC_BUDGET:
            break
    if window and d_sum > 0:
        latest_cap = round(e_sum / (d_sum / 100.0), 1)
        latest_soh = round(latest_cap / nominal * 100, 1) if nominal else None
        # The scatter of the individual estimates behind that figure. It is a PRECISION, not an
        # accuracy: the divisor is still a counted SoC, so a tight band means the charges agree
        # with each other, not that the pack is truly this size. Shown so the headline stops
        # reading like a lab measurement (riri19's last point). One charge has no scatter.
        caps = [p["capacity_kwh"] for p in window]
        spread = round(statistics.pstdev(caps), 2) if len(caps) > 1 else None
    else:
        latest_cap = latest_soh = spread = None
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
        "latest_spread_kwh": spread,
        "latest_spread_pct": round(spread / nominal * 100, 1) if (spread and nominal) else None,
        "window_count": len(window),
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
# A parked stretch this long that produced NO bar is worth naming in the diagnostics bundle. Below
# it, a car reports dozens of short stops a day and listing them would bury the one that matters.
_VAMPIRE_REJECT_MIN_HOURS = 1.0


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
    # 🔴 The parks that produced NO bar, and why. Without this the page and the bundle could only
    # say "nothing after the 5th", and there was no way to tell a stop that never happened from one
    # the car reported flat — #241, where a 19.5-hour park left no trace anywhere and two separate
    # theories about it turned out to be wrong. A rejection is a fact about the data; it belongs
    # next to the acceptances. → [[signal-absent-is-not-signal-zero]]
    rejected = []
    agg = {"drop": 0.0, "hours": 0.0}

    def _flush(w, ongoing=False, close=None, woke_driving=False):
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
        if not (hours >= min_hours and drop_r >= floor - 1e-9) and hours >= _VAMPIRE_REJECT_MIN_HOURS:
            # Named, not swallowed. A flat park is the one that used to leave nothing at all:
            # the counters only ever saw windows that already cleared the noise floor, so
            # "the car reported the same SoC for nineteen hours" and "the car was never parked"
            # arrived on screen as the same blank.
            rejected.append({
                "start": t0.isoformat(), "end": t1.isoformat(), "hours": round(hours, 1),
                "soc_start": round(w["soc0"], 1), "soc_end": round(soc_end, 1),
                "drop_pct": drop_r,
                # 🔴 `woke_driving` first, and it is not a nicety. The park is closed by the
                # odometer guard below WITHOUT the wake-close, on purpose: the car had already
                # covered ground when it reported again, so its SoC now carries driving
                # consumption and calling that standby would inflate the drain. The refusal is
                # right — reading "flat" for it was not. On a car whose cloud freezes the SoC
                # while parked and only refreshes once it is moving, this is EVERY park, which is
                # why riri19's chart simply stopped (#241).
                "why": ("woke_driving" if woke_driving else
                        "short" if hours < min_hours else
                        "flat" if drop_r <= 0 else "below_noise_floor"),
                "ongoing": ongoing,
            })
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
            _flush(cur, woke_driving=True)
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
    # The rejections are capped like the windows, and the cap is REPORTED: a silent truncation
    # reads as "these are all of them" when it is not. → [[feedback-a-search-needs-an-upper-bound]]
    rejected_total = len(rejected)
    return {"windows": charted, "count": len(charted),
            "measurable_count": measurable, "below_threshold": measurable - len(charted),
            "active_use_count": active_use_count,
            "min_drop_pct": round(min_drop_pct, 1),
            # The user's own minimum park length, so the page can name it when that is what
            # rejected the most recent stop. `reject_min_hours` below is a different number:
            # the floor under which a park is not even worth listing as rejected.
            "min_hours": round(min_hours, 1),
            "typical_pct_per_day": typical, "lookback_days": lookback_days,
            "rejected": rejected[-limit:], "rejected_total": rejected_total,
            "reject_min_hours": _VAMPIRE_REJECT_MIN_HOURS}


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

_TRACK_GAP_MULTIPLIER = 3.0   # a jump this many times the trip's own median sample interval...
_TRACK_GAP_MIN_S = 60         # ...but never under a minute — keeps normal jitter at a fast
                              # poll rate from being mistaken for a real signal loss


def _split_track_gaps(rows: list[dict]) -> list[dict]:
    """One trip's time-ordered GPS rows → alternating solid/gap segments, so a real signal-loss
    stretch (tunnel, dead zone, a cloud hiccup) draws as a short dashed bridge instead of a solid
    line implying an actually-driven straight path — same treatment the trip-profile chart
    already gives a gap in SoC/speed (blank rather than joined). The threshold is relative to
    THIS trip's own typical sampling interval (median inter-sample delay × _TRACK_GAP_MULTIPLIER,
    floored at _TRACK_GAP_MIN_S), so it self-adjusts to whatever driving poll interval is
    configured instead of a hardcoded cadence.

    Each dict is {"points": [[lat,lon], ...], "gap": bool}; a gap segment is always exactly its
    two bounding (last-known, first-resumed) points. rows must be pre-sorted chronologically."""
    n = len(rows)
    if n == 0:
        return []
    if n < 2:
        return [{"points": [[round(rows[0]["latitude"], 5), round(rows[0]["longitude"], 5)]], "gap": False}]

    def pt(r):
        return [round(r["latitude"], 5), round(r["longitude"], 5)]

    times = [_trip_epoch(r["recorded_at"]) for r in rows]
    deltas = sorted(times[i] - times[i - 1] for i in range(1, n)
                    if times[i] is not None and times[i - 1] is not None and times[i] > times[i - 1])
    threshold = max(deltas[len(deltas) // 2] * _TRACK_GAP_MULTIPLIER, _TRACK_GAP_MIN_S) if deltas else _TRACK_GAP_MIN_S

    segments = []
    run = [rows[0]]
    for i in range(1, n):
        t0, t1 = times[i - 1], times[i]
        if t0 is not None and t1 is not None and (t1 - t0) > threshold:
            if len(run) >= 2:
                segments.append({"points": [pt(r) for r in run], "gap": False})
            segments.append({"points": [pt(rows[i - 1]), pt(rows[i])], "gap": True})
            run = [rows[i]]
        else:
            run.append(rows[i])
    if len(run) >= 2:
        segments.append({"points": [pt(r) for r in run], "gap": False})
    return segments


def _downsample_points(pts: list, keep: int) -> list:
    """Evenly reduce a plain [lat,lon] list to ``keep`` points, always ending on the real last
    point — the same index math _rows_to_segments has always used, factored out so it can be
    applied per solid RUN now instead of once per whole trip."""
    if keep >= len(pts):
        return pts
    st = len(pts) / keep
    ds = [pts[int(i * st)] for i in range(keep)]
    ds[-1] = pts[-1]
    return ds


def _rows_to_segments(rows, max_points: int) -> list[list[dict]]:
    """Group ordered (trip_id, lat, lon, recorded_at) rows into one track per trip (never joined
    across trips), each track split into alternating solid/gap-bridge segments (see
    _split_track_gaps). Solid runs are proportionally downsampled to ~max_points total (summed
    across every trip) while keeping each run's real first/last point; gap bridges are already
    just their 2 endpoints and are never thinned further. Shared by the global map
    (get_all_track) and the report's month map."""
    tracks: list[list[dict]] = []
    cur_id, cur_rows = None, []
    for r in rows:
        if r["trip_id"] != cur_id:
            if len(cur_rows) >= 2:
                tracks.append(_split_track_gaps(cur_rows))
            cur_rows, cur_id = [], r["trip_id"]
        cur_rows.append(r)
    if len(cur_rows) >= 2:
        tracks.append(_split_track_gaps(cur_rows))

    total = sum(len(seg["points"]) for t in tracks for seg in t if not seg["gap"])
    if total <= max_points or total == 0:
        return tracks
    step = total / max_points
    out = []
    for t in tracks:
        new_t = []
        for seg in t:
            if seg["gap"]:
                new_t.append(seg)
                continue
            keep = max(2, int(len(seg["points"]) / step))
            new_t.append({"points": _downsample_points(seg["points"], keep), "gap": False})
        out.append(new_t)
    return out


def get_all_track(max_points: int = 12000, max_trips: Optional[int] = None) -> list[list[dict]]:
    """Every trip's GPS track as a list of tracks (one per trip, never joined across trips),
    each itself a list of {"points": [[lat,lon],...], "gap": bool} segments — so the global map
    draws the actual driven roads as connected lines, with a real signal-loss stretch shown as a
    dashed bridge (_split_track_gaps) instead of a solid line implying an actually-driven path.
    Downsampled to roughly ``max_points`` total while always keeping each run's first and last
    point, so the lines stay continuous even when zoomed in.

    `max_trips` (None/0 = every trip) caps the map to the N most recently STARTED trips — a long
    driving history otherwise leaves the whole map a solid mess of hundreds of overlapping lines.
    Capping the trip COUNT (not just the point budget) also means the SAME max_points is spent on
    fewer trips, so each kept trip's own line stays closer to the actually-driven road instead of
    being thinned into long chords by a budget split hundreds of ways."""
    db = _get()
    trip_filter = "SELECT id FROM trips WHERE vehicle_id = COALESCE(?, vehicle_id)"
    params: list = [_current_vehicle_id()]
    if max_trips:
        trip_filter += " ORDER BY started_at DESC LIMIT ?"
        params.append(max_trips)
    rows = db.execute(
        f"SELECT trip_id, latitude, longitude, recorded_at FROM trip_positions "
        f"WHERE trip_id IN ({trip_filter}) "
        "AND latitude IS NOT NULL AND longitude IS NOT NULL ORDER BY trip_id, id",
        params
    ).fetchall()
    return _rows_to_segments(rows, max_points)


def get_month_track(month: str, max_points: int = 8000) -> list[list[dict]]:
    """GPS tracks for every trip STARTED in the given local 'YYYY-MM' — the report's month
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
        "SELECT trip_id, latitude, longitude, recorded_at FROM trip_positions "
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


def trip_local_start_hhmm(trip_id: int) -> Optional[str]:
    """A trip's start as HH:MM in the display time zone — for naming trips inside a message rather
    than calling them "the adjacent one" (beta #19). None when the trip or its start is missing."""
    row = _get().execute(
        "SELECT started_at FROM trips WHERE id = ? AND vehicle_id = COALESCE(?, vehicle_id)",
        (trip_id, _current_vehicle_id())).fetchone()
    if not row or not row["started_at"]:
        return None
    dt = _local_dt(row["started_at"])
    return dt.strftime("%H:%M") if dt else None
