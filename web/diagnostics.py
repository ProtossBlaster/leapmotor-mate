"""Diagnostics helpers for the Settings → Diagnostics card.

Gathers a self-service support snapshot (version, model, DB stats, feature flags, current
state) and reads the rotating log files both processes write under the data dir, so a user
hitting a problem can copy/download logs + context to attach to a GitHub issue — instead of
us asking them to dig through Docker / Home-Assistant add-on logs by hand.

Everything here is read-only and redacts obvious secrets + the VIN before it leaves the box.
"""
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import db_reader

# Coordinate signal IDs stripped from the raw-signal dump before it leaves the box, so the
# bundle can be shared publicly without revealing where the car (home) is. 3724/3725 = lon/lat,
# 2190/2191 = fallbacks, 2/3 = the signed pair. Everything else in the dict is non-locating.
_GPS_SIGNAL_IDS = {"2", "3", "2190", "2191", "3724", "3725"}


def data_dir() -> Path:
    """The persistent data dir — parent of the DB (set via DB_PATH in run.sh, /data on the add-on)."""
    return Path(os.environ.get("DB_PATH", "/data/leapmotor_mate.db")).parent


POLLER_LOG = "mate-poller.log"
WEB_LOG = "mate-web.log"
_LOG_FILES = {"poller": POLLER_LOG, "web": WEB_LOG}


# ── redaction ────────────────────────────────────────────────────────────────
# Defensive: the app never logs credentials, but a diagnostics bundle is shared publicly,
# so scrub anything that smells like one, plus the 17-char VIN and e-mail addresses.
# Sensitive key names — bare or compound (private_key, access_token, refresh_token…), plus the
# Leapmotor device_id (a long account-bound identifier the API auth logs in the clear).
_SECRET_ROOT = (
    r'(?:passwords?|passwd|passphrase|pwd|pass|pins?|secrets?|tokens?|credentials?|auth|apikey|keys?|'
    r'device[_-]?ids?)')
# An optional word-prefix joined by `_`/`-` (access_token, private_key). The separator is REQUIRED,
# so words that merely CONTAIN a root (monkey, compass, passenger) are never matched.
_SECRET_KEY = r'(?:\w+[_-])?' + _SECRET_ROOT
# key=value / key: value, in plain OR JSON form (optional matching quotes around the key),
# value either a quoted string (spaces kept) or an unquoted run up to a delimiter.
_KV_SECRET_RE = re.compile(
    r'(?i)(["\']?)\b(' + _SECRET_KEY + r')\b\1?\s*[:=]\s*'
    r'''("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|[^\s,;}\n]+)''')
_AUTH_RE = re.compile(r'(?i)\bauthorization\b\s*[:=].*')   # whole header value to EOL
_BEARER_RE = re.compile(r'(?i)\bbearer\s+[\w.\-]+')         # "Bearer <token>" with no key=
# `*` in the local part also catches an already-partly-masked address (sil***@dxc.com → ***@***).
_EMAIL_RE = re.compile(r'\b[\w.+*-]+@[\w.-]+\.\w{2,}\b')
_VIN_RE = re.compile(r'\b([A-HJ-NPR-Z0-9]{17})\b')
# camelCase secret keys (no separator, so the compound regex above misses them): the Leapmotor
# remote-control field `operatePassword`, plus userToken/apiKey-style names. The CAPITALISED
# suffix is required, so plain words (compass, passenger, compassHeading) are never matched.
_CAMEL_SECRET_RE = re.compile(
    r'''(?:["']?)\b([a-z]\w*?(?:Password|Passwd|Pwd|Pin|Token|Secret|Credential|ApiKey|AuthKey))\b["']?\s*[:=]\s*'''
    r'''("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|[^\s,;}\n]+)''')
# A latitude/longitude PAIR in parentheses — the trip-start log "@ (45.4717, 1.5433)". Truncated to
# ~1 decimal (≈10 km) so a publicly-shared bundle can't pinpoint home. Only a paren-wrapped decimal
# pair matches, so SoC / kWh / efficiency numbers in the logs are left untouched.
_COORD_RE = re.compile(r'\(\s*(-?\d{1,3}\.\d)\d*\s*,\s*(-?\d{1,3}\.\d)\d*\s*\)')


def mask_vin(vin: str | None) -> str:
    if not vin:
        return "—"
    return f"{vin[:3]}…{vin[-4:]}" if len(vin) >= 8 else "…"


def _redact(text: str, vin: str | None = None) -> str:
    text = _KV_SECRET_RE.sub(lambda m: f"{m.group(2)}=***", text)
    text = _CAMEL_SECRET_RE.sub(lambda m: f"{m.group(1)}=***", text)   # operatePassword=…
    text = _AUTH_RE.sub("authorization=***", text)
    text = _BEARER_RE.sub("bearer ***", text)
    text = _EMAIL_RE.sub("***@***", text)
    # The real VIN appears lowercase + glued inside the MQTT discovery topic
    # (leapmotor_mate_lfza…820), which the generic uppercase \b regex below can't see — so when we
    # know the car's VIN, replace it literally first, any case.
    if vin:
        text = re.sub(re.escape(vin), mask_vin(vin), text, flags=re.IGNORECASE)
    text = _VIN_RE.sub(lambda m: f"{m.group(1)[:3]}…{m.group(1)[-4:]}", text)
    text = _COORD_RE.sub(lambda m: f"({m.group(1)}…, {m.group(2)}…)", text)
    return text


# ── system snapshot ──────────────────────────────────────────────────────────
def build_system_info(version: str) -> dict:
    """Cheap (no live cloud call) support snapshot for the card + the bundle header."""
    vehicle, settings = db_reader.get_vehicle()
    db = db_reader._get()

    def _count(table: str) -> int:
        try:
            return db.execute(
                f"SELECT COUNT(*) c FROM {table} WHERE vehicle_id = COALESCE(?, vehicle_id)",
                (db_reader._current_vehicle_id(),)).fetchone()["c"]
        except Exception:  # noqa: BLE001
            return -1

    last = None
    try:
        last = db.execute(
            "SELECT recorded_at, soc, gear, charging FROM positions "
            "WHERE vehicle_id = COALESCE(?, vehicle_id) ORDER BY id DESC LIMIT 1",
            (db_reader._current_vehicle_id(),)
        ).fetchone()
    except Exception:  # noqa: BLE001
        pass

    age_min = None
    if last and last["recorded_at"]:
        try:
            age_min = round(
                (datetime.now(timezone.utc) - datetime.fromisoformat(last["recorded_at"]))
                .total_seconds() / 60, 1)
        except (TypeError, ValueError):
            pass

    # Positions date-span — so a "my history vanished" report (e.g. vampire-drain empty, #63) can
    # be diagnosed at a glance: a span far shorter than expected + a non-zero retention = pruning.
    span = "—"
    try:
        r = db.execute("SELECT MIN(recorded_at) a, MAX(recorded_at) b FROM positions "
                       "WHERE vehicle_id = COALESCE(?, vehicle_id) AND recorded_at IS NOT NULL",
                       (db_reader._current_vehicle_id(),)).fetchone()
        if r and r["a"] and r["b"]:
            days = round((datetime.fromisoformat(r["b"]) - datetime.fromisoformat(r["a"]))
                         .total_seconds() / 86400, 1)
            span = f"{r['a'][:10]} → {r['b'][:10]} ({days}d)"
    except Exception:  # noqa: BLE001
        pass

    return {
        "version": version,
        "model": (vehicle or {}).get("car_type") or "—",
        "year": (vehicle or {}).get("year") or "—",
        "vin_masked": mask_vin((vehicle or {}).get("vin")),
        "battery_kwh": settings.get("battery_capacity_kwh", "—"),
        # The SoH denominator, snapshotted the first time the capacity is saved. Without it a
        # bundle cannot answer "why is my battery health above 100%" — the number that decides it
        # was simply not in the file (@danielvilhena, #221). Absent means never snapshotted, in
        # which case the health page falls back to the capacity above.
        "battery_nominal_kwh": settings.get("battery_capacity_nominal_kwh", "— (not set)"),
        "language": settings.get("language", "en"),
        "db_size_mb": round(db_reader.get_db_size_bytes() / 1048576, 1),
        "counts": {"trips": _count("trips"), "charges": _count("charges"),
                   "positions": _count("positions")},
        "poll_parked": settings.get("poll_parked", "30"),
        "poll_driving": settings.get("poll_driving", "10"),
        "retention_days": settings.get("positions_retention_days", "0"),
        "vampire_min_drop_pct": settings.get("vampire_min_drop_pct", "0.2"),
        "vampire_min_hours": settings.get("vampire_min_hours", "1"),
        # 🔴 The two Advanced floors that decide whether a charge is SEEN AT ALL, and neither was
        # in the bundle. #230: @adoewa's `charge_detect_min_a` was **14.5 A** where the default is
        # 2.0, and a home AC charge moves the pack at 11-12 A — so `_is_charging` returned False on
        # every one of 202 polls while the battery filled from 49.8% to 90.0%. The bundle reported
        # the vampire-drain thresholds and not this one; with it the answer was thirty seconds
        # away instead of half a day.
        "charge_detect_min_a": settings.get("charge_detect_min_a", "2.0"),
        "charge_reconstruct_min_pct": settings.get("charge_reconstruct_min_pct", "2.0"),
        "positions_span": span,
        "features": {
            "mqtt": settings.get("mqtt_enabled") == "1",
            # The wallbox TICK, nothing else. It used to be `ha_url or SUPERVISOR_TOKEN`, which
            # under the add-on is True whatever the user chose — so a bundle said "wallbox=True"
            # to someone who had switched it off, and triage contradicted them (#226). The two
            # facts are now separate: `ha` is reachability, `wallbox` is the switch that decides
            # whether the meter is read and billed at all.
            "wallbox": settings.get("wallbox_enabled", "0") == "1",
            "ha": bool(settings.get("ha_url") or os.environ.get("SUPERVISOR_TOKEN")),
            "abrp": settings.get("abrp_enabled") == "1",
            "addon": bool(os.environ.get("SUPERVISOR_TOKEN")),
        },
        "last_poll_iso": last["recorded_at"] if last else None,
        "last_poll_age_min": age_min,
        "last_soc": last["soc"] if last else None,
        "last_gear": last["gear"] if last else None,
        "last_charging": bool(last["charging"]) if last else None,
    }


# ── logs ─────────────────────────────────────────────────────────────────────
def read_log_tail(which: str, lines: int = 200) -> str:
    """Last `lines` of the poller/web log file (redacted). Returns a friendly note if absent."""
    name = _LOG_FILES.get(which)
    if not name:
        return f"(unknown log '{which}')"
    path = data_dir() / name
    if not path.exists():
        return ("(no log file yet — it appears after the next restart, once the file logger is "
                "active. Until then, see the container / Home Assistant add-on log.)")
    try:
        with path.open("r", errors="replace") as fh:
            tail = fh.readlines()[-max(1, min(lines, 2000)):]
        vehicle, _ = db_reader.get_vehicle()
        return _redact("".join(tail), (vehicle or {}).get("vin")).strip() or "(log is empty)"
    except Exception as e:  # noqa: BLE001
        return f"(could not read log: {e})"


def read_full_log(which: str) -> str:
    """The COMPLETE rotating set for `which` — active file + its .1/.2 backups, oldest→newest,
    redacted. Unlike read_log_tail (a 200-line screenful) this carries the full retained window
    (~days of driving), so a shared bundle holds the whole run-up to a problem, not just the tail.
    Missing backups are skipped; a read error on one file degrades to a marker, never an exception."""
    name = _LOG_FILES.get(which)
    if not name:
        return f"(unknown log '{which}')"
    base = data_dir() / name
    # RotatingFileHandler backups: <file>.1 is the newest backup, <file>.2 older → read .2, .1, then
    # the active file so the result reads oldest → newest.
    paths = [base.with_name(f"{name}.{i}") for i in (2, 1)] + [base]
    chunks = []
    for p in paths:
        if not p.exists():
            continue
        try:
            with p.open("r", errors="replace") as fh:
                chunks.append(fh.read())
        except Exception as e:  # noqa: BLE001
            chunks.append(f"(could not read {p.name}: {e})\n")
    if not chunks:
        return ("(no log file yet — it appears after the next restart, once the file logger is "
                "active. Until then, see the container / Home Assistant add-on log.)")
    vehicle, _ = db_reader.get_vehicle()
    return _redact("".join(chunks), (vehicle or {}).get("vin")).strip() or "(log is empty)"


# ── shareable bundle ─────────────────────────────────────────────────────────
_BUNDLE_PARTS = ("info", "poller", "web", "signals")   # user-selectable sections


def _gps_shape_line(signals: dict) -> str:
    """Which coordinate signals the car sends, and the hemisphere sign — no coordinates.

    Stripping all six GPS ids protects the user's address, but it also blinded triage on the one
    bug class that lives in those ids: #158 was a west-of-Greenwich car plotted in the sea, and the
    bundle could not say whether its signed pair (2/3) even arrives. Presence flags plus the
    remembered sign answer that without leaking a position — the sign narrows it to a quadrant of
    the planet, which the bundle's own language field already gives away."""
    def _shape(raw) -> str:
        """+ / − / zero for one signal. The SIGN is the whole question this section exists to
        answer, and presence alone could not answer it: `not in (None, "")` calls a signal that
        arrives as 0 "present", so the line read `2, 3, …` on rop12770's bundle and I spent two
        hours concluding the signed pair was arriving when it may have been arriving empty. A zero
        is not a coordinate — it is the axis saying nothing — and it must not look like one.

        One character per axis leaks nothing the next line does not: a sign narrows the car to a
        quadrant, which the remembered sign and the bundle's own language field already give away.
        The magnitude, which is what would actually locate someone, never appears."""
        if raw in (None, ""):
            return "absent"
        try:
            v = float(raw)
        except (TypeError, ValueError):
            return "unreadable"
        return "zero" if v == 0 else ("−" if v < 0 else "+")

    shapes = ", ".join(f"{i}:{_shape(signals.get(i))}"
                       for i in sorted(_GPS_SIGNAL_IDS, key=int)
                       if signals.get(i) not in (None, ""))
    return (f"signals present : {shapes or 'none'} "
            f"(2/3 = signed · 3724/3725 + 2190/2191 = unsigned magnitudes · "
            f"zero = arrives but says nothing)\n"
            f"remembered sign : lat={db_reader.get_setting('gps_lat_sign') or 'unknown'} "
            f"lon={db_reader.get_setting('gps_lon_sign') or 'unknown'} "
            f"(learned from a signed poll; unknown = never seen one)")


def _signals_section(signals: dict | None, vin: str | None) -> str:
    """The car's raw signal dict as pretty JSON, with the GPS coordinate ids stripped and the
    usual secret/VIN redaction applied — so it's safe to drop straight into a shared bundle."""
    if not signals:
        return ("(no live signals — car asleep or unreachable; use the car briefly, then download "
                "again)")
    clean = {k: v for k, v in signals.items() if k not in _GPS_SIGNAL_IDS}
    return _redact(json.dumps(clean, indent=2, sort_keys=True), vin)


def _temperature_line() -> str:
    """How often each temperature actually arrives, and its last value (#144).

    The two are `core` sensors — the capability system can never hide them — so when an owner sees
    a dash all summer the only question is whether the car sends the signal at all. Nothing in the
    bundle could answer it: 77 000 lines, four mentions of "temp", not one a reading. A count of
    non-NULL over the retained polls answers it in one line, and `0 of N` is a finding rather than
    an absence of information.
    """
    try:
        db = db_reader._get()
        vid = db_reader._current_vehicle_id()
        r = db.execute(
            "SELECT COUNT(*) n, "
            "       SUM(inside_temp IS NOT NULL) cab, SUM(battery_min_temp IS NOT NULL) bat, "
            "       SUM(climate_target_temp IS NOT NULL) tgt "
            "  FROM positions WHERE vehicle_id = COALESCE(?, vehicle_id)", (vid,)).fetchone()
        last = db.execute(
            "SELECT inside_temp, battery_min_temp, climate_target_temp FROM positions "
            " WHERE vehicle_id = COALESCE(?, vehicle_id) ORDER BY id DESC LIMIT 1",
            (vid,)).fetchone()
    except Exception:  # noqa: BLE001
        return "Temperatures : (unavailable)"
    n = int((r["n"] if r else 0) or 0)
    if not n:
        return "Temperatures : (no polls retained)"

    def _one(label, got, val):
        got = int(got or 0)
        seen = f"{got} of {n}"
        if got == 0:
            return f"{label} NEVER ({seen}) — the car does not send this signal"
        return f"{label} {seen}, last {'—' if val is None else f'{val:.1f}'}"

    return ("Temperatures : "
            + " · ".join((_one("cabin", r["cab"], last and last["inside_temp"]),
                          _one("battery", r["bat"], last and last["battery_min_temp"]),
                          _one("A/C target", r["tgt"], last and last["climate_target_temp"]))))


def _vampire_section() -> str:
    """What get_vampire_drain() actually computes — so an 'empty/missing battery-drain chart'
    report (e.g. #63) shows the real count/windows, not just the user's screenshot. Uses BOTH the
    tunables the battery page passes — `min_drop_pct` AND `min_hours` — so the bundle reproduces
    what the user sees: a high threshold that charts nothing shows count=0 here too, with
    measurable>0 revealing the cause. Passing only one of the two made the bundle disagree with the
    page (a raised min_hours charts far fewer, longer parks) and sent triage down a false trail
    (#154)."""
    try:
        mdp = float(db_reader.get_setting("vampire_min_drop_pct", "0.2") or 0.2)
        mh = float(db_reader.get_setting("vampire_min_hours", "1") or 1)
        v = db_reader.get_vampire_drain(min_drop_pct=mdp, min_hours=mh)
    except Exception as e:  # noqa: BLE001
        return f"(vampire calc failed: {e})"
    out = [f"count={v.get('count')}  measurable={v.get('measurable_count')}  "
           # ⚠️ min_drop is a DROP in SoC points, not a rate: get_vampire_drain compares it against
           # `soc0 - soc_end` as-is, so a 12-hour stop and a three-day one face the same number.
           # It read "%/day" here for a long time — the one place we ourselves read during triage.
           f"below_threshold={v.get('below_threshold')}  min_drop_pct={v.get('min_drop_pct')} % SoC  "
           f"typical={v.get('typical_pct_per_day')} %/day  lookback={v.get('lookback_days')}d"]
    for w in (v.get("windows") or [])[-15:]:
        out.append(f"  {str(w['start'])[:16]} → {str(w['end'])[:16]}  {w['drop_pct']}% / {w['hours']}h "
                   f"= {w['pct_per_day']} %/day  reliable={w['reliable']}"
                   + ("  ongoing" if w.get("ongoing") else ""))
    # 🔴 And the parks that produced NOTHING, with the reason. Until #241 the bundle could only
    # show what had been accepted, so "the chart stops on the 5th" had no follow-up question: a
    # park the car reported FLAT (same SoC for nineteen hours, the cloud repeating one frame) and
    # a park that never happened looked identical from here. Reading `why` answers it without
    # anyone digging in the database.
    rej = v.get("rejected") or []
    total = v.get("rejected_total", len(rej))
    out.append(f"  parks that produced NO bar: {total}"
               + (f" (showing the last {len(rej)})" if total > len(rej) else "")
               + f" · listed from {v.get('reject_min_hours')}h up"
               + "  ·  why: short=under your min_hours · flat=SoC never moved in the samples"
               " · below_noise_floor=moved less than 0.2%"
               " · woke_driving=the car had already covered ground when it reported again, so its"
               " drop is not standby alone")
    for w in rej[-15:]:
        out.append(f"    {str(w['start'])[:16]} → {str(w['end'])[:16]}  {w['hours']}h  "
                   f"SoC {w['soc_start']}→{w['soc_end']}  drop {w['drop_pct']}%  ⛔ {w['why']}"
                   + ("  ongoing" if w.get("ongoing") else ""))
    return "\n".join(out)


def _soc_daily_section() -> str:
    """Per-day SoC hi→lo + km driven for the last 14 days — reveals the parked-drain pattern and
    any data gaps behind a 'my history vanished' report (#63)."""
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
        rows = db_reader._get().execute(
            "SELECT substr(recorded_at,1,10) d, COUNT(*) n, MIN(soc) lo, MAX(soc) hi, "
            "MIN(odometer_km) o0, MAX(odometer_km) o1 FROM positions "
            "WHERE vehicle_id = COALESCE(?, vehicle_id) AND soc IS NOT NULL AND recorded_at >= ? "
            "GROUP BY d ORDER BY d", (db_reader._current_vehicle_id(), cutoff)).fetchall()
    except Exception as e:  # noqa: BLE001
        return f"(soc-daily failed: {e})"
    out = []
    for r in rows:
        drove = (r["o1"] or 0) - (r["o0"] or 0)
        out.append(f"  {r['d']}  n={str(r['n']):<5} soc {r['hi']:.1f}→{r['lo']:.1f}  drove {drove:.0f} km")
    return "\n".join(out) or "(no SoC samples in the last 14 days)"


def _cost_wallbox_section() -> str:
    """Pricing config (mode per type + base prices + dynamic sensors), wallbox entity mapping, and the
    last few charges (type · DC · AC/wallbox · cost) — so a 'why is my cost X, kWh Y' report (#109)
    can be diagnosed without guessing. No credentials: entity names + kWh/cost figures only."""
    def _num(x):
        try:
            return f"{float(x):.2f}"
        except (TypeError, ValueError):
            return "—"
    lines = []
    try:
        cfg = db_reader.get_cost_config()
        prices = db_reader.get_charge_prices()
        modes = cfg.get("modes") or {}
        lines.append(f"Cost method  : {cfg.get('method', '-')} · legacy mode {cfg.get('mode', '-')} "
                     f"· TOU bands {len(cfg.get('bands') or [])}")
        lines.append("Mode / type  : " + ", ".join(
            f"{t}={modes.get(t, cfg.get('mode', 'flat'))}" for t in ("HOME", "AC", "FAST", "HPC")))
        lines.append("Base €/kWh   : " + (", ".join(f"{k}={v}" for k, v in sorted(prices.items()))
                                          or "(none set)"))
        for t in ("HOME", "AC", "FAST", "HPC"):
            if modes.get(t, cfg.get("mode", "flat")) == "dynamic":
                lines.append(f"Dynamic sensor ({t}): "
                             f"{db_reader.get_dynamic_price_entity_for(t) or '(none)'}")
    except Exception as e:  # noqa: BLE001
        lines.append(f"(cost config unavailable: {e})")
    try:
        we = db_reader.get_setting("wallbox_entities", "")
        lines.append("Wallbox map  : "
                     + (we or "(no entities mapped → no AC energy → HOME cost billed on DC/SoC)"))
        lines.append(f"Auto-HOME    : {db_reader.get_setting('wallbox_auto_home', '0')}")
    except Exception as e:  # noqa: BLE001
        lines.append(f"(wallbox config unavailable: {e})")
    try:
        db = db_reader._get()
        rows = db.execute(
            "SELECT started_at, location_type, energy_added_kwh, ac_energy_kwh, cost, "
            "wallbox_energy_start_kwh, reconstructed FROM charges "
            "WHERE vehicle_id = COALESCE(?, vehicle_id) AND ended_at IS NOT NULL "
            "ORDER BY started_at DESC LIMIT 8", (db_reader._current_vehicle_id(),)).fetchall()
        lines.append("")
        lines.append("Last charges — DC (battery) · AC (wallbox) · cost · wb baseline · "
                     "show_wb=does-the-card-show-AC · raw ac_energy value (#109 diagnosis):")
        for r in (rows or []):
            ac_raw = r["ac_energy_kwh"]
            # EXACT card condition (charges.html `show_wb`): a truthy AC energy on a HOME charge.
            # If show_wb is False here but cost was billed on AC, ac_energy was lost between billing
            # and render — the smoking gun for "cost on AC, card shows DC".
            show_wb = bool(ac_raw) and r["location_type"] == "HOME"
            lines.append(
                f"  {(r['started_at'] or '')[:16]} {(r['location_type'] or '-'):5} "
                f"DC={_num(r['energy_added_kwh'])} AC={_num(ac_raw)} cost={_num(r['cost'])} "
                f"wb_start={_num(r['wallbox_energy_start_kwh'])} show_wb={show_wb} "
                f"raw_ac={ac_raw!r} recon={r['reconstructed']}")
        if not rows:
            lines.append("  (no charges)")
    except Exception as e:  # noqa: BLE001
        lines.append(f"(charges unavailable: {e})")
    return "\n".join(lines)


# The rows themselves, not figures worked out from them. Silvio, 06/08/26, after #230 took half an
# hour of log archaeology and still could not answer the question: *«non potremmo esportare dal DB
# anche le ricariche e i viaggi… non tutto solo ad esempio gli ultimi 10-15 giorni»*.
_ROWS_DAYS      = 15     # his window
_CHARGES_FLOOR  = 10     # …but never fewer than this: charging weekly gives 2 rows in a fortnight
_TRIPS_CAP      = 200    # …and never more than this, SAID OUT LOUD when it bites


def _rows_cutoff(db, table: str, column: str = "started_at") -> str:
    """15 days back from the NEWEST row, not from today — a bundle downloaded weeks after the car
    went quiet must still carry the last fortnight it actually moved.

    ⚠️ `positions` timestamps its rows `recorded_at`, not `started_at`. Defaulting silently sent the
    query into a sqlite3.Error, which this function answers with "" — "no cutoff" — so the missed-
    charge scan walked all 207 000 rows of a real database instead of a fortnight, and reported
    episodes from two months back. Caught only by running it on a real database; the fixture had
    ten rows and every window contained them.
    """
    try:
        newest = db.execute(
            f"SELECT MAX({column}) FROM {table} "  # noqa: S608 — both are literals, never input
            "WHERE vehicle_id = COALESCE(?, vehicle_id)",
            (db_reader._current_vehicle_id(),)).fetchone()[0]
    except sqlite3.Error:
        return ""
    if not newest:
        return ""
    try:
        return (datetime.fromisoformat(newest) - timedelta(days=_ROWS_DAYS)).isoformat()
    except (TypeError, ValueError):
        return ""


def _n(x, dp: int = 2) -> str:
    try:
        return f"{float(x):.{dp}f}"
    except (TypeError, ValueError):
        return "—"


def _end_hhmm(started: str | None, ended: str | None) -> str:
    """`07:37` when it ended the same day, `+1d 07:37` when it ran past midnight.

    Most home charges are overnight, and a bare `13:25 → 07:37` reads as an 18-hour session that
    somehow went backwards. The day marker costs three characters and removes the double-take.
    """
    if not ended:
        return "IN CORSO"
    hhmm = ended[11:16]
    try:
        days = (datetime.fromisoformat(ended).date() - datetime.fromisoformat(started).date()).days
    except (TypeError, ValueError):
        return hhmm
    return f"+{days}d {hhmm}" if days else hhmm


def _charges_section() -> str:
    """Every charge Mate recorded in the window, as it sits in the table.

    ⚠️ No coordinates, no `location_name`, no `location_url`, no `note`: all four are user-typed or
    locating, and the bundle's header promises GPS removed. `location_type` (HOME/AC/FAST/HPC) says
    what triage needs without saying where.

    Distinct from the `Last charges` line in the cost section, which is the DERIVED #109 diagnosis
    (`show_wb`, `raw_ac`). Two lists, two questions — do not merge them.
    """
    try:
        db = db_reader._get()
        cut = _rows_cutoff(db, "charges")
        rows = db.execute(
            "SELECT started_at, ended_at, start_soc, end_soc, energy_added_kwh, ac_energy_kwh,"
            "       gross_kwh, cost, charge_type, location_type, max_power_kw, duration_min,"
            "       reconstructed, wb_stuck_kwh, manual_entry, is_free, id, merged_into_id"
            "  FROM charges WHERE vehicle_id = COALESCE(?, vehicle_id)"
            " ORDER BY started_at DESC LIMIT ?",
            (db_reader._current_vehicle_id(), max(_CHARGES_FLOOR, 400))).fetchall()
    except sqlite3.Error as e:
        return f"(charges unavailable: {e})"
    if not rows:
        return "(no charges)"
    keep = [r for r in rows if not cut or (r["started_at"] or "") >= cut]
    if len(keep) < _CHARGES_FLOOR:
        keep = rows[:_CHARGES_FLOOR]        # the floor: a fortnight can hold two charges
    out = [f"last {_ROWS_DAYS}d (at least {_CHARGES_FLOOR} rows) · {len(keep)} of {len(rows)} · "
           "DC=battery AC=meter gross=typed-in · recon/stuck/manual = the three known-defect marks"]
    for r in keep:
        out.append(
            f"  {(r['started_at'] or '')[:16]} → {_end_hhmm(r['started_at'], r['ended_at']):8} "
            f"SoC {_n(r['start_soc'], 1)}→{_n(r['end_soc'], 1)}  "
            f"DC={_n(r['energy_added_kwh'])} AC={_n(r['ac_energy_kwh'])} gross={_n(r['gross_kwh'])} "
            f"cost={_n(r['cost'])}  {(r['charge_type'] or '-'):4}/{(r['location_type'] or '-'):6} "
            f"max={_n(r['max_power_kw'], 1)}kW {str(r['duration_min'] or '—'):>4}min  "
            f"recon={r['reconstructed'] or 0} stuck={_n(r['wb_stuck_kwh'])} "
            f"manual={r['manual_entry'] or 0} free={r['is_free'] or 0} "
            # The bundle shows the PIECES, never the composed group: it exists to
            # investigate, and the rows the car reported are the evidence. The marker says
            # which session they were joined into, so a split is legible from here.
            f"#{r['id']} merged={r['merged_into_id'] or '—'}")
    return "\n".join(out)


def _trips_section() -> str:
    """Every trip Mate recorded in the window, as it sits in the table.

    ⚠️ Same privacy rule as the charges above, plus the two geohashes — and `note`, which is free
    text a user may have put an address or a plate in.

    🔴 The cap is announced. A list quietly shortened to 200 reads as "this is everything", and that
    is how a correct file produces a wrong conclusion.
    """
    try:
        db = db_reader._get()
        cut = _rows_cutoff(db, "trips")
        rows = db.execute(
            "SELECT started_at, ended_at, distance_km, start_soc, end_soc, efficiency_kwh_100km,"
            "       ec_kwh, ec_stable, regen_kwh, duration_min, start_odometer_km,"
            "       end_odometer_km, merged_into_id, reconstructed, fuel_start_l, fuel_end_l"
            "  FROM trips WHERE vehicle_id = COALESCE(?, vehicle_id)"
            "   AND (? = '' OR started_at >= ?) ORDER BY started_at DESC",
            (db_reader._current_vehicle_id(), cut, cut)).fetchall()
    except sqlite3.Error as e:
        return f"(trips unavailable: {e})"
    if not rows:
        return "(no trips)"
    shown, cut_off = rows[:_TRIPS_CAP], max(0, len(rows) - _TRIPS_CAP)
    head = f"last {_ROWS_DAYS}d · {len(shown)} trips"
    if cut_off:
        head += f"  ⚠️ TRUNCATED: {cut_off} more in the window are NOT listed (cap {_TRIPS_CAP})"
    out = [head]
    for r in shown:
        fuel = ("" if r["fuel_start_l"] is None and r["fuel_end_l"] is None
                else f"  fuel {_n(r['fuel_start_l'], 3)}→{_n(r['fuel_end_l'], 3)}")
        out.append(
            f"  {(r['started_at'] or '')[:16]} → {(r['ended_at'] or '')[11:16] or 'IN CORSO':5} "
            f"{_n(r['distance_km'], 1):>7} km  SoC {_n(r['start_soc'], 1)}→{_n(r['end_soc'], 1)}  "
            f"eff={_n(r['efficiency_kwh_100km'], 1)} ec={_n(r['ec_kwh'])}"
            f"{'*' if r['ec_stable'] else ' '} regen={_n(r['regen_kwh'])} "
            f"odo {_n(r['start_odometer_km'], 0)}→{_n(r['end_odometer_km'], 0)} "
            f"{str(r['duration_min'] or '—'):>4}min  merged={r['merged_into_id'] or '—'} "
            f"recon={r['reconstructed'] or 0}{fuel}")
    return "\n".join(out)


# The nine settings that silently change how Mate behaves, with the value the code uses when nobody
# has touched them. Kept beside `db_reader.AUDITED_SETTINGS` — that one decides what is RECORDED,
# this one what is REPORTED and what counts as stock.
#
# 🔴 A default written twice drifts, and then this section calls a modified install stock — the exact
# question it exists to answer. The test holds each of these against the code that reads it.
_ADVANCED_DEFAULTS = {
    "poll_parked": "30", "poll_driving": "10",
    "charge_detect_min_a": "2.0",           # poller/client.py:_CHARGE_CURRENT_MIN_A
    "charge_reconstruct_min_pct": "2.0",    # poller/recorder.py:_reconstruct_min_pct
    "vampire_min_drop_pct": "0.2", "vampire_min_hours": "1",
    "charge_dc_min_kw": "11",
    "soh_temp_min_c": "15",                 # web/db_reader.py get_battery_health — NOT 10
    "map_station_min_sessions": "1",        # web/main.py charger locator — NOT 2
    "positions_retention_days": "0",
}


def _advanced_settings_section() -> str:
    """What the behaviour settings are set to, what they should be, and when they last moved.

    #230 (@adoewa, 06/08/26): `charge_detect_min_a` sat at **14.5 A** against a default of 2.0. A
    home AC charge moves the pack at 11-12 A, so `_is_charging` returned False on all 202 polls of
    a charge that filled the battery from 49.8% to 90.0%. The bundle reported the vampire-drain
    thresholds and not this one, and the answer took half a day instead of thirty seconds.

    Each of these was a range slider in a form that saved on `change` — released the thumb, saved
    the value, no confirmation, no trace. The forms now need a Save press and every change is
    written down; this prints both, so the next "it changed by itself" is answered from the file.
    """
    lines, off = [], 0
    for key, default in _ADVANCED_DEFAULTS.items():
        cur = db_reader.get_setting(key, default) or default
        same = str(cur).rstrip("0").rstrip(".") == str(default).rstrip("0").rstrip(".")
        off += 0 if same else 1
        lines.append(f"  {'  ' if same else '⚠ '}{key:28} {str(cur):>8}"
                     + ("" if same else f"   (default {default})"))
    head = (f"{len(_ADVANCED_DEFAULTS)} behaviour settings · "
            + ("all at their defaults" if not off else f"⚠ {off} NOT at default — see below"))
    trail = db_reader.get_settings_audit()
    if trail:
        lines.append("")
        lines.append(f"  changes recorded ({len(trail)}, newest first):")
        for r in trail:
            lines.append(f"    {(r['changed_at'] or '')[:16]}  {r['key']}: "
                         f"{r['old_value']} → {r['new_value']}")
    else:
        lines.append("")
        lines.append("  changes recorded: none since this version was installed")
    return "\n".join([head] + lines)


# The floor for "the battery really climbed", taken from the poller rather than invented here:
# `recorder._reconstruct_min_pct`. One idea of a real rise, not two that drift apart.
_RISE_MIN_PCT = 2.0


def _missed_charges_section() -> str:
    """Every charge the CAR took while parked, and what Mate had in hand while it happened.

    #230 (@adoewa, 06/08/26): his C10 climbed 49.8% → 90.0% over 3¼ hours and no session opened.
    Proving it took half an hour over 30 000 log lines, and the question that decides which fix is
    right — what the CABLE signal said during those hours — could not be answered at all: the bundle
    carries the raw signals as one snapshot, taken ten hours after the cable came out.

    The values were never missing. `positions` holds them per poll and his 202 rows were on his disk
    the whole time; nothing read them. Silvio, same day: *«si analizza, si verifica il problema e
    solo alla fine si risolve… non si va a tentativi»* — this is the analysis step, made automatic
    and retroactive: it answers on data already collected, without waiting for a recurrence.

    🔑 The line that matters is the frame count, because it splits the two causes:

        frames 202/202 distinct → the car was ONLINE and the cloud never raised the flag
        frames   1/477 distinct → the car was OFFLINE and the cloud repeated its last frame

    Both shapes were in his own night, four hours apart. The second is the ordinary dead-zone case
    the reconstructor already covers; the first is the one nothing covers.

    ⚠️ Rises that WERE recorded are listed too. A flagged line with no control group beside it is a
    coincidence — his two good charges are what proved Mate's machinery works and moved the question
    onto the cloud.
    """
    try:
        db = db_reader._get()
        cut = _rows_cutoff(db, "positions", "recorded_at") or ""
        rows = db.execute(
            "SELECT recorded_at, soc, charging, plug_connected, charge_current_a, frame_ts,"
            "       speed_kmh, gear FROM positions"
            " WHERE vehicle_id = COALESCE(?, vehicle_id) AND soc IS NOT NULL"
            "   AND (? = '' OR recorded_at >= ?) ORDER BY recorded_at",
            (db_reader._current_vehicle_id(), cut, cut)).fetchall()
        charges = db.execute(
            "SELECT id, started_at, ended_at FROM charges"
            " WHERE vehicle_id = COALESCE(?, vehicle_id)"
            "   AND (? = '' OR COALESCE(ended_at, started_at) >= ?)",
            (db_reader._current_vehicle_id(), cut, cut)).fetchall()
    except sqlite3.Error as e:
        return f"(unavailable: {e})"
    if not rows:
        return "(no polls retained)"

    def moving(r) -> bool:
        # The poller's own motion gate: regen while driving pushes the SoC up and the current
        # negative, which is exactly the pair that would otherwise read as a charge.
        return (r["speed_kmh"] or 0) > 2.0 or (r["gear"] or "P") not in ("P", "", None)

    episodes, run = [], []
    for r in list(rows) + [None]:
        if r is None or moving(r):
            if len(run) >= 2:
                lo_i = min(range(len(run)), key=lambda i: run[i]["soc"])
                hi_i = max(range(lo_i, len(run)), key=lambda i: run[i]["soc"])
                if run[hi_i]["soc"] - run[lo_i]["soc"] >= _RISE_MIN_PCT:
                    episodes.append(run[lo_i:hi_i + 1])
            run = []
        else:
            run.append(r)

    head = (f"last {_ROWS_DAYS}d · {len(episodes)} SoC rise(s) ≥{_RISE_MIN_PCT}% while parked · "
            "frames distinct/total: many = the car was ONLINE, 1 = the cloud repeated one frame")
    if not episodes:
        return head + "\n  (none)"
    out = [head]
    for ep in episodes:
        t0, t1 = ep[0]["recorded_at"], ep[-1]["recorded_at"]
        hit = next((c["id"] for c in charges
                    if (c["started_at"] or "") <= t1 and (c["ended_at"] or t1) >= t0), None)
        amps = [r["charge_current_a"] for r in ep if r["charge_current_a"] is not None]
        out.append(
            f"  {t0[:16]} → {_end_hhmm(t0, t1):8} SoC {_n(ep[0]['soc'], 1)}→{_n(ep[-1]['soc'], 1)}  "
            f"{len(ep)} poll  " + (f"charge #{hit}" if hit else "🔴 NO CHARGE ROW"))
        # ⚠️ `frame_ts` is a RECENT column: on a database that predates it every row is NULL, and
        # counting distinct values then reads "1/1829" — which says "the cloud repeated one frame
        # for fifteen hours" about a charge that went perfectly. Absent is not repeated. Where the
        # column is missing, fall back to distinct SoC readings: a re-served frame carries the same
        # SoC too, so it answers the same question and every database has it.
        seen = [r["frame_ts"] for r in ep if r["frame_ts"] is not None]
        if len(seen) >= max(2, len(ep) // 2):
            fresh = f"frames {len(set(seen))}/{len(ep)} distinct"
        else:
            fresh = f"frames n/a · SoC {len({r['soc'] for r in ep})}/{len(ep)} distinct"
        out.append(
            f"      {fresh}   "
            f"plug={sum(1 for r in ep if r['plug_connected'])}/{len(ep)}  "
            f"chg={sum(1 for r in ep if r['charging'])}/{len(ep)}  "
            f"A min={_n(min(amps), 1) if amps else '—'} max={_n(max(amps), 1) if amps else '—'}")
    return "\n".join(out)


def _abilities_section() -> str:
    """The car's DECLARED ability codes (leapmotor_api VehicleAbility) — the ground truth for what
    THIS model actually supports, so we stop assuming every car has the same commands (#67). Shows the
    raw codes + names and calls out the climate/seat features that differ across models (the T03 lacks
    several the B10/C10 have; also lets us learn a new model like the B05 the moment it connects)."""
    vehicle, _ = db_reader.get_vehicle()
    raw = (vehicle or {}).get("abilities")
    if not raw:
        return ("(not reported yet — restart the add-on once on this version so the poller stores the "
                "car's abilities, then re-download this diagnostic)")
    try:
        codes = sorted({int(c) for c in json.loads(raw)})
    except (ValueError, TypeError):
        return f"(unparseable abilities value: {raw!r})"
    known = None
    try:
        from leapmotor_api.models import VehicleAbility
        known = {int(m) for m in VehicleAbility}     # the codes the library can actually name

        def _name(c: int) -> str:
            try:
                return VehicleAbility(c).name
            except ValueError:
                return f"CODE{c}"
    except Exception:  # noqa: BLE001 — never let a lib change break the diagnostic
        def _name(c: int) -> str:
            return f"CODE{c}"

    present = set(codes)

    def _flags(pairs) -> str:
        return "  ".join(f"{label}={'✓' if code in present else '✗'}" for label, code in pairs)

    # Comfort features that RELIABLY differ model-to-model — verified against a real B10, which declares
    # all of these. We deliberately do NOT flag "fan/auto climate": the ability codes don't map to it
    # (the B10's fan works yet it declares neither CLIMATE_ADVANCED nor AC_PRESET), so a T03-vs-B10
    # climate gap must be read from the raw `codes` diff + on-car behaviour, not inferred from one flag.
    comfort = _flags([("SEAT_HEAT", 14), ("FRONT_SEAT_HEAT", 21),
                      ("SEAT_VENT_DRV", 42), ("SEAT_VENT_PAS", 43), ("STEERING_HEAT", 15)])
    out = [
        f"codes  : {','.join(str(c) for c in codes)}",
        f"named  : {', '.join(_name(c) for c in codes)}",
        f"comfort: {comfort}",
    ]
    # Codes the car DECLARES but this library version can't name yet (newer than the enum). Surfaced on
    # their own line so these unknowns pop out — they're exactly the leads worth diffing across models
    # and investigating on-car. Omitted when the enum isn't importable (we then can't tell mapped apart).
    if known is not None:
        unmapped = [c for c in codes if c not in known]
        out.append(f"unmapped: {','.join(str(c) for c in unmapped) if unmapped else '(none)'}")
    return "\n".join(out)


def build_bundle(version: str, parts=_BUNDLE_PARTS, lines: int = 300, signals: dict | None = None) -> str:
    """One redacted text blob to attach to an issue. `parts` selects which sections to include
    (any of 'info', 'poller', 'web', 'signals'); a one-line version header is always present. The
    'signals' section dumps the car's raw signals with GPS coordinates stripped (caller passes a
    freshly-fetched signal dict) so the whole bundle stays safe to share publicly."""
    want = {p for p in parts if p in _BUNDLE_PARTS} or set(_BUNDLE_PARTS)
    out = [f"===== LeapMotor Mate {version} — diagnostics ====="]

    if "info" in want:
        info = build_system_info(version)
        f = info["features"]
        out += [
            f"Model / year : {info['model']} / {info['year']}",
            f"VIN          : {info['vin_masked']}",
            f"Battery kWh  : {info['battery_kwh']}  (SoH reference: {info['battery_nominal_kwh']})",
            f"Language     : {info['language']}",
            f"DB size (MB) : {info['db_size_mb']}",
            f"Rows         : trips={info['counts']['trips']} "
            f"charges={info['counts']['charges']} positions={info['counts']['positions']}",
            f"Poll (s)     : parked={info['poll_parked']} driving={info['poll_driving']}",
            # 🔴 The floor a charge has to clear to be SEEN. Default 2.0 A; a home AC charge moves
            # the pack at 11-12 A, so anything above that silently stops every charge from being
            # recorded (#230: it was 14.5). Printed next to the poll cadence because both are
            # Advanced settings a user can change and then forget.
            f"Charge detect: min {info['charge_detect_min_a']} A "
            f"(default 2.0 — above ~11 A a home charge is never seen) · "
            f"reconstruct ≥{info['charge_reconstruct_min_pct']} %",
            f"Positions    : span {info['positions_span']} · retention {info['retention_days']}d (0=keep all)",
            f"Vampire thr  : min_drop {info['vampire_min_drop_pct']} % SoC · "
            f"min_hours {info['vampire_min_hours']} h (chart display thresholds)",
            f"Features     : mqtt={f['mqtt']} wallbox={f['wallbox']} ha={f['ha']} "
            f"abrp={f['abrp']} addon={f['addon']}",
            # Only present in the Mac/Windows app. Worth its own line because "Mate won't update"
            # has two unrelated causes, and this number is what separates a shell too old to run
            # the newest release from a genuine fault — the first question triage should ask.
            *([f"MateDesktop : {os.environ['MATE_DESKTOP_VERSION']} (app shell)"]
              if os.environ.get("MATE_DESKTOP_VERSION") else []),
            f"Last poll    : {info['last_poll_iso']} (age {info['last_poll_age_min']} min) "
            f"soc={info['last_soc']} gear={info['last_gear']} charging={info['last_charging']}",
            # #144 — @staffhotel-beep's European T03 reports neither temperature, and his bundle
            # could not say so: it carried no temperature at all. He was asked for the one artefact
            # that could not answer the question. So: how many of the retained polls carried each,
            # and the last value. A car that has NEVER sent one reads 0 of N, which is the answer.
            _temperature_line(),
        ]
        out += ["", "----- battery standby / vampire-drain (computed) -----", _vampire_section()]
        out += ["", "----- SoC by day (last 14d · hi→lo · km driven) -----", _soc_daily_section()]
        out += ["", "----- behaviour settings (and when they last moved) -----",
                _advanced_settings_section()]
        out += ["", "----- cost & wallbox config -----", _cost_wallbox_section()]
        # The rows themselves, so a "this charge/trip is wrong" report is answered from the table
        # instead of inferred from 30 000 log lines (#230, 06/08/26).
        out += ["", "----- charges (from the database) -----", _charges_section()]
        out += ["", "----- trips (from the database) -----", _trips_section()]
        out += ["", "----- charges the car took, and what Mate had in hand -----",
                _missed_charges_section()]
        out += ["", "----- vehicle abilities (what the car DECLARES it can do) -----", _abilities_section()]
    if "poller" in want:
        out += ["", "----- poller log (full retained window) -----", read_full_log("poller")]
    if "web" in want:
        out += ["", "----- web log (full retained window) -----", read_full_log("web")]
    if "signals" in want:
        vehicle, _ = db_reader.get_vehicle()
        if signals:
            out += ["", "----- GPS shape (no coordinates) -----", _gps_shape_line(signals)]
        out += ["", "----- raw signals (GPS removed) -----",
                _signals_section(signals, (vehicle or {}).get("vin"))]
    out += ["", "===== end ====="]
    return "\n".join(out)
