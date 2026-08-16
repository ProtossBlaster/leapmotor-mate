"""LeapMotor Mate — web server."""
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

sys.path.insert(0, str(Path(__file__).parent))
import battery_packs
import db_reader
import capability_profile
import command_client
import car_image
import i18n
import ha_client
import geocode
import charger_locator
import mqtt_check
import auth
import security
import update_check

MATE_VERSION = "3.14.1"  # bump together with the git tag + add-on config.yaml at release

import diagnostics
import demo
import maintenance
import research
import ec_enrich
import elevation_enrich

_IS_DEMO = demo.is_demo()
demo.install(command_client, ha_client)   # no-op unless MATE_DEMO is set


def _add_file_log() -> None:
    """Mirror web logs to a small rotating file under the data dir for the Diagnostics card
    (companion to the poller's). Best-effort; never blocks startup."""
    try:
        from logging.handlers import RotatingFileHandler
        root = logging.getLogger()
        # Idempotent. `uvicorn.run("main:app")` (at the bottom of this file) imports this module a SECOND
        # time — as `main`, after it already ran as `__main__` — in the SAME process, so this module-level
        # setup fires twice. Without this guard root would collect two handlers to the same file and EVERY
        # web log line would be written twice (that was riri19's doubled diagnostics).
        for h in root.handlers:
            if isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", "").endswith(diagnostics.WEB_LOG):
                return
        fh = RotatingFileHandler(str(diagnostics.data_dir() / diagnostics.WEB_LOG),
                                 maxBytes=1_000_000, backupCount=2)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"))
        root.addHandler(fh)
        root.setLevel(logging.INFO)
    except Exception:  # noqa: BLE001
        pass


_add_file_log()
log = logging.getLogger("mate.web")


def _ensure_schema() -> None:
    """Bring the database up to the schema this process is about to read from.

    The tables are the poller's, and until now the web merely hoped its migrations had run. v3.6.6
    added `charges.gross_kwh`; the web named it in five queries; and every install whose poller had
    not started yet answered **500 on the Charges page and on every trip detail**. Guarding that one
    column fixed that one column — this is what stops the next one, because a reader that depends on
    a schema should guarantee it rather than hope.

    Only the schema: `ensure_schema` was lifted out of Database.__init__ precisely so the eleven data
    repairs beside it stay with the process that owns the data. Best-effort — a database that cannot
    be opened is the poller's problem to report, and the pages that need no new column still work.
    Runs twice per process (uvicorn re-imports this module) and that is fine: every step is
    `IF NOT EXISTS` or `if column not in …`.
    """
    try:
        import pathlib as _pl
        import sqlite3 as _sq
        # `web/` and `poller/` share five module names — main, crypto, geohash, capability_profile,
        # session_share — so the poller's directory may not simply be added to the path: in front,
        # uvicorn re-imports poller/main.py and the web does not boot at all (measured); behind, an
        # import inside the poller would find the web's geohash, which differs by 64 lines. Hence a
        # module that (a) is named nothing the web has and (b) imports nothing itself, on the path
        # only for the length of one import.
        _poller = str(_pl.Path(__file__).resolve().parent.parent / "poller")
        sys.path.insert(0, _poller)
        try:
            import schema as _schema
        finally:
            sys.path.remove(_poller)
        conn = _sq.connect(db_reader.DB_PATH, timeout=15)
        try:
            _schema.ensure_schema(conn)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — never let this stop the web from starting
        log.warning("Schema check skipped: %s", exc)


_ensure_schema()


def _repair_manual_charge_timezones() -> None:
    """#181: hand-entered charges were saved as bare wall-clock text while the renderer reads a
    zone-less value as UTC, so every one of them showed pushed forward by the user's whole offset.
    Convert the old rows once, here. Safe to run twice — the repair skips anything that already
    carries a zone — which also covers this module being imported a second time by uvicorn (see
    _add_file_log above)."""
    try:
        n = db_reader.repair_manual_charge_timezones()
        if n:
            log.info("Timezone repair: %d manually entered charge(s) moved to UTC (#181)", n)
    except Exception:  # noqa: BLE001 — never block startup over a repair
        pass


def _pin_auto_timezone() -> None:
    """Runs BEFORE the repair below, and the order is the point: the repair refuses to convert while
    the zone is Auto (it will not bake in a guess), so an install left on Auto could never have its
    old hand-entered rows put right. Pinning the resolved zone first gives the repair the answer it
    was waiting for. Safe either way — local_to_utc_iso is a no-op on a value that already carries a
    zone, so rows written after v2.12.1 cannot be shifted twice."""
    try:
        name = db_reader.pin_auto_timezone()
        if name:
            log.info("Time zone was on Automatic and is now recorded as %s — nothing moved, "
                     "but what you type is no longer anchored to an unnamed clock", name)
    except Exception:  # noqa: BLE001 — never block startup over a migration
        pass


def _check_secret_key() -> None:
    """Say at boot, once, if the stored secrets belong to a key we no longer have (#227).

    The poller has warned about this for as long as the encryption has existed; the web did not,
    and the web is the screen. Runs after the schema is ensured and before anything tries to log
    in, so the explanation sits ABOVE the wall of failures it explains rather than under it."""
    try:
        db_reader.check_decryption()
    except Exception:  # noqa: BLE001 — never block startup over a diagnostic
        pass


_check_secret_key()
_pin_auto_timezone()
_repair_manual_charge_timezones()

app = FastAPI(title="LeapMotor Mate")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# Per-trip EC enrichment runs on a background timer (not only on page renders), so a fresh trip's
# cloud EC is picked up + re-fetched-until-stable even when nobody has the app open.
ec_enrich.start_background()
# Same background timer for per-trip elevation gain/loss + outside temperature (Open-Meteo lookup on
# the GPS track); no-op unless the feature is enabled.
elevation_enrich.start_background()


def _nice(x) -> str:
    """Show a number with at most 2 decimals, stripping trailing zeros
    (e.g. 1.77 → "1.77", 310.027 → "310.03", 48 → "48"), with the decimal separator of the UI
    language — see units.decimal_point for why."""
    if x is None:
        return "—"
    import units as _u
    return _u.decimal_point(f"{float(x):.2f}".rstrip("0").rstrip("."))

templates.env.filters["nice"] = _nice


def _dec(x, n: int = 1) -> str:
    """A number with EXACTLY `n` decimals, trailing zeros kept, decimal separator per the UI
    language. This is `"%.1f"|format(x)` with the separator rule added and nothing else changed —
    "12.0" stays "12,0" rather than collapsing to "12" the way `nice` would, because these are the
    places that deliberately show a fixed precision (SoC to a tenth, kWh to a tenth or hundredth)."""
    if x is None:
        return "—"
    import units as _u
    return _u.decimal_point(f"{float(x):.{n}f}")

templates.env.filters["dec"] = _dec


def _money(x) -> str:
    """Format a monetary amount with the configured currency symbol, placement
    and decimal digits. Decimal/thousands separators follow the UI language
    (comma for it/fr/de, dot for en) — no `locale`/`babel` dependency."""
    if x is None:
        return "—"
    cur = db_reader.get_currency()
    s = f"{float(x):,.{cur['dec']}f}"
    if db_reader.get_language() != "en":
        # swap separators: 1,234.50 -> 1.234,50
        s = s.translate(str.maketrans({",": ".", ".": ","}))
    sym = cur["symbol"]
    # Use an explicit non-breaking space so "20,14 €" never wraps mid-amount.
    return f"{sym}{s}" if cur["pos"] == "before" else f"{s}\u00a0{sym}"

templates.env.filters["money"] = _money


def _price_3(x) -> str:
    """A UNIT price \u2014 \u20ac/L at the pump, \u20ac/kWh at the plug: fixed 3 decimals with the UI-language
    decimal separator (comma for it/fr/de, dot for en), no currency symbol \u2014 the template appends
    ' <sym>/L' or ' <sym>/kWh'. Both are quoted to 3 decimals in the real world (1,858 \u20ac/L,
    0,250 \u20ac/kWh) and `money`'s 2 would flatten 0,250 and 0,199 onto 0,25 and 0,20. Unlike `nice`
    this keeps the decimals, and unlike a bare number it follows `money`'s comma/dot rule."""
    if x is None:
        return "\u2014"
    s = f"{float(x):,.3f}"
    if db_reader.get_language() != "en":
        s = s.translate(str.maketrans({",": ".", ".": ","}))
    return s

templates.env.filters["price3"] = _price_3


def _localdate(s) -> str:
    """ISO timestamp → date in the UI language's format (e.g. it '10 giu 2026',
    en '10 Jun 2026') instead of the raw ISO '2026-06-10'. Time stays separate."""
    dt = db_reader._local_dt(s)
    return i18n.fmt_day_month_year(db_reader.get_language(), dt) if dt else (s or "")

templates.env.filters["localdate"] = _localdate

# Display-time unit conversion (DB stays metric — see units.py). Filters format "<value> <unit>";
# the *_unit() / *_val() globals give a bare unit label or converted number (chart axes / JS data).
import units
for _name in ("dist", "speed", "temp", "pressure", "elev"):
    templates.env.filters[_name] = getattr(units, _name)
templates.env.filters["eff"] = units.efficiency
def _eff_cls(e) -> str:
    """Efficiency → pill colour class — shared by trip_row.html wherever a trip is rendered
    (day-drawer, search results, merge candidates), not just the old trips.html-local macro."""
    if e is None:
        return ""
    return "eff-good" if e < 18 else "eff-mid" if e < 22 else "eff-bad"


templates.env.globals.update(
    dist_unit=units.dist_unit, speed_unit=units.speed_unit, temp_unit=units.temp_unit,
    pressure_unit=units.pressure_unit, eff_unit=units.eff_unit, elev_unit=units.elev_unit,
    dist_val=units.dist_val, speed_val=units.speed_val, temp_val=units.temp_val,
    eff_val=units.eff_val, elev_val=units.elev_val, unit_system=units.get_unit_system,
    dist100_unit=units.dist100_unit, cost100_val=units.cost100_val,
    eff_cls=_eff_cls,
    # #222 — whether the charger's-own-kWh field can be offered at all. A GLOBAL, not a per-route
    # value: the charge card is rendered by the page AND by two partials that build their context by
    # hand, so a flag passed through _ctx reached the page and silently vanished from the day drawer
    # and the search results — which is where you actually look at a charge. @ghuaywen-ai, who asked
    # for the field, watched it disappear between v3.6.6 and v3.6.8. A callable, so it answers per
    # render: the poller can add the column while the web is running.
    gross_kwh_ok=lambda: db_reader._charges_have_gross(db_reader._get()),
    # #144 — the temperature sensors this car has never once reported, so the status card can leave
    # them out instead of promising a number that will never arrive. A GLOBAL for exactly the reason
    # above: `status_card.html` is rendered by the Overview AND by partials that build their own
    # context, and a value threaded through `_ctx` would reach the page and vanish from the rest.
    # A callable, so a sensor that starts working un-hides itself without a restart.
    absent_temps=db_reader.never_reported_temps,
)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


# ── Setup check middleware ────────────────────────────────────────────────────

@app.middleware("http")
async def setup_check(request: Request, call_next):
    path = request.url.path
    # Cross-site write guard. Ahead of every other gate — including the always-public paths and
    # the auth check — because it protects the install that has NO password, which is the default
    # one. See web/security.py for why an absent origin is allowed and why add-on mode is exempt.
    if not security.origin_allowed(request.method, path, request.headers, str(request.base_url)):
        return Response("cross-site request refused", status_code=403)
    # Always-public: static assets, the liveness probe, and the login page/handler.
    if path.startswith("/static/") or path == "/healthz" or path.startswith("/login"):
        return await call_next(request)
    # Optional standalone auth (no-op as an add-on behind HA ingress). Applies to
    # everything else — including /setup and /api — so nothing is reachable unauthenticated.
    if auth.enabled() and not auth.valid(request.cookies.get(auth.COOKIE, "")):
        if path.startswith("/api/"):
            return Response("authentication required", status_code=401)
        return RedirectResponse(request.headers.get("x-ingress-path", "") + "/login")
    # Setup wizard gate (unchanged).
    if path.startswith("/setup") or path.startswith("/api/"):
        return await call_next(request)
    if os.environ.get("LEAPMOTOR_USER"):  # env-var dev mode skips the wizard
        return await call_next(request)
    if not db_reader.is_setup_complete():
        # Honor the HA ingress path so the redirect stays inside the add-on panel
        return RedirectResponse(request.headers.get("x-ingress-path", "") + "/setup")
    # BetaTester consent gate (research build only): one-time acknowledgement before any page.
    if research.research_enabled() and db_reader.get_setting("research_consent", "0") != "1" \
            and not path.startswith("/research/consent"):
        return RedirectResponse(request.headers.get("x-ingress-path", "") + "/research/consent")
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Declared AFTER setup_check on purpose: Starlette runs the most recently added middleware
    outermost, so this one wraps the guard above and every response carries the headers —
    including the guard's own 403 and the login page, which are exactly the responses an
    attacker gets to see. The load-bearing header is the anti-framing pair: it closes the
    clickjacking half of the attack, which no origin check can see because that click really
    does happen inside Mate. Skipped as an add-on — HA frames the panel deliberately."""
    resp = await call_next(request)
    if not security.is_addon():
        for k, v in security.SECURITY_HEADERS.items():
            resp.headers.setdefault(k, v)
    return resp


# ── Helpers ──────────────────────────────────────────────────────────────────

def _soc_color(soc: float) -> str:
    if soc >= 50: return "#22c55e"
    if soc >= 20: return "#f59e0b"
    return "#ef4444"

def _driving(pos: dict) -> bool:
    """Active drive = any gear other than Park (so a stop in traffic with gear D
    still reads as driving, not 'Parked'); speed is a fallback if the gear lags."""
    return (pos.get("gear") or "P") != "P" or pos.get("speed_kmh", 0) > 1

def _state_color(pos: dict) -> str:
    if pos.get("charging"): return "text-yellow-400"
    if _driving(pos): return "text-blue-400"
    if pos.get("plug_connected"): return "text-teal-300"   # cable in, not actively charging
    return "text-green-400"

def _opt_float(s: str) -> "float | None":
    """An optional numeric search-filter field, as FastAPI/Pydantic won't: a query param
    typed `float | None = None` only defaults to None when the param is ABSENT, not when
    it's present-but-empty — and an empty number/date input still submits its name with
    an empty VALUE (e.g. `cost_min=`), which FastAPI then 422s trying to parse "" as a
    float. Every optional numeric search filter takes `str` and goes through this instead
    (#175 — an empty advanced-filter field made the whole search request fail silently,
    since htmx doesn't swap in a non-2xx response)."""
    s = (s or "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _fmt_dur(minutes) -> str:
    """Readable duration: '10h 19m' from an hour up, '45 min' below, '—' when missing —
    so a long charge reads as hours, not a bare '619 min'."""
    try:
        m = int(round(float(minutes)))
    except (TypeError, ValueError):
        return "—"
    if m < 60:
        return f"{m} min"
    return f"{m // 60}h {m % 60:02d}m"


def _ctx(**kwargs):
    """Add shared helpers + i18n to every template context."""
    # Lazy auto-confirm sweep (like update_check: piggybacks on page renders, no bg loop).
    # Self-guarding no-op unless the wallbox_auto_home toggle is on AND a closed untyped
    # wallbox charge exists — so by the time any page shows charges, they're already tagged.
    db_reader.auto_confirm_home_charges()
    # Same piggyback for the 📍 station labels — settings probe + tiny SELECT per render,
    # the OSM lookups run in a background thread on a TTL (see charger_locator.maybe_sweep).
    charger_locator.maybe_sweep()
    # Same piggyback for per-trip EC (driving) energy enrichment (Phase 2) — no-op unless the
    # feature is enabled; the cloud calls run in a background thread on a TTL.
    ec_enrich.maybe_sweep()
    # Same piggyback for per-trip elevation + outside temperature (Open-Meteo) — no-op unless due.
    elevation_enrich.maybe_sweep()
    lang = db_reader.get_language()
    t = i18n.get_t(lang)
    def state_label(pos: dict) -> str:
        if pos.get("charging"): return t("state_charging")
        if _driving(pos): return t("state_driving")
        # Charge finished (or paused) but the cable is still plugged in — don't read as a plain
        # "Parked"; surface that the car is still connected.
        if pos.get("plug_connected"):
            return t("state_charge_complete") if pos.get("charge_completed") else t("state_plugged")
        return t("state_parked")

    def ago(seconds) -> str:
        """"How long ago", in the reader's own language. db_reader computes the seconds and can't
        translate them (no request, no locale there), so every "…ago" on screen goes through here.
        Until #178 put an Italian phrase next to it, the Overview's `last seen` was English for
        everyone and nobody noticed it standing alone."""
        if seconds is None:
            return "—"
        s = max(int(seconds), 0)
        if s < 60:
            return t("ago_s").format(n=s)
        if s < 3600:
            return t("ago_m").format(n=s // 60)
        return t("ago_h").format(n=s // 3600)

    wallbox_enabled = db_reader.get_setting("wallbox_enabled", "0") == "1"
    # Active wallbox profile: shown in sidebar + page title + profiles panel.
    # Only resolved when wallbox is on AND a profile has been loaded.
    wb_active_profile_name = None
    wb_active_profile_id   = None
    if wallbox_enabled:
        _pid = db_reader.get_setting("wallbox_active_profile", "")
        if _pid:
            _profiles = _get_wallbox_profiles()
            _match = next((p for p in _profiles if p["id"] == _pid), None)
            if _match:
                wb_active_profile_name = _match["name"]
                wb_active_profile_id   = _pid
    # Per-model nav gating: hide links to pages the car physically can't use (e.g. T03 has no
    # prepare-car — no PREPARE right). Reuse a vehicle already in the context, else a cheap lookup.
    _veh = kwargs.get("vehicle")
    if _veh is None:
        _veh, _ = db_reader.get_vehicle()
    _car_type = (_veh or {}).get("car_type", "")
    # Ability-gated command buttons: hide those the car doesn't declare it can do (e.g. the T03
    # never declares code 53 = unlock charge cable — #142). None abilities → shown (never on a guess).
    _abilities = capability_profile.parse_abilities((_veh or {}).get("abilities"))
    # The sidebar car picker. One car — everyone, today — means a list of one, and base.html then
    # renders exactly the badge it has always rendered; the picker only exists from the second car.
    _vehicles = db_reader.get_vehicles()
    # A car that walked in from the poller and never met the wizard is running on its model's
    # default pack. On every page, because the figures it bends are on every page.
    _unconfigured = db_reader.unconfigured_vehicles()
    return {**kwargs, "lang": lang, "t": t, "version": MATE_VERSION, "demo": _IS_DEMO,
            "unconfigured_cars": ", ".join(
                (v.get("car_type") or (v.get("vin") or "")[-6:]) for v in _unconfigured),
            "vehicles": _vehicles,
            # Inside the Mac/Windows app there are TWO versions that matter: Mate itself, which
            # updates on its own, and the app shell around it, which almost never does. When a
            # user reports "it stopped updating", only the second number distinguishes a shell too
            # old to run the newest Mate from an actual fault. Empty everywhere else.
            "shell_version": os.environ.get("MATE_DESKTOP_VERSION", ""),
            # …and WHICH desktop, because removing the app differs between them. Windows has a
            # real uninstaller in Settings ▸ Apps that already takes the data with it; macOS has
            # none — an app is dragged to the Bin, and the Bin has never heard of Application
            # Support, which is why a Mac fills up with folders belonging to programs it no longer
            # has. So the "remove everything" button is offered on the Mac only.
            "shell_platform": os.environ.get("MATE_DESKTOP_PLATFORM", ""),
            "update": update_check.get_update_status(MATE_VERSION),
            "prepare_car_shown": not capability_profile.model_hidden(_car_type, "prepare_car"),
            "unlock_charger_shown": capability_profile.command_shown(
                (_veh or {}).get("vin", ""), "unlock_charger", abilities=_abilities),
            # Charge-schedule view: cars without weekly/cyclic charge abilities (e.g. T03) get the
            # simple start-time + SoC form — no end-time window, no day chips (#146).
            "charge_schedule_advanced": capability_profile.charge_schedule_advanced(_abilities),
            # The car's charge window as "HH:MM – HH:MM", or "" when there isn't one enabled. Lives
            # here rather than in the Overview routes because the hero card is rendered from TWO
            # places (the page, and the 30-second self-refresh) and a value wired into only one of
            # them is how this template has broken before.
            "sched_window": _charge_window(),
            "wallbox_enabled": wallbox_enabled,
            "is_reev": db_reader.is_reev_car(),
            "research": research.research_enabled(),
            "ec_periods": db_reader.get_setting("ec_periods_enabled", "1") == "1",
            "wb_active_profile_name": wb_active_profile_name,
            "wb_active_profile_id":   wb_active_profile_id,
            "currency": db_reader.get_currency(), "auth_enabled": auth.enabled(),
            # Security card + banner: unprotected() is false as an add-on (ingress authenticates),
            # so nobody behind HA is nagged about a lock they already have.
            "auth_unprotected": auth.unprotected(), "auth_env": auth.env_password_wins(),
            "auth_dismissed": db_reader.get_setting("auth_warning_dismissed", "0") == "1",
            "desktop_notice_dismissed": db_reader.get_setting("desktop_notice_dismissed", "0") == "1",
            # The BetaTester banner is dismissible too (@ebagnoli, beta #13). The BETA badge beside
            # the version stays regardless — the warning is what you take in once, the build is what
            # you must be able to tell at a glance.
            "beta_notice_dismissed": db_reader.get_setting("beta_notice_dismissed", "0") == "1",
            "is_addon": auth.is_addon(),
            "soc_color": _soc_color, "state_label": state_label, "state_color": _state_color, "ago": ago,
            "is_driving": _driving, "fmt_dur": _fmt_dur}


# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/healthz")
async def healthz():
    """Liveness probe. 200 while awaiting setup or when the poll loop ran recently;
    503 if the poller looks wedged/dead. The threshold is well past the 900s offline
    backoff so a deep-sleeping car never reads as unhealthy."""
    import time as _t
    if _IS_DEMO:
        return JSONResponse({"status": "demo"}, status_code=200)
    if not db_reader.is_setup_complete():
        return JSONResponse({"status": "awaiting_setup"}, status_code=200)
    try:
        ts = float(db_reader.get_setting("last_loop_ts", "0") or 0)
    except (TypeError, ValueError):
        ts = 0.0
    age = _t.time() - ts
    healthy = ts > 0 and age < 1800   # 2x the offline poll interval
    return JSONResponse(
        {"status": "ok" if healthy else "stale",
         "last_poll_age_s": round(age) if ts else None},
        status_code=200 if healthy else 503,
    )


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if not auth.enabled() or auth.valid(request.cookies.get(auth.COOKIE, "")):
        return RedirectResponse(request.headers.get("x-ingress-path", "") + "/")
    return templates.TemplateResponse(request, "login.html", _ctx(page="login", error=False))


@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request):
    form = await request.form()
    ingress = request.headers.get("x-ingress-path", "")
    if not auth.enabled():
        return RedirectResponse(ingress + "/", status_code=303)
    # One shared password and no lockout means a box on the LAN can grind through a wordlist as
    # fast as the network allows. Throttle first, so the guess never reaches the comparison.
    if not auth.attempt_allowed(request.client.host if request.client else ""):
        return templates.TemplateResponse(request, "login.html",
                                          _ctx(page="login", error=True, throttled=True),
                                          status_code=429)
    if auth.check_password(form.get("password") or ""):
        auth.note_success(request.client.host if request.client else "")
        resp = RedirectResponse(ingress + "/", status_code=303)
        resp.set_cookie(auth.COOKIE, auth.make_token(), max_age=auth.TTL,
                        httponly=True, samesite="strict", path="/")
        return resp
    auth.note_failure(request.client.host if request.client else "")
    return templates.TemplateResponse(request, "login.html",
                                      _ctx(page="login", error=True), status_code=401)


@app.get("/logout")
async def logout(request: Request):
    resp = RedirectResponse(request.headers.get("x-ingress-path", "") + "/login", status_code=303)
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


@app.get("/", response_class=HTMLResponse)
async def overview(request: Request):
    vehicle, settings = db_reader.get_vehicle()
    status = db_reader.get_latest_status()
    trips = db_reader.get_trips(limit=3)
    # get_trips() returns raw UTC rows; overview.html slices started_at[:10]/[11:16]
    # directly, so localize here like the Trips page / trip detail do (issue #12).
    for tr in trips:
        tr["started_at"] = db_reader._local_iso(tr.get("started_at"))
        tr["ended_at"] = db_reader._local_iso(tr.get("ended_at"))
    charges = db_reader.get_charges(limit=1)
    return templates.TemplateResponse(request, "overview.html", _ctx(
        page="overview", vehicle=vehicle, settings=settings,
        status=status, recent_trips=trips,
        last_charge=charges[0] if charges else None,
        v2l=db_reader.get_v2l_status(),
        charge_limit=_configured_charge_limit(),
        car_resp=db_reader.command_responsiveness(),
        battery_price=db_reader.current_blended_price(),   # #200 — must match /api/status-card
    ))


@app.get("/trips", response_class=HTMLResponse)
async def trips_page(request: Request, highlight: int = 0):
    vehicle, _ = db_reader.get_vehicle()
    summary = db_reader.get_trips_summary()
    total   = summary.get("count") or 0
    # The calendar's Month view opens on today — or, following a ?highlight=<id> link, on
    # whichever month that ONE trip actually falls on (same convention as Ricariche).
    today = db_reader.today_local()
    cal_year, cal_month, cal_open_day = today.year, today.month, 0
    if highlight:
        hl_date = db_reader.get_trip_local_date(highlight)
        if hl_date:
            cal_year, cal_month, cal_open_day = hl_date.year, hl_date.month, hl_date.day
    return templates.TemplateResponse(request, "trips.html", _ctx(
        page="trips", vehicle=vehicle,
        total=total, highlight=highlight, summary=summary,
        # The petrol half of the hero, for a range-extender. reev_fuel_summary() is the one place
        # litres are totalled — a second sum here would be the third copy of a rule that has already
        # cost us a release (beta #23).
        reev_summary=db_reader.reev_fuel_summary(),
        # …and the electric half on the SAME kilometres, so the two tiles standing side by side
        # divide by the same distance (@michapr, beta #24). A BEV keeps the measured efficiency.
        reev_total=(db_reader.reev_total_consumption()
                    if db_reader.is_reev_car() else None),
        # No merge_gap_* here any more: the slider moved into the day drawer, which gets them
        # from the day route (#204). The page keeps only #merge-modal, which lives outside the
        # swapped calendar area so the drawer's 🔗 preview still has somewhere to open.
        cal_year=cal_year, cal_month=cal_month, cal_open_day=cal_open_day,
        cal_years=db_reader.get_trip_years(),
        calendar_html=_trips_calendar_html(cal_year, cal_month, cal_open_day),   # drawn, not fetched
    ))


def _trips_calendar_ctx(year: int, month: int, open_day: int = 0) -> dict:
    """The Viaggi calendar's context, without a Response around it — so the page can draw the grid
    itself on first paint. Same split as _charges_calendar_ctx, for the same reason (#240)."""
    import calendar as calmod
    from datetime import date
    today = db_reader.today_local()
    year, month = year or today.year, month or today.month
    month = max(1, min(12, month))
    cal = db_reader.get_trips_calendar_month(year, month)
    weeks = []
    for week in calmod.Calendar(firstweekday=0).monthdayscalendar(year, month):
        row = []
        for day in week:
            row.append(None if day == 0 else {
                "day": day, **cal["days"].get(day, {"count": 0, "km": 0.0, "regen": 0.0,
                                                      "cost": 0.0, "avg_eff": None})})
        weeks.append(row)
    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    lang = db_reader.get_language()
    ctx = {
        "t": i18n.get_t(lang), "year": year, "month": month, "weeks": weeks, "total": cal["total"],
        "month_label": i18n.fmt_month_year(lang, date(year, month, 1)),
        "weekday_abbrs": i18n.weekday_abbrs(lang),
        "prev_year": prev_year, "prev_month": prev_month,
        "next_year": next_year, "next_month": next_month,
        "today": today, "fmt_dur": _fmt_dur,
        "is_reev": db_reader.is_reev_car(), "research": research.research_enabled(),
        # What this month's totals deliberately leave out. Computed here, inside the calendar's own
        # context, so it is swapped together with the grid when the month buttons are used.
        "offline": db_reader.offline_gaps_summary(year, month),
    }
    if open_day and open_day in cal["days"]:
        ctx["open_day"] = open_day     # so the grid can ring the day the drawer is showing
        ctx["open_day_trips"] = db_reader.get_trips_calendar_day(year, month, open_day)
        ctx["open_day_label"] = i18n.fmt_day_month_year(lang, date(year, month, open_day))
        # Same totals the drawer's own endpoint passes — the day content is rendered from HERE too
        # (month view opening straight onto a day), and a header that only appears down one of the
        # two paths is the classic way this template has broken before.
        ctx["open_day_totals"] = db_reader.trips_totals(ctx["open_day_trips"])
    return ctx


def _trips_calendar_html(year: int, month: int, open_day: int = 0) -> str:
    return templates.get_template("partials/trips_calendar_month.html").render(
        _trips_calendar_ctx(year, month, open_day))


def _render_trips_calendar(request: Request, year: int, month: int, open_day: int = 0):
    """Shared by /api/trips/calendar and the search endpoint's empty-filters fallback."""
    return templates.TemplateResponse(request, "partials/trips_calendar_month.html",
                                      _trips_calendar_ctx(year, month, open_day))


@app.get("/api/trips/calendar", response_class=HTMLResponse)
async def trips_calendar(request: Request, year: int = 0, month: int = 0, open_day: int = 0):
    """Viaggi 'calendar' Month view (HTMX partial, day totals only) — the day drawer loads
    a day's actual trips lazily on click (see trips_calendar_day below)."""
    return _render_trips_calendar(request, year, month, open_day)


@app.get("/api/trips/calendar/day", response_class=HTMLResponse)
async def trips_calendar_day(request: Request, year: int, month: int, day: int,
                             merge: int = 0, gap: int = db_reader.TRIP_MERGE_GAP_DEFAULT):
    """One day's trips for the Month view's day drawer, with that day's own totals (#175).

    `merge=1` swaps that list for THIS day's mergeable pairs, each with the 🔗 connector between
    them, and keeps the max-stop slider (#204 @riri19). The pairs used to live in their own
    all-history view that replaced the whole calendar — which is how they lost the date: the
    drawer's heading already says which day this is, so under it a row needs only its clock."""
    lang = db_reader.get_language()
    from datetime import date
    d = date(year, month, day)
    gap = max(db_reader.TRIP_MERGE_GAP_MIN, min(db_reader.TRIP_MERGE_GAP_MAX, gap))
    day_trips = db_reader.get_trips_calendar_day(year, month, day)
    return templates.TemplateResponse(request, "partials/trips_calendar_day.html", {
        "t": i18n.get_t(lang), "fmt_dur": _fmt_dur,
        "is_reev": db_reader.is_reev_car(), "research": research.research_enabled(),
        "trips": day_trips,
        "day_totals": db_reader.trips_totals(day_trips),
        "day_label": i18n.fmt_day_month_year(lang, d),
        # The drawer builds its own 🔗 / slider URLs, so it needs the day back as three numbers.
        "year": year, "month": month, "day": day,
        "merge_mode": bool(merge), "gap": gap,
        # Chains, not pairs: pairs overlap on the trip between them and drew it twice (#249).
        "candidates": db_reader.get_merge_chains(gap, day=d) if merge else [],
        "merge_gap_min": db_reader.TRIP_MERGE_GAP_MIN, "merge_gap_max": db_reader.TRIP_MERGE_GAP_MAX,
    })


@app.get("/api/trips/search", response_class=HTMLResponse)
async def trips_search(request: Request, q: str = "", drive_mode: str = "",
                        km_min: str = "", km_max: str = "",
                        eff_min: str = "", eff_max: str = "",
                        duration_min: str = "", duration_max: str = "",
                        date_from: str = "", date_to: str = "",
                        year: int = 0, month: int = 0):
    """Viaggi free-text + advanced-filter search (HTMX partial) — replaces the calendar with
    a flat, most-recent-first list of matches. No filter at all (search cleared) falls back
    to the calendar Month view instead of an empty list. km_min/km_max are typed in the
    user's own distance unit (the form label follows dist_unit()) and converted to km here,
    same as a manually-entered odometer reading — search_trips itself is always metric.
    The numeric fields arrive as strings (see _opt_float) — an empty advanced-filter field
    must never 422 the whole request."""
    n_km_min, n_km_max = _opt_float(km_min), _opt_float(km_max)
    n_eff_min, n_eff_max = _opt_float(eff_min), _opt_float(eff_max)
    n_duration_min, n_duration_max = _opt_float(duration_min), _opt_float(duration_max)
    if not any((q.strip(), drive_mode.strip(), n_km_min is not None, n_km_max is not None,
                n_eff_min is not None, n_eff_max is not None, n_duration_min is not None,
                n_duration_max is not None, date_from.strip(), date_to.strip())):
        return _render_trips_calendar(request, year, month)
    lang = db_reader.get_language()
    trips = db_reader.search_trips(
        text=q, drive_mode=drive_mode,
        km_min=units.dist_to_km(n_km_min), km_max=units.dist_to_km(n_km_max),
        eff_min=n_eff_min, eff_max=n_eff_max, duration_min=n_duration_min, duration_max=n_duration_max,
        date_from=date_from, date_to=date_to)
    # The same split Ricariche got in #191, which Viaggi never got: a result standing on its own has
    # to say WHICH DAY it was — a hit read "17:52 → 18:15" and nothing more. Set the label HERE, in
    # the search route only, and let the row print it when it's there. Unconditionally in the row
    # and the calendar's day drawer starts repeating its own heading underneath itself, once per
    # trip — the same reason merging moved INTO the drawer rather than carrying its own dates (#204).
    for t in trips:
        dt = db_reader._local_dt(t.get("started_at"))
        t["date_label"] = i18n.fmt_day_month_year(lang, dt) if dt else None
    today = db_reader.today_local()
    return templates.TemplateResponse(request, "partials/trips_search_results.html", {
        "t": i18n.get_t(lang), "fmt_dur": _fmt_dur,
        "is_reev": db_reader.is_reev_car(), "research": research.research_enabled(),
        "trips": trips, "year": year or today.year, "month": month or today.month,
    })


def _route_svg(points: list[dict], w: int = 84, h: int = 48, pad: int = 6) -> str:
    """Render a downsampled GPS track as a tiny, aspect-correct SVG thumbnail."""
    import math
    if not points or len(points) < 2:
        # Single point / no track → small marker dot, keeps row layout stable.
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}">'
                f'<circle cx="{w/2}" cy="{h/2}" r="3" fill="#e63946"/></svg>')

    lats = [p["latitude"] for p in points]
    lons = [p["longitude"] for p in points]
    lat0 = sum(lats) / len(lats)
    kx = math.cos(math.radians(lat0)) or 1e-6  # lon → lat distance correction

    xs = [lon * kx for lon in lons]
    ys = [-lat for lat in lats]               # flip so north is up
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    dx, dy = (maxx - minx) or 1e-9, (maxy - miny) or 1e-9
    scale = min((w - 2 * pad) / dx, (h - 2 * pad) / dy)
    ox = (w - dx * scale) / 2
    oy = (h - dy * scale) / 2

    def proj(x, y):
        return (round((x - minx) * scale + ox, 1),
                round((y - miny) * scale + oy, 1))

    pts = [proj(x, y) for x, y in zip(xs, ys)]
    d = "M" + " L".join(f"{px} {py}" for px, py in pts)
    sx, sy = pts[0]
    ex, ey = pts[-1]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}">'
        f'<path d="{d}" fill="none" stroke="#ffffff" stroke-width="3.5" '
        f'stroke-linecap="round" stroke-linejoin="round" opacity="0.35"/>'
        f'<path d="{d}" fill="none" stroke="#e63946" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
        f'<circle cx="{sx}" cy="{sy}" r="2.6" fill="#22c55e"/>'
        f'<circle cx="{ex}" cy="{ey}" r="2.6" fill="#94a3b8"/>'
        f'</svg>'
    )


@app.get("/trips/{trip_id}/route.svg")
async def trip_route_svg(trip_id: int):
    svg = _route_svg(db_reader.get_trip_route(trip_id))
    return Response(content=svg, media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/trips/{trip_id}", response_class=HTMLResponse)
async def trip_detail(request: Request, trip_id: int):
    vehicle, _ = db_reader.get_vehicle()
    trip = db_reader.get_trip_detail(trip_id)
    if not trip:
        return RedirectResponse(request.headers.get("x-ingress-path", "") + "/trips")
    # Same server-side currency formatting the Map gives its charging-station popups (main.py's
    # map_page) — a bare .toFixed(2) in the map script would show "13.00" with no symbol.
    for c in trip["charges"]:
        c["cost_fmt"] = _money(c["cost"]) if c.get("cost") is not None else None
    adjacent = db_reader.get_adjacent_trips(trip_id)
    return templates.TemplateResponse(request, "trip_detail.html", _ctx(
        page="trips", vehicle=vehicle, trip=trip,
        prev_trip_id=adjacent["prev_id"], next_trip_id=adjacent["next_id"],
    ))


@app.get("/trips/{trip_id}/similar", response_class=HTMLResponse)
def trip_similar(request: Request, trip_id: int):
    """🔎 'Compare similar trips' — every OTHER trip on the same route (same start/end
    corner of the map AND a validated GPS-overlap of the actual path, not just nearby
    endpoints — see db_reader.get_similar_trips), so temperature/traffic/efficiency can be
    compared apples-to-apples across the same commute over time.

    Deliberately NOT `async def`, unlike almost every other route here. The others do a
    fixed amount of work; this one re-walks the GPS trace of every candidate trip, so its
    cost grows with how long you have owned the car — measured at ~1s for 1000 trips on the
    same commute, and a Raspberry Pi is slower than the machine that was measured on. A
    synchronous body inside `async def` would hold the event loop for that whole time and
    freeze the WHOLE interface, not just this page; declared as a plain `def`, FastAPI runs
    it in a worker thread and everything else stays responsive."""
    vehicle, _ = db_reader.get_vehicle()
    trip = db_reader.get_trip_detail(trip_id)
    if not trip:
        return RedirectResponse(request.headers.get("x-ingress-path", "") + "/trips")
    similar = db_reader.get_similar_trips(trip_id)
    return templates.TemplateResponse(request, "trip_similar.html", _ctx(
        page="trips", vehicle=vehicle, trip=trip, similar=similar, fmt_dur=_fmt_dur,
    ))


@app.delete("/trips/{trip_id}")
async def delete_trip(request: Request, trip_id: int):
    """Permanently delete one trip + its GPS track (HTMX, confirmed in the UI).
    Redirects the browser back to the trips list (ingress-path aware)."""
    db_reader.delete_trip(trip_id)
    base = request.headers.get("x-ingress-path", "")
    return Response(status_code=200, headers={"HX-Redirect": f"{base}/trips"})


@app.get("/api/trips/merge-preview", response_class=HTMLResponse)
async def trips_merge_preview(request: Request, a: int, b: int, gap: int = db_reader.TRIP_MERGE_GAP_DEFAULT):
    """The combined trip the merge WOULD produce — stats + a route thumbnail — for the confirm step."""
    g = db_reader.preview_merge(a, b)
    if not g:
        return HTMLResponse("")
    return templates.TemplateResponse(request, "partials/merge_preview.html", _ctx(g=g, a=a, b=b, gap=gap))


@app.get("/api/trips/merge-route.svg")
async def trips_merge_route_svg(a: int, b: int):
    svg = _route_svg(db_reader.get_merge_preview_route(a, b), w=260, h=120)
    return Response(content=svg, media_type="image/svg+xml")


@app.post("/api/trips/merge", response_class=HTMLResponse)
async def trips_merge(request: Request, a: int, b: int, gap: int = db_reader.TRIP_MERGE_GAP_DEFAULT):
    """Merge trip b into a (the earlier becomes parent). Reversible. On success reloads the page so
    the tree shows the combined trip; on a guard failure returns an inline message."""
    res = db_reader.merge_trips(a, b, gap)
    if res.get("ok"):
        # Auto-convert the combined drive: merged close trips are one cloud session, so the official
        # getEC the individual trips couldn't isolate is now retrievable over the combined window.
        # Best-effort — a merge never fails because the cloud has no data.
        try:
            ec_enrich.convert_trip(res.get("parent_id"))
        except Exception:  # noqa: BLE001
            pass
        return Response(status_code=200, headers={"HX-Refresh": "true"})
    t = i18n.get_t(db_reader.get_language())
    return HTMLResponse(f'<div style="color:#f87171;font-size:13px;padding:6px 0">⚠️ {t("merge_failed")}</div>')


@app.post("/api/trips/unmerge", response_class=HTMLResponse)
async def trips_unmerge(request: Request, parent: int):
    """Split a merged group back into its original trips (reversible — nothing was lost)."""
    db_reader.unmerge_trip(parent)
    return Response(status_code=200, headers={"HX-Refresh": "true"})


@app.post("/api/trips/{trip_id}/convert-ec", response_class=HTMLResponse)
async def trip_convert_ec(request: Request, trip_id: int):
    """'Convert with official data' button: fetch the cloud getEC for this one trip and apply it,
    replacing the SoC estimate (reversible — SoC kept as backup). On success reload the page so the
    official figure + breakdown appear; if the cloud has no data for it (too old / unresolved), show
    an inline message and change nothing."""
    res = ec_enrich.convert_trip(trip_id)
    if res.get("ok"):
        return Response(status_code=200, headers={"HX-Refresh": "true"})
    t = i18n.get_t(db_reader.get_language())
    if res.get("reason") == "merged_cloud":
        # Actionable (amber): merging the two trips would recover the data.
        return HTMLResponse(f'<span class="text-amber-400 text-xs">⚠️ {t("ec_convert_merged")}</span>')
    if res.get("reason") == "shared_session":
        # Actionable (amber): Mate reads these trips as one power-on session, so the official figure
        # covers them together — merging lets it convert the combined drive over its full distance.
        # Name the other trips by their start time (beta #19): "the adjacent one" doesn't say which,
        # and @michapr's was the previous one. The list also carries the count for free, so a session
        # holding three trips no longer describes itself in the singular.
        from html import escape as _escape          # module-local elsewhere in this file
        _times = [c for c in (db_reader.trip_local_start_hhmm(i)
                              for i in res.get("other_ids") or []) if c]
        return HTMLResponse(f'<span class="text-amber-400 text-xs">⚠️ '
                            f'{_escape(t("ec_convert_shared").format(trips=", ".join(_times)))}</span>')
    if res.get("reason") == "implausible":
        # The cloud returned a value, but it's an incomplete aggregation (would imply an impossible
        # efficiency). Calm tone: the reliable SoC estimate above is deliberately kept, nothing broke.
        return HTMLResponse(f'<span class="text-slate-400 text-xs">ℹ️ {t("ec_convert_implausible")}</span>')
    # Not an error — the cloud just has no per-trip detail for this (often short) trip. Calm, neutral
    # tone so it doesn't read as "something is broken": the SoC estimate above stands.
    return HTMLResponse(f'<span class="text-slate-400 text-xs">ℹ️ {t("ec_convert_nodata")}</span>')


@app.post("/api/trips/{trip_id}/revert-ec", response_class=HTMLResponse)
async def trip_revert_ec(request: Request, trip_id: int):
    """'Revert to estimate' button: undo a getEC conversion, restoring the SoC estimate that was
    backed up at convert time. Reloads the page so the original figure reappears and the Convert
    button comes back. Recovery path for a conversion that landed on incomplete cloud data."""
    db_reader.revert_trip_ec(trip_id)
    return Response(status_code=200, headers={"HX-Refresh": "true"})


@app.post("/api/trips/{trip_id}/calc-elevation", response_class=HTMLResponse)
async def trip_calc_elevation(request: Request, trip_id: int):
    """'Calculate elevation' button: on-demand Open-Meteo lookup for this trip (and its merged
    segments) — for trips predating the feature, or ones the background sweep hasn't reached/solved
    yet. Fetches both the elevation profile and the outside temperature. On success reload the page so
    the figures appear; on a genuine miss show an inline message and change nothing."""
    res = elevation_enrich.recalc_trip(trip_id)
    if res.get("ok"):
        return Response(status_code=200, headers={"HX-Refresh": "true"})
    t = i18n.get_t(db_reader.get_language())
    return HTMLResponse(f'<span class="text-slate-400 text-xs">ℹ️ {t("elevation_calc_failed")}</span>')


@app.post("/api/trips/{trip_id}/note", response_class=HTMLResponse)
async def set_trip_note(request: Request, trip_id: int):
    """#107: save the trip's user note + manual driving tags (drive mode, One-Pedal). Returns a
    small 'saved' confirmation that HTMX drops next to the button. Driving tags are manual because
    the Leapmotor cloud doesn't expose drive mode or One-Pedal (verified on-car)."""
    form = await request.form()
    note = form.get("note") or ""
    drive_mode = (form.get("drive_mode") or "").strip().lower() or None
    op_raw = (form.get("one_pedal") or "").strip()
    one_pedal = int(op_raw) if op_raw in ("0", "1") else None
    db_reader.save_trip_note(trip_id, note, drive_mode=drive_mode, one_pedal=one_pedal)
    return HTMLResponse("✓ " + i18n.get_t(db_reader.get_language())("note_saved"))


@app.post("/api/trips/{trip_id}/auto-note", response_class=HTMLResponse)
async def trip_generate_auto_note(request: Request, trip_id: int):
    """🧭 manual 'Generate' button: builds the start/end address+time+temperature summary
    and writes it straight into the trip's note field, replacing whatever was there (the UI
    confirms with the user first when there was something to lose — trip_detail.html's
    conditional hx-confirm). Reverse-geocoding is a live network call (Nominatim's usage
    policy forbids bulk/automated lookups), so this only ever runs on a click for THIS one
    trip — never a background sweep. Swaps in just the textarea, so the Salva button below
    still submits the fresh text normally."""
    import asyncio
    provider = db_reader.get_setting("geocoder_provider", "")
    key = db_reader.get_secret("geocoder_key", "") or None
    note = await asyncio.get_event_loop().run_in_executor(
        None, db_reader.generate_trip_auto_note, trip_id, provider, key)
    t = i18n.get_t(db_reader.get_language())
    return templates.TemplateResponse(request, "partials/trip_note_textarea.html",
                                      {"trip": {"id": trip_id, "note": note}, "t": t})


@app.delete("/api/charges/{charge_id}")
async def delete_charge(request: Request, charge_id: int):
    """Permanently delete one charge session (HTMX, confirmed in the UI). Reloads the charges list,
    so day/month/lifetime totals recompute."""
    db_reader.delete_charge(charge_id)
    base = request.headers.get("x-ingress-path", "")
    return Response(status_code=200, headers={"HX-Redirect": f"{base}/charges"})


@app.get("/api/charges/merge-preview", response_class=HTMLResponse)
async def charges_merge_preview(request: Request, a: int, b: int):
    """The single charge the merge WOULD produce — figures plus the pause it swallows — so the
    confirm step shows what changes, not just that something will."""
    g = db_reader.preview_merge_charges(a, b)
    if not g:
        return HTMLResponse("")
    return templates.TemplateResponse(request, "partials/charge_merge_preview.html",
                                      _ctx(g=g, a=a, b=b))


@app.post("/api/charges/merge", response_class=HTMLResponse)
async def charges_merge(request: Request, a: int, b: int):
    """Join two charge rows the car split when it declared the cable gone on a pause. The earlier
    becomes the parent. Reversible. Re-validates every guard here — the page only offers the
    button, it does not grant the permission."""
    res = db_reader.merge_charges(a, b)
    if res.get("ok"):
        return Response(status_code=200, headers={"HX-Refresh": "true"})
    t = i18n.get_t(db_reader.get_language())
    return HTMLResponse(f'<div style="color:#f87171;font-size:13px;padding:6px 0">⚠️ {t("charge_merge_failed")}</div>')


@app.post("/api/charges/unmerge", response_class=HTMLResponse)
async def charges_unmerge(request: Request, parent: int):
    """Split a merged charge back into the rows the car reported (reversible — nothing was lost)."""
    db_reader.unmerge_charges(parent)
    return Response(status_code=200, headers={"HX-Refresh": "true"})


@app.post("/api/charges/{charge_id}/note", response_class=HTMLResponse)
async def set_charge_note(request: Request, charge_id: int):
    """#107: save the charge's optional user note (station location, shade, reliability, weather…).
    Returns a small 'saved' confirmation that HTMX drops next to the button."""
    form = await request.form()
    db_reader.save_charge_note(charge_id, form.get("note") or "")
    return HTMLResponse("✓ " + i18n.get_t(db_reader.get_language())("note_saved"))


@app.post("/api/charges/{charge_id}/auto-note", response_class=HTMLResponse)
async def charge_generate_auto_note(request: Request, charge_id: int):
    """🧭 manual 'Generate' button: builds the station-address + start/end time+temperature
    summary and writes it straight into the charge's note field, replacing whatever was
    there (the UI confirms with the user first when there was something to lose —
    charge_card.html's conditional hx-confirm). Both the station lookup and the
    reverse-geocoding a HOME charge skips are live network calls, so this only ever runs
    on a click for THIS one charge — never a background sweep. Swaps in just the
    textarea, so the Salva button below still submits the fresh text normally."""
    import asyncio
    provider = db_reader.get_setting("geocoder_provider", "")
    key = db_reader.get_secret("geocoder_key", "") or None
    note = await asyncio.get_event_loop().run_in_executor(
        None, db_reader.generate_charge_auto_note, charge_id, provider, key)
    t = i18n.get_t(db_reader.get_language())
    return templates.TemplateResponse(request, "partials/charge_note_textarea.html",
                                      {"c": {"id": charge_id, "note": note}, "t": t})


@app.get("/charges", response_class=HTMLResponse)
async def charges_page(request: Request, highlight: int = 0, station: str = ""):
    vehicle, _ = db_reader.get_vehicle()
    stats   = db_reader.get_charge_stats()
    prices  = db_reader.get_charge_prices()
    status  = db_reader.get_latest_status()
    total   = stats.get("session_count") or 0
    station_info = None
    if station:
        # Uncapped: a station reached via ?station=<key> must resolve regardless of the map's
        # top_n cap, same as search_charges(station=...) below.
        stations = db_reader.get_charging_stations(top_n=None)
        station_info = next((s for s in stations if s["key"] == station), None)
    # The calendar's Month view opens on today — or, following a ?highlight=<id> link (e.g. a
    # map popup), on whichever month that ONE charge actually falls on, so it isn't invisible.
    today = db_reader.today_local()
    cal_year, cal_month, cal_open_day = today.year, today.month, 0
    if highlight:
        hl_date = db_reader.get_charge_local_date(highlight)
        if hl_date:
            cal_year, cal_month, cal_open_day = hl_date.year, hl_date.month, hl_date.day
    return templates.TemplateResponse(request, "charges.html", _ctx(
        page="charges", vehicle=vehicle,
        stats=stats, total=total, highlight=highlight,
        charge_types=db_reader.charge_types_localised(), prices=prices,
        status=status, ac_dc=db_reader.get_ac_dc_stats(),
        unconfirmed=db_reader.unconfirmed_charges_count(),
        unconfirmed_id=db_reader.newest_unconfirmed_charge_id(),   # the banner links to it
        station=station, station_info=station_info,
        cal_year=cal_year, cal_month=cal_month, cal_open_day=cal_open_day,
        cal_years=db_reader.get_charge_years(station or None),
        charges_have_odometer=db_reader.charges_have_odometer(),
        # Drawn here, not fetched: see _charges_calendar_ctx. The htmx load-trigger still runs on
        # top of this — it is what re-opens the remembered day and what the ?highlight= scroll
        # hangs off — but the history no longer DEPENDS on it coming back.
        calendar_html=_charges_calendar_html(cal_year, cal_month, station, cal_open_day),
    ))


def _charges_calendar_ctx(year: int, month: int, station: str, open_day: int = 0) -> dict:
    """The Ricariche calendar's context, WITHOUT a Response around it.

    Split out so the Charges page can draw the grid itself on first paint instead of shipping an
    empty box and an htmx request. That box was the whole of #240: htmx swaps nothing when a
    request fails, so one 500 left a "…" on screen with no error, no retry and no way back —
    above a banner announcing a charge to confirm. Drawn server-side there is always a calendar,
    whatever happens to the request afterwards. → _render_charges_calendar for the htmx side.
    """
    import calendar as calmod
    from datetime import date
    today = db_reader.today_local()
    year, month = year or today.year, month or today.month
    month = max(1, min(12, month))
    cal = db_reader.get_charges_calendar_month(year, month, station=station or None)
    weeks = []
    for week in calmod.Calendar(firstweekday=0).monthdayscalendar(year, month):
        row = []
        for day in week:
            row.append(None if day == 0 else {
                "day": day, **cal["days"].get(day, {"count": 0, "kwh": 0.0, "cost": 0.0, "has_cost": False})})
        weeks.append(row)
    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    lang = db_reader.get_language()
    ctx = {
        "t": i18n.get_t(lang), "year": year, "month": month, "weeks": weeks, "total": cal["total"],
        "month_label": i18n.fmt_month_year(lang, date(year, month, 1)),
        "weekday_abbrs": i18n.weekday_abbrs(lang),
        "prev_year": prev_year, "prev_month": prev_month,
        "next_year": next_year, "next_month": next_month,
        "today": today, "station": station,
        "charge_types": db_reader.charge_types_localised(), "fmt_dur": _fmt_dur,
        # 🔴 Not optional, and not only for the drawer below: with `open_day` this response
        # renders charge_card.html INLINE (grid + day together, see the open_day branch), and
        # that card prints `currency.symbol`. Without this the whole calendar was a 500 — and
        # htmx swaps nothing on a 500, so the page kept its "…" placeholder and never came back.
        # base.html re-adds open_day from the remembered day on every request for that month, so
        # it stayed broken across reloads. (#240, found by Silvio pressing Refresh.)
        "currency": db_reader.get_currency(),
    }
    if open_day and open_day in cal["days"]:
        ctx["open_day"] = open_day     # so the grid can ring the day the drawer is showing
        ctx["open_day_charges"] = db_reader.get_charges_calendar_day(year, month, open_day, station=station or None)
        ctx["open_day_label"] = i18n.fmt_day_month_year(lang, date(year, month, open_day))
    return ctx


def _charges_calendar_html(year: int, month: int, station: str, open_day: int = 0) -> str:
    """The same grid as a string, for the page to embed on first paint."""
    return templates.get_template("partials/charges_calendar_month.html").render(
        _charges_calendar_ctx(year, month, station, open_day))


def _render_charges_calendar(request: Request, year: int, month: int, station: str, open_day: int = 0):
    """Shared by /api/charges/calendar and the search endpoint's empty-filters fallback."""
    return templates.TemplateResponse(request, "partials/charges_calendar_month.html",
                                      _charges_calendar_ctx(year, month, station, open_day))


def _fuel_calendar_ctx(year: int, month: int, open_day: int = 0) -> dict:
    """The Rifornimenti Month view (beta #14 @gm27271). Same shape as the Charges one above — same
    week grid, same prev/next/today nav, same day drawer — because it IS the same view over a
    different table, and a second way of laying out a month would drift from the first. Split from
    its Response for the same reason as _charges_calendar_ctx (#240)."""
    import calendar as calmod
    from datetime import date
    today = db_reader.today_local()
    year, month = year or today.year, month or today.month
    month = max(1, min(12, month))
    cal = db_reader.get_fuel_calendar_month(year, month)
    empty = {"count": 0, "liters": 0.0, "cost": 0.0, "has_cost": False}
    weeks = []
    for week in calmod.Calendar(firstweekday=0).monthdayscalendar(year, month):
        weeks.append([None if day == 0 else {"day": day, **cal["days"].get(day, empty)} for day in week])
    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    lang = db_reader.get_language()
    ctx = {
        "t": i18n.get_t(lang), "year": year, "month": month, "weeks": weeks, "total": cal["total"],
        "month_label": i18n.fmt_month_year(lang, date(year, month, 1)),
        "weekday_abbrs": i18n.weekday_abbrs(lang),
        "prev_year": prev_year, "prev_month": prev_month,
        "next_year": next_year, "next_month": next_month,
        "today": today, "currency": db_reader.get_currency(),
    }
    if open_day and open_day in cal["days"]:
        ctx["open_day"] = open_day     # so the grid can ring the day the drawer is showing
        ctx["open_day_fuel"] = db_reader.get_fuel_calendar_day(year, month, open_day)
        ctx["open_day_label"] = i18n.fmt_day_month_year(lang, date(year, month, open_day))
    return ctx


def _fuel_calendar_html(year: int, month: int, open_day: int = 0) -> str:
    return templates.get_template("partials/fuel_calendar_month.html").render(
        _fuel_calendar_ctx(year, month, open_day))


def _render_fuel_calendar(request: Request, year: int, month: int, open_day: int = 0):
    return templates.TemplateResponse(request, "partials/fuel_calendar_month.html",
                                      _fuel_calendar_ctx(year, month, open_day))


@app.get("/api/fuel/calendar", response_class=HTMLResponse)
async def fuel_calendar(request: Request, year: int = 0, month: int = 0, open_day: int = 0):
    """Rifornimenti Month view (HTMX partial, day totals only). Behind the SAME gate as the page it
    belongs to: a route that serves the page's data without the page's guard hands a BEV or a
    non-research build the very fuel history the guard exists to withhold."""
    if _fuel_blocked():
        return HTMLResponse("", status_code=403)
    return _render_fuel_calendar(request, year, month, open_day)


@app.get("/api/fuel/calendar/day", response_class=HTMLResponse)
async def fuel_calendar_day(request: Request, year: int, month: int, day: int):
    """One day's refuels for the Month view's day drawer. Gated like everything else fuel."""
    if _fuel_blocked():
        return HTMLResponse("", status_code=403)
    lang = db_reader.get_language()
    from datetime import date
    return templates.TemplateResponse(request, "partials/fuel_calendar_day.html", {
        "t": i18n.get_t(lang), "currency": db_reader.get_currency(),
        "fuel": db_reader.get_fuel_calendar_day(year, month, day),
        "day_label": i18n.fmt_day_month_year(lang, date(year, month, day)),
    })


@app.get("/api/charges/calendar", response_class=HTMLResponse)
async def charges_calendar(request: Request, year: int = 0, month: int = 0, station: str = "", open_day: int = 0):
    """Ricariche 'calendar' Month view (HTMX partial, day totals only) — the day drawer
    loads a day's actual charge cards lazily on click (see charges_calendar_day below),
    so a month never ships more markup than the handful of days the user opens."""
    return _render_charges_calendar(request, year, month, station, open_day)


@app.get("/api/charges/calendar/day", response_class=HTMLResponse)
async def charges_calendar_day(request: Request, year: int, month: int, day: int, station: str = ""):
    """One day's charge cards for the Month view's day drawer."""
    lang = db_reader.get_language()
    charges = db_reader.get_charges_calendar_day(year, month, day, station=station or None)
    from datetime import date
    return templates.TemplateResponse(request, "partials/charges_calendar_day.html", {
        "t": i18n.get_t(lang), "charge_types": db_reader.charge_types_localised(), "fmt_dur": _fmt_dur,
        "charges": charges, "day_label": i18n.fmt_day_month_year(lang, date(year, month, day)),
        "currency": db_reader.get_currency(),   # the price box is labelled in the reader's money
    })


@app.get("/api/charges/search", response_class=HTMLResponse)
async def charges_search(request: Request, q: str = "", type: str = "",
                          cost_min: str = "", cost_max: str = "",
                          kwh_min: str = "", kwh_max: str = "",
                          date_from: str = "", date_to: str = "", station: str = "",
                          year: int = 0, month: int = 0):
    """Ricariche free-text + advanced-filter search (HTMX partial) — replaces the calendar
    with a flat, most-recent-first list of matches (see charge_card.html). No filter at all
    (search cleared) falls back to the calendar Month view instead of an empty list. The
    numeric fields arrive as strings (see _opt_float) — an empty advanced-filter field must
    never 422 the whole request (#175: htmx doesn't swap in a non-2xx response, so the
    search silently did nothing whenever ANY unfilled number field was submitted)."""
    n_cost_min, n_cost_max = _opt_float(cost_min), _opt_float(cost_max)
    n_kwh_min, n_kwh_max = _opt_float(kwh_min), _opt_float(kwh_max)
    if not any((q.strip(), type.strip(), n_cost_min is not None, n_cost_max is not None,
                n_kwh_min is not None, n_kwh_max is not None, date_from.strip(), date_to.strip())):
        return _render_charges_calendar(request, year, month, station)
    lang = db_reader.get_language()
    charges = db_reader.search_charges(
        text=q, charge_type=type, cost_min=n_cost_min, cost_max=n_cost_max,
        kwh_min=n_kwh_min, kwh_max=n_kwh_max, date_from=date_from, date_to=date_to,
        station=station or None)
    # #191 (@riri19): a result card shows "16:38 → 16:42" and nothing else — in the calendar the
    # day is the heading above it, but a search result stands alone and the day was simply gone.
    # He searched "Intermarché", found the session, and had to go back to the calendar to learn
    # WHEN. Same day label the history tree uses, so the two views read alike.
    for c in charges:
        dt = db_reader._local_dt(c.get("started_at"))
        c["date_label"] = i18n.fmt_day_month_year(lang, dt) if dt else None
    today = db_reader.today_local()
    return templates.TemplateResponse(request, "partials/charges_search_results.html", {
        "t": i18n.get_t(lang), "charge_types": db_reader.charge_types_localised(), "fmt_dur": _fmt_dur,
        "charges": charges, "station": station,
        # charge_card.html labels its price box in the reader's own money, and this partial is
        # rendered on its own — it inherits nothing from _ctx. Three routes were missing it.
        "currency": db_reader.get_currency(),
        "year": year or today.year, "month": month or today.month,
    })


@app.get("/statistics", response_class=HTMLResponse)
async def statistics(request: Request):
    vehicle, _ = db_reader.get_vehicle()
    grouped  = db_reader.get_stats_grouped()
    totals   = db_reader.get_stats_summary()
    totals["v2l_total_kwh"] = db_reader.get_v2l_total_kwh()
    # REEV only — what the driving cost, counting every trip including the ones where the
    # efficiency figure above deliberately goes blank because the generator was running.
    _reev = db_reader.is_reev_car()
    totals["reev_total"] = db_reader.reev_total_consumption() if _reev else None
    # …and beside it what was actually bought, which Mate measures rather than derives.
    totals["reev_spend"] = db_reader.reev_actual_spend() if _reev else None
    # …and what the GENERATOR itself drinks while it runs (@michapr, beta #26). Gated on the DATA,
    # not just on the markup: `wallbox_enabled` taught us that hiding a card while the figure is
    # still computed is not a gate at all. REEV stays capability-gated AND beta-only.
    totals["reev_fuel"] = (db_reader.reev_fuel_summary()
                           if (_reev and research.research_enabled()) else None)
    # …and the one number that answers "plug in here, or just burn petrol?" (@ebagnoli, beta #13).
    # Same gate as the card above — REEV capability AND the BetaTester build — and gated on the
    # DATA, not the markup: computing it for a car that must not see it is not a gate.
    totals["reev_breakeven"] = (db_reader.reev_breakeven_kwh_price()
                                if (_reev and research.research_enabled()) else None)
    # …and what all of it COST: every euro spent over every kilometre driven (@michapr's card, on
    # Silvio's basis — a cost is the whole cost, not the driving's share of it). The litres burned
    # are passed only so a car with no tank never reads the fuel table.
    if _reev:
        _rt = totals["reev_total"]
        totals["cost100"] = db_reader.cost_per_100km(_rt["total_fuel_l"] if _rt else 0.0)
    else:
        totals["cost100"] = db_reader.cost_per_100km()
    # …and what Mate measured but refuses to attribute: the kilometres the car covered while the
    # cloud had nothing new to say. They are kept out of every figure above — both the distance and
    # the charge that went with it — so this is the only place they are ever stated.
    totals["offline_gaps"] = db_reader.offline_gaps_summary()
    return templates.TemplateResponse(request, "statistics.html", _ctx(
        page="statistics", vehicle=vehicle,
        grouped=grouped, totals=totals,
    ))


@app.get("/report", response_class=HTMLResponse)
async def report(request: Request, month: str | None = None):
    from datetime import datetime, timezone
    vehicle, _ = db_reader.get_vehicle()
    data = db_reader.get_monthly_report(month)
    track = db_reader.get_month_track(data["month"]) if data.get("has_data") else []
    now_month = datetime.now(timezone.utc).astimezone(db_reader._local_tz()).strftime("%Y-%m")
    return templates.TemplateResponse(request, "report.html", _ctx(
        page="report", vehicle=vehicle, r=data, track=track,
        is_current_month=(data.get("month") == now_month),
    ))


@app.get("/battery", response_class=HTMLResponse)
async def battery_page(request: Request):
    vehicle, _ = db_reader.get_vehicle()
    health = db_reader.get_battery_health()
    vampire = db_reader.get_vampire_drain(
        min_drop_pct=float(db_reader.get_setting("vampire_min_drop_pct", "0.2") or 0.2),
        min_hours=float(db_reader.get_setting("vampire_min_hours", "1") or 1))
    return templates.TemplateResponse(request, "battery.html", _ctx(
        page="battery", vehicle=vehicle, health=health, vampire=vampire,
    ))


# Sample REEV signals (from gm27271's real C10 REEV) for the research-only /reev?demo preview.
# 3261=3259+3260 and 3258=3256+3257 hold (the confirmed range arithmetic).
_REEV_DEMO_SIGNALS = {
    "3235": "96.2",   # fuel level %
    "3259": "796",    # fuel range (display)      3256 max
    "3256": "830",    # fuel range (rated/max)
    "3260": "54",     # EV range (display)        3257 max
    "3257": "147",    # EV range (rated/max, ~WLTP)
    "3261": "850",    # combined range (display)  = 796 + 54
    "3258": "977",    # combined range (rated/max) = 830 + 147
    "3262": "1",      # range mode
}


@app.get("/reev", response_class=HTMLResponse)
async def reev_page(request: Request):
    """Dedicated, isolated REEV (range-extender) view. BETA / data-collection: it surfaces the
    raw fuel signals we currently know (mapped from a real B10 REEV in kerniger/leapmotor-ha#46)
    WITHOUT plumbing them into trips/charges/stats yet — those get touched only once the
    behaviour is validated against real REEV data. Live signal fetch (no pipeline storage)."""
    import asyncio
    is_research = research.research_enabled()
    # REEV is capability-gated (is_reev) AND beta-only (research): a BEV owner must NEVER reach any REEV
    # surface, on any build, and the official build exposes none of it. Redirect anyone who isn't a REEV
    # car on a research build — the sole exception is the ?demo layout preview (research-only, no car).
    is_reev_car = db_reader.is_reev_car()
    demo = bool(is_research and request.query_params.get("demo"))
    if not (is_reev_car and is_research) and not demo:
        return RedirectResponse(request.headers.get("x-ingress-path", "") + "/")
    vehicle, _ = db_reader.get_vehicle()
    # Research-only preview: /reev?demo=1 renders the dashboard with sample REEV values (from a real
    # C10 REEV) so the layout can be reviewed without a range-extender car. Inert in the official build.
    if is_research and request.query_params.get("demo"):
        sig = _REEV_DEMO_SIGNALS
    else:
        live = await asyncio.get_event_loop().run_in_executor(None, command_client.get_fresh_signals)
        # Fall back to the last stored raw signals (research capture) when a live fetch isn't available
        # — e.g. a replayed tester bundle, or a transient cloud hiccup — so the dashboard stays populated.
        sig = live or (db_reader.latest_raw_signals() if is_research else {})
    signals = sig

    def f(key):
        v = sig.get(key)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    reev = {
        "has_fuel":              sig.get("3235") is not None,   # the REEV marker
        "fuel_level_pct":        f("3235"),                     # fuel tank level %
        "fuel_range_km":         f("3259"),                     # range on fuel (display/adaptive)
        "combined_range_km":     f("3261"),                     # battery + fuel (display/adaptive)
        "battery_range_km":      f("3260"),                     # EV-only range (display/adaptive)
        "range_mode":            f("3262"),                     # 1 on BEV; may vary on REEV
        # Phase A — the rated/max range trio (parallel to the display one, arithmetic 3256+3257=3258).
        "fuel_range_max_km":     f("3256"),
        "battery_range_max_km":  f("3257"),
        "combined_range_max_km": f("3258"),
    }
    # Battery side of the dual-energy picture (SoC + capacity) — from the pipeline, not a live signal.
    status = db_reader.get_latest_status() or {}
    reev["soc_pct"] = status.get("soc")
    # Phase B — the cloud's own consumption (electric kWh/100km + fuel L/100km). Best-effort; only for a
    # real REEV or the demo, so a BEV owner in research mode doesn't pay an extra cloud call for nothing.
    consumption = None
    if reev["has_fuel"] and request.query_params.get("demo") and is_research:
        consumption = {"elec_kwh_100km": 17.8, "fuel_l_100km": 6.2, "fuel_mpg": 37.9}
    elif reev["has_fuel"]:
        consumption = await asyncio.get_event_loop().run_in_executor(
            None, command_client.get_plugin_consumption)
    # Absolute ELECTRIC energy (last 7 days), driver/A·C/other split — the metered getEC kWh, the
    # counterpart to the fuel litres shown above. Reuses the 6h Statistics cache, so it only hits the
    # cloud on a cold/stale cache (never an extra call per REEV page view).
    energy_abs = None
    if demo:
        energy_abs = {"driving_kwh": 12.4, "ac_kwh": 1.9, "other_kwh": 0.8, "total_kwh": 15.1,
                      "driving_pct": 82.1, "ac_pct": 12.6, "other_pct": 5.3}
    elif reev["has_fuel"]:
        import time as _t
        if not _energy_cache["data"] or _t.time() - _energy_cache["ts"] >= 6 * 3600:
            eb = await asyncio.get_event_loop().run_in_executor(None, command_client.get_energy_breakdown)
            if eb:
                _energy_cache["data"] = eb
                _energy_cache["ts"] = _t.time()
        energy_abs = _energy_cache["data"]
    return templates.TemplateResponse(request, "reev.html", _ctx(
        page="reev", vehicle=vehicle, reev=reev, consumption=consumption, signals_ok=bool(signals),
        energy_abs=energy_abs,                        # absolute electric kWh (7-day getEC split)
        reev_summary=db_reader.reev_fuel_summary(),   # on-board range-extender consumption (from trips)
        demo=bool(is_research and request.query_params.get("demo")),
        logbook_html=_logbook_list_html() if is_research else "",
        raw_count=db_reader.count_raw_signals() if is_research else 0,
    ))


def _logbook_list_html() -> str:
    """Render the logbook entries (newest first) as the HTMX-swapped list."""
    import datetime as _dt
    notes = db_reader.get_logbook()
    if not notes:
        return '<div class="text-xs text-slate-500">—</div>'
    out = []
    for n in notes:
        ts = _dt.datetime.fromtimestamp(n["ts"] / 1000).strftime("%Y-%m-%d %H:%M")
        note = (n["note"] or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        out.append(f'<div class="flex gap-2 text-xs border-b border-slate-800 py-1.5">'
                   f'<span class="text-slate-500 font-mono flex-shrink-0">{ts}</span>'
                   f'<span class="text-slate-200">{note}</span></div>')
    return "".join(out)


@app.post("/api/research/logbook", response_class=HTMLResponse)
async def research_logbook_add(request: Request):
    """BetaTester only: add a timestamped logbook note, return the refreshed list."""
    if not research.research_enabled():
        return Response(status_code=404)
    form = await request.form()
    db_reader.add_logbook_note(form.get("note", ""))
    return HTMLResponse(_logbook_list_html())


# Which trip columns the research bundle may carry. An allow-list on purpose — see research_export.
# Grouped by the question each group answers, because that is the only defence against it growing
# into "everything except the coordinates" one convenient column at a time.
_RESEARCH_TRIP_FIELDS = (
    # identity & shape
    "id", "started_at", "ended_at", "distance_km", "duration_min", "merged_into_id", "reconstructed",
    # the battery, both ways of measuring it — the whole range-extender argument in four columns
    "start_soc", "end_soc", "ec_kwh", "ec_driving", "ec_ac", "ec_other", "ec_tried", "ec_stable",
    "efficiency_kwh_100km", "efficiency_soc", "regen_kwh",
    # the tank, both ways of measuring it: the car's own millilitre counter and the percentage gauge
    "fuel_start_pct", "fuel_end_pct", "fuel_start_l", "fuel_end_l",
    # what Mate MADE of the two above — the numbers actually printed on the trip
    "engine_ran", "engine_km", "fuel_used_l", "fuel_l_100km",
    # ⚠️ `fuel_cost` and NOT `cost`: the electric one is added later, by _localized_trips, which the
    # calendar goes through and this export does not. Allow-listing it would have shipped a column
    # that is blank on every row — and a blank cost column reads as "this drive cost nothing".
    "reev_elec_kwh", "reev_elec_kwh_100km", "fuel_cost",
    # conditions, which is what a consumption that looks wrong usually turns out to be
    "drive_mode", "one_pedal", "outside_temp_start_c", "outside_temp_end_c",
    "elevation_gain_m", "elevation_loss_m",
)


@app.get("/api/research/export")
async def research_export():
    """BetaTester only: build an ENCRYPTED bundle (redacted raw-signal history + logbook + trips)
    for the tester to attach to a beta issue. GPS is stripped; the bundle is sealed to our public
    key so only the maintainer's private key can open it."""
    if not research.research_enabled():
        return Response(status_code=404)
    import csv, io, json, time, zipfile
    rows = research.redact_signal_rows(db_reader.get_raw_signal_rows())
    logbook = db_reader.get_logbook(limit=1000000)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        s = io.StringIO(); w = csv.writer(s)
        w.writerow(["ts_ms", "sig_key", "value"]); w.writerows(rows)
        z.writestr("raw_signals_log.csv", s.getvalue())
        s = io.StringIO(); w = csv.writer(s)
        w.writerow(["ts_ms", "note"]); w.writerows([(n["ts"], n["note"]) for n in logbook])
        z.writestr("logbook.csv", s.getvalue())
        # The trips themselves — the one thing this bundle could never answer. Raw signals say what
        # the car sent; they do not say what Mate MADE of it, and every open range-extender question
        # is about the second (which trips got a getEC reading at all, whether the tank counter and
        # the tank percentage agree, whether ΔSoC and getEC tell the same story on a drive the
        # generator ran through). Without it we were reduced to reasoning from our own BEV.
        #
        # An ALLOW-list, not a "drop the coordinates" list: `get_trips` is SELECT *, so a column
        # added next month would ride out of here on its own. Nothing about WHERE anyone drove
        # leaves the machine — no coordinates, no geohashes, no addresses — and the columns that do
        # are the ones an energy question is made of.
        trips = db_reader.get_trips(limit=1_000_000)
        s = io.StringIO(); w = csv.DictWriter(s, fieldnames=_RESEARCH_TRIP_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(trips)
        z.writestr("trips.csv", s.getvalue())
        # Cloud consumption probe: UNMAPPED raw responses (getEC 24h/7d + 6-week rank), so a REEV's
        # fuel/L-100km field surfaces even though the BEV mapping ignores it. Best-effort: a cloud
        # hiccup must not fail the export.
        cloud = None
        try:
            cloud = command_client.get_consumption_probe_raw()
        except Exception:  # noqa: BLE001
            cloud = None
        if cloud:
            import re as _re
            cp = json.dumps(cloud, indent=2, ensure_ascii=False)
            cp = _re.sub(r"LFZ[A-Z0-9]{12,}", "<VIN>", cp)  # defensive: responses carry no VIN, but scrub anyway
            z.writestr("cloud_probes.json", cp)
        # The charges, and what the behaviour settings are set to — both lifted from the
        # diagnostics bundle, which already writes them redacted (no coordinates, no location name,
        # no free text) and has a test holding that promise.
        #
        # They were missing and it cost us twice. @pdifeo's beta #30 opens with "Ricarica e poi
        # ritorno alla base" and his bundle carried 96,987 signal rows, 98 trips and not one charge
        # — the half of the day he wrote about first. And beta #29 (@michapr) was ENTIRELY about a
        # charge recorded as two: answering it meant asking him for a second, different file.
        # The settings for the same reason a day later: #250 turned out to be ten lines of them (a
        # 13.5 A detection floor on a car charging at 13), and without them we would have hunted a
        # defect that was not there.
        #
        # Cheap by any measure: 20-40 charge rows over a fortnight, against a raw-signal log that is
        # 98% of the payload.
        try:
            z.writestr("charges.txt", diagnostics._charges_section())
        except Exception:  # noqa: BLE001 — a bundle must never fail on one section
            pass
        try:
            z.writestr("settings.txt", diagnostics._advanced_settings_section())
        except Exception:  # noqa: BLE001
            pass
        try:
            _charge_count = len(db_reader.get_charges(limit=1_000_000))
        except Exception:  # noqa: BLE001
            _charge_count = None
        z.writestr("meta.json", json.dumps({
            "mate_version": MATE_VERSION,
            "charges": _charge_count,
            "car_type": (db_reader.get_vehicle()[0] or {}).get("car_type"),
            "is_reev": "1" if db_reader.is_reev_car() else "0",
            "signal_rows": len(rows), "logbook_notes": len(logbook),
            "cloud_probes": bool(cloud),
            "redacted_signals": sorted(research.REDACT_SIGNALS),
            # Said here so the answer to "how much of this history has a getEC reading" is in the
            # bundle rather than something we work out and then have to be believed about.
            "trips": len(trips),
            "trips_with_ec_kwh": sum(1 for t in trips if t.get("ec_kwh") is not None),
            "trip_fields": list(_RESEARCH_TRIP_FIELDS),
        }, indent=2))
    encrypted = research.encrypt_bundle(buf.getvalue())
    fname = f"mate-beta-bundle-{int(time.time())}.matebeta"
    return Response(encrypted, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.get("/research/consent", response_class=HTMLResponse)
async def research_consent_page(request: Request):
    """One-time BetaTester consent gate (research build only)."""
    if not research.research_enabled():
        return RedirectResponse(request.headers.get("x-ingress-path", "") + "/")
    lang = db_reader.get_language()
    return templates.TemplateResponse(request, "consent.html", {"request": request, "t": i18n.get_t(lang)})


@app.post("/research/consent")
async def research_consent_accept(request: Request):
    db_reader.set_setting("research_consent", "1")
    return RedirectResponse(request.headers.get("x-ingress-path", "") + "/", status_code=303)


def _fuel_blocked() -> bool:
    """REEV+research gate, mirroring reev_page: a BEV / non-research visitor must never reach fuel data,
    on any build. Applied to the page AND the write endpoints."""
    return not (db_reader.is_reev_car() and research.research_enabled())


def _fuel_local_to_utc(ts_str: str) -> str:
    """A <input type=datetime-local> value ('YYYY-MM-DDTHH:MM', naive LOCAL) → UTC ISO for storage, so
    a refuel sits on the same UTC timeline as trips/positions (the WAC orders by it). Blank/garbage → now."""
    from datetime import datetime, timezone
    try:
        dt = datetime.fromisoformat(ts_str)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc).isoformat()
    if dt.tzinfo is None:
        _tz = db_reader._local_tz()
        dt = dt.replace(tzinfo=_tz) if _tz else dt.astimezone()
    return dt.astimezone(timezone.utc).isoformat()


def _fuel_ctx(request: Request):
    """Shared context for the Rifornimenti page + its HTMX partials: the refuel log and the live tank
    state (level from the car's fuel %, blended €/L from the fuel WAC)."""
    from datetime import datetime, timezone
    vehicle, _ = db_reader.get_vehicle()
    vid = vehicle["id"] if vehicle else None
    # Look for refuels the car's own gauge already recorded (beta #14 @gm27271). Incremental after
    # the first pass, so this costs nothing on a page that is opened often; the first pass is the
    # expensive one and it is also the valuable one — it finds the fills from before the feature.
    db_reader.scan_fuel_refuels(vid)
    detected = db_reader.list_fuel_detected(vid)
    for d in detected:
        for key, src in (("ts_local", "ts"), ("ts_from_local", "ts_from")):
            _ld = db_reader._local_dt(d.get(src))
            d[key] = _ld.strftime("%d/%m/%Y %H:%M") if _ld else (d.get(src) or "")
    purchases = db_reader.list_fuel_purchases()
    for p in purchases:                       # localise the timestamp for display (DB is UTC)
        _ld = db_reader._local_dt(p.get("ts"))
        p["ts_local"] = _ld.strftime("%d/%m/%Y %H:%M") if _ld else (p.get("ts") or "")
    fuel_pct = db_reader.latest_fuel_pct(vid)
    # Litres the car counted itself (3263) when it reports them; otherwise the tank % against the
    # model's capacity — which is where a C10 used to read 5 % high off a 50 L assumption.
    liters = db_reader.latest_fuel_liters(vid)
    if liters is None and fuel_pct is not None:
        liters = fuel_pct / 100.0 * db_reader.reev_tank_l()
    liters = round(liters, 1) if liters is not None else None
    blend = db_reader.fuel_blended_price_at(vid, datetime.now(timezone.utc).isoformat()) if vid is not None else None
    tank = {"fuel_pct": fuel_pct, "liters": liters,
            "eur_per_l": round(blend, 3) if blend else None,
            "value": round(liters * blend, 2) if (liters and blend) else None}
    now_local = datetime.now(timezone.utc).astimezone(db_reader._local_tz()).strftime("%Y-%m-%dT%H:%M")
    today = db_reader.today_local()
    return _ctx(page="fuel", vehicle=vehicle, purchases=purchases, tank=tank, now_local=now_local,
                detected=detected,
                # drawn, not fetched — see _charges_calendar_ctx (#240). The wrapper still listens
                # for `fuelChanged`, which is what refreshes it after an entry is added or deleted.
                calendar_html=_fuel_calendar_html(today.year, today.month))


@app.get("/fuel", response_class=HTMLResponse)
async def fuel_page(request: Request):
    if _fuel_blocked():
        return RedirectResponse(request.headers.get("x-ingress-path", "") + "/")
    return templates.TemplateResponse(request, "fuel.html", _fuel_ctx(request))


@app.post("/api/fuel/add", response_class=HTMLResponse)
async def fuel_add(request: Request):
    if _fuel_blocked():
        return RedirectResponse(request.headers.get("x-ingress-path", "") + "/", status_code=303)
    form = await request.form()
    try:
        liters = float(form.get("liters"))
    except (TypeError, ValueError):
        liters = 0.0
    ppl = (form.get("price_per_l") or "").strip()
    tot = (form.get("total_cost") or "").strip()
    note = (form.get("note") or "").strip() or None
    ts = _fuel_local_to_utc((form.get("ts") or "").strip())
    if liters > 0 and (ppl or tot):
        try:
            # €/L wins if both are given (keeps the pair consistent — total is then derived from it).
            if ppl:
                db_reader.add_fuel_purchase(ts, liters, price_per_l=float(ppl), note=note)
            else:
                db_reader.add_fuel_purchase(ts, liters, total_cost=float(tot), note=note)
        except (ValueError, TypeError):
            pass
    # …and tell the month calendar below to redraw, so the refuel you just typed shows up in its day
    # instead of waiting for a page reload (beta #14).
    return templates.TemplateResponse(request, "partials/fuel_content.html", _fuel_ctx(request),
                                      headers={"HX-Trigger": "fuelChanged"})


@app.post("/api/fuel/detected/confirm", response_class=HTMLResponse)
async def fuel_detected_confirm(request: Request):
    """"Yes, that was a refuel — here's what it cost." Mate supplies the instant and the litres; the
    price is the one thing the cloud can never know, so it is the one field he has to fill."""
    if _fuel_blocked():
        return RedirectResponse(request.headers.get("x-ingress-path", "") + "/", status_code=303)
    form = await request.form()
    ppl = (form.get("price_per_l") or "").strip()
    tot = (form.get("total_cost") or "").strip()
    try:
        liters = float(form.get("liters"))
    except (TypeError, ValueError):
        liters = 0.0
    if ppl or tot:
        try:
            db_reader.confirm_fuel_detected(
                int(form.get("id")), liters=liters or None,
                price_per_l=float(ppl) if ppl else None,
                total_cost=None if ppl else float(tot),
                note=(form.get("note") or "").strip() or None)
        except (TypeError, ValueError):
            pass
    return templates.TemplateResponse(request, "partials/fuel_content.html", _fuel_ctx(request),
                                      headers={"HX-Trigger": "fuelChanged"})


@app.post("/api/fuel/detected/dismiss", response_class=HTMLResponse)
async def fuel_detected_dismiss(request: Request):
    """"That was not a refuel." Tombstoned rather than deleted — see dismiss_fuel_detected."""
    if _fuel_blocked():
        return RedirectResponse(request.headers.get("x-ingress-path", "") + "/", status_code=303)
    form = await request.form()
    try:
        db_reader.dismiss_fuel_detected(int(form.get("id")))
    except (TypeError, ValueError):
        pass
    return templates.TemplateResponse(request, "partials/fuel_content.html", _fuel_ctx(request),
                                      headers={"HX-Trigger": "fuelChanged"})


@app.post("/api/fuel/{purchase_id}/auto-note", response_class=HTMLResponse)
async def fuel_generate_auto_note(request: Request, purchase_id: int):
    """🧭 on a refuel: writes where the car was standing into that refuel's note, so the pump
    identifies itself instead of being typed in — asked for by @gm27271 (beta discussion #14).
    Same shape as the charge button above: a live reverse-geocode, so it only ever runs on a click
    for THIS one refuel, never as a background sweep. Swaps the whole list back because the note
    is shown inline on the row rather than in an editable field of its own."""
    if _fuel_blocked():
        return RedirectResponse(request.headers.get("x-ingress-path", "") + "/", status_code=303)
    import asyncio
    provider = db_reader.get_setting("geocoder_provider", "")
    key = db_reader.get_secret("geocoder_key", "") or None
    await asyncio.get_event_loop().run_in_executor(
        None, db_reader.generate_fuel_auto_note, purchase_id, provider, key)
    return templates.TemplateResponse(request, "partials/fuel_content.html", _fuel_ctx(request),
                                      headers={"HX-Trigger": "fuelChanged"})


@app.post("/api/fuel/delete", response_class=HTMLResponse)
async def fuel_delete(request: Request):
    if _fuel_blocked():
        return RedirectResponse(request.headers.get("x-ingress-path", "") + "/", status_code=303)
    form = await request.form()
    try:
        db_reader.delete_fuel_purchase(int(form.get("id")))
    except (TypeError, ValueError):
        pass
    return templates.TemplateResponse(request, "partials/fuel_content.html", _fuel_ctx(request),
                                      headers={"HX-Trigger": "fuelChanged"})


def _maint_ctx(request: Request):
    """Shared context for the maintenance page + its HTMX partial responses."""
    from datetime import date
    vehicle, _ = db_reader.get_vehicle()
    lang = db_reader.get_language()
    status = db_reader.get_latest_status() or {}
    data = maintenance.compute(vehicle, status.get("odometer_km"), lang)
    return _ctx(page="maintenance", vehicle=vehicle, maint=data,
                m=maintenance.chrome(lang), today=date.today().isoformat())


@app.get("/maintenance", response_class=HTMLResponse)
async def maintenance_page(request: Request):
    return templates.TemplateResponse(request, "maintenance.html", _maint_ctx(request))


@app.post("/api/maintenance/log", response_class=HTMLResponse)
async def maintenance_log(request: Request):
    form = await request.form()
    vehicle, _ = db_reader.get_vehicle()
    st = (form.get("service_type") or "").strip()
    dt = (form.get("date") or "").strip()
    if vehicle and st and dt:
        try:
            # the km field is entered in the user's unit (mi for UK/US) → store as km
            km = units.dist_to_km(float(form.get("km"))) if (form.get("km") or "") != "" else None
        except (TypeError, ValueError):
            km = None
        maintenance.add_log(vehicle["id"], st, dt, km, (form.get("note") or "").strip())
    return templates.TemplateResponse(request, "partials/maintenance_content.html", _maint_ctx(request))


@app.post("/api/maintenance/unlog", response_class=HTMLResponse)
async def maintenance_unlog(request: Request):
    form = await request.form()
    vehicle, _ = db_reader.get_vehicle()
    st = (form.get("service_type") or "").strip()
    if vehicle and st:
        maintenance.delete_log(vehicle["id"], st)
    return templates.TemplateResponse(request, "partials/maintenance_content.html", _maint_ctx(request))


@app.post("/api/maintenance/baseline", response_class=HTMLResponse)
async def maintenance_baseline(request: Request):
    form = await request.form()
    dt = (form.get("date") or "").strip()
    if dt:
        # #112 (@Wartopia): the user now sets the delivery ODOMETER too (in their unit → store km), so a
        # first-service bar can start from the real odometer (0 for a new car) instead of the odometer
        # Mate happened to see on install. Blank → keep the current value.
        try:
            km = units.dist_to_km(float(form.get("km"))) if (form.get("km") or "").strip() != "" else None
        except (TypeError, ValueError):
            km = None
        if km is None:
            _, km, _explicit = maintenance.get_baseline()   # fall back to the current anchor km
        maintenance.set_baseline(dt, km)
    return templates.TemplateResponse(request, "partials/maintenance_content.html", _maint_ctx(request))


@app.get("/map", response_class=HTMLResponse)
async def map_page(request: Request):
    vehicle, _ = db_reader.get_vehicle()
    # How many trip routes to draw (get_all_track's max_trips), set from the box on the map's own
    # legend row — same POST-Redirect-GET convention as the station-count box (save_map_trip_count
    # below): 0 = every trip, the pre-existing behavior, so a fresh install is unaffected.
    try:
        trips_shown = max(0, int(db_reader.get_setting("map_trips_shown", "0")))
    except (TypeError, ValueError):
        trips_shown = 0
    track    = db_reader.get_all_track(max_trips=trips_shown or None)
    places   = db_reader.get_frequent_places()
    # Default 1, deliberately: a station used ONCE is exactly what this layer is for (see
    # get_charging_stations). The slider exists for the opposite taste — someone who charges
    # in many one-off places and wants the map down to the spots they actually return to.
    try:
        min_sessions = max(1, int(db_reader.get_setting("map_station_min_sessions", "1")))
    except (TypeError, ValueError):
        min_sessions = 1
    # How many station markers to draw (get_charging_stations' top_n), set from the box on the
    # map's own legend row rather than buried in Settings. READ ONLY here — the box POSTs to
    # save_map_station_count and comes back through a redirect, so the page that renders the
    # map never writes anything.
    try:
        stations_top_n = max(0, int(db_reader.get_setting("map_station_top_n", "15")))
    except (TypeError, ValueError):
        stations_top_n = 15
    stations = db_reader.get_charging_stations(
        min_sessions=min_sessions, top_n=None if stations_top_n == 0 else stations_top_n)
    # Popup markup is built client-side from this JSON (see map.html), so the currency symbol/
    # placement/decimal-separator formatting `| money` gives every other cost on the site has to be
    # baked in server-side here too — a bare .toFixed(2) in JS would show "13.00" with no symbol.
    for s in stations:
        s["cost_fmt"] = _money(s["cost"]) if s["cost"] is not None else None
        for c in s["recent"]:
            c["cost_fmt"] = _money(c["cost"]) if c["cost"] is not None else None
    return templates.TemplateResponse(request, "map.html", _ctx(
        page="map", vehicle=vehicle, track=track, places=places, stations=stations,
        stations_top_n=stations_top_n, trips_shown=trips_shown,
    ))


# Comfort tiles to display: (comfort_state key, capability feature that gates it, i18n label, icon).
# Seats are shown per-side (driver/passenger), mirrors per-side (left/right); gating is at the
# feature level (e.g. both seat-heat tiles hide together if seat_heat is broken on this car).
_COMFORT_ROWS = (
    # (comfort_state key, gating feature, i18n label, icon kind, accent)
    ("seat_heat_driver",     "seat_heat",     "comfort_seat_heat_driver",     "seat_heat", "heat"),
    ("seat_heat_passenger",  "seat_heat",     "comfort_seat_heat_passenger",  "seat_heat", "heat"),
    ("seat_vent_driver",     "seat_vent",     "comfort_seat_vent_driver",     "seat_vent", "vent"),
    ("seat_vent_passenger",  "seat_vent",     "comfort_seat_vent_passenger",  "seat_vent", "vent"),
    ("mirror_heat_left",     "mirror_heat",   "comfort_mirror_heat_left",     "mirror",    "heat"),
    ("mirror_heat_right",    "mirror_heat",   "comfort_mirror_heat_right",     "mirror",    "heat"),
    ("steering_heat",        "steering_heat", "comfort_steering_heat",        "steering",  "heat"),  # last → mirrors stay paired on mobile
)

# Comfort rows controllable as a simple on/off toggle (steering/mirror — no level on the car).
# skey -> (gating command feature, on-command key, off-command key). Both mirror tiles share the
# single mirror command. Seats are handled separately (level slider).
_COMFORT_TOGGLE = {
    "steering_heat":     ("steering_heat_cmd", "steering_heat_on", "steering_heat_off"),
    "mirror_heat_left":  ("mirror_heat_cmd",   "mirror_heat_on",   "mirror_heat_off"),
    "mirror_heat_right": ("mirror_heat_cmd",   "mirror_heat_on",   "mirror_heat_off"),
}


def _comfort_rows(vin, car_type=""):
    """Read-only comfort STATE sensors for the Commands page. The poller writes the live
    values to settings as `comfort_state_<vin>`; we show only the ones not confirmed broken
    on this car (the remote command may be broken even when the state sensor works), and drop
    what this model physically lacks (e.g. T03 has no ventilated seats — MODEL_ABSENT)."""
    if not vin:
        return []
    raw = db_reader.get_setting(f"comfort_state_{vin.lower()}", "")
    try:
        state = json.loads(raw) if raw else {}
    except ValueError:
        state = {}
    rows = []
    for skey, feat, label_key, icon, accent in _COMFORT_ROWS:
        if not capability_profile.is_shown(vin, feat, car_type=car_type):
            continue
        v = int(state.get(skey) or 0)
        row = {"icon": icon, "accent": accent, "label_key": label_key, "value": v,
               "on": v > 0, "control": None, "skey": skey}
        # Seats → level slider (0–3); steering/mirror → on/off toggle. Gated by the command capability.
        if skey.startswith("seat_"):
            _, func, side = skey.split("_", 2)        # func: heat|vent, side: driver|passenger
            if capability_profile.is_shown(vin, f"seat_{func}_cmd", car_type=car_type):
                row.update(control="slider", func=func,
                           position=("driver" if side == "driver" else "copilot"))
        elif skey in _COMFORT_TOGGLE:
            cfeat, cmd_on, cmd_off = _COMFORT_TOGGLE[skey]
            if capability_profile.is_shown(vin, cfeat, car_type=car_type):
                row.update(control="toggle", cmd_on=cmd_on, cmd_off=cmd_off)
        rows.append(row)
    return rows


# Optimistic comfort state: comfort_state is otherwise only written at poll time (~30 s), so the
# cmd-grid auto-refresh (5–15 s after a command) re-rendered comfort controls with the OLD value,
# making them appear to "revert". After a web comfort command we merge the expected sensor values
# so the refresh shows the action immediately; the next poll overwrites with the real values.
_COMFORT_CMD_OPTIMISTIC = {
    "steering_heat_on":  {"steering_heat": 2},
    "steering_heat_off": {"steering_heat": 0},
    "mirror_heat_on":    {"mirror_heat_left": 1, "mirror_heat_right": 1},
    "mirror_heat_off":   {"mirror_heat_left": 0, "mirror_heat_right": 0},
}


def _optimistic_comfort(vin, updates):
    if not vin or not updates:
        return
    key = f"comfort_state_{vin.lower()}"
    try:
        cur = json.loads(db_reader.get_setting(key, "") or "{}")
    except ValueError:
        cur = {}
    cur.update(updates)
    db_reader.set_setting(key, json.dumps(cur, separators=(",", ":")))


def _wins_pct() -> int:
    """Last commanded window position % (the slider reflects what was SET, not open/closed — the
    B10 can't report its real window position)."""
    try:
        return max(0, min(int(db_reader.get_setting("windows_cmd_pct", "0") or 0), 100))
    except (TypeError, ValueError):
        return 0


# The %-stops a car actually ACTUATES. The B10 ignores everything except these (empirically
# 0/2/5/10 native = 0/20/50/100% — #62), so its slider snaps to 4 discrete stops; continuous
# models (T03) get the full 0–100 range. Add a model here as its valid stops are confirmed on-car.
# C10 = "exactly like the B10" (kerniger / leapmotor-ha disc. #47: native 5 → ~50%, 50/100 ignored);
# B05 shares the B10 platform → same discrete steps. Mirroring the B10 stops keeps their sliders on
# the values that move the windows — a continuous slider would let them pick an ignored native (e.g. 3).
_WINDOWS_STOPS = {"B10": [0, 20, 50, 100], "C10": [0, 20, 50, 100], "B05": [0, 20, 50, 100]}
def _wins_stops() -> list:
    vehicle, _ = db_reader.get_vehicle()
    ct = ((vehicle or {}).get("car_type") or "").upper()
    return _WINDOWS_STOPS.get(ct, list(range(0, 101)))
def _wins_idx(stops, pct) -> int:
    """Slider index of the stop nearest the last commanded %."""
    return min(range(len(stops)), key=lambda i: abs(stops[i] - pct)) if stops else 0
def _wins_ctx() -> dict:
    stops = _wins_stops()
    pct = _wins_pct()
    return {"wins_pct": pct, "wins_stops": stops, "wins_idx": _wins_idx(stops, pct)}


@app.get("/commands", response_class=HTMLResponse)
async def commands(request: Request):
    vehicle, _ = db_reader.get_vehicle()
    status = db_reader.get_latest_status()
    comfort = _comfort_rows(vehicle.get("vin") if vehicle else None, (vehicle or {}).get("car_type", ""))
    return templates.TemplateResponse(request, "commands.html", _ctx(
        page="commands", vehicle=vehicle, status=status, comfort=comfort, **_wins_ctx(),
        ac_off_shown=capability_profile.command_shown(vehicle.get("vin") if vehicle else None, "climate_off"),
        is_t03=(vehicle.get("car_type") or "").upper() == "T03" if vehicle else False,
    ))


def _parse_vehicle_status(sig: dict, vin: str | None = None, cmd_pct: int | None = None) -> dict:
    """Parse tyres / doors / windows / temps from a fresh signal dict (live, not DB). `vin` gates
    the per-car window-% fallback against its capability profile (#62). `cmd_pct` = the last
    position WE commanded — used as the window-opening % on cars whose % sensor is dead (B10), shown
    only for windows the open/closed flag confirms open."""
    def f(k):
        try: return float(sig.get(k)) if sig.get(k) is not None else None
        except (TypeError, ValueError): return None
    def i(k):
        try: return int(float(sig.get(k))) if sig.get(k) is not None else None
        except (TypeError, ValueError): return None
    def bar(k):
        v = f(k); return round(v / 100.0, 2) if v is not None else None
    def is_open(k):
        v = i(k); return None if v is None else (v != 0)
    # The open/closed flags (1693-1696) work on the B10 but are DEAD on the T03 (stay 0 even when
    # open); the position % (3727/3728/1879/1880) is the opposite — live on the T03, dead/garbage on
    # the B10 (#62). Fall back to the % ONLY where it isn't a known-broken sensor for this car (per
    # the capability profile), otherwise the B10's dead-but-noisy % false-positives every window.
    use_pct = bool(vin) and capability_profile.is_shown(vin, "windows_pct")
    # Per-window open via the shared flag-OR-position-% helper (#62), returning [FL, FR, RL, RR].
    # The Overview tile, Commands grid and command-confirm now use the same helper, so they agree.
    win = capability_profile.window_open_states(sig, use_pct)
    def win_pct(state_k, pct_k):
        # Real sensor where trusted (T03); else fall back to the last commanded % — but only for a
        # window the flag reports OPEN (any non-zero flag; the B10 reports 2 when open, see
        # window_open_states), so a shut window (flag 0) never shows a stale number.
        if use_pct:
            return i(pct_k)
        return cmd_pct if (cmd_pct and (i(state_k) or 0) != 0) else None
    return {
        # Wheel→signal mapping corrected from a TWO-B10 vs official-app cross-check (GitHub #32:
        # the UK reporter's car + Silvio's IT car, both showing 280 kPa at the rear-right):
        # pressures map ascending 2646=FL/2653=FR/2660=RL/2667=RR (the leapmotor-api doc order was
        # wrong); each pressure's paired state signal moves with it (FL=2655/FR=2648/RL=2662/RR=2641).
        "tyres": {
            "fl": {"bar": bar("2646"), "low": i("2655") == 1},
            "fr": {"bar": bar("2653"), "low": i("2648") == 1},
            "rl": {"bar": bar("2660"), "low": i("2662") == 1},
            "rr": {"bar": bar("2667"), "low": i("2641") == 1},
        },
        "doors": {
            "driver":     is_open("1277"), "passenger": is_open("1278"),
            "rear_left":  is_open("1279"), "rear_right": is_open("1280"),
            "trunk":      is_open("1281"),
        },
        "windows": {
            "fl": win[0], "fr": win[1],
            "rl": win[2], "rr": win[3],
            # Opening % per window: the real sensor on the T03, the last commanded position on the
            # B10 (its sensor is dead) — shown only for windows the flag confirms open.
            "fl_pct": win_pct("1693", "3727"), "fr_pct": win_pct("1694", "3728"),
            "rl_pct": win_pct("1695", "1879"), "rr_pct": win_pct("1696", "1880"),
            "sunshade": is_open("1724"),
        },
        "temps": {"battery": f("1182"), "cabin": f("1349")},  # no ambient-temp signal exists
        # Climate panel — signals validated on-car 2026-06-20: base mode 3713 (0 auto/1 cool/3 heat/
        # 4 vent), fan level 1941 (acAirVolume 1-7; holds last level when off), recirculation 1943.
        "climate": {
            "on": is_open("1938"),
            "mode": {0: "auto", 1: "cool", 3: "heat", 4: "vent"}.get(i("3713")),
            "fan": (i("1941") or None),
            "recirc": (i("1943") == 1) if i("1943") is not None else None,
            "target": f("2183"),
        },
    }


@app.get("/scheduling", response_class=HTMLResponse)
async def scheduling_page(request: Request):
    """Charge schedule (cmd 190) + climate pre-conditioning schedule (cmd 171). Both cards lazy-load
    their current values from the car. The climate write works on the B10 (ClimaSchedulerT01 solved
    2026-06-07 — the old code -2 was an expired start_time, now anchored to the next occurrence)."""
    vehicle, _ = db_reader.get_vehicle()
    return templates.TemplateResponse(request, "scheduling.html", _ctx(
        page="scheduling", vehicle=vehicle,
    ))


@app.get("/prepare-car", response_class=HTMLResponse)
async def prepare_car_page(request: Request):
    """One-touch vehicle preparation (cmd 360 immediate / 361 schedule). Mirrors the official app:
    bundle A/C + seats + steering + mirror + destination, run now or on a schedule. B10/C10 only."""
    vehicle, _ = db_reader.get_vehicle()
    # Inert on models without the PREPARE right (e.g. T03) — hide the page like its nav link.
    if capability_profile.model_hidden((vehicle or {}).get("car_type", ""), "prepare_car"):
        return RedirectResponse(request.headers.get("x-ingress-path", "") + "/")
    ready_cfg = db_reader.get_ready_automation_config()
    wins_stops = _wins_stops()
    return templates.TemplateResponse(request, "prepare_car.html", _ctx(
        page="prepare_car", vehicle=vehicle,
        ready_cfg=ready_cfg, wins_stops=wins_stops,
        # Slider INDEX for the saved windows_pct — same nearest-stop snap the Comandi page uses,
        # so a value saved before the car's stop table changed still lands somewhere sane.
        ready_wins_idx=_wins_idx(wins_stops, ready_cfg["windows_pct"] or 0),
    ))


@app.get("/vehicle", response_class=HTMLResponse)
async def vehicle_page(request: Request):
    vehicle, _ = db_reader.get_vehicle()
    return templates.TemplateResponse(request, "vehicle.html", _ctx(
        page="vehicle", vehicle=vehicle,
    ))


@app.get("/navigation", response_class=HTMLResponse)
async def navigation_page(request: Request):
    vehicle, _ = db_reader.get_vehicle()
    status = db_reader.get_latest_status()
    return templates.TemplateResponse(request, "navigation.html", _ctx(
        page="navigation", vehicle=vehicle, status=status,
    ))


@app.get("/api/nav/geocode", response_class=JSONResponse)
async def nav_geocode(address: str = "", city: str = ""):
    import asyncio
    provider = db_reader.get_setting("geocoder_provider", "")
    key = db_reader.get_secret("geocoder_key", "") or None
    try:
        res = await asyncio.get_event_loop().run_in_executor(
            None, geocode.geocode, address, city, provider, key)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)}, status_code=502)
    if not res:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse(res)


@app.get("/api/nav/current-address", response_class=HTMLResponse)
async def nav_current_address():
    import asyncio
    status = db_reader.get_latest_status() or {}
    lat, lon = status.get("latitude"), status.get("longitude")
    if not lat or not lon:
        return HTMLResponse("—")
    provider = db_reader.get_setting("geocoder_provider", "")
    key = db_reader.get_secret("geocoder_key", "") or None
    try:
        addr = await asyncio.get_event_loop().run_in_executor(
            None, geocode.reverse_geocode, lat, lon, provider, key)
    except Exception:  # noqa: BLE001
        addr = None
    return HTMLResponse(addr or "—")


@app.post("/api/nav/send", response_class=HTMLResponse)
async def nav_send(request: Request):
    import asyncio
    form = await request.form()
    try:
        lat = float(form.get("lat"))
        lon = float(form.get("lon"))
    except (TypeError, ValueError):
        return HTMLResponse('<span style="color:#ef4444">✗</span>', status_code=400)
    address = (form.get("address") or "").strip()
    name = (form.get("name") or address or "Destinazione")[:30]
    ok, msg = await asyncio.get_event_loop().run_in_executor(
        None, command_client.send_destination, name, address, lat, lon)
    t = i18n.get_t(db_reader.get_language())
    if ok:
        return HTMLResponse(f'<span style="color:#22c55e">✓ {t("nav_sent")}</span>')
    return HTMLResponse(_cmd_error_html(msg))


@app.get("/api/nav/chargers", response_class=JSONResponse)
async def nav_chargers(radius: int = 2000, q: str = "", n: int = 25):
    """Public charging stations around the car's current position (Navigation page),
    nearest first. OSM (keyless) + Open Charge Map (with key) + the Italian PUN.
    `q` filters by operator/network (e.g. 'electra') — handy in dense areas where a
    specific network sits beyond the nearest few. `n` is the page size (how many to
    show: 25/50/100) — in a dense city the nearest 25 all sit within ~2 km, so a
    larger `n` is what actually reaches farther out. With no `q`, an empty radius
    auto-widens to the nearest stations within 10 km (`widened`)."""
    import asyncio
    status = db_reader.get_latest_status() or {}
    lat, lon = status.get("latitude"), status.get("longitude")
    if not lat or not lon:
        return JSONResponse({"error": "no_position"}, status_code=404)
    radius = max(250, min(radius, 10000))
    q = (q or "").strip()[:40]
    limit = n if n in (25, 50, 100) else 25
    loop = asyncio.get_event_loop()
    try:
        res = await loop.run_in_executor(
            None, charger_locator.find_nearby, lat, lon, radius, limit, q)
        widened = False
        # Auto-widen only the unfiltered "nearest" view: climb a radius ladder so the
        # first non-empty rung gives a small, complete (truly-nearest) set. With an
        # operator filter, an empty result is a real "this network isn't nearby".
        if res == [] and not q:
            for wider in (2500, 5000, 10000):
                if wider <= radius:
                    continue
                res = await loop.run_in_executor(
                    None, charger_locator.find_nearby, lat, lon, wider, 5)
                if res:           # found some — stop climbing
                    widened = True
                    break
                if res is None:   # transient error — handled below
                    break
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)}, status_code=502)
    if res is None:  # every source down/rate-limited
        return JSONResponse({"error": "unreachable"}, status_code=502)
    return JSONResponse({"chargers": res, "widened": widened})


@app.get("/api/vehicle-status", response_class=HTMLResponse)
async def vehicle_status_api(request: Request):
    if _IS_DEMO:
        return templates.TemplateResponse(request, "partials/vehicle_status.html",
                                          _ctx(vs=demo.vehicle_status(db_reader)))
    import asyncio
    vehicle, _ = db_reader.get_vehicle()
    signals = await asyncio.get_event_loop().run_in_executor(None, command_client.get_fresh_signals)
    vs = _parse_vehicle_status(signals, (vehicle or {}).get("vin"), _wins_pct()) if signals else None
    return templates.TemplateResponse(request, "partials/vehicle_status.html", _ctx(vs=vs))


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    vehicle, settings = db_reader.get_vehicle()
    prices = db_reader.get_charge_prices()
    settings = {**settings, **prices,
                "abrp_enabled": db_reader.get_setting("abrp_enabled", "0"),
                "abrp_token_set": db_reader.abrp_token_in_use(),
                "abrp_cars_without_token": db_reader.abrp_cars_without_token(),
                "mqtt_enabled": db_reader.get_setting("mqtt_enabled", "0"),
                "mqtt_broker": db_reader.get_setting("mqtt_broker", ""),
                "mqtt_port": db_reader.get_setting("mqtt_port", "1883"),
                "mqtt_user": db_reader.get_setting("mqtt_user", ""),
                "mqtt_pass_set": bool(db_reader.get_setting("mqtt_pass", "")),
                "mqtt_prefix": db_reader.get_setting("mqtt_prefix", "leapmotor"),
                "mqtt_tls": db_reader.get_setting("mqtt_tls", "0"),
                "mqtt_tls_insecure": db_reader.get_setting("mqtt_tls_insecure", "0"),
                "mqtt_discovery": db_reader.get_setting("mqtt_discovery", "1"),
                "geocoder_provider": db_reader.get_setting("geocoder_provider", ""),
                "geocoder_key_set": bool(db_reader.get_setting("geocoder_key", "")),
                "charger_locator": db_reader.get_setting("charger_locator", "0"),
                "charger_locator_ocm_key_set": bool(db_reader.get_setting("ocm_key", "")),
                "charger_locator_tomtom_key_set": bool(db_reader.get_setting("tomtom_key", "")),
                "positions_retention_days": db_reader.get_setting("positions_retention_days", "0"),
                "desktop_autostart": db_reader.get_setting("desktop_autostart", "0"),
                "map_station_min_sessions": db_reader.get_setting("map_station_min_sessions", "1"),
                "charge_reconstruct_min_pct": db_reader.get_setting("charge_reconstruct_min_pct", "2.0"),
                "vampire_min_drop_pct": db_reader.get_setting("vampire_min_drop_pct", "0.2"),
                "vampire_min_hours": db_reader.get_setting("vampire_min_hours", "1"),
                "charge_dc_min_kw": db_reader.get_setting("charge_dc_min_kw", "11"),
                "wallbox_auto_home": db_reader.get_setting("wallbox_auto_home", "0"),
                "default_drive_mode": db_reader.get_setting("default_drive_mode", ""),
                "default_one_pedal": db_reader.get_setting("default_one_pedal", ""),
                "db_size_mb": round(db_reader.get_db_size_bytes() / 1048576, 1)}
    # Per-card open/collapsed state for the settings accordion — saved in the DB (shared
    # across devices). Cards start collapsed so the page stays compact, EXCEPT 'vehicle': it's
    # tiny (model + VIN + the Logout/change-account button) and keeping it open makes the logout
    # discoverable without hunting. The user's chevron toggles are remembered per card.
    card_open = {k: _card_open(k, k == "vehicle") for k in _UI_CARD_KEYS}
    # Sections still "new" to THIS user = flagged new AND not yet interacted with.
    new_sections = {k for k in _NEW_SETTINGS_SECTIONS
                    if db_reader.get_setting(f"card_seen_{k}", "") != "1"}
    return templates.TemplateResponse(request, "settings.html", _ctx(
        page="settings", vehicle=vehicle, settings=settings, card_open=card_open,
        new_sections=new_sections,
        charge_types=db_reader.charge_types_localised(),
        ha_url=db_reader.get_setting("ha_url", ""),
        ha_has_token=bool(db_reader.get_setting("ha_token", "")),
        ha_supervisor=bool(os.environ.get("SUPERVISOR_TOKEN")),
        # Dev/env-var mode skips the wizard, so the credentials live in the environment,
        # not the DB — the Logout button (which clears DB creds) wouldn't apply there.
        env_login=bool(os.environ.get("LEAPMOTOR_USER")),
        # WHICH Leapmotor account this instance polls with (beta #13, @ebagnoli). He runs several
        # instances on several accounts and had no way to tell them apart from inside Mate — the
        # card named the car but never the login. Empty → the row is skipped.
        account_user=db_reader.get_account_user(),
        wb_keywords=db_reader.get_setting("wb_keywords", ""),
        currencies=db_reader.CURRENCIES,
        currency_code=db_reader.get_currency_code(),
        timezones=db_reader.timezone_options(),
        timezone_code=db_reader.get_timezone(),
        diag=diagnostics.build_system_info(MATE_VERSION),
        measured_capacity=db_reader.get_battery_health().get("latest_capacity_kwh"),
        # The capacity actually in use, and the SoH reference. The form used to carry its own
        # default (67.1) while the code read another (65.0) — two defaults for one value, and
        # whichever got written first decided the reference for ever (@danielvilhena, #221).
        capacity_kwh=db_reader.get_battery_capacity_kwh(),
        # A car still sitting on a default Mate has since disproved (the C10 RWD's 69.9, #246).
        # Offered, never applied: a capacity is the owner's to calibrate.
        superseded_pack=db_reader.superseded_pack_kwh(),
        # The charge-detection floor set above what the car draws — the detector switched
        # off by a slider, which shows up as half a charge rather than none (#250).
        charge_floor_peak=db_reader.charge_threshold_too_high(),
        capacity_nominal=db_reader.get_setting("battery_capacity_nominal_kwh", ""),
        # Written by the poller when it hears another Mate on our own topic prefix (BetaTester #13).
        # Shown beside the prefix field, which is where the fix lives.
        mqtt_collision=_mqtt_collision(),
    ))


def _mqtt_collision():
    """What the poller recorded the last time it heard another Mate on our topic prefix, or None.

    Kept as a setting rather than passed in memory because the two live in different processes: the
    poller is the one holding the broker connection, the web layer is the one with a page to say it
    on. Stale entries clear themselves — the poller rewrites this only while it is still hearing the
    other install, and it stops being shown once that install is gone or has moved off."""
    from datetime import datetime, timezone   # module-scope import: this file has none
    raw = db_reader.get_setting("mqtt_collision", "")
    if not raw or db_reader.get_setting("mqtt_enabled", "0") != "1":
        return None
    try:
        out = json.loads(raw)
    except (ValueError, TypeError):
        return None
    # Only while it is still true: the poller stamps every sighting, and one older than a few poll
    # cycles means nobody is colliding with us any more.
    try:
        seen = datetime.fromisoformat(out.get("at", ""))
        if (datetime.now(timezone.utc) - seen).total_seconds() > 3600:
            return None
    except (ValueError, TypeError):
        return None
    return out


@app.get("/costs", response_class=HTMLResponse)
async def costs_page(request: Request):
    """Charging-costs page: base per-type prices + time-of-use bands."""
    vehicle, settings = db_reader.get_vehicle()
    prices = db_reader.get_charge_prices()
    cfg = db_reader.get_cost_config()
    return templates.TemplateResponse(request, "costs.html", _ctx(
        page="costs", vehicle=vehicle,
        settings={**settings, **prices},
        charge_types=db_reader.charge_types_localised(),
        cost_mode=cfg["mode"], cost_modes=cfg["modes"], tou_method=cfg["method"],
        tou_bands_json=json.dumps(cfg["bands"]), cost_modes_json=json.dumps(cfg["modes"]),
    ))


@app.get("/wallbox", response_class=HTMLResponse)
async def wallbox_page(request: Request):
    """Wallbox page — only reachable when enabled in Settings. Data wiring to
    Home Assistant comes next; for now this previews the intended layout."""
    if db_reader.get_setting("wallbox_enabled", "0") != "1":
        return RedirectResponse(request.headers.get("x-ingress-path", "") + "/settings")
    vehicle, _ = db_reader.get_vehicle()
    today = db_reader.today_local()
    return templates.TemplateResponse(request, "wallbox.html", _ctx(
        page="wallbox", vehicle=vehicle,
        configured=ha_client.is_configured() and bool(ha_client.get_mapping()),
        cal_year=today.year, cal_month=today.month,
        cal_years=db_reader.get_wallbox_years(),
        calendar_html=_wallbox_calendar_html(today.year, today.month),   # drawn, not fetched
    ))


@app.post("/api/settings/wallbox")
async def save_wallbox(request: Request):
    """Toggle the Wallbox feature. Saved to the DB, then the page is reloaded
    (HX-Refresh) so the sidebar shows/hides the Wallbox entry immediately."""
    form = await request.form()
    enabled = "1" if form.get("wallbox_enabled") in ("1", "on", "true") else "0"
    db_reader.set_setting("wallbox_enabled", enabled)
    return Response(status_code=204, headers={"HX-Refresh": "true"})


def _ha_test_html() -> str:
    """Small inline status snippet for the HA connection test."""
    if os.environ.get("SUPERVISOR_TOKEN"):
        src = "Supervisor (add-on)"
    else:
        src = "URL + token"
    res = ha_client.test_connection()
    if res.get("ok"):
        return (f'<span style="color:#22c55e;font-size:13px">✓ Connected via {src}'
                f' — {res.get("message", "API running")}</span>')
    err = res.get("error", "unknown")
    if err == "not_configured":
        return '<span style="color:#64748b;font-size:13px">Enter the HA URL and token, then test</span>'
    return f'<span style="color:#f87171;font-size:13px">✗ {err}</span>'


@app.post("/api/settings/ha", response_class=HTMLResponse)
async def save_ha(request: Request):
    """Save the standalone HA URL + Long-Lived token, then test the connection."""
    form = await request.form()
    if "ha_url" in form:
        db_reader.set_setting("ha_url", (form.get("ha_url") or "").strip())
    if form.get("ha_token"):  # don't wipe a saved token on an empty submit
        db_reader.set_secret("ha_token", form.get("ha_token").strip())
    db_reader.set_setting("wallbox_active_profile", "")  # settings edited directly → stale
    return HTMLResponse(_ha_test_html())


@app.get("/api/wallbox/test", response_class=HTMLResponse)
async def wallbox_test(request: Request):
    return HTMLResponse(_ha_test_html())


@app.get("/api/wallbox/status", response_class=HTMLResponse)
async def wallbox_status(request: Request):
    """A small live connection dot — green when HA actually answers, red otherwise.
    Works for both add-on (Supervisor) and standalone (URL+token)."""
    t = i18n.get_t(db_reader.get_language())
    ok = ha_client.test_connection().get("ok")
    if ok:
        return HTMLResponse(
            '<span class="inline-flex items-center gap-1.5 text-xs text-emerald-400">'
            '<span class="w-2 h-2 rounded-full bg-emerald-400"></span>' + t("ha_status_ok") + '</span>')
    return HTMLResponse(
        '<span class="inline-flex items-center gap-1.5 text-xs text-red-400">'
        '<span class="w-2 h-2 rounded-full bg-red-400"></span>' + t("ha_status_ko") + '</span>')


@app.get("/api/wallbox/entities", response_class=HTMLResponse)
async def wallbox_entities(request: Request, show_all: int = 0):
    """Lazy-loaded entity picker: discovered HA entities + role selects,
    pre-filled with the saved mapping or an auto-detected best guess.

    show_all=1 (advanced mode, issue #21): list EVERY sensor/number entity instead of just
    charger-named/typed ones, and skip the device-narrowing filter — so foreign-language names
    or a generic energy-meter/relay (not a branded wallbox) can be mapped manually."""
    advanced = bool(show_all)
    all_entities = ha_client.list_entities(only_wallbox=not advanced)
    # Auto-detected defaults for any role, overridden by what the user saved →
    # new roles get a sensible pre-fill while saved choices are preserved.
    mapping = {**ha_client.auto_map(all_entities), **ha_client.get_mapping()}
    if advanced:
        # Keep only mappable domains (sensor/number) so the dropdowns stay usable.
        entities = [e for e in all_entities
                    if e["entity_id"].split(".", 1)[0] in ("sensor", "number", "input_number")]
        # Advanced mode is the manual escape hatch → no per-role unit narrowing.
        role_entities = {role: entities for role in ha_client.WB_ROLES}
    else:
        # Offer only the wallbox device's own sensors in the dropdowns (not every HA entity)…
        entities = ha_client.filter_device_entities(all_entities, mapping)
        # …and, per role, only the sensors whose unit fits (kW for power, kWh for energy, …) so a
        # wrong-unit pick can't corrupt the stored data. The saved choice is always kept visible.
        role_entities = {role: ha_client.entities_for_role(role, entities, mapping.get(role))
                         for role in ha_client.WB_ROLES}
    return templates.TemplateResponse(request, "partials/wallbox_entities.html", _ctx(
        role_entities=role_entities, mapping=mapping, roles=ha_client.WB_ROLES, show_all=advanced,
        wb_max_power_static=db_reader.get_setting("wb_max_power_static", ""),
        wb_max_power_unit=db_reader.get_setting("wb_max_power_unit", "kW") or "kW",
    ))


@app.post("/api/settings/wallbox-entities", response_class=HTMLResponse)
async def save_wallbox_entities(request: Request):
    form = await request.form()
    mapping = {role: form.get(role, "").strip()
               for role in ha_client.WB_ROLES if form.get(role, "").strip()}
    db_reader.set_setting("wallbox_entities", json.dumps(mapping))
    # #111: optional static "max available" value — used only when no sensor is mapped for it (display).
    static_unit = (form.get("max_power_static_unit", "kW") or "kW").strip()
    db_reader.set_setting("wb_max_power_static", (form.get("max_power_static", "") or "").strip())
    db_reader.set_setting("wb_max_power_unit", static_unit if static_unit in ("kW", "A") else "kW")
    db_reader.set_setting("wallbox_active_profile", "")  # settings edited directly → stale
    t = i18n.get_t(db_reader.get_language())
    return HTMLResponse(f'<span style="color:#22c55e;font-size:13px">{t("wallbox_saved")}</span>')


@app.post("/api/settings/wallbox-keywords", response_class=HTMLResponse)
async def save_wallbox_keywords(request: Request):
    """Save custom wallbox keywords for entity filtering."""
    form = await request.form()
    keywords = (form.get("wb_keywords", "") or "").strip()
    db_reader.set_setting("wb_keywords", keywords)
    db_reader.set_setting("wallbox_active_profile", "")  # settings edited directly → stale
    t = i18n.get_t(db_reader.get_language())
    return HTMLResponse(f'<span style="color:#22c55e;font-size:13px">{t("wallbox_saved")}</span>')


@app.post("/api/settings/wallbox-auto-home", response_class=HTMLResponse)
async def save_wallbox_auto_home(request: Request):
    """Opt-in: auto-assign HOME to charges the wallbox measured (idea: @hubcasale, PR #47).
    On enable, sweep immediately so the pending backlog is confirmed right away and the
    feedback can say how many — costs go through the same engine as a manual confirm."""
    form = await request.form()
    val = "1" if form.get("wallbox_auto_home") in ("1", "on", "true") else "0"
    db_reader.set_setting("wallbox_auto_home", val)
    n = db_reader.auto_confirm_home_charges() if val == "1" else 0
    t = i18n.get_t(db_reader.get_language())
    msg = t("wallbox_saved") + (" · " + t("wallbox_auto_home_applied").format(n=n) if n else "")
    return HTMLResponse(f'<span style="color:#22c55e;font-size:13px">{msg}</span>')


@app.post("/api/settings/trip-defaults", response_class=HTMLResponse)
async def save_trip_defaults(request: Request):
    """#119: app-level defaults that pre-fill a NEW trip's drive mode / One-Pedal. The cloud never
    reports either (verified on-car), so they can only be tagged by hand — this spares a fixed-habit
    driver from re-tagging every trip. "" = keep 'not set' (the historical behaviour). Existing trips
    are left untouched: this only seeds trips created from now on."""
    form = await request.form()
    dm = (form.get("default_drive_mode") or "").strip().lower()
    db_reader.set_setting("default_drive_mode", dm if dm in db_reader.DRIVE_MODES else "")
    op = (form.get("default_one_pedal") or "").strip()
    db_reader.set_setting("default_one_pedal", op if op in ("0", "1") else "")
    t = i18n.get_t(db_reader.get_language())
    return HTMLResponse(f'<span style="color:#22c55e;font-size:13px">{t("trip_defaults_saved")}</span>')


# ── Wallbox saved profiles ───────────────────────────────────────────────────

def _get_wallbox_profiles() -> list:
    raw = db_reader.get_setting("wallbox_profiles", "[]")
    try:
        profiles = json.loads(raw)
        if not isinstance(profiles, list):
            return []
        return profiles
    except (ValueError, TypeError):
        return []


def _set_wallbox_profiles(profiles: list) -> None:
    db_reader.set_setting("wallbox_profiles", json.dumps(profiles, separators=(",", ":")))


@app.get("/api/settings/wallbox-profiles", response_class=HTMLResponse)
async def wallbox_profiles_list(request: Request):
    """Lazy-load the profiles picker panel (embedded in the wallbox settings card)."""
    return templates.TemplateResponse(request, "partials/wallbox_profiles.html", _ctx(
        profiles=_get_wallbox_profiles(), wb_profile_error=None,
    ))


@app.post("/api/settings/wallbox-profiles/save", response_class=HTMLResponse)
async def wallbox_profile_save(request: Request):
    """Snapshot the active wallbox settings (HA URL, token, keywords, entity mapping,
    energy prices and wallbox flags) under a user-chosen name so it can be restored
    later with one click."""
    import uuid
    form = await request.form()
    name = (form.get("name") or "").strip()
    t = i18n.get_t(db_reader.get_language())
    profiles = _get_wallbox_profiles()
    if not name:
        return templates.TemplateResponse(request, "partials/wallbox_profiles.html", _ctx(
            profiles=profiles,
            wb_profile_error=t("wb_profile_name_required"),
        ))
    if any(p["name"].strip().lower() == name.lower() for p in profiles):
        return templates.TemplateResponse(request, "partials/wallbox_profiles.html", _ctx(
            profiles=profiles,
            wb_profile_error=t("wb_profile_name_taken"),
        ))
    prices = db_reader.get_charge_prices()
    profiles.append({
        "id": uuid.uuid4().hex[:8],
        "name": name,
        "ha_url":            db_reader.get_setting("ha_url", ""),
        "ha_token":          db_reader.get_setting("ha_token", ""),   # stored encrypted
        "wb_keywords":       db_reader.get_setting("wb_keywords", ""),
        "wallbox_entities":  db_reader.get_setting("wallbox_entities", ""),
        "wallbox_enabled":   db_reader.get_setting("wallbox_enabled", "0"),
        "wallbox_auto_home": db_reader.get_setting("wallbox_auto_home", "0"),
        "price_home_kwh":    prices.get("price_home_kwh"),
        "price_ac_kwh":      prices.get("price_ac_kwh"),
        "price_fast_kwh":    prices.get("price_fast_kwh"),
        "price_hpc_kwh":     prices.get("price_hpc_kwh"),
    })
    _set_wallbox_profiles(profiles)
    new_id = profiles[-1]["id"]
    db_reader.set_setting("wallbox_active_profile", new_id)
    return templates.TemplateResponse(request, "partials/wallbox_profiles.html", _ctx(
        profiles=profiles, wb_profile_error=None,
    ))


@app.post("/api/settings/wallbox-profiles/{profile_id}/load", response_class=HTMLResponse)
async def wallbox_profile_load(request: Request, profile_id: str):
    """Restore a saved wallbox profile as the active configuration and reload the page."""
    t = i18n.get_t(db_reader.get_language())
    profiles = _get_wallbox_profiles()
    # Guard: refuse to switch while a charge is active (switching mid-charge would blend
    # two physical wallboxes into the same session's energy counter).
    status = db_reader.get_latest_status()
    if status and status.get("charging"):
        return templates.TemplateResponse(request, "partials/wallbox_profiles.html", _ctx(
            profiles=profiles,
            wb_profile_error=t("wb_profile_charging_blocked"),
        ))
    profile = next((p for p in profiles if p["id"] == profile_id), None)
    if profile is None:
        return templates.TemplateResponse(request, "partials/wallbox_profiles.html", _ctx(
            profiles=profiles,
            wb_profile_error=t("wb_profile_not_found"),
        ))
    db_reader.set_setting("ha_url", profile.get("ha_url", ""))
    if profile.get("ha_token"):
        db_reader.set_setting("ha_token", profile["ha_token"])  # already encrypted
    db_reader.set_setting("wb_keywords", profile.get("wb_keywords", ""))
    db_reader.set_setting("wallbox_entities", profile.get("wallbox_entities", ""))
    db_reader.set_setting("wallbox_enabled", profile.get("wallbox_enabled", "0"))
    db_reader.set_setting("wallbox_auto_home", profile.get("wallbox_auto_home", "0"))
    for price_key in ("price_home_kwh", "price_ac_kwh", "price_fast_kwh", "price_hpc_kwh"):
        val = profile.get(price_key)
        if val is not None:
            try:
                db_reader.update_charge_price(price_key, float(val))
            except (ValueError, TypeError):
                pass
    db_reader.set_setting("wallbox_active_profile", profile_id)
    return Response(status_code=204, headers={"HX-Refresh": "true"})


@app.post("/api/settings/wallbox-profiles/{profile_id}/delete", response_class=HTMLResponse)
async def wallbox_profile_delete(request: Request, profile_id: str):
    """Remove a saved wallbox profile (irreversible). Clears the active-profile
    indicator if the deleted one was the one currently in use."""
    profiles = [p for p in _get_wallbox_profiles() if p["id"] != profile_id]
    _set_wallbox_profiles(profiles)
    if db_reader.get_setting("wallbox_active_profile", "") == profile_id:
        db_reader.set_setting("wallbox_active_profile", "")
    return templates.TemplateResponse(request, "partials/wallbox_profiles.html", _ctx(
        profiles=profiles, wb_profile_error=None,
    ))


@app.get("/api/wallbox/live", response_class=HTMLResponse)
async def wallbox_live(request: Request):
    wb = ha_client.get_live()
    wb["cost"] = db_reader.latest_home_charge_cost()  # cost comes from Mate's charges, not HA
    status = db_reader.get_latest_status()
    # Session metrics only make sense when THIS car is on the wallbox — otherwise the
    # live reading could be another vehicle charging on the same wallbox.
    car_plugged = bool(status and status.get("plug_connected"))
    # …and the warning that says so has to name the car the owner actually has. It said "B10" to
    # everyone until #248 (@Ng-EY, a C10 owner): a line about WHICH vehicle the numbers belong to
    # is the worst place to get the vehicle wrong. Empty before the poller has seen the car.
    vehicle, _ = db_reader.get_vehicle()
    return templates.TemplateResponse(request, "partials/wallbox_live.html", _ctx(
        wb=wb, car_plugged=car_plugged, car_model=(vehicle or {}).get("car_type") or ""))


def _wallbox_sessions_grouped() -> list:
    """Charges-with-power nested year → month → day, each session carrying the
    AC-vs-DC kWh comparison; node totals + efficiency rolled up."""
    from collections import OrderedDict
    lang = db_reader.get_language()
    years: "OrderedDict" = OrderedDict()
    # ⚠️ `_wallbox_home_charges_raw`, not `charges_with_power`: the latter carries no
    # `ac_energy_kwh` at all and stops at the newest 30, which made a "history" tree quietly
    # partial. Same source as the calendar and the tiles, so the whole page speaks with one voice.
    rows = db_reader._wallbox_home_charges_raw()
    for r in rows:
        dt = db_reader._local_dt(r["started_at"])
        if dt is None:
            continue
        # From the stored columns. This used to be one Home Assistant history fetch per session —
        # for every session in the whole history, on every open of this list (#229).
        sess = {"id": r["id"], "time": dt.strftime("%H:%M"),
                **db_reader.wallbox_session_energy(r)}
        yr, mo, day = dt.strftime("%Y"), i18n.fmt_month_year(lang, dt), i18n.fmt_day_month_year(lang, dt)
        Y = years.setdefault(yr, {"label": yr, "_rows": [], "months": OrderedDict()})
        M = Y["months"].setdefault(mo, {"label": mo, "_rows": [], "days": OrderedDict()})
        D = M["days"].setdefault(day, {"label": day, "_rows": [], "sessions": []})
        D["sessions"].append(sess)
        for node in (Y, M, D):
            node["_rows"].append(r)

    # Rolled up through the SAME helper as everything else, so a charge missing its meter figure is
    # skipped on both sides of the ratio here too — it used to add its battery kWh alone.
    def _seal(n):
        n.update({k: v for k, v in db_reader.wallbox_ac_dc_totals(n.pop("_rows")).items()
                  if k in ("ac", "dc", "eff")})
    trees = list(years.values())
    for Y in trees:
        for M in Y["months"].values():
            for D in M["days"].values():
                _seal(D)
            _seal(M)
        _seal(Y)
    return trees


@app.get("/api/wallbox/sessions", response_class=HTMLResponse)
async def wallbox_sessions(request: Request):
    """Year/month/day history tree with the AC-vs-DC kWh comparison per session."""
    return templates.TemplateResponse(request, "partials/wallbox_sessions.html", _ctx(
        tree=_wallbox_sessions_grouped(),
    ))


def _wallbox_day_sessions(year: int, month: int, day: int) -> list:
    """A day's sessions with their AC/DC comparison — straight off the stored columns.

    It used to fetch Home Assistant's history for each session on every click and integrate the
    power sensor, while the meter's own kWh sat on the charge row (#229). Same numbers as the tiles
    and the month strip on the same page now, which they were not before."""
    return db_reader.get_wallbox_calendar_day(year, month, day)


def _wallbox_calendar_ctx(year: int, month: int, open_day: int = 0) -> dict:
    """Wallbox 'calendar' Month view — day totals from the ALREADY-STORED charge columns; the day
    drawer computes each session's precise AC/DC comparison lazily on click (see
    wallbox_calendar_day below), so opening the page never fires a Home Assistant history request
    per session like the old full accordion did. Split from its Response so the page can draw the
    grid on first paint (#240)."""
    import calendar as calmod
    from datetime import date
    today = db_reader.today_local()
    year, month = year or today.year, month or today.month
    month = max(1, min(12, month))
    cal = db_reader.get_wallbox_calendar_month(year, month)
    weeks = []
    for week in calmod.Calendar(firstweekday=0).monthdayscalendar(year, month):
        row = []
        for day in week:
            row.append(None if day == 0 else {
                "day": day, **cal["days"].get(day, {"count": 0, "ac": 0.0, "dc": 0.0, "eff": None})})
        weeks.append(row)
    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    lang = db_reader.get_language()
    ctx = {
        "t": i18n.get_t(lang), "year": year, "month": month, "weeks": weeks, "total": cal["total"],
        "month_label": i18n.fmt_month_year(lang, date(year, month, 1)),
        "weekday_abbrs": i18n.weekday_abbrs(lang),
        "prev_year": prev_year, "prev_month": prev_month,
        "next_year": next_year, "next_month": next_month,
        "today": today,
    }
    if open_day and open_day in cal["days"]:
        ctx["open_day"] = open_day     # so the grid can ring the day the drawer is showing
        ctx["open_day_sessions"] = _wallbox_day_sessions(year, month, open_day)
        ctx["open_day_label"] = i18n.fmt_day_month_year(lang, date(year, month, open_day))
    return ctx


def _wallbox_calendar_html(year: int, month: int, open_day: int = 0) -> str:
    return templates.get_template("partials/wallbox_calendar_month.html").render(
        _wallbox_calendar_ctx(year, month, open_day))


@app.get("/api/wallbox/calendar", response_class=HTMLResponse)
async def wallbox_calendar(request: Request, year: int = 0, month: int = 0, open_day: int = 0):
    """The HTMX side of the grid above."""
    return templates.TemplateResponse(request, "partials/wallbox_calendar_month.html",
                                      _wallbox_calendar_ctx(year, month, open_day))


@app.get("/api/wallbox/calendar/day", response_class=HTMLResponse)
async def wallbox_calendar_day(request: Request, year: int, month: int, day: int):
    """One day's sessions for the Month view's day drawer."""
    lang = db_reader.get_language()
    from datetime import date
    return templates.TemplateResponse(request, "partials/wallbox_calendar_day.html", {
        "t": i18n.get_t(lang),
        "sessions": _wallbox_day_sessions(year, month, day),
        "day_label": i18n.fmt_day_month_year(lang, date(year, month, day)),
    })


@app.get("/api/wallbox/compare-chart", response_class=HTMLResponse)
async def wallbox_compare_chart(request: Request):
    """Comparison chart for a picked charge session (Wallbox-page session selector)."""
    try:
        cid = int(request.query_params.get("charge_id"))
    except (TypeError, ValueError):
        return HTMLResponse('<div class="text-sm text-slate-500 py-2">—</div>')
    curve = db_reader.get_charge_power_curve(cid)
    return templates.TemplateResponse(request, "partials/charge_power_chart.html", _ctx(
        cid=cid, labels=curve["labels"], power=curve["power"], soc=curve["soc"],
        wb_power=_wallbox_overlay(curve, cid),
    ))


@app.get("/api/wallbox/control", response_class=HTMLResponse)
async def wallbox_control(request: Request):
    """Max-current control (loaded once; does NOT auto-refresh, so a drag isn't wiped)."""
    return templates.TemplateResponse(request, "partials/wallbox_control.html", _ctx(
        cfg=ha_client.get_max_current_config(), applied=None,
    ))


def _wallbox_totals() -> dict:
    """Lifetime AC delivered vs DC into battery, from the columns the poller stored.

    Until #229 this integrated the wallbox POWER sensor's Home Assistant history for every session
    on every page load — code from 3 June, when `ac_energy_kwh` did not exist and that was the only
    way to know the wall's kWh. The column arrived on 9 June and nobody moved these tiles onto it,
    so from 26 July (when the calendar below was built on the column) the same page answered twice:
    @Wartopia saw 141.5 % on top and 92.5 % underneath, with seven perfectly sane charges.

    Same helper as the calendar now, so the two cannot disagree again. No cloud call, no HA call —
    the number was written down when the energy flowed.
    """
    return db_reader.wallbox_ac_dc_totals(db_reader._wallbox_home_charges_raw())


@app.get("/api/wallbox/summary", response_class=HTMLResponse)
async def wallbox_summary(request: Request):
    """Control row: max-current tile + lifetime AC/DC/efficiency total tiles."""
    return templates.TemplateResponse(request, "partials/wallbox_summary.html", _ctx(
        cfg=ha_client.get_max_current_config(), applied=None, totals=_wallbox_totals(),
    ))


@app.post("/api/wallbox/max-current", response_class=HTMLResponse)
async def wallbox_set_max_current(request: Request):
    form = await request.form()
    try:
        val = float(form.get("max_current"))
    except (TypeError, ValueError):
        val = None
    ok = ha_client.set_max_current(val) if val is not None else False
    # Show the value the user JUST set (optimistic), keeping the entity's min/max/step/unit. HA's
    # number.set_value is async and a device-backed wallbox entity often still reports the old/idle
    # value (frequently 0) for a moment, so re-reading immediately would snap the slider back to 0.
    cfg = ha_client.get_max_current_config()
    if ok and val is not None and cfg:
        cfg = {**cfg, "value": val}
    return templates.TemplateResponse(request, "partials/wallbox_control.html", _ctx(
        cfg=cfg, applied=ok,
    ))


# ── Charge type update (HTMX) ────────────────────────────────────────────────

@app.post("/api/charges/{charge_id}/type", response_class=HTMLResponse)
async def set_charge_type(request: Request, charge_id: int):
    form = await request.form()
    location_type = form.get("location_type", "HOME")
    # MANUAL = the user types the real total paid; it overrides the automatic cost (the
    # public-charging jungle can't be modelled by a per-kWh tariff). Everything else is computed in
    # update_charge_type: a HOME charge is billed on the wallbox energy the poller measured at charge
    # start/stop (the counter delta — exact), if available, else on the battery (DC/SoC) energy.
    manual_cost = None
    if location_type == "MANUAL":
        try:
            manual_cost = float(str(form.get("cost", "")).strip().replace(",", "."))
        except (ValueError, TypeError):
            manual_cost = None
    # The charger's own kWh (#222) is NOT read here: it has its own endpoint below, so re-tagging a
    # charge can never touch a number the owner typed, and typing that number can never re-tag it.
    charge = db_reader.update_charge_type(charge_id, location_type, manual_cost=manual_cost)
    t = i18n.get_t(db_reader.get_language())
    if location_type == "MANUAL":
        cost_title = t("cost_basis_manual")
    elif location_type == "HOME" and charge.get("ac_energy_kwh"):
        cost_title = t("cost_basis_ac")
    else:
        cost_title = t("cost_basis_dc")
    return templates.TemplateResponse(request, "partials/charge_type_badge.html", {
        "charge": charge,
        "charge_types": db_reader.charge_types_localised(),
        "currency": db_reader.get_currency(),   # the price box is labelled in the reader's money
        "t": t,                   # the partial's #120 free toggle (HOME) needs the translator
        "cost_oob": True,         # also refresh the cost cell (it changes with the type/basis)
        "cost_title": cost_title,
    })


@app.post("/api/charges/{charge_id}/gross-kwh", response_class=HTMLResponse)
async def set_charge_gross_kwh(request: Request, charge_id: int):
    """#222 @ghuaywen-ai — the kWh the charger's own display said it delivered, typed by the owner.

    The box it comes from opens only on purpose and always opens EMPTY, so this route has to read an
    empty field as "leave it alone": an accidental open followed by Enter must change nothing. A
    zero is the deliberate way back — it reads as never-typed everywhere and the cost returns to the
    measured basis. Anything unparseable is treated as empty rather than as a zero, because a typo
    must not silently wipe a good figure."""
    form = await request.form()
    gross_kwh = None
    _g = str(form.get("gross_kwh", "")).strip().replace(",", ".")
    if _g:
        try:
            gross_kwh = max(0.0, min(float(_g), 500.0))
        except (ValueError, TypeError):
            gross_kwh = None
    charge = db_reader.set_charge_gross_kwh(charge_id, gross_kwh)
    t = i18n.get_t(db_reader.get_language())
    return templates.TemplateResponse(request, "partials/charge_gross_kwh.html", {
        "charge": charge,
        "t": t,
        "cost_oob": True,   # the cost is billed on this figure when there is no meter — refresh it
    })


@app.post("/api/charges/{charge_id}/free", response_class=HTMLResponse)
async def set_charge_free(request: Request, charge_id: int):
    """#120: toggle the FREE mark on a HOME charge (e.g. self-produced solar, or any free home
    charge). The charge stays HOME — and on the Home side of the Home-vs-Public split — while its
    cost is pinned to 0. HOME-only; the free-away case is the FREE location_type."""
    form = await request.form()
    free = str(form.get("is_free", "")).strip() in ("1", "on", "true")
    charge = db_reader.set_charge_free(charge_id, free)
    t = i18n.get_t(db_reader.get_language())
    cost_title = t("cost_basis_ac") if charge.get("ac_energy_kwh") else t("cost_basis_dc")
    return templates.TemplateResponse(request, "partials/charge_type_badge.html", {
        "charge": charge,
        "charge_types": db_reader.charge_types_localised(),
        "currency": db_reader.get_currency(),   # the price box is labelled in the reader's money
        "t": t,                   # the partial's #120 free toggle needs the translator
        "cost_oob": True,         # free flips the cost to 0 → refresh the cost cell too
        "cost_title": cost_title,
    })


@app.post("/api/charges/{charge_id}/locate", response_class=HTMLResponse)
async def relocate_charge(request: Request, charge_id: int):
    """Manual 📍 recalculation, on demand — unlike the automatic background sweep
    (charger_locator.sweep_now, which silently picks the nearest name), this lets the
    user re-run the lookup for one charge and, when there's more than one plausible
    match, choose instead of Mate guessing (see find_station_candidates)."""
    import asyncio
    charge = db_reader.get_charge_location(charge_id)
    t = i18n.get_t(db_reader.get_language())
    if not charge or not charge.get("latitude") or not charge.get("longitude"):
        return templates.TemplateResponse(request, "partials/charge_location.html",
                                          {"charge": charge or {"id": charge_id}, "t": t, "error": True})
    options, ok = await asyncio.get_event_loop().run_in_executor(
        None, charger_locator.find_station_candidates, charge["latitude"], charge["longitude"])
    if not ok:
        return templates.TemplateResponse(request, "partials/charge_location.html",
                                          {"charge": charge, "t": t, "error": True})
    if not options:
        # Nothing found — but this charge may already carry a name, from a source that has
        # since dropped the station, or from a keyed source (Open Charge Map) whose key the
        # user has since removed. Writing the empty answer here would not just blank the label:
        # '' is the sweep's "resolved as nothing here" sentinel, so the name would never be
        # looked up again. A button that quietly destroys what it was asked to refresh is worse
        # than one that says it found nothing. On a charge with no name either, writing nothing
        # simply leaves it to the ongoing sweep, exactly as if the button had never been pressed.
        return templates.TemplateResponse(request, "partials/charge_location.html",
                                          {"charge": charge, "t": t, "not_found": True})
    if len(options) == 1:
        name, url = options[0].get("name") or "", options[0].get("url")
        db_reader.set_charge_location_name(charge_id, name, url)
        charge["location_name"], charge["location_url"] = name, url
        return templates.TemplateResponse(request, "partials/charge_location.html",
                                          {"charge": charge, "t": t})
    return templates.TemplateResponse(request, "partials/charge_location_choices.html",
                                      {"charge": charge, "options": options, "t": t})


@app.post("/api/charges/{charge_id}/locate/confirm", response_class=HTMLResponse)
async def confirm_charge_location(request: Request, charge_id: int):
    """User's pick from the ambiguity popup (find_station_candidates found more than
    one plausible station) — persists it exactly like the automatic sweep would."""
    form = await request.form()
    name = (form.get("name") or "").strip()
    url = (form.get("url") or "").strip() or None
    charge = db_reader.get_charge_location(charge_id)
    if not charge:
        return HTMLResponse("", status_code=404)
    db_reader.set_charge_location_name(charge_id, name, url)
    charge["location_name"], charge["location_url"] = name, url
    t = i18n.get_t(db_reader.get_language())
    return templates.TemplateResponse(request, "partials/charge_location.html",
                                      {"charge": charge, "t": t})


@app.get("/api/charges/{charge_id}/locate/cancel", response_class=HTMLResponse)
async def cancel_charge_location(request: Request, charge_id: int):
    """Dismiss the ambiguity popup without changing anything already saved."""
    charge = db_reader.get_charge_location(charge_id)
    t = i18n.get_t(db_reader.get_language())
    return templates.TemplateResponse(request, "partials/charge_location.html",
                                      {"charge": charge or {"id": charge_id}, "t": t})


@app.post("/api/charges/{charge_id}/locate/manual", response_class=HTMLResponse)
async def set_manual_charge_location(request: Request, charge_id: int):
    """✏️ free-text station name — for a station OSM/OCM simply doesn't have (#193:
    "Where to add stationname"). No coordinates/URL involved, just the label the
    automatic lookup could never produce on its own. Persists through
    set_charge_location_name exactly like a picked candidate would — location_name
    IS NOT NULL either way, so the background sweep (_LOCATION_CANDIDATES_WHERE) never
    revisits this charge. An empty submission changes nothing (closes the input with
    whatever was already saved, same as clicking away)."""
    form = await request.form()
    name = (form.get("name") or "").strip()[:200]
    charge = db_reader.get_charge_location(charge_id)
    if not charge:
        return HTMLResponse("", status_code=404)
    if name:
        db_reader.set_charge_location_name(charge_id, name, None)
        charge["location_name"], charge["location_url"] = name, None
    t = i18n.get_t(db_reader.get_language())
    return templates.TemplateResponse(request, "partials/charge_location.html",
                                      {"charge": charge, "t": t})


def _wallbox_overlay(curve: dict, charge_id: int) -> list | None:
    """Wallbox power (from HA history) resampled onto the car curve's timestamps,
    so it overlays the car's DC power on the same axis. None when unavailable.
    Only HOME charges get the overlay — on a public/away charge the home wallbox
    is irrelevant (and could even be charging another car)."""
    times = curve.get("times") or []
    mapping = ha_client.get_mapping()
    wallbox_on = db_reader.get_setting("wallbox_enabled", "0") == "1"
    if (not wallbox_on or not times or not ha_client.is_configured()
            or not mapping.get("power") or not db_reader.is_home_charge(charge_id)):
        return None
    hist = ha_client.get_history(mapping["power"], times[0], times[-1])
    if not hist:
        return None
    out, j, last = [], 0, None
    for t in times:
        e = ha_client.epoch(t)
        if e is None:
            out.append(None)
            continue
        while j < len(hist) and hist[j][0] <= e:   # step-hold: last known wallbox value ≤ sample time
            last = hist[j][1]
            j += 1
        out.append(round(last, 3) if last is not None else None)
    return out if any(v is not None for v in out) else None


def _typed_charge_payload(form) -> tuple[dict | None, bool]:
    """The fields a TYPED-IN charge is made of, read once for both forms that write one — the "add a
    past charge" form and the #188 edit form. Returns (payload, ok): two callers, one answer on what
    a comma decimal, a blank cost or an out-of-range SoC mean, so the pair can't drift.

    The wall clock is anchored here too: a time the user typed means nothing until it's tied to a
    zone, and everything the DB holds is UTC (#181)."""
    def _num(name):
        raw = str(form.get(name, "") or "").strip().replace(",", ".")
        try:
            return float(raw) if raw else None
        except ValueError:
            return None

    def _soc(name):
        v = _num(name)
        return v if (v is not None and 0 <= v <= 100) else None

    date = (form.get("date") or "").strip()
    time_ = (form.get("time") or "").strip() or "12:00"        # noon default → no day-shift on display
    energy = _num("energy") or 0.0
    if not date or energy <= 0:
        return None, False

    end_date = (form.get("end_date") or "").strip()
    end_time = (form.get("end_time") or "").strip()
    ended_at = None
    if end_date or end_time:
        ended_at = db_reader.local_to_utc_iso(f"{end_date or date}T{end_time or time_}:00")

    started_at = db_reader.local_to_utc_iso(f"{date}T{time_}:00")
    # An end before the start is a typo, not a negative charge: drop it rather than store a span
    # that would come back as a negative duration everywhere it's summed.
    if ended_at and ended_at <= started_at:
        ended_at = None
    # #237 — the car's total distance at that charge, typed in the reader's OWN unit and stored in
    # km like every distance in the database. It is the only way kilometres can ever reach a charge
    # from before Mate was installed: no poll of it was ever made, and nothing here can invent them.
    odo = _num("odometer")
    return {
        "started_at": started_at,
        "ended_at": ended_at,
        "energy_kwh": energy,
        "cost": _num("cost"),
        "charge_type": (form.get("charge_type") or "AC").strip().upper(),
        "start_soc": _soc("start_soc"),
        "end_soc": _soc("end_soc"),
        "odometer_km": units.dist_to_km(odo) if odo and odo > 0 else None,
    }, True


@app.post("/api/charges/manual", response_class=HTMLResponse)
async def add_manual_charge_api(request: Request):
    """Add a historical charge by hand (#87) — for sessions from before Mate was installed, so the
    lifetime totals / monthly report are complete. Only date+energy are required; cost and AC/DC are
    optional. No power curve (manual entries carry no telemetry); SoC and an end time can be given
    through the CSV import or corrected afterwards on the charge itself (#188)."""
    form = await request.form()
    t = i18n.get_t(db_reader.get_language())
    payload, ok = _typed_charge_payload(form)
    if not ok:
        return HTMLResponse(f'<span style="color:#ef4444">✗ {t("manual_charge_required")}</span>', status_code=400)
    db_reader.add_manual_charge(**payload)
    return HTMLResponse(f'<span style="color:#22c55e">✓ {t("manual_charge_added")}</span>',
                        headers={"HX-Trigger": "chargeAdded"})


@app.post("/api/charges/{charge_id}/edit", response_class=HTMLResponse)
async def edit_manual_charge_api(request: Request, charge_id: int):
    """Correct a charge that was typed in — the times, the SoC, the energy, the cost (#188).

    @adoewa entered his whole charging history from a spreadsheet and then found the only things he
    could change were the note, the AC/DC tag and the cost; the start and end times and the SoC, which
    the add form had never asked him for, were frozen. Restricted to typed-in rows: on a MEASURED
    charge those numbers are readings, and a form over them would let a typo overwrite what the car
    reported. db_reader enforces that too — this route is not the only gate."""
    form = await request.form()
    t = i18n.get_t(db_reader.get_language())
    payload, ok = _typed_charge_payload(form)
    if not ok:
        return HTMLResponse(f'<span style="color:#ef4444">✗ {t("manual_charge_required")}</span>', status_code=400)
    if not db_reader.update_manual_charge(charge_id, **payload):
        return HTMLResponse(f'<span style="color:#ef4444">✗ {t("charge_edit_not_manual")}</span>', status_code=400)
    return HTMLResponse(f'<span style="color:#22c55e">✓ {t("charge_edit_saved")}</span>',
                        headers={"HX-Trigger": "chargeAdded"})


@app.get("/api/charges/import-template.csv")
async def charges_import_template():
    """The empty, self-documenting CSV the user fills in and re-imports (idea from #111)."""
    import charge_import
    return Response(
        charge_import.TEMPLATE,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="leapmotor-mate-charges-template.csv"'},
    )


@app.post("/api/charges/import", response_class=HTMLResponse)
async def charges_import_api(request: Request):
    """Bulk-import historical charges from the filled-in CSV (#111). Each clean line goes in through the
    SAME path as a manual entry; each rejected line is reported with its reason — one typo never blocks
    the whole file, and dirty data never reaches the DB."""
    from html import escape as _esc
    import charge_import
    t = i18n.get_t(db_reader.get_language())
    form = await request.form()
    up = form.get("file")
    if up is None or not hasattr(up, "read"):
        return HTMLResponse(f'<span style="color:#ef4444">✗ {t("import_no_file")}</span>', status_code=400)
    raw = await up.read()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8-sig", "replace")        # utf-8-sig drops the BOM Excel prepends
    rows, errors = charge_import.parse_charge_csv(raw, tz=db_reader._local_tz())
    # #237 — a line that matches a session already here FILLS IT IN (the odometer) instead of adding
    # a second copy. Re-importing used to double the archive in silence; it now says which lines were
    # new and which were completed, because "152 imported" reads identically in both worlds.
    added = filled = 0
    for r in rows:
        outcome = db_reader.import_charge_row(r)
        added += outcome == "added"
        filled += outcome == "filled"
    parts = []
    if added:
        parts.append(f'<span style="color:#22c55e">✓ {_esc(t("import_done").format(n=added))}</span>')
    if filled:
        parts.append(f'<span style="color:#22c55e">{" · " if added else "✓ "}'
                     f'{_esc(t("import_filled").format(n=filled))}</span>')
    if errors:
        shown = "".join(f"<li>{_esc(e)}</li>" for e in errors[:50])
        more = f'<li>… (+{len(errors) - 50})</li>' if len(errors) > 50 else ""
        parts.append(f'<div style="color:#f59e0b;margin-top:6px">⚠ {_esc(t("import_skipped").format(n=len(errors)))}'
                     f'<ul style="margin:4px 0 0 18px;list-style:disc;font-size:12px">{shown}{more}</ul></div>')
    # Every line already present and carrying nothing new: not an error, and emphatically not
    # "imported 152". Silence would be worse — the user would re-upload, and before #237 that is
    # exactly how an archive doubled.
    if rows and not added and not filled and not errors:
        parts.append(f'<span style="color:#94a3b8">{_esc(t("import_all_known").format(n=len(rows)))}</span>')
    if not rows and not errors:
        parts.append(f'<span style="color:#94a3b8">{_esc(t("import_none"))}</span>')
    return HTMLResponse("".join(parts),
                        headers={"HX-Trigger": "chargeAdded"} if (added or filled) else {})


@app.get("/api/charge/{charge_id}/power-chart", response_class=HTMLResponse)
async def charge_power_chart(request: Request, charge_id: int):
    """Lazy-loaded power-over-time chart for one charge session (expandable in the list).
    When a wallbox is configured, overlays its delivered AC power vs the car's DC power."""
    curve = db_reader.get_charge_power_curve(charge_id)
    return templates.TemplateResponse(request, "partials/charge_power_chart.html", _ctx(
        cid=charge_id,
        labels=curve["labels"], power=curve["power"], soc=curve["soc"],
        wb_power=_wallbox_overlay(curve, charge_id),
    ))


@app.post("/api/settings/prices", response_class=HTMLResponse)
async def save_prices(request: Request):
    form = await request.form()
    for key in ["price_home_kwh", "price_ac_kwh", "price_fast_kwh", "price_hpc_kwh"]:
        val = form.get(key)
        if val:
            try:
                db_reader.update_charge_price(key, float(val))
            except ValueError:
                pass
    # If a profile is active, keep the indicator and sync the new prices into its snapshot
    # so that reloading the profile later restores the updated values.
    active_id = db_reader.get_setting("wallbox_active_profile", "")
    if active_id:
        profiles = _get_wallbox_profiles()
        updated = False
        for p in profiles:
            if p["id"] == active_id:
                for price_key in ("price_home_kwh", "price_ac_kwh", "price_fast_kwh", "price_hpc_kwh"):
                    val = form.get(price_key)
                    if val:
                        try:
                            p[price_key] = float(val)
                            updated = True
                        except ValueError:
                            pass
                break
        if updated:
            _set_wallbox_profiles(profiles)
    t = i18n.get_t(db_reader.get_language())
    return HTMLResponse(f'<span style="color:#22c55e;font-size:13px">{t("costs_saved")}</span>')


@app.post("/api/costs/mode", response_class=HTMLResponse)
async def save_cost_mode(request: Request):
    """Save the pricing mode PER CHARGE TYPE (#106) — one `cost_mode_<TYPE>` select each.
    A legacy single `cost_mode` field (older clients / tests) still works and applies to
    every type. Bands/method untouched."""
    form = await request.form()
    legacy = form.get("cost_mode")
    if legacy:
        modes = {t: legacy for t in db_reader._TOU_TYPES}
    else:
        cfg = db_reader.get_cost_config()
        modes = {t: form.get(f"cost_mode_{t}") or cfg["modes"].get(t, "flat")
                 for t in db_reader._TOU_TYPES}
    db_reader.save_cost_modes(modes)
    return HTMLResponse("")


@app.post("/api/costs/tou", response_class=HTMLResponse)
async def save_cost_tou(request: Request):
    """Save the calc method + the user's time bands (JSON from the band editor)."""
    form = await request.form()
    try:
        bands = json.loads(form.get("bands_json", "[]") or "[]")
    except (ValueError, TypeError):
        bands = []
    cfg = db_reader.get_cost_config()
    db_reader.save_cost_config(cfg["mode"], form.get("tou_method", "split"), bands)
    # NOTE: recomputing existing charge costs from the bands is the next step.
    t = i18n.get_t(db_reader.get_language())
    return HTMLResponse(f'<span style="color:#22c55e;font-size:13px">{t("costs_saved")}</span>')


@app.get("/api/costs/dynamic-entities", response_class=HTMLResponse)
async def dynamic_price_entities(request: Request, ctype: str = ""):
    """Lazy-loaded picker for the dynamic-pricing sensor: candidate HA entities (price
    keyword or a per-kWh currency unit), pre-selected with the saved choice if any.
    `ctype` (#106) scopes the picker to ONE charge type (its own sensor, falling back to
    the legacy single one); without it, the legacy global picker is rendered."""
    entities = ha_client.list_price_entities()
    selected = (db_reader.get_dynamic_price_entity_for(ctype) if ctype
                else db_reader.get_dynamic_price_entity())
    return templates.TemplateResponse(request, "partials/dynamic_price_entities.html", _ctx(
        entities=entities, selected=selected, ctype=ctype,
    ))


@app.post("/api/costs/dynamic", response_class=HTMLResponse)
async def save_cost_dynamic(request: Request):
    """Save the chosen dynamic-price sensor entity — per charge type when `ctype` is posted
    (#106), else the legacy single entity."""
    form = await request.form()
    ctype = (form.get("ctype") or "").strip()
    if ctype:
        db_reader.save_dynamic_price_entity_for(ctype, form.get("dynamic_price_entity", ""))
    else:
        db_reader.save_dynamic_price_entity(form.get("dynamic_price_entity", ""))
    t = i18n.get_t(db_reader.get_language())
    return HTMLResponse(f'<span style="color:#22c55e;font-size:13px">{t("costs_saved")}</span>')


@app.post("/api/settings/abrp", response_class=HTMLResponse)
async def save_abrp(request: Request):
    """Enable/disable ABRP live telemetry and store the user's personal token."""
    form = await request.form()
    db_reader.set_setting("abrp_enabled", "1" if form.get("abrp_enabled") in ("1", "on", "true") else "0")
    tok = (form.get("abrp_token") or "").strip()
    vin = ((form.get("vin") or "") if isinstance(form.get("vin"), str) else "").strip()
    if tok:  # masked field: only overwrite on a non-empty submit (keep existing otherwise)
        # ABRP is one token per VEHICLE on ABRP's side too, so with two cars each wants its own
        # (#186): one token for both fed a single ABRP vehicle two cars' positions and SoCs.
        if vin:
            if not any(v.get("vin") == vin for v in db_reader.get_vehicles()):
                return HTMLResponse("unknown vin", status_code=422)
            db_reader.set_secret(f"abrp_token_{vin.lower()}", tok)
        else:
            db_reader.set_secret("abrp_token", tok)
    t = i18n.get_t(db_reader.get_language())
    return HTMLResponse(f'<span style="color:#22c55e;font-size:13px">{t("abrp_saved")}</span>')


@app.post("/api/settings/geocoder", response_class=HTMLResponse)
async def save_geocoder(request: Request):
    """Store the optional TomTom API key used for better address/house-number
    coverage on the Navigation page. Empty = keyless Photon/Nominatim."""
    form = await request.form()
    if "geocoder_provider" in form:
        db_reader.set_setting("geocoder_provider", (form.get("geocoder_provider") or "").strip())
    # The automatic 🧭 note, which is the only thing here that reaches out on its own initiative
    # — the rest of this card is consulted when the user asks for an address. Guarded by the
    # marker rather than by the checkbox alone, because an unticked box submits nothing at all
    # and would otherwise read as "turn it off" on any form that never showed it.
    if "auto_note_present" in form:
        db_reader.set_setting("auto_note", "1" if form.get("auto_note") else "0")
    gkey = (form.get("geocoder_key") or "").strip()
    if gkey:  # masked field: only overwrite on a non-empty submit
        db_reader.set_secret("geocoder_key", gkey)
    t = i18n.get_t(db_reader.get_language())
    return HTMLResponse(f'<span style="color:#22c55e;font-size:13px">{t("geocoder_saved")}</span>')


@app.post("/api/settings/charger-locator", response_class=HTMLResponse)
async def save_charger_locator(request: Request):
    """Toggle the 📍 station labels. Turning it ON kicks an immediate background backfill
    of the unlabelled public charges (history included); the Navigation page search is
    user-triggered and independent of this toggle."""
    import asyncio
    form = await request.form()
    on = "1" if form.get("charger_locator") else "0"
    db_reader.set_setting("charger_locator", on)
    okey = (form.get("charger_locator_ocm_key") or "").strip()
    if okey:
        db_reader.set_secret("ocm_key", okey)
    tkey = (form.get("charger_locator_tomtom_key") or "").strip()
    if tkey:
        db_reader.set_secret("tomtom_key", tkey)
    t = i18n.get_t(db_reader.get_language())
    if on == "1" and db_reader.has_location_lookup_candidates():
        asyncio.get_event_loop().run_in_executor(None, charger_locator.sweep_now)
        return HTMLResponse(f'<span style="color:#22c55e;font-size:13px">{t("charger_locator_started")}</span>')
    return HTMLResponse(f'<span style="color:#22c55e;font-size:13px">{t("charger_locator_saved")}</span>')


def _key_test_html(label: str, ok: bool, reason: "str | None", t) -> str:
    if ok:
        return f'<span style="color:#22c55e;font-size:13px">🟢 {label}: {t("charger_locator_key_ok")}</span>'
    msg = t("charger_locator_key_invalid") if reason == "invalid_key" else t("charger_locator_key_error")
    return f'<span style="color:#ef4444;font-size:13px">🔴 {label}: {msg}</span>'


@app.post("/api/settings/charger-locator/test", response_class=HTMLResponse)
async def test_charger_locator_keys(request: Request):
    """Verify the OCM/TomTom keys currently in the form actually work, BEFORE saving —
    same empty-submit-means-saved-value convention as the MQTT password test (#91):
    the fields are masked (type=password), so an empty submit falls back to the
    already-saved secret rather than testing an empty string."""
    import asyncio
    form = await request.form()
    okey = (form.get("charger_locator_ocm_key") or "").strip() or db_reader.get_secret("ocm_key", "")
    tkey = (form.get("charger_locator_tomtom_key") or "").strip() or db_reader.get_secret("tomtom_key", "")
    t = i18n.get_t(db_reader.get_language())
    if not okey and not tkey:
        return HTMLResponse(f'<span style="color:#94a3b8;font-size:13px">{t("charger_locator_test_none")}</span>')
    loop = asyncio.get_event_loop()
    parts = []
    if okey:
        ok, reason = await loop.run_in_executor(None, charger_locator.test_ocm_key, okey)
        parts.append(_key_test_html("Open Charge Map", ok, reason, t))
    if tkey:
        ok, reason = await loop.run_in_executor(None, charger_locator.test_tomtom_key, tkey)
        parts.append(_key_test_html("TomTom", ok, reason, t))
    return HTMLResponse("<br>".join(parts))


@app.post("/api/settings/charger-locator/backfill", response_class=HTMLResponse)
async def backfill_charger_links(request: Request):
    """One-time recovery of the 🔗 link on charges labelled BEFORE location_url
    existed (or resolved from a link-less source alone, e.g. PUN) — never touches the
    already-saved name. Runs in the background (can take a while, one Overpass call
    per charge); charger_locator.backfill_urls_now is single-flight, so clicking again
    while one is running is a safe no-op.

    A run handles at most _BACKFILL_LIMIT charges — Overpass is free and shared, and
    emptying a long backlog in one burst is how an IP earns a temporary block. So the
    count is shown: "50/137" tells the user this took a bite, not the whole thing, and
    that pressing again is the way to continue."""
    import asyncio
    t = i18n.get_t(db_reader.get_language())
    pending = len(db_reader.get_labelled_charges_missing_url(10000))
    if not pending:
        return HTMLResponse(f'<span style="color:#94a3b8;font-size:13px">{t("charger_locator_backfill_none")}</span>')
    asyncio.get_event_loop().run_in_executor(None, charger_locator.backfill_urls_now)
    batch = min(pending, charger_locator._BACKFILL_LIMIT)
    tail = f" — {batch}/{pending}" if pending > batch else ""
    return HTMLResponse(
        f'<span style="color:#22c55e;font-size:13px">{t("charger_locator_backfill_started")}{tail}</span>')


@app.post("/api/settings/charger-locator/map-threshold", response_class=HTMLResponse)
async def save_map_station_threshold(request: Request):
    """Minimum charges at the same GPS spot before it earns a marker among the Map's
    Charging stations (get_charging_stations' min_sessions). Default 1 — every station,
    including the one-off charger on a trip, which is the very thing this layer is for.
    Raising it is for the opposite taste: thinning a crowded map down to the spots the
    driver actually returns to."""
    form = await request.form()
    try:
        n = max(1, min(10, int(form.get("map_station_min_sessions") or 1)))
    except (TypeError, ValueError):
        n = 1
    db_reader.set_setting("map_station_min_sessions", str(n))
    t = i18n.get_t(db_reader.get_language())
    return HTMLResponse(f'<span style="color:#22c55e;font-size:13px">{t("map_station_threshold_saved")}</span>')


@app.post("/api/settings/map-station-count")
async def save_map_station_count(request: Request):
    """How many station markers the Map draws (get_charging_stations' top_n); 0 = all of them.
    Set from the box on the map's own legend row, but a POST like its twin above — a GET that
    writes a stored preference is re-applied by every bookmark, Back button and link prefetch
    that touches the URL, and on a shared install one person's link would change everyone's map.
    Redirects back to the map (POST-Redirect-GET) so the new marker set is simply what the page
    renders next. Clamped: the box is the only way in, but a hand-typed number shouldn't be
    stored verbatim any more than the threshold's 1–10 is."""
    form = await request.form()
    try:
        n = max(0, min(999, int(form.get("top_n") or 0)))
    except (TypeError, ValueError):
        n = 15
    db_reader.set_setting("map_station_top_n", str(n))
    return RedirectResponse(request.headers.get("x-ingress-path", "") + "/map", status_code=303)


@app.post("/api/settings/map-trip-count")
async def save_map_trip_count(request: Request):
    """How many trip routes the Map draws (get_all_track's max_trips, the N most recently
    STARTED trips); 0 = every trip. Same box-on-the-map / POST-Redirect-GET convention as
    save_map_station_count above, and the same reason: a GET that writes a stored preference
    would be re-applied by a bookmark, Back, or link prefetch."""
    form = await request.form()
    try:
        n = max(0, min(9999, int(form.get("trips") or 0)))
    except (TypeError, ValueError):
        n = 0
    db_reader.set_setting("map_trips_shown", str(n))
    return RedirectResponse(request.headers.get("x-ingress-path", "") + "/map", status_code=303)


@app.post("/api/settings/retention", response_class=HTMLResponse)
async def save_retention(request: Request):
    """Save GPS-sample retention (positions_retention_days; 0 = keep forever). The poller
    prunes old non-charging samples daily; trips and charge curves are always kept."""
    form = await request.form()
    try:
        days = max(0, int(form.get("positions_retention_days") or 0))
    except (TypeError, ValueError):
        days = 0
    db_reader.set_setting("positions_retention_days", str(days))
    t = i18n.get_t(db_reader.get_language())
    return HTMLResponse(f'<span style="color:#22c55e;font-size:13px">{t("retention_saved")}</span>')


def _csv_response(rows: list, filename: str) -> Response:
    """Rows can carry different key sets — e.g. get_trips()'s "segment_ids" only appears on
    merged trips — and csv.DictWriter crashes (500) the moment a later row has a key the
    FIRST row's fieldnames didn't cover. Union every row's keys instead, in first-seen
    order, and drop list/dict-typed values (segment_ids is internal merge bookkeeping, not
    something a CSV export needs — and would otherwise show as a raw Python repr).

    "Droppable" is decided over EVERY row before any column is chosen, because a key can be a list
    on one row and a scalar on another. Judging it row by row would let the scalar occurrence admit
    the column and the list occurrence then print as `[2, 3]` — the raw repr this exists to avoid,
    reappearing through the same door the crash came from."""
    import csv, io
    buf = io.StringIO()
    if rows:
        drop = {k for r in rows for k, v in r.items() if isinstance(v, (list, dict))}
        fieldnames = []
        for r in rows:
            for k in r:
                if k not in fieldnames and k not in drop:
                    fieldnames.append(k)
        clean_rows = [{k: v for k, v in r.items() if k in fieldnames} for r in rows]
        w = csv.DictWriter(buf, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(clean_rows)
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/export/trips.csv")
async def export_trips_csv():
    return _csv_response(db_reader.get_trips(limit=1_000_000), "leapmotor-mate-trips.csv")


@app.get("/api/export/charges.csv")
async def export_charges_csv():
    return _csv_response(db_reader.get_charges(limit=1_000_000), "leapmotor-mate-charges.csv")


@app.get("/trips/{trip_id}/route.gpx")
async def export_trip_gpx(trip_id: int):
    import xml.sax.saxutils as su
    pts = db_reader.get_trip_track(trip_id)
    trip = db_reader.get_trip_detail(trip_id)
    name = su.escape(f"Leapmotor trip {trip_id}" + (f" — {trip['started_at'][:16]}" if trip and trip.get('started_at') else ""))
    seg = "".join(
        f'<trkpt lat="{p["latitude"]}" lon="{p["longitude"]}">'
        + (f'<time>{p["recorded_at"]}</time>' if p.get("recorded_at") else "")
        + (f'<extensions><speed>{p["speed_kmh"]}</speed></extensions>' if p.get("speed_kmh") is not None else "")
        + '</trkpt>'
        for p in pts
    )
    gpx = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<gpx version="1.1" creator="Leapmotor Mate" xmlns="http://www.topografix.com/GPX/1/1">'
           f'<trk><name>{name}</name><trkseg>{seg}</trkseg></trk></gpx>')
    return Response(gpx, media_type="application/gpx+xml",
                    headers={"Content-Disposition": f'attachment; filename="leapmotor-trip-{trip_id}.gpx"'})


@app.get("/api/export/database")
async def export_database():
    """Download the SQLite database as a backup. NB: encrypted credentials need the
    matching /data/secret.key to be usable on another install."""
    try:
        db_reader.checkpoint()
    except Exception:  # noqa: BLE001
        pass
    return FileResponse(db_reader.DB_PATH, media_type="application/octet-stream",
                        filename="leapmotor_mate.db")


@app.post("/api/import/database", response_class=HTMLResponse)
async def import_database(request: Request):
    """Restore a `leapmotor_mate.db` downloaded from Export → replace the current DB with it (losing
    ZERO data) while keeping the login you just entered, then restart so the app reopens the restored
    data + runs migrations. This is how you carry ALL your data across a reinstall (e.g. moving an old
    BetaTester install onto the numbered add-on) without losing a single row — and normal disaster
    recovery for everyone."""
    t = i18n.get_t(db_reader.get_language())
    form = await request.form()
    up = form.get("file")
    if up is None or not hasattr(up, "read"):
        return HTMLResponse("<div class='text-sm text-red-400'>⚠️ " + t("restore_db_nofile") + "</div>",
                            status_code=400)
    blob = await up.read()
    try:
        res = db_reader.restore_database(blob)
    except ValueError as e:
        return HTMLResponse("<div class='text-sm text-red-400'>⚠️ " + str(e) + "</div>", status_code=400)
    except Exception as e:  # noqa: BLE001
        return HTMLResponse("<div class='text-sm text-red-400'>⚠️ " + str(e) + "</div>", status_code=500)
    _restart_container()   # exit 42 → run.sh relaunches poller + web → both reopen the restored DB
    n = res["counts"]
    return HTMLResponse(
        "<div class='text-sm text-emerald-400'>✓ " + t("restore_db_ok") + "</div>"
        "<div class='text-xs text-slate-400 mt-1'>"
        + f"{n['raw_signals_log']} · {n['trips']} " + t("trips_title").lower()
        + f" · {n['charges']} " + t("charges_title").lower()
        + f" · {n['positions']} pos</div>"
        "<script>setTimeout(function(){location.reload();}, 20000);</script>")


@app.post("/api/settings/mqtt", response_class=HTMLResponse)
async def save_mqtt(request: Request):
    """Save the MQTT bridge config (broker + options). Opt-in via the enable flag."""
    form = await request.form()
    def flag(name): return "1" if form.get(name) in ("1", "on", "true") else "0"
    db_reader.set_setting("mqtt_enabled", flag("mqtt_enabled"))
    db_reader.set_setting("mqtt_discovery", flag("mqtt_discovery"))
    db_reader.set_setting("mqtt_tls", flag("mqtt_tls"))
    db_reader.set_setting("mqtt_tls_insecure", flag("mqtt_tls_insecure"))
    for key in ("mqtt_broker", "mqtt_port", "mqtt_user", "mqtt_pass", "mqtt_prefix"):
        if key in form:
            val = (form.get(key) or "").strip()
            if key == "mqtt_pass":
                if val:  # masked field: keep the existing password on an empty submit
                    db_reader.set_secret(key, val)
            else:
                db_reader.set_setting(key, val)
    t = i18n.get_t(db_reader.get_language())
    return HTMLResponse(f'<span style="color:#22c55e;font-size:13px">{t("mqtt_saved")}</span>')


@app.post("/api/settings/mqtt/test", response_class=HTMLResponse)
async def test_mqtt(request: Request):
    """Try to connect to the broker with the values currently in the form (before
    saving), so the user can verify host/port/credentials/TLS first.

    The password field is masked — it renders as •••••••• and never carries the real
    value — so an EMPTY submit means "use the saved password", the same convention as
    Save (set_secret only on a non-empty value) and the status dot. Without this
    fallback, clicking Test without retyping the password would test with NO password
    and report "Not authorised", even though the running bridge (which uses the saved
    password) stays green — the exact mismatch reported in issue #91."""
    form = await request.form()
    mqtt_pass = (form.get("mqtt_pass") or "").strip() or db_reader.get_secret("mqtt_pass", "") or None
    import asyncio
    ok, reason = await asyncio.get_event_loop().run_in_executor(
        None, lambda: mqtt_check.check_connection(
            form.get("mqtt_broker", ""),
            form.get("mqtt_port", "1883"),
            form.get("mqtt_user") or None,
            mqtt_pass,
            form.get("mqtt_tls") in ("1", "on", "true"),
            form.get("mqtt_tls_insecure") in ("1", "on", "true"),
        ))
    t = i18n.get_t(db_reader.get_language())
    if ok:
        return HTMLResponse(f'<span style="color:#22c55e;font-size:13px">🟢 {t("mqtt_connected")}</span>')
    return HTMLResponse(f'<span style="color:#ef4444;font-size:13px">🔴 {t("mqtt_failed")}: {reason}</span>')


# Every collapsible card on the Settings accordion. Used both to build the initial
# open/collapsed map and as the allowlist for the ui-state save endpoint.
_UI_CARD_KEYS = {"locale", "vehicle", "battery", "polling", "charge_detect", "trips", "advanced",
                 "abrp", "geocoder", "charger_locator", "wallbox", "mqtt",
                 "database", "export", "diagnostics"}

# Settings sections flagged "new in a recent release": the section shows a NEW badge on its header
# until the user OPENS it (card_seen_<key>=1, badge never returns — so a new feature isn't missed if
# buried in the changelog). Maintenance: add a section's key here when you SHIP that new section,
# then drop it a release or two later. Empty = no section is "new" right now (the mechanism is idle).
_NEW_SETTINGS_SECTIONS: set[str] = {"trips"}   # #119: trip drive-mode / One-Pedal defaults


def _card_open(key: str, default: bool) -> bool:
    """Open/collapsed state of a settings card: the user's last saved choice if any,
    otherwise `default`. Stored server-side so it survives reloads and is the same on
    every device/browser."""
    saved = db_reader.get_setting(f"ui_{key}_open", "")
    return saved == "1" if saved in ("0", "1") else default


@app.post("/api/settings/ui-state")
async def save_ui_state(request: Request):
    """Persist a settings card's open/collapsed state (chevron). Fire-and-forget from
    the page; saved to the DB so it's shared across devices, not just this browser."""
    form = await request.form()
    key = (form.get("key") or "").strip()
    if key in _UI_CARD_KEYS:
        # "Seen" ack: any click inside a NEW-badged section clears its badge for good.
        if form.get("seen") in ("1", "on", "true"):
            db_reader.set_setting(f"card_seen_{key}", "1")
        # The chevron's open/collapsed state (sent on every toggle).
        if form.get("open") is not None:
            db_reader.set_setting(f"ui_{key}_open",
                                  "1" if form.get("open") in ("1", "on", "true") else "0")
    return Response(status_code=204)


@app.post("/api/settings/pin")
async def set_operation_pin(request: Request):
    """Change the car's operation PIN without unlinking the account (discussion #225, @alextchao).

    Until now this lived in exactly two places: the setup wizard wrote it, and Logout cleared it. So
    changing four digits on the car meant signing out of the Leapmotor account and walking the whole
    wizard again — no data was lost (history is keyed by VIN) but nothing about that is obvious to
    someone who just wants to retype a PIN.

    Typed twice, like the access password (#214 @rop12770): a PIN stored with a typo fails at the
    car, hours later, with an error that says nothing about which digit was wrong.

    ⚠️ Nothing else has to be poked. The stored secret beats `LEAPMOTOR_PIN` in both readers, the web
    command session is dropped here so the next command re-authenticates, and the poller already
    compares (user, pass, PIN) against the ones it started with on every cycle and exits with 42 for
    run.sh to relaunch it. That watcher was written for the account switch; a PIN change is the same
    shape and it was already covered."""
    form = await request.form()
    pin = ((form.get("pin") or "") if isinstance(form.get("pin"), str) else "").strip()
    pin2 = ((form.get("pin2") or "") if isinstance(form.get("pin2"), str) else "").strip()
    vin = ((form.get("vin") or "") if isinstance(form.get("vin"), str) else "").strip()
    if not pin:
        return Response("empty", status_code=422)
    if pin2 != pin:
        return Response("mismatch", status_code=422)
    # A `vin` means "this car's own PIN" (#186, @cookingeek): two Leapmotors on one account can want
    # different four digits, and the commands are the only thing that would notice. A VIN we do not
    # have is refused rather than stored, so a stale form cannot mint a secret for a phantom car.
    if vin:
        if not any(v.get("vin") == vin for v in db_reader.get_vehicles()):
            return Response("unknown vin", status_code=422)
        db_reader.set_operate_pin(pin, vin)
        # Giving a car its own PIN is somebody looking at that car, so it answers the "never set up"
        # strip too. It matters for a car whose model default happens to be right: there is nothing
        # to change in the capacity box, and without this the strip could not be cleared at all
        # except by walking the wizard again (@cookingeek set a separate PIN and kept the strip).
        db_reader.mark_vehicle_configured(vin)
        command_client._session._reset()
        return Response(status_code=204, headers={"HX-Refresh": "true"})
    db_reader.set_secret("leapmotor_pin", pin)
    command_client._session._reset()
    return Response(status_code=204, headers={"HX-Refresh": "true"})


@app.post("/api/account/logout")
async def account_logout(request: Request):
    """Sign out of the Leapmotor account so a different one can be linked, WITHOUT touching
    any data: trips, charges, positions and the shared app certificate are all left intact.
    Only the stored login is cleared and the setup wizard re-opened. The poller notices the
    credential change and restarts itself to re-authenticate as the new account (run.sh →
    container restart); history is keyed by VIN, so the same car's records carry over."""
    db_reader.set_setting("leapmotor_user", "")
    db_reader.set_secret("leapmotor_pass", "")
    db_reader.set_secret("leapmotor_pin", "")
    db_reader.set_setting("setup_complete", "0")
    command_client._session._reset()          # drop the web command session too
    resp = Response(status_code=204)
    resp.headers["HX-Redirect"] = request.headers.get("x-ingress-path", "") + "/setup"
    return resp


@app.post("/api/account/factory-reset")
async def account_factory_reset(request: Request):
    """Full, IRREVERSIBLE wipe (Settings → Delete account / Factory reset). Erases ALL local data —
    account, trips, charges, positions and every setting (MQTT / wallbox / prices / HA included) —
    and reopens the setup wizard as a brand-new install. Unlike Logout (which keeps history by VIN),
    this keeps nothing except the app-level TLS cert on disk, so the re-onboard still needs only the
    login. Type-to-confirm guarded. The destructive table wipe is done by the POLLER at startup
    (sole DB writer there → no race); here we set the marker, open the setup gate so the redirect
    below isn't bounced during the ~2s relaunch window, drop the cached car image, and relaunch the
    whole app (web exits 42 → run.sh restarts poller + web; the poller wipes on its next startup)."""
    form = await request.form()
    if (form.get("confirm") or "").strip().upper() != "RESET":
        return Response(status_code=400)
    db_reader.set_setting("factory_reset_pending", "1")
    db_reader.set_setting("setup_complete", "0")        # gate open immediately; poller wipes the rest
    command_client._session._reset()
    # Drop the cached car-picture artifacts (on disk, not in the DB) so the next account starts clean.
    for p in (_car_picture_pkg_path(), _car_picture_cache_path()):
        try:
            os.remove(p)
        except OSError:
            pass
    _car_image_memo.clear()
    car_image.clear_cache()
    _restart_container()        # exit 42 → run.sh relaunches poller + web; poller wipes on startup
    resp = Response(status_code=204)
    resp.headers["HX-Redirect"] = request.headers.get("x-ingress-path", "") + "/setup"
    return resp


def _status_dot(color: str, label: str) -> HTMLResponse:
    """A small coloured status dot + label for an integration summary header —
    same visual language as the Wallbox connection badge."""
    return HTMLResponse(
        f'<span class="inline-flex items-center gap-1.5 text-xs text-{color}-400">'
        f'<span class="w-2 h-2 rounded-full bg-{color}-400"></span>{label}</span>')


@app.get("/api/settings/abrp/status", response_class=HTMLResponse)
async def abrp_status(request: Request):
    """At-a-glance ABRP state for the collapsed summary. ABRP is fire-and-forget
    telemetry (no live connection to test), so this reflects config state."""
    t = i18n.get_t(db_reader.get_language())
    if db_reader.get_setting("abrp_enabled", "0") != "1":
        return _status_dot("slate", t("status_off"))
    if not db_reader.abrp_token_in_use():
        return _status_dot("amber", t("status_unconfigured"))
    return _status_dot("emerald", t("status_active"))


@app.get("/api/settings/mqtt/status", response_class=HTMLResponse)
async def mqtt_status(request: Request):
    """Live MQTT broker state for the collapsed summary — grey when off, amber when
    enabled but unconfigured, green/red from a bounded connect (like the Wallbox dot)."""
    t = i18n.get_t(db_reader.get_language())
    if db_reader.get_setting("mqtt_enabled", "0") != "1":
        return _status_dot("slate", t("status_off"))
    broker = db_reader.get_setting("mqtt_broker", "")
    if not broker:
        return _status_dot("amber", t("status_unconfigured"))
    import asyncio
    ok, _reason = await asyncio.get_event_loop().run_in_executor(
        None, lambda: mqtt_check.check_connection(
            broker,
            db_reader.get_setting("mqtt_port", "1883"),
            db_reader.get_setting("mqtt_user", "") or None,
            db_reader.get_secret("mqtt_pass", "") or None,
            db_reader.get_setting("mqtt_tls", "0") == "1",
            db_reader.get_setting("mqtt_tls_insecure", "0") == "1",
        ))
    return _status_dot("emerald", t("ha_status_ok")) if ok else _status_dot("red", t("ha_status_ko"))


@app.post("/api/settings/language")
async def set_language(request: Request):
    """Change the UI language after setup. Saved to the DB, then the page is reloaded
    (HX-Refresh) so every server-rendered string switches to the new language."""
    form = await request.form()
    lang = form.get("language", "en")
    db_reader.set_setting("language", lang if lang in ("en", "it", "fr", "de", "pl", "pt-PT", "nl", "es") else "en")
    return Response(status_code=204, headers={"HX-Refresh": "true"})


@app.post("/api/settings/currency")
async def set_currency(request: Request):
    """Change the currency used to format every monetary amount. Reloads the page
    (HX-Refresh) so all server-rendered costs re-render with the new symbol."""
    form = await request.form()
    db_reader.set_currency(form.get("currency", "EUR"))
    return Response(status_code=204, headers={"HX-Refresh": "true"})


@app.post("/api/settings/units")
async def set_units(request: Request):
    """Change the measurement system (metric / imperial UK / imperial US). Display-only — the DB
    stays metric — so a page reload (HX-Refresh) re-renders every km/°C/bar in the chosen units."""
    form = await request.form()
    sys = form.get("unit_system", "metric")
    db_reader.set_setting("unit_system", sys if sys in units.UNIT_SYSTEMS else "metric")
    return Response(status_code=204, headers={"HX-Refresh": "true"})


@app.post("/api/settings/timezone")
async def set_timezone(request: Request):
    """Change the timezone every date/time is displayed in (trips, charges, reports) and that ToU
    charges are priced by from here on. '' = Auto (container/system tz). Display-only — the DB stays
    UTC — so a page reload (HX-Refresh) re-renders every timestamp in the chosen zone (#145)."""
    form = await request.form()
    db_reader.set_timezone(form.get("timezone", ""))
    return Response(status_code=204, headers={"HX-Refresh": "true"})


@app.post("/api/select-vehicle")
async def select_vehicle(request: Request):
    """Switch which car the whole interface is showing.

    Every scoped read resolves through `_current_vehicle_id()`, so ONE setting moves the trips, the
    charges, the map, the statistics, the model badge and the ability-gated buttons together —
    which is why this reloads the page rather than swapping a panel. A VIN we do not have is
    refused (204 with no refresh) instead of blanking the interface.
    """
    form = await request.form()
    if not db_reader.set_active_vehicle(str(form.get("vin", "") or "")):
        return Response(status_code=204)
    return Response(status_code=204, headers={"HX-Refresh": "true"})


@app.post("/api/settings/password")
async def set_ui_password(request: Request):
    """Turn the login on (or change the password) from the page instead of a compose file.

    Reachable without a session ONLY while none is set — that is the first-run window, and the
    middleware already enforces it: once a password exists this route sits behind the same auth
    gate as everything else. On success the caller is handed a session immediately, so switching
    protection on doesn't bounce the person who just did it to a login screen."""
    form = await request.form()
    if auth.env_password_wins():
        return Response("set in the environment", status_code=409)   # don't pretend otherwise
    pw = form.get("password") or ""
    # Typed twice, and the two must match (#214 @rop12770). With one box a typo is hashed in
    # silence and the machine you set it on keeps working — the browser saved what you actually
    # typed — so the mistake only surfaces on the NEXT device, when there is no longer any way to
    # know what you pressed. Required rather than optional: both forms send it, and a password
    # nobody typed twice is exactly the one that locks its owner out. Enforced here and not only in
    # the browser — the form's `required`/`minlength` are a convenience, not a guarantee.
    if (form.get("password2") or "") != pw:
        return Response("mismatch", status_code=422)
    if not auth.set_password(pw):
        return Response("too short", status_code=422)
    resp = Response(status_code=204, headers={"HX-Refresh": "true"})
    resp.set_cookie(auth.COOKIE, auth.make_token(), max_age=auth.TTL,
                    httponly=True, samesite="strict", path="/")
    return resp


@app.post("/api/settings/dismiss-auth-warning")
async def dismiss_auth_warning(request: Request):
    """Hide the "no password" banner for good. Someone who binds Mate to loopback, or fronts it
    with their own auth, is not wrong — nagging them forever would just teach everyone to ignore
    banners. The Settings card still shows the state."""
    db_reader.set_setting("auth_warning_dismissed", "1")
    return Response(status_code=204, headers={"HX-Refresh": "true"})


@app.post("/api/settings/dismiss-desktop-notice")
async def dismiss_desktop_notice(request: Request):
    """Hide the "records only while open" notice once the user has taken it in.

    No page refresh, unlike the password banner: that one changes what the app IS (protected or
    not), while this only states how it already behaves. Reloading the whole page to remove a
    strip the browser has already dropped would be a visible stutter for nothing."""
    db_reader.set_setting("desktop_notice_dismissed", "1")
    # 200, not 204: htmx performs NO swap on a 204 by design, so hx-swap="delete" never ran and the
    # strip sat there until the next page load. Measured in a browser — the POST went out, answered
    # 204, and the bar stayed. An empty 200 lets the delete happen, which is what the paragraph
    # above wanted: the element goes, the page does not reload.
    return Response(status_code=200)


@app.post("/api/settings/dismiss-beta-notice")
async def dismiss_beta_notice(request: Request):
    """Hide the BetaTester banner once the tester has read it.

    On a phone it sat above the open menu and hid its top entries (@ebagnoli, beta #13) — a warning
    standing between someone and the navigation. Lowering it was the fix; letting it be closed is
    what stops it being in the way at all. Nothing is lost by closing it: the BETA badge next to the
    version says which build this is for as long as the install lives, which is the part a tester
    running two instances side by side actually needs."""
    db_reader.set_setting("beta_notice_dismissed", "1")
    return Response(status_code=200)   # 200, not 204 — see dismiss_desktop_notice


@app.post("/api/settings/desktop-autostart", response_class=HTMLResponse)
async def save_desktop_autostart(request: Request):
    """Record whether the desktop app should start when the user logs in.

    Mate records the answer and does nothing with it. Registering a program to run at login is
    the shell's business and nobody else's: it is a LaunchAgent on macOS and a registry entry on
    Windows, and neither belongs in code that also runs inside Home Assistant. The shell reads
    this setting back — on every launch, and while the app is open — so the same switch drives
    both platforms and changing how registration works never means releasing Mate.

    Why it is worth having at all: the app records only while it is open, and a car does its
    charging overnight. Someone who has to remember to launch Mate will miss exactly the data
    they installed it for."""
    form = await request.form()
    on = str(form.get("desktop_autostart", "")).strip() in ("1", "on", "true")
    db_reader.set_setting("desktop_autostart", "1" if on else "0")
    t = i18n.get_t(db_reader.get_language())
    msg = t("desktop_autostart_on") if on else t("desktop_autostart_off")
    colour = "#22c55e" if on else "#94a3b8"
    return HTMLResponse(f'<span style="color:{colour};font-size:13px">{msg}</span>')


@app.post("/api/settings/desktop-remove", response_class=HTMLResponse)
async def desktop_remove_everything(request: Request):
    """Ask the desktop app to remove itself and everything it keeps — macOS only.

    Mate does not delete anything here. It exits with code 43, and the shell around it does the
    work: that code is the sibling of the 42 Mate already uses to ask for a restart, and the same
    reasoning applies — where the data lives and how an app is removed is the shell's business,
    not the business of code that also runs inside Home Assistant and Docker.

    Why it exists at all: macOS has no uninstaller. An app is dragged to the Bin, and the Bin has
    never heard of Application Support — which is why a Mac accumulates folders belonging to
    programs it no longer has. The only place left to offer "take it all away" is inside the app,
    as its last act. Windows needs none of this: its uninstaller is in Settings ▸ Apps and already
    removes the data, so offering a second route from inside the app would just be confusing.

    Refused anywhere else — no shell means nothing to ask, and Home Assistant and Docker manage
    their own storage."""
    if not os.environ.get("MATE_DESKTOP_VERSION"):
        return HTMLResponse('<span class="text-slate-400 text-xs">—</span>', status_code=400)
    if os.environ.get("MATE_DESKTOP_PLATFORM", "") != "macOS":
        return HTMLResponse('<span class="text-slate-400 text-xs">—</span>', status_code=400)
    t = i18n.get_t(db_reader.get_language())
    # Delayed like the relaunch is, so this response reaches the page before the process goes.
    threading.Timer(1.2, lambda: os._exit(43)).start()
    return HTMLResponse(
        f'<span style="color:#f87171;font-size:13px">{t("desktop_remove_doing")}</span>')


@app.post("/api/settings/password/clear")
async def clear_ui_password(request: Request):
    """Turn the login back off. Only reachable from inside an authenticated session (the gate in
    setup_check), so it can't be used to strip protection from outside."""
    if auth.env_password_wins():
        return Response("set in the environment", status_code=409)
    auth.clear_password()
    return Response(status_code=204, headers={"HX-Refresh": "true"})


# ── HTMX partial ─────────────────────────────────────────────────────────────

@app.get("/api/charging-live", response_class=HTMLResponse)
async def charging_live(request: Request):
    status = db_reader.get_latest_status()
    return templates.TemplateResponse(request, "partials/charging_live.html", _ctx(status=status))


@app.get("/api/battery-card", response_class=HTMLResponse)
async def battery_card(request: Request):
    status = db_reader.get_latest_status()
    return templates.TemplateResponse(request, "partials/battery_card.html", _ctx(status=status))


@app.get("/api/status-card", response_class=HTMLResponse)
async def status_card(request: Request):
    status = db_reader.get_latest_status()
    vehicle, _ = db_reader.get_vehicle()
    return templates.TemplateResponse(request, "partials/status_card.html", _ctx(
        status=status, vehicle=vehicle,
        car_resp=db_reader.command_responsiveness(),
        battery_price=db_reader.current_blended_price(),   # #200 — must match the overview route
    ))


@app.get("/api/v2l-card", response_class=HTMLResponse)
async def v2l_card(request: Request):
    """The Overview's V2L block, refreshed live (every 10 s, matching the V2L poll cadence) so the
    instantaneous power tracks the load during a session — the parent "Last charge" card is static."""
    return templates.TemplateResponse(request, "partials/v2l_card.html",
                                      _ctx(v2l=db_reader.get_v2l_status()))


def _configured_charge_limit() -> int | None:
    """The SoC the current charge will stop at, for the Overview hero's ETA label. None when it is
    not knowable → the template falls back to 100.

    🔴 What the poller stores is the **charging PLAN's** target, not the "charging upper limit" the
    owner drags in the official app — the cloud does not expose that one at all. The library names
    the block for what it is: `config["3"]` maps `percent`→`chargesocSetting`, `isEnable`→
    `chargeScheduleEnabled`, plus the plan's start, end and days.

    So it counts only while the plan is switched ON. With the plan off the car charges to its own
    upper limit and that number means nothing: @ghuaywen-ai's C10 (#252) had the plan OFF, its
    stored SoC at 90 and the car's limit at 100, and the hero read "23h 25m to 90%". `_charge_window`
    below has always applied the same rule to the plan's WINDOW, for the same reason."""
    try:
        if not db_reader.get_charge_schedule_window().get("enabled"):
            return None
        return int(db_reader.get_setting("charge_limit_percent", "") or 0) or None
    except (TypeError, ValueError, AttributeError):
        # get_charge_schedule_window swallows its own database errors and answers a dict,
        # so there is nothing sqlite-shaped to catch here — and sqlite3 is not imported in
        # this module, which would have turned any such error into a NameError.
        return None


def _charge_window() -> str:
    """"HH:MM – HH:MM" for the car's enabled charge window, else "" (#173). Read from the settings
    the poller caches — never a cloud call, so it costs nothing on a page that redraws every 30 s.
    Both times must be present and differ: a 00:00–00:00 window is the cloud's way of saying
    "nothing set", and showing it would be worse than showing nothing."""
    try:
        w = db_reader.get_charge_schedule_window()   # the SELECTED car's plan (#186)
        if not w["enabled"]:
            return ""
        start, end = w["start"], w["end"]
    except Exception:  # noqa: BLE001 — a decoration must never take the page down
        return ""
    if not start or not end or start == end:
        return ""
    return f"{start} – {end}"


@app.get("/api/overview-hero", response_class=HTMLResponse)
async def overview_hero(request: Request):
    """Overview hero card (image + live status chips + quick commands + charging animation).
    Auto-refreshed every 30s by the #hero-card wrapper so the chips and toggle states stay
    current; reads the last polled status from the DB (no extra cloud call)."""
    status = db_reader.get_latest_status()
    vehicle, _ = db_reader.get_vehicle()
    return templates.TemplateResponse(request, "partials/overview_hero.html", _ctx(
        status=status, vehicle=vehicle, charge_limit=_configured_charge_limit(),
    ))


@app.post("/api/refresh", response_class=HTMLResponse)
async def refresh_now(request: Request):
    """On-demand status pull from the Leapmotor cloud (like kerniger's 'Refresh data' button): fetch
    the car's current state RIGHT NOW instead of waiting for the next ~30s poll, then re-render the
    status card. Mate still reads PASSIVELY, so this won't wake a sleeping car — it only skips the
    wait when the car is already awake (e.g. while charging or just used)."""
    import asyncio
    signals = await asyncio.get_event_loop().run_in_executor(None, command_client.get_fresh_signals)
    if signals:
        db_reader.save_fresh_signals(signals)
    # Global button (sidebar) → reload the current page so the fresh state shows wherever the user is.
    return Response(status_code=200, headers={"HX-Refresh": "true"})


@app.get("/api/debug/signals", response_class=JSONResponse)
async def debug_signals():
    """Read-only diagnostic: the car's current RAW signal dict from the cloud (same fetch as the
    Refresh button — no data is stored or changed). Used to reverse-engineer a signal that arrives
    wrong, e.g. GitHub #30: a UK car shown in the sea because its longitude comes through with the
    wrong sign. `gps_signals` isolates the GPS ids so a west-longitude user can paste just those;
    `all_signals` is the full dict for spotting an unmapped sign/hemisphere field."""
    import asyncio
    sig = await asyncio.get_event_loop().run_in_executor(None, command_client.get_fresh_signals)
    if not sig:
        return JSONResponse(
            {"error": "no live signals (car asleep or unreachable) — retry right after using the car"},
            status_code=503)
    # 2/3 are the SIGNED pair, 3724/3725 the unsigned magnitudes (2190/2191 fallbacks). The signed
    # ids were missing from this list — the one endpoint written to debug a sign bug didn't show
    # the sign (#158). `parsed` now goes through the same resolver that writes the position row,
    # so what you read here is what Mate would store.
    gps_ids = ("2", "3", "3724", "3725", "2190", "2191")
    return JSONResponse({
        "gps_signals": {k: sig.get(k) for k in gps_ids if k in sig},
        "parsed": {"latitude":  db_reader._coord_from_signals(sig, "lat"),
                   "longitude": db_reader._coord_from_signals(sig, "lon")},
        "remembered_sign": {"lat": db_reader.get_setting("gps_lat_sign", "unknown"),
                            "lon": db_reader.get_setting("gps_lon_sign", "unknown")},
        "all_signals": sig,
    })


def _diag_pre(text: str, label: str) -> HTMLResponse:
    """An HTMX fragment: a scrollable monospace block of `text` with a Copy button. The copy logic
    lives in a `diagCopy(btn)` JS helper on the settings page — NOT an inline onclick — so we never
    have to escape quotes into the attribute (an earlier inline version broke the HTML)."""
    import html as _html
    t = i18n.get_t(db_reader.get_language())
    return HTMLResponse(
        f'<div data-diag class="mt-3">'
        f'<div class="flex items-center justify-between mb-1">'
        f'<span class="text-[11px] text-slate-500 font-mono">{_html.escape(label)}</span>'
        f'<button type="button" data-copied="{_html.escape(t("diag_copied"))}" onclick="diagCopy(this)" '
        f'class="text-[11px] border border-slate-600 text-slate-300 rounded px-2 py-1 hover:border-brand">'
        f'{_html.escape(t("diag_copy"))}</button>'
        f'</div>'
        f'<pre class="bg-slate-900 border border-slate-700 rounded-lg p-3 text-[11px] text-slate-300 '
        f'font-mono max-h-96 overflow-auto whitespace-pre-wrap break-words">{_html.escape(text)}</pre>'
        f'</div>')


@app.get("/api/diagnostics/logs", response_class=HTMLResponse)
async def diagnostics_logs(which: str = "poller"):
    """Recent lines of the poller/web rotating log file (redacted), as an HTMX fragment."""
    return _diag_pre(diagnostics.read_log_tail(which, lines=200), f"mate-{which}.log")


@app.get("/api/diagnostics/signals", response_class=HTMLResponse)
async def diagnostics_signals_fragment():
    """The car's current raw signal dict (live fetch), as an HTMX fragment for the card."""
    import asyncio
    sig = await asyncio.get_event_loop().run_in_executor(None, command_client.get_fresh_signals)
    text = (json.dumps(sig, indent=2, sort_keys=True) if sig
            else "(no live signals — car asleep or unreachable; retry right after using the car)")
    return _diag_pre(text, "signals.json")


@app.get("/api/diagnostics/bundle")
async def diagnostics_bundle(parts: str = "info,poller,web,signals"):
    """Redacted snapshot as a downloadable .txt the user can attach to a GitHub issue. `parts`
    (comma-separated: info, poller, web, signals) selects which sections to include — chosen via
    the card's checkboxes. VIN/credentials are masked and the raw signals have their GPS
    coordinates stripped → the whole bundle is safe to share publicly. When 'signals' is selected
    we do one live fetch (same as the Refresh button) so the dump reflects the car's current state."""
    sel = [p.strip() for p in parts.split(",") if p.strip()]
    signals = None
    if "signals" in sel:
        import asyncio
        signals = await asyncio.get_event_loop().run_in_executor(None, command_client.get_fresh_signals)
    body = diagnostics.build_bundle(MATE_VERSION, parts=sel, signals=signals)
    # The bundle is now days of logs; ship it zipped (plain text → ~10× smaller) so it stays a small
    # attachment on a GitHub issue. One entry, same redaction already applied to `body`.
    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("leapmotor-mate-diagnostics.txt", body)
    return Response(content=buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": "attachment; filename=leapmotor-mate-diagnostics.zip"})


def _missed_charges_preview_html(t, cands: list[dict]) -> str:
    """Render the missed-charge scan result: a list of what WOULD be added + a confirm
    button, or a 'nothing found' note. Dates shown in local time."""
    import html as _html
    if not cands:
        return f'<div class="text-xs text-slate-400 mt-2">{_html.escape(t("missed_none"))}</div>'
    rows = []
    for c in cands:
        day = db_reader._local_iso(c["started_at"])[:16].replace("T", " ")
        rows.append(
            f'<li class="flex items-center justify-between gap-2 py-1 border-b border-slate-800">'
            f'<span class="text-slate-300">{_html.escape(day)}</span>'
            f'<span class="text-slate-400">{c["start_soc"]:.0f}% → {c["end_soc"]:.0f}% '
            f'· <span class="text-brand">{c["energy_kwh"]:.1f} kWh</span></span></li>')
    btn = (f'<button hx-post="api/diagnostics/missed-charges" hx-target="#missed-result" hx-swap="innerHTML" '
           f'hx-on::after-request="if(event.detail.successful)setTimeout(function(){{location.reload()}},800)" '
           f'class="mt-3 bg-brand text-white text-sm font-semibold rounded-lg px-4 py-2">'
           f'{_html.escape(t("missed_add").format(n=len(cands)))}</button>')
    return (f'<div class="mt-2 text-xs"><div class="text-slate-400 mb-1">{_html.escape(t("missed_found").format(n=len(cands)))}</div>'
            f'<ul class="space-y-0.5">{"".join(rows)}</ul>{btn}</div>')


@app.get("/api/diagnostics/missed-charges", response_class=HTMLResponse)
async def missed_charges_preview(request: Request):
    """Dry-run scan for charges that happened while the car was asleep before live
    reconstruction existed (GitHub #35). Shows what WOULD be added — nothing is written."""
    import asyncio
    t = i18n.get_t(db_reader.get_language())
    cands = await asyncio.get_event_loop().run_in_executor(
        None, lambda: db_reader.scan_missed_charges(apply=False))
    return HTMLResponse(_missed_charges_preview_html(t, cands))


@app.post("/api/diagnostics/missed-charges", response_class=HTMLResponse)
async def missed_charges_apply(request: Request):
    """Apply the missed-charge scan — insert the candidates as reconstructed charges.
    Idempotent (re-running creates no duplicates)."""
    import asyncio
    t = i18n.get_t(db_reader.get_language())
    created = await asyncio.get_event_loop().run_in_executor(
        None, lambda: db_reader.scan_missed_charges(apply=True))
    return HTMLResponse(f'<span style="color:#22c55e">✓ {t("missed_added").format(n=len(created))}</span>')


# ── Command routes ────────────────────────────────────────────────────────────

_COMMANDS = {
    "lock":              command_client.lock,
    "unlock":            command_client.unlock,
    "open_trunk":        command_client.open_trunk,
    "close_trunk":       command_client.close_trunk,
    "find_car":          command_client.find_car,
    "ac_on":             command_client.ac_on,
    "ac_off":            command_client.ac_off,
    "quick_cool":        command_client.quick_cool,
    "quick_heat":        command_client.quick_heat,
    "quick_vent":        command_client.quick_vent,
    "windshield_defrost":command_client.windshield_defrost,
    "recirc_toggle":     command_client.recirc_toggle,
    "open_windows":      command_client.open_windows,
    "close_windows":     command_client.close_windows,
    "battery_preheat":   command_client.battery_preheat,
    "open_sunshade":     command_client.open_sunshade,
    "close_sunshade":    command_client.close_sunshade,
    # Surfaced on the Charges page (charge-limit card) + MQTT; B10-confirmed actuating.
    "unlock_charger":    command_client.unlock_charger,
    # Surfaced as comfort toggles (Commands page) + MQTT buttons; work on the B10 via
    # the kerniger payloads (since 1.11.4).
    "steering_heat_on":  command_client.steering_heat_on,
    "steering_heat_off": command_client.steering_heat_off,
    "mirror_heat_on":    command_client.mirror_heat_on,
    "mirror_heat_off":   command_client.mirror_heat_off,
    "seat_heat_driver_on":  command_client.seat_heat_driver_on,
    "seat_heat_driver_off": command_client.seat_heat_driver_off,
    "seat_vent_driver_on":  command_client.seat_vent_driver_on,
    "seat_vent_driver_off": command_client.seat_vent_driver_off,
    # Staged but NOT surfaced in any UI — the B10 accepts these yet doesn't actuate
    # them (like the old A/C-off). Kept wired so they can be exposed instantly if a
    # future leapmotor-api / vehicle update makes them work.
    "battery_preheat_off": command_client.battery_preheat_off,
    "sentry_on":         command_client.sentry_on,
    "sentry_off":        command_client.sentry_off,
}

@app.get("/api/cmd-grid", response_class=HTMLResponse)
async def cmd_grid(request: Request):
    status = db_reader.get_latest_status()
    vehicle, _ = db_reader.get_vehicle()
    vin = vehicle.get("vin") if vehicle else None
    comfort = _comfort_rows(vin, (vehicle or {}).get("car_type", ""))
    return templates.TemplateResponse(request, "partials/cmd_grid.html", _ctx(
        status=status, comfort=comfort, **_wins_ctx(),
        ac_off_shown=capability_profile.command_shown(vin, "climate_off"),
        is_t03=(vehicle.get("car_type") or "").upper() == "T03" if vehicle else False,
    ))


@app.post("/api/seat/{func}/{position}", response_class=HTMLResponse)
async def set_seat(request: Request, func: str, position: str):
    """Seat heat/vent level (0–3) via the kerniger payload. The slider posts ?level=N."""
    form = await request.form()
    try:
        level = int(form.get("level", 0))
    except (TypeError, ValueError):
        level = 0
    import asyncio
    ok, msg = await asyncio.get_event_loop().run_in_executor(
        None, lambda: command_client.seat_comfort(func, position, level))
    if ok:
        side = "driver" if position == "driver" else "passenger"
        _veh, _ = db_reader.get_vehicle()
        _optimistic_comfort(_veh.get("vin") if _veh else None,
                            {f"seat_{func}_{side}": max(0, min(level, 3))})
        _t = i18n.get_t(db_reader.get_language())
        txt = f"{_t('lvl_abbr')} {level}" if level > 0 else _t('lvl_off')
        return HTMLResponse(f'<span style="color:#22c55e">✓ {txt}</span>')
    return HTMLResponse(_cmd_error_html(msg))


@app.post("/api/windows", response_class=HTMLResponse)
async def set_windows_api(request: Request):
    """Open all windows to a 0–100% position (slider). cmd 230 is global — all four move together."""
    form = await request.form()
    try:
        pct = max(0, min(int(form.get("pct", 0)), 100))
    except (TypeError, ValueError):
        pct = 0
    import asyncio
    ok, msg = await asyncio.get_event_loop().run_in_executor(
        None, lambda: command_client.set_windows(pct))
    if ok:
        db_reader.write_optimistic_status({"windows_open": 1 if pct > 0 else 0})
        # Remember the commanded position: the B10 can't report its real window % (dead sensor),
        # so the slider reflects what was last SET rather than guessing from open/closed (#62).
        db_reader.set_setting("windows_cmd_pct", str(pct))
        return HTMLResponse(f'<span style="color:#22c55e">✓ {pct}%</span>')
    return HTMLResponse(_cmd_error_html(msg))


@app.post("/api/climate-temp", response_class=HTMLResponse)
async def set_climate_temp_api(request: Request):
    """Set the climate target temperature (18–32 °C); auto-picks cool/heat vs the cabin temp."""
    form = await request.form()
    try:
        temp = int(form.get("temp", 0))
    except (TypeError, ValueError):
        return HTMLResponse('<span style="color:#ef4444">✗</span>', status_code=400)
    inside = (db_reader.get_latest_status() or {}).get("inside_temp")
    import asyncio
    ok, msg = await asyncio.get_event_loop().run_in_executor(
        None, lambda: command_client.set_climate_temp(temp, inside))
    if ok:
        import time
        db_reader.boost_selected_car(60)   # re-poll THIS car within seconds, not 30s
        return HTMLResponse(f'<span style="color:#22c55e">✓ {max(18, min(temp, 32))}°C</span>')
    return HTMLResponse(_cmd_error_html(msg))


@app.post("/api/climate-fan", response_class=HTMLResponse)
async def set_fan_level_api(request: Request):
    """Set the A/C fan level 1-7 (signal 1941), preserving the current mode / recirc / temp."""
    form = await request.form()
    try:
        level = max(1, min(int(form.get("level", 0)), 7))
    except (TypeError, ValueError):
        return HTMLResponse('<span style="color:#ef4444">✗</span>', status_code=400)
    import asyncio
    ok, msg = await asyncio.get_event_loop().run_in_executor(
        None, lambda: command_client.set_fan_level(level))
    if ok:
        import time
        db_reader.boost_selected_car(60)   # re-poll THIS car within seconds, not 30s
        return HTMLResponse(f'<span style="color:#22c55e">✓ {level}/7</span>')
    return HTMLResponse(_cmd_error_html(msg))


@app.post("/api/climate-recirc", response_class=HTMLResponse)
async def set_recirc_api(request: Request):
    """Toggle air recirculation (signal 1943; on = recirculate, off = fresh air), preserving the
    current mode / fan / temp."""
    form = await request.form()
    on = str(form.get("on", "")).lower() in ("1", "true", "on")
    import asyncio
    ok, msg = await asyncio.get_event_loop().run_in_executor(
        None, lambda: command_client.set_recirc(on))
    if ok:
        import time
        db_reader.boost_selected_car(60)   # re-poll THIS car within seconds, not 30s
        return HTMLResponse(f'<span style="color:#22c55e">✓</span>')
    return HTMLResponse(_cmd_error_html(msg))


@app.post("/api/poll-settings", response_class=HTMLResponse)
async def poll_settings(request: Request):
    """Save the user-tunable poll cadence (parked / driving seconds). The poller picks
    these up live on its next cycle."""
    form = await request.form()
    try:
        parked = max(10, min(int(form.get("poll_parked", 30)), 600))
        driving = max(10, min(int(form.get("poll_driving", 10)), 60))
    except (ValueError, TypeError):
        return HTMLResponse('<span style="color:#ef4444">Invalid value</span>', status_code=400)
    db_reader.set_setting("poll_parked", str(parked))
    db_reader.set_setting("poll_driving", str(driving))
    return HTMLResponse(f'<span style="color:#22c55e">✓ {parked}s / {driving}s</span>')


@app.post("/api/settings/charge-detect", response_class=HTMLResponse)
async def charge_detect_settings(request: Request):
    """Save the charge-detection current floor (A). Below this the plugged-in current
    is treated as idle/noise. The poller applies it live on its next cycle."""
    form = await request.form()
    try:
        amps = max(0.5, min(float(form.get("charge_detect_min_a", 2.0)), 16.0))
    except (ValueError, TypeError):
        return HTMLResponse('<span style="color:#ef4444">Invalid value</span>', status_code=400)
    db_reader.set_setting("charge_detect_min_a", str(amps))
    return HTMLResponse(f'<span style="color:#22c55e">✓ {amps:g} A</span>')


# Defaults + safe ranges for the Advanced tunables. The reconstruction floor is
# clamped to >=1.0 on purpose: below that, normal SoC sensor noise / BMS recalibration
# while parked would invent phantom charges.
_ADVANCED_DEFAULTS = {
    "charge_reconstruct_min_pct": (2.0, 1.0, 10.0),
    "vampire_min_drop_pct":       (0.2, 0.1, 2.0),
    "vampire_min_hours":          (1.0, 1.0, 12.0),
    "charge_dc_min_kw":           (11.0, 11.0, 32.0),
    "soh_temp_min_c":             (15.0, 0.0, 25.0),
}


@app.post("/api/settings/advanced", response_class=HTMLResponse)
async def advanced_settings(request: Request):
    """Save the Advanced tunables (reconstruction floor, vampire-drain noise floor,
    AC/DC power threshold). The poller re-reads them live on its next cycle; the
    vampire + AC/DC values are read at compute/finalize time. A `reset` field restores
    every default."""
    form = await request.form()
    t = i18n.get_t(db_reader.get_language())
    if form.get("reset"):
        for key, (default, _lo, _hi) in _ADVANCED_DEFAULTS.items():
            db_reader.set_setting(key, str(default))
        return HTMLResponse(f'<span style="color:#22c55e">{t("adv_reset_done")}</span>')
    try:
        for key, (default, lo, hi) in _ADVANCED_DEFAULTS.items():
            if key in form:
                val = max(lo, min(float(form.get(key, default)), hi))
                db_reader.set_setting(key, str(val))
    except (ValueError, TypeError):
        return HTMLResponse('<span style="color:#ef4444">Invalid value</span>', status_code=400)
    return HTMLResponse(f'<span style="color:#22c55e">{t("adv_saved")}</span>')


@app.post("/api/settings/capacity", response_class=HTMLResponse)
async def capacity_settings(request: Request):
    """Override the usable battery capacity used for energy calculations (#35). The
    first override snapshots the current value as the SoH reference so the health page
    keeps measuring against the as-new spec, not the new (possibly aged) figure."""
    form = await request.form()
    t = i18n.get_t(db_reader.get_language())
    try:
        kwh = max(10.0, min(float(form.get("battery_capacity_kwh", 0)), 200.0))
    except (ValueError, TypeError):
        return HTMLResponse('<span style="color:#ef4444">Invalid value</span>', status_code=400)
    # The SoH reference is now correctable. It used to be snapshotted once and then frozen for
    # good, with no way to see or change it — so a reference caught from the wrong number (the two
    # defaults this form and the code carried, #221) put battery health above 100% for ever, and
    # the only figure that could explain it was invisible. Sent empty → the old behaviour: keep
    # what is there, or snapshot the value being replaced.
    try:
        _typed = str(form.get("battery_nominal_kwh", "") or "").strip()
        nominal = max(10.0, min(float(_typed), 200.0)) if _typed else None
    except (ValueError, TypeError):
        return HTMLResponse('<span style="color:#ef4444">Invalid value</span>', status_code=400)
    if nominal is not None:
        db_reader.set_setting("battery_capacity_nominal_kwh", str(nominal))
    elif not db_reader.get_setting("battery_capacity_nominal_kwh", ""):
        db_reader.set_setting("battery_capacity_nominal_kwh",
                              db_reader.get_setting("battery_capacity_kwh", str(kwh)))
    db_reader.set_setting("battery_capacity_kwh", str(kwh))
    # Mirror onto the car's own row so the poller's per-vehicle energy math honours the override
    # (it reads vehicles.capacity_kwh, not the global setting). No-op on single-car beyond keeping
    # the two in sync; multi-car will target the selected vehicle.
    _nom = db_reader.get_setting("battery_capacity_nominal_kwh", "")
    db_reader.set_vehicle_capacity_current(kwh, float(_nom) if _nom else None)
    # Somebody has now chosen this car's pack, which is the question the "never set up" strip asks.
    # Before v3.14.1 only the wizard could answer it, so a car corrected properly from here went on
    # being accused — @cookingeek, on the first install with two real cars.
    db_reader.mark_vehicle_configured()
    return HTMLResponse(f'<span style="color:#22c55e">✓ {kwh:g} kWh</span>')


_BOOST_DEFAULT_S = 300   # 5 min — covers the "got in the car → started driving" window


@app.api_route("/api/boost", methods=["GET", "POST"])
async def boost(seconds: int = _BOOST_DEFAULT_S):
    """Trigger fast (10s) polling for a window, so the poller catches a trip start that
    would otherwise be missed during deep sleep. Meant to be called when you get in the
    car (e.g. an iPhone Bluetooth shortcut, relayed by HA on the LAN — Mate stays local).
    Coordinated with the poller via settings['boost_until']."""
    import time
    seconds = max(30, min(int(seconds or _BOOST_DEFAULT_S), 1800))
    until = time.time() + seconds
    db_reader.set_setting("boost_until", str(until))
    return {"status": "boost on", "seconds": seconds}


@app.get("/api/charge-plan")
async def get_charge_plan():
    import asyncio
    plan = await asyncio.get_event_loop().run_in_executor(None, command_client.get_charge_plan)
    return plan or {}


_energy_cache: dict = {"data": None, "ts": 0.0}


@app.get("/api/energy-breakdown", response_class=HTMLResponse)
async def energy_breakdown(request: Request, refresh: int = 0):
    """Last-week energy split (driving / A/C / other) as an HTML partial. Cached 6h
    (weekly data changes slowly) to avoid an API call on every Statistics page load."""
    import time, asyncio
    if refresh or not _energy_cache["data"] or time.time() - _energy_cache["ts"] >= 6 * 3600:
        data = await asyncio.get_event_loop().run_in_executor(None, command_client.get_energy_breakdown)
        if data:
            _energy_cache["data"] = data
            _energy_cache["ts"] = time.time()
    return templates.TemplateResponse(request, "partials/energy_breakdown.html", _ctx(eb=_energy_cache["data"]))


_rank_cache: dict = {"data": None, "ts": 0.0}


@app.get("/api/consumption-rank", response_class=HTMLResponse)
async def consumption_rank(request: Request, refresh: int = 0):
    """6-week consumption trend (kWh/100km) + driver ranking, as an HTML partial. Cached 6h."""
    import time, asyncio
    if refresh or not _rank_cache["data"] or time.time() - _rank_cache["ts"] >= 6 * 3600:
        data = await asyncio.get_event_loop().run_in_executor(None, command_client.get_consumption_rank)
        if data:
            _rank_cache["data"] = data
            _rank_cache["ts"] = time.time()
    return templates.TemplateResponse(request, "partials/consumption_rank.html", _ctx(cr=_rank_cache["data"]))


_cum_cache: dict = {"data": None, "ts": 0.0}


@app.get("/api/cumulative-summary", response_class=HTMLResponse)
async def cumulative_summary(request: Request, refresh: int = 0):
    """Since-delivery cumulative totals from the cloud (total energy incl. parked, mileage, lifetime
    kWh/100km, driving vs parked split), as an HTML partial. Lifetime data → cached 6h."""
    import time, asyncio
    if refresh or not _cum_cache["data"] or time.time() - _cum_cache["ts"] >= 6 * 3600:
        data = await asyncio.get_event_loop().run_in_executor(None, command_client.get_cumulative_summary)
        if data is not None:
            _cum_cache["data"] = data
            _cum_cache["ts"] = time.time()
    return templates.TemplateResponse(request, "partials/cumulative_summary.html", _ctx(cs=_cum_cache["data"]))


_period_cache: dict = {}


@app.get("/api/plugin-consumption", response_class=HTMLResponse)
async def plugin_consumption(request: Request, refresh: int = 0):
    """The car's OWN two consumptions — kWh/100 km and L/100 km — over ITS OWN window.

    getPlugInLastNweeks100kmEC takes no date range: the request carries the VIN and nothing else,
    so unlike getEC this cannot be asked for "July". What it answers is the six-week block the
    official app puts under "last 6 weeks", and the figures match that screen to the decimal
    (@pdifeo's C10: 11.1 kWh + 1.6 L, ours: 20.0 + 0.0). Confirmed on a range-extender bundle and
    on our own BEV — same shape on both, fuel simply zero on the electric car.

    Its own window, said out loud on the card, because a number borrowed from another period and
    printed beside monthly ones is how two right figures start contradicting each other."""
    import time, asyncio
    key = "plugin6w"
    c = _period_cache.get(key)
    if refresh or not c or time.time() - c["ts"] >= 1800:
        data = await asyncio.get_event_loop().run_in_executor(
            None, command_client.get_plugin_consumption)
        if data is not None:
            _period_cache[key] = {"data": data, "ts": time.time()}
        pc = data if data is not None else (c["data"] if c else None)
    else:
        pc = c["data"]
    return templates.TemplateResponse(request, "partials/plugin_consumption.html", _ctx(pc=pc))


def _enrich_eb_with_trip_totals(eb: "dict | None", begin_ts: int, end_ts: int) -> "dict | None":
    """Add local distance/duration/average-kWh-per-100km to a getEC breakdown, computed from
    trips in the SAME window — mirrors the car's own "since last charge" screen (Distanza/Durata/
    Media shown next to the same Guida/AC/Altro split). No-op when there's no energy to average.

    Guard (GitHub #105): the cloud getEC total covers the car's WHOLE life, Mate's trips table
    only starts at install. A window that begins before the first recorded trip would pair
    all-of-the-car's energy with a fraction of its distance → a nonsense average (177.7 kWh/100km
    in the report). For such windows the split is returned WITHOUT distance/duration/average;
    `trips_since` carries the first-trip date so the template can say why. 1 day of slack keeps
    the Trips-page all-time card (window begins at the first trip's local midnight) enriched."""
    if not eb or not eb.get("total_kwh"):
        return eb
    first_ts = db_reader.get_first_trip_ts()
    if first_ts is not None and begin_ts < first_ts - 86400:
        from datetime import datetime
        d = datetime.fromtimestamp(first_ts, tz=db_reader._local_tz())
        return {**eb, "trips_since": d.strftime("%d/%m/%Y")}
    tot = db_reader.get_trip_totals_between(begin_ts, end_ts)
    dist_km = tot.get("distance_km") or 0
    eb = {**eb, "distance_km": dist_km, "duration_min": tot.get("duration_min") or 0}
    if dist_km > 0:
        eb["avg_kwh100"] = round(eb["total_kwh"] / dist_km * 100, 1)
    # The petrol half of the SAME window (@michapr, beta #11). getPlugIn gives both energies
    # measured by the car but only over its own six weeks; a period the reader picked has to be
    # answered from the trips. L/100 km over the generator-on distance, not the whole window —
    # spreading the litres across electric kilometres prints a figure no REEV owner recognises.
    _f = db_reader.get_fuel_totals_between(begin_ts, end_ts)
    if _f.get("fuel_l"):
        eb["fuel_l"] = _f["fuel_l"]
        eb["fuel_engine_km"] = _f["engine_km"]
        # Over the window's WHOLE distance, the basis the car itself uses (getPlugIn's oc100km) —
        # the same figure sits a card above, and two different denominators under one label is how
        # a correct number gets reported as a bug (@michapr, beta #23).
        if dist_km > 0:
            eb["fuel_l_100km"] = round(_f["fuel_l"] / dist_km * 100, 1)
    return db_reader.flag_short_cloud_total(eb, tot, dist_km)




def _clamp_begin_to_first_trip(begin_ts: int) -> int:
    """#121 — align a getEC window's start to the first-ever recorded trip. The cloud getEC covers
    days before Mate was installed (the car reports to the cloud on its own), but Mate's trips don't,
    so a calendar window that begins before the first trip (a fresh install mid-month) paired
    full-window energy with partial trip distance and the average got blanked ("—"). Clamping the
    start makes cloud energy and trip distance span the SAME period, so the real getEC average shows.
    No-op once the first trip predates the window (max keeps the given start) — so an established
    user's normal month is untouched."""
    first_ts = db_reader.get_first_trip_ts()
    return max(begin_ts, first_ts) if first_ts is not None else begin_ts


@app.get("/api/energy-period", response_class=HTMLResponse)
async def energy_period(request: Request, period: str = "", start: str = "", end: str = "",
                         month: str = "", refresh: int = 0):
    """Energy split (driving / A/C / other) for a CURRENT period (day|week|month) OR a custom
    date range [start,end] (YYYY-MM-DD; day-granular — the cloud has no finer reliable bound).
    Reuses the shared energy_breakdown partial, with the label adapted to the period. The split
    is the DRIVING-session figure (same as the official app); parked/standby energy isn't in it.
    `month` ('YYYY-MM') optionally overrides `period=month` to a SPECIFIC month (e.g. the one the
    Report page has navigated to) instead of the real current one — same day-by-day-growing /
    full-month bounds as the Report page's own driving tiles. Cached 30 min per resolved window."""
    import time, asyncio
    from datetime import datetime, timedelta, timezone
    t = i18n.get_t(db_reader.get_language())
    tz = db_reader._local_tz()
    now = datetime.now(timezone.utc).astimezone(tz)
    try:
        if period == "month" and month:
            b_ts, e_ts = _report_month_bounds(month, now)
            b, e = datetime.fromtimestamp(b_ts, tz=tz), datetime.fromtimestamp(e_ts, tz=tz)
            label, key = i18n.fmt_month_year(db_reader.get_language(), b), f"p:month:{month}:{e_ts}"
        elif start and end:
            b = datetime.strptime(start, "%Y-%m-%d").replace(
                hour=0, minute=0, second=0, microsecond=0, tzinfo=now.tzinfo)
            e = datetime.strptime(end, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59, microsecond=0, tzinfo=now.tzinfo)
            if b > e:
                b, e = e.replace(hour=0, minute=0, second=0), b.replace(hour=23, minute=59, second=59)
            if e > now:
                e = now
            _fd = lambda s: datetime.strptime(s, "%Y-%m-%d").strftime("%d/%m/%Y")
            label = f"{_fd(start)} → {_fd(end)}"
            key = f"r:{start}:{end}"
        elif period == "week":
            b = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
            e, label, key = now, t("ec_this_week"), f"p:week:{now.strftime('%Y-%m-%d')}"
        elif period == "month":
            b = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            e, label, key = now, t("ec_this_month"), f"p:month:{now.strftime('%Y-%m')}"
        elif period == "alltime":
            first = db_reader.get_first_trip_date()
            if first:
                b = datetime.strptime(first, "%Y-%m-%d").replace(
                    hour=0, minute=0, second=0, microsecond=0, tzinfo=now.tzinfo)
            else:
                b = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=730)
            e, label, key = now, t("ec_alltime_title"), f"p:all:{first or 'na'}:{now.strftime('%Y-%m-%d')}"
        else:
            b = now.replace(hour=0, minute=0, second=0, microsecond=0)
            e, label, key = now, t("ec_today"), f"p:day:{now.strftime('%Y-%m-%d')}"
        begin_ts, end_ts = int(b.timestamp()), int(e.timestamp())
    except Exception:
        return templates.TemplateResponse(request, "partials/energy_breakdown.html", _ctx(eb=None, eb_label=None))
    begin_ts = _clamp_begin_to_first_trip(begin_ts)   # #121: month/custom windows before the first trip
    c = _period_cache.get(key)
    if refresh or not c or time.time() - c["ts"] >= 1800:
        data = await asyncio.get_event_loop().run_in_executor(
            None, command_client.get_energy_breakdown_range, begin_ts, end_ts)
        # Never cache a None: a transient cloud miss must not poison the slot for 30 min — the next
        # load retries. Keep the last good value if this fetch missed. (Genuinely-empty windows just
        # re-query each load; that's rare and correct.)
        if data is not None:
            _period_cache[key] = {"data": data, "ts": time.time()}
        eb = data if data is not None else (c["data"] if c else None)
    else:
        eb = c["data"]
    eb = _enrich_eb_with_trip_totals(eb, begin_ts, end_ts)
    return templates.TemplateResponse(request, "partials/energy_breakdown.html",
                                      _ctx(eb=eb, eb_label=label))


@app.get("/api/energy-since-charge", response_class=HTMLResponse)
async def energy_since_charge(request: Request, refresh: int = 0):
    """Driving energy split (getEC) for the window [end of the last completed charge, now] —
    i.e. everything driven since the battery was last topped up. Same live cloud query +
    partial as the day/week/month/alltime period views, just with the charge's end as the
    lower bound instead of a calendar boundary. Cached 30 min, keyed to that end timestamp so
    a newly-finished charge starts a fresh window automatically (no stale cache to clear)."""
    import time, asyncio
    from datetime import datetime, timezone
    t = i18n.get_t(db_reader.get_language())
    last_charge_end = db_reader.get_last_charge_end()
    if last_charge_end is None:
        return templates.TemplateResponse(request, "partials/energy_breakdown.html",
                                          _ctx(eb=None, eb_label=t("ec_since_charge_title")))
    now = datetime.now(timezone.utc).astimezone(db_reader._local_tz())
    begin_ts, end_ts = int(last_charge_end.timestamp()), int(now.timestamp())
    label = f"{t('ec_since_charge_title')} · {last_charge_end.strftime('%d/%m %H:%M')}"
    key = f"p:sincecharge:{begin_ts}"
    c = _period_cache.get(key)
    if refresh or not c or time.time() - c["ts"] >= 1800:
        data = await asyncio.get_event_loop().run_in_executor(
            None, command_client.get_energy_breakdown_range, begin_ts, end_ts)
        if data is not None:
            _period_cache[key] = {"data": data, "ts": time.time()}
        eb = data if data is not None else (c["data"] if c else None)
    else:
        eb = c["data"]
    eb = _enrich_eb_with_trip_totals(eb, begin_ts, end_ts)
    return templates.TemplateResponse(request, "partials/energy_breakdown.html",
                                      _ctx(eb=eb, eb_label=label))


def _report_month_bounds(month: str, now: "datetime") -> "tuple[int, int]":
    """[begin_ts, end_ts] (epoch seconds) for a 'YYYY-MM' report month: begin = the 1st at
    00:00 local; end = "now" while the month is still in progress (so the getEC total grows
    day by day), or the month's last moment once it's closed. `now` must be tz-aware in the
    same local tz as `month` is interpreted in."""
    import calendar
    from datetime import datetime
    y, mo = int(month[:4]), int(month[5:7])
    b = datetime(y, mo, 1, tzinfo=now.tzinfo)
    last_day = calendar.monthrange(y, mo)[1]
    month_end = datetime(y, mo, last_day, 23, 59, 59, tzinfo=now.tzinfo)
    e = min(now, month_end)
    return int(b.timestamp()), int(e.timestamp())


@app.get("/api/report-driving", response_class=HTMLResponse)
async def report_driving(request: Request, month: str, refresh: int = 0):
    """Consumo medio + Energia consumata for the Monthly Report's driving tiles, sourced from the
    SAME live getEC query as the Statistics/Report period cards — so "today" (both here and in the
    getEC card above) always shows the SAME figure, instead of this section's old per-trip local sum
    (which silently differs whenever a trip fell back to the SoC estimate). `month` = 'YYYY-MM', the
    one the Report page is already showing (always passed explicitly — never guessed here). Cached
    30 min per month, same as the other period cards."""
    import time, asyncio
    from datetime import datetime, timezone
    tz = db_reader._local_tz()
    try:
        now = datetime.now(timezone.utc).astimezone(tz)
        begin_ts, end_ts = _report_month_bounds(month, now)
    except (ValueError, TypeError):
        return templates.TemplateResponse(request, "partials/report_driving_energy.html", _ctx(eb=None))
    begin_ts = _clamp_begin_to_first_trip(begin_ts)   # #121: a first partial month starts before the first trip
    key = f"p:reportmonth:{month}:{end_ts}"
    c = _period_cache.get(key)
    if refresh or not c or time.time() - c["ts"] >= 1800:
        data = await asyncio.get_event_loop().run_in_executor(
            None, command_client.get_energy_breakdown_range, begin_ts, end_ts)
        if data is not None:
            _period_cache[key] = {"data": data, "ts": time.time()}
        eb = data if data is not None else (c["data"] if c else None)
    else:
        eb = c["data"]
    eb = _enrich_eb_with_trip_totals(eb, begin_ts, end_ts)
    return templates.TemplateResponse(request, "partials/report_driving_energy.html", _ctx(eb=eb))


def _car_picture_cache_path() -> str:
    db_path = os.environ.get("DB_PATH", "leapmotor_mate.db")
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), "car_picture.png")


def _car_picture_pkg_path() -> str:
    """The layer package of the SELECTED car. The cloud serves it per VIN, so the cache has to be
    per VIN too: one `car_picture_pkg.zip` for the whole install meant two cars overwrote each
    other's package on every switch, and the Overview showed whichever had been fetched last
    (@cookingeek, the first install with two real cars). Falls back to the shared name when no car
    is known yet, which is also every single-car install's old file — nothing re-downloads for
    people with one Leapmotor."""
    db_path = os.environ.get("DB_PATH", "leapmotor_mate.db")
    vin = _selected_vin_for_assets()
    name = f"car_picture_pkg_{vin}.zip" if vin else "car_picture_pkg.zip"
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), name)


def _selected_vin_for_assets() -> str:
    """Lowercased VIN of the car in the picker, or "" — and "" also when there is only one car, so
    a single-car install keeps using the files it already has on disk."""
    try:
        cars = db_reader.get_vehicles()
        if len(cars) < 2:
            return ""
        v, _ = db_reader.get_vehicle()
        return ((v or {}).get("vin") or "").strip().lower()
    except Exception:
        return ""


# Composed images memoised by body-state signature so a 30s hero refresh on an unchanged state
# doesn't recomposite. Cleared when the package is re-downloaded.
_car_image_memo: dict = {}
# The hero <img> carries a ?v=<state> token, so each distinct state is a distinct URL → a state
# change is a fresh fetch while an unchanged state is served from the browser cache (no re-download
# on every 30s hero refresh).
_CAR_IMG_CACHE = {"Cache-Control": "max-age=300"}

# Re-download the car-picture package once after each (re)start — so a colour changed on the car since
# it was first cached gets picked up (#143: the cloud has the new colour, but Mate had cached the old
# render forever, refreshing only on a manual ?refresh=1). Cheap: at most one cloud fetch per boot, and
# it keeps showing the cached image if the cloud is unreachable at that moment. Mate updates often, so
# "once per (re)start" refreshes the image regularly without any timer or continuous polling.
_car_pic_boot_refresh = True

# Back-off after a FAILED package download. The `os.path.exists(pkg_path)` short-circuit below can
# only save a cloud call once a package has been cached at least once — so on an install where the
# download has never succeeded, every single request went back to the cloud: two attempts, each
# resetting the session, on every 30s hero refresh (a failed fetch answers 404, which the browser
# doesn't cache, so it asks again). On an account the cloud is refusing, that is a login storm, and
# the cloud answers a login storm with the `code 39` throttle — @arnolds77 (#177) logged 42 session
# resets and 35 refusals in 45 minutes. The image is decoration; it must never hammer the account
# the rest of Mate depends on. A manual ?refresh=1 always goes now — that's the user asking.
_CAR_PIC_RETRY_S = 600.0
_car_pic_retry_after = 0.0


@app.get("/api/car-picture")
async def car_picture(refresh: int = 0):
    """Serve the owner's vehicle image — composed LIVE from the per-vehicle layer package to reflect
    the current state (charge cable, charging animation, trunk), like the official app. The package
    ZIP is cached to disk; a manual ?refresh=1 OR the first request after each restart re-downloads it,
    so a colour changed on the car gets picked up (#143). Falls back to the cached package, then its
    static render, then the legacy PNG, on any problem — so the Overview never breaks."""
    global _car_pic_boot_refresh, _car_pic_retry_after
    import asyncio
    pkg_path = _car_picture_pkg_path()
    # Re-download from the cloud on a manual ?refresh=1, OR once after each (re)start (#143 — the car's
    # colour may have changed since the package was first cached). Every other request serves the disk
    # cache, so this is NOT a continuous refresh: at most one cloud fetch per boot.
    want_fresh = bool(refresh) or _car_pic_boot_refresh
    pkg = None
    if not want_fresh and os.path.exists(pkg_path):
        try:
            with open(pkg_path, "rb") as f:
                pkg = f.read()
        except OSError:
            pkg = None
    if pkg is None:
        fresh_pkg = None
        if refresh or time.monotonic() >= _car_pic_retry_after:      # see _CAR_PIC_RETRY_S above
            fresh_pkg = await asyncio.get_event_loop().run_in_executor(None, command_client.get_car_picture_package)
            _car_pic_retry_after = 0.0 if fresh_pkg else time.monotonic() + _CAR_PIC_RETRY_S
        if want_fresh:
            # Attempted the once-per-boot refresh (success or not) → don't re-poll a down cloud on
            # every request; the next real chance is the next restart (or a manual ?refresh=1).
            _car_pic_boot_refresh = False
        if fresh_pkg:
            pkg = fresh_pkg
            try:
                with open(pkg_path, "wb") as f:
                    f.write(pkg)
            except OSError:
                pass
            _car_image_memo.clear()
            car_image.clear_cache()
        elif want_fresh and os.path.exists(pkg_path):
            # Forced/boot refresh but the cloud is unreachable → keep the cached image, don't go blank.
            try:
                with open(pkg_path, "rb") as f:
                    pkg = f.read()
            except OSError:
                pkg = None
    if not pkg:
        legacy = _car_picture_cache_path()
        if os.path.exists(legacy):
            return FileResponse(legacy, media_type="image/png")
        return Response(status_code=404)

    status = db_reader.get_latest_status() or {}
    # The VIN is part of the key, not decoration: two cars parked with everything shut share a body
    # state, so a memo keyed on the state alone served the second car the first one's picture.
    sig = (_selected_vin_for_assets(),) + tuple(bool(status.get(k)) for k in (
        "plug_connected", "charging", "trunk_open",
        "door_driver_open", "door_passenger_open", "door_rear_left_open", "door_rear_right_open",
        "window_fl_open", "window_rl_open"))
    if not refresh and sig in _car_image_memo:
        body, mime = _car_image_memo[sig]
        return Response(content=body, media_type=mime, headers=_CAR_IMG_CACHE)
    try:
        body, mime = await asyncio.get_event_loop().run_in_executor(None, car_image.compose, pkg, status)
        _car_image_memo[sig] = (body, mime)
        return Response(content=body, media_type=mime, headers=_CAR_IMG_CACHE)
    except Exception as e:
        log.warning("Live car image compose failed (%s) — using the static render", e)
        static = car_image.static_image(pkg)
        if static:
            return Response(content=static, media_type="image/png")
        legacy = _car_picture_cache_path()
        if os.path.exists(legacy):
            return FileResponse(legacy, media_type="image/png")
        return Response(status_code=404)


@app.post("/api/charge-limit", response_class=HTMLResponse)
async def set_charge_limit(request: Request):
    form = await request.form()
    try:
        percent = int(form.get("percent", 0))
    except (ValueError, TypeError):
        return HTMLResponse('<span style="color:#ef4444">Invalid value</span>', status_code=400)
    if not (50 <= percent <= 100):
        return HTMLResponse('<span style="color:#ef4444">Must be 50–100%</span>', status_code=400)
    import asyncio
    ok, msg = await asyncio.get_event_loop().run_in_executor(
        None, lambda: command_client.set_charge_limit(percent)
    )
    if ok:
        # Mirror the new limit into settings so the Overview hero shows the right "to X%" at once,
        # before the next poll re-reads it from the car.
        db_reader.set_setting("charge_limit_percent", str(percent))
        return HTMLResponse(f'<span style="color:#22c55e">✓ Limit set to {percent}%</span>')
    return HTMLResponse(_cmd_error_html(msg))


# ── Scheduling (native B10: charge cmd 190, climate cmd 171) ───────────────────
@app.get("/api/charge-schedule")
async def get_charge_schedule_api():
    """Current charge schedule (read-only) — populates the Charges-page form."""
    import asyncio
    sched = await asyncio.get_event_loop().run_in_executor(None, command_client.get_charge_schedule)
    return sched or {}


@app.post("/api/charge-schedule", response_class=HTMLResponse)
async def save_charge_schedule_api(request: Request):
    """Read-modify-write the charge window (enable / target SoC / start / end / days). Days come
    as `days` checkbox values = their position in the Monday-first `cycles` mask (0=Mon..6=Sun);
    the chips are DISPLAYED in the app's Dom→Sab order but each carries its Mon-first index.
    Changes the car's stored schedule — fired only on explicit user save."""
    import re, asyncio
    form = await request.form()
    enabled = (form.get("enabled") or "") in ("1", "on", "true", "True")
    try:
        soc = int(form.get("soc_limit") or 80)
    except (ValueError, TypeError):
        soc = 80
    start = (form.get("start_time") or "").strip()
    end = (form.get("end_time") or "").strip()
    sel_days = set(form.getlist("days"))                      # Mon-first positions, e.g. {"0","1"} = Mon+Tue
    cycles = command_client.cycles_from_day_flags([str(i) in sel_days for i in range(7)])
    t = i18n.get_t(db_reader.get_language())
    if not re.match(r"^\d{2}:\d{2}$", start) or not re.match(r"^\d{2}:\d{2}$", end):
        return HTMLResponse(f'<span style="color:#ef4444">✗ {t("sched_bad_time")}</span>', status_code=400)
    if not (50 <= soc <= 100):
        return HTMLResponse('<span style="color:#ef4444">✗ 50–100%</span>', status_code=400)
    ok, msg = await asyncio.get_event_loop().run_in_executor(
        None, lambda: command_client.save_charge_schedule(
            enabled=enabled, soc_limit=soc, start_time=start, end_time=end, cycles=cycles))
    if ok:
        # Refresh the Overview's cached window straight away (#173). The poller re-reads it from the
        # car only every 30 min; without this the chip would keep showing the OLD times right after
        # you changed them here, which reads as a bug rather than as a cache.
        db_reader.set_setting("charge_sched_enabled", "1" if enabled else "0")
        db_reader.set_setting("charge_sched_start", start)
        db_reader.set_setting("charge_sched_end", end)
        return HTMLResponse(f'<span style="color:#22c55e">✓ {t("sched_saved")}</span>',
                            headers={"HX-Trigger": "chargeScheduleSaved"})
    return HTMLResponse(_cmd_error_html(msg))


@app.get("/api/climate-schedule")
async def get_climate_schedule_api():
    """Current climate (pre-conditioning) schedule (read-only) — populates the Scheduling-page form.
    Returns the first entry (Mate manages a single climate schedule) or {}."""
    import asyncio
    sched = await asyncio.get_event_loop().run_in_executor(None, command_client.get_climate_schedule)
    return (sched or [{}])[0] if sched else {}


@app.post("/api/climate-schedule", response_class=HTMLResponse)
async def save_climate_schedule_api(request: Request):
    """Write the climate (pre-conditioning) schedule (cmd 171). WORKS on the B10 — the historic
    code -2 was an expired start_time (ClimaSchedulerT01, solved 2026-06-07); save_climate_schedule
    anchors start_time to the next future occurrence. Mate manages a single climate entry (full
    replacement). `preset` ∈ cool|heat|vent|defrost|none; `days` are 0=Sun..6=Sat. Fired only on save."""
    import re, asyncio
    form = await request.form()
    enabled = (form.get("enabled") or "") in ("1", "on", "true", "True")
    preset = (form.get("preset") or "cool").strip()
    start = (form.get("start_time") or "").strip()
    days = sorted(int(d) for d in form.getlist("days") if str(d).isdigit() and 0 <= int(d) <= 6)
    try:
        temp = int(form.get("temperature") or 25)
    except (ValueError, TypeError):
        temp = 25
    t = i18n.get_t(db_reader.get_language())
    if preset not in command_client.CLIMATE_PRESETS:
        return HTMLResponse('<span style="color:#ef4444">✗ preset</span>', status_code=400)
    if enabled and not re.match(r"^\d{2}:\d{2}$", start):
        return HTMLResponse(f'<span style="color:#ef4444">✗ {t("sched_bad_time")}</span>', status_code=400)
    ok, msg = await asyncio.get_event_loop().run_in_executor(
        None, lambda: command_client.save_climate_schedule(
            enabled=enabled, preset=preset, start_hhmm=start or "07:00",
            day_positions=days, temperature=temp))
    if ok:
        return HTMLResponse(f'<span style="color:#22c55e">✓ {t("sched_saved")}</span>',
                            headers={"HX-Trigger": "climateScheduleSaved"})
    return HTMLResponse(_cmd_error_html(msg))


# ── One-touch prepare-car (cmd 360 immediate / 361 schedule) ─────────────────────────────────
def _parse_prepare_form(form) -> dict:
    """Build the prepare-car bundle (datacontent) from form fields. Off dimensions are omitted."""
    ac_mode = (form.get("ac_mode") or "off").strip()
    ac_preset = ac_mode if ac_mode in command_client.CLIMATE_PRESETS else None
    try:
        ac_temp = int(form.get("ac_temperature") or 25)
    except (ValueError, TypeError):
        ac_temp = 25
    seats = {s: (form.get("seat_" + s) or "off") for s in ("driver", "copilot", "left_rear", "right_rear")}
    seats = {k: (v if v in ("off", "heat", "vent") else "off") for k, v in seats.items()}
    steering = (form.get("steering") or "") in ("1", "on", "true", "True")
    mirror = (form.get("mirror") or "") in ("1", "on", "true", "True")
    dest = None
    dlat, dlon = (form.get("dest_lat") or "").strip(), (form.get("dest_lon") or "").strip()
    if dlat and dlon:
        try:
            float(dlat); float(dlon)
            dest = {"lat": dlat, "lon": dlon, "address": (form.get("dest_address") or "").strip(),
                    "name": (form.get("dest_name") or "").strip(), "key": ""}
        except (ValueError, TypeError):
            dest = None
    return command_client.build_prepare_bundle(
        ac_preset=ac_preset, ac_temperature=ac_temp, seats=seats,
        steering=steering, mirror=mirror, dest=dest)


@app.get("/api/prepare-car/schedules", response_class=HTMLResponse)
async def prepare_car_schedules_api(request: Request):
    """Render the current one-touch prepare-car schedule list (cmd 361, read-only)."""
    import asyncio
    entries = await asyncio.get_event_loop().run_in_executor(None, command_client.get_prepare_car_schedule)
    return templates.TemplateResponse(request, "partials/prepare_car_list.html",
                                      _ctx(entries=entries or []))


@app.post("/api/prepare-car/schedule", response_class=HTMLResponse)
async def save_prepare_car_schedule_api(request: Request):
    """Add or edit (by set_id) one prepare-car schedule entry (cmd 361). Full-state replacement: we
    read the current list, replace/append our entry, and write them all back. start_time is anchored
    to the next future occurrence (the -2 expired-appointment lesson). Fired only on save."""
    import re, asyncio, time as _t
    form = await request.form()
    t = i18n.get_t(db_reader.get_language())
    start = (form.get("start_time") or "").strip()
    if not re.match(r"^\d{2}:\d{2}$", start):
        return HTMLResponse(f'<span style="color:#ef4444">✗ {t("sched_bad_time")}</span>', status_code=400)
    days = sorted(int(d) for d in form.getlist("days") if str(d).isdigit() and 0 <= int(d) <= 6)
    set_id = (form.get("set_id") or "").strip() or None
    bundle = _parse_prepare_form(form)
    if not bundle:
        return HTMLResponse(f'<span style="color:#ef4444">✗ {t("prep_pick_one")}</span>', status_code=400)

    def _save():
        cur = command_client.get_prepare_car_schedule() or []
        entry = command_client.build_prepare_entry(
            bundle=bundle, start_hhmm=start, day_positions=days, set_id=set_id)
        others = []
        for e in cur:
            if e.get("set_id") == entry["set_id"]:
                continue
            e = dict(e)
            e.setdefault("update_time", str(int(_t.time() * 1000)))  # keep preserved entries writable
            others.append(e)
        return command_client.set_prepare_car_schedule(others + [entry])

    ok, msg = await asyncio.get_event_loop().run_in_executor(None, _save)
    if ok:
        return HTMLResponse(f'<span style="color:#22c55e">✓ {t("sched_saved")}</span>',
                            headers={"HX-Trigger": "prepareCarSaved"})
    return HTMLResponse(_cmd_error_html(msg))


@app.post("/api/prepare-car/schedule/delete", response_class=HTMLResponse)
async def delete_prepare_car_schedule_api(request: Request):
    """Delete one prepare-car schedule entry by set_id (writes back the remaining list)."""
    import asyncio, time as _t
    form = await request.form()
    t = i18n.get_t(db_reader.get_language())
    set_id = (form.get("set_id") or "").strip()

    def _del():
        cur = command_client.get_prepare_car_schedule() or []
        remaining = []
        for e in cur:
            if e.get("set_id") == set_id:
                continue
            e = dict(e)
            e.setdefault("update_time", str(int(_t.time() * 1000)))
            remaining.append(e)
        return command_client.set_prepare_car_schedule(remaining)

    ok, msg = await asyncio.get_event_loop().run_in_executor(None, _del)
    if ok:
        return HTMLResponse(f'<span style="color:#22c55e">✓ {t("sched_saved")}</span>',
                            headers={"HX-Trigger": "prepareCarSaved"})
    return HTMLResponse(_cmd_error_html(msg))


@app.post("/api/prepare-car/now", response_class=HTMLResponse)
async def prepare_car_now_api(request: Request):
    """Trigger an IMMEDIATE one-touch preparation (cmd 360). ⚠️ Actuates the car now."""
    import asyncio
    form = await request.form()
    t = i18n.get_t(db_reader.get_language())
    bundle = _parse_prepare_form(form)
    if not bundle:
        return HTMLResponse(f'<span style="color:#ef4444">✗ {t("prep_pick_one")}</span>', status_code=400)
    ok, msg = await asyncio.get_event_loop().run_in_executor(
        None, lambda: command_client.prepare_car_now(bundle))
    if ok:
        return HTMLResponse(f'<span style="color:#22c55e">✓ {t("prep_sent")}</span>')
    return HTMLResponse(_cmd_error_html(msg))


@app.post("/api/prepare-car/off", response_class=HTMLResponse)
async def prepare_car_off_api(request: Request):
    """Cancel an active preparation — turn A/C + seats + steering + mirror OFF (several commands)."""
    import asyncio
    t = i18n.get_t(db_reader.get_language())
    ok, msg = await asyncio.get_event_loop().run_in_executor(None, command_client.prepare_car_off)
    if ok:
        return HTMLResponse(f'<span style="color:#22c55e">✓ {t("prep_off_done")}</span>')
    return HTMLResponse(_cmd_error_html(msg))


@app.post("/api/prepare-car/ready-automation", response_class=HTMLResponse)
async def save_ready_automation_api(request: Request):
    """Save the Ready-triggered automation config — read by the POLLER every poll (not by this
    process); nothing is actuated here, this only persists settings."""
    form = await request.form()
    db_reader.save_ready_automation_config(form)
    t = i18n.get_t(db_reader.get_language())
    return HTMLResponse(f'<span style="color:#22c55e">✓ {t("ready_saved")}</span>')


_OPTIMISTIC = {
    "lock":          {"is_locked": 1},
    "unlock":        {"is_locked": 0},
    "open_trunk":    {"trunk_open": 1},
    "close_trunk":   {"trunk_open": 0},
    # All four windows move together (cmd 230 is global), so the optimistic count is 4 open / 0
    # closed — the Overview "Finestrini aperti N" badge flips with the state instead of lagging.
    "open_windows":  {"windows_open": 1, "windows_open_count": 4, "window_fl_open": 1, "window_rl_open": 1},
    "close_windows": {"windows_open": 0, "windows_open_count": 0, "window_fl_open": 0, "window_rl_open": 0},
    "open_sunshade": {"sunshade_open": 1},
    "close_sunshade":{"sunshade_open": 0},
}

# Climate tiles: a tile that's ON is turned off by sending ac_switch (best-effort —
# the B10 doesn't honour a real A/C-off via the API); a tile that's OFF sends its own
# mode command.
# Direction is decided from the real signal state. NO optimistic overlay — climate
# state is read from signals (2669 cool / 2681 heat / 1945 defrost / 1938 on), so the
# UI never shows a fake value. Frontend is unchanged; this is backend logic only.
_CLIMATE_TILES = {
    "ac_on":              "climate_on",
    "quick_cool":         "climate_cooling",
    "quick_heat":         "climate_heating",
    "quick_vent":         "climate_venting",
    "windshield_defrost": "climate_defrost",
}


def _windows_open_now(sig: dict) -> bool:
    """Any window open, by the same flag-OR-position-% rule the Vehicle page uses (#62) — so the
    post-command verification of open/close_windows confirms on the T03 (whose open/closed flags
    stay 0 even when open) instead of timing out and wiping the optimistic state."""
    vehicle, _ = db_reader.get_vehicle()
    vin = (vehicle or {}).get("vin")
    return any(capability_profile.window_open_states(
        sig, bool(vin) and capability_profile.is_shown(vin, "windows_pct")))


_FIELD_CHECK = {
    "is_locked":       lambda sig: int(sig.get("1298") or 0) == 1,
    "trunk_open":      lambda sig: int(sig.get("1281") or 0) != 0,
    "windows_open":    _windows_open_now,
    "sunshade_open":   lambda sig: int(sig.get("1724") or 0) != 0,   # 1724 = shade opening % (0 = closed)
    "climate_on":      lambda sig: int(sig.get("1938") or 0) == 1,
    "climate_cooling": lambda sig: int(sig.get("2669") or 0) == 2,
    "climate_heating": lambda sig: int(sig.get("2681") or 0) == 2,
    "climate_defrost": lambda sig: int(sig.get("1945") or 0) == 2,
}

# Commands that trigger slow physical movement — UI shows ⏳ until confirmed
_SLOW_COMMANDS = {"open_sunshade", "close_sunshade", "open_trunk", "close_trunk"}


# Monotonic counter bumped for every accepted command. A post-command verification
# captures the epoch it was started for and stands down if a newer command supersedes
# it — so command #1's eventual timeout can never clear command #2's optimistic overlay
# (GitHub #34).
_command_epoch = 0


def _cmd_error_html(msg: str) -> str:
    """HTML for a FAILED command. A 'timeout_car' (cloud accepted the command but the car
    didn't confirm in time = weak coverage / standby) and a cloud-unreachable error are shown
    as AMBER notices — it's the car or the network, not a Mate bug — while genuine errors stay
    red. data-warn keeps the message visible (no grid refetch)."""
    t = i18n.get_t(db_reader.get_language())
    outcome = command_client._classify_outcome(False, msg)
    if outcome == "timeout_car":
        return f'<span data-warn="1" style="color:#fbbf24">⏱️ {t("cmd_timeout_car")}</span>'
    if outcome == "cloud_unreachable":
        return f'<span data-warn="1" style="color:#fbbf24">📡 {t("cmd_cloud_unreach")}</span>'
    return f'<span style="color:#ef4444">✗ {msg}</span>'


def _command_confirmed(expected: dict, signals: dict) -> bool:
    """True when the live signals match every expected field (empty expected → True)."""
    for field, want in expected.items():
        checker = _FIELD_CHECK.get(field)
        if checker and bool(checker(signals)) != bool(want):
            return False
    return True


def _post_command_refresh(expected: dict, epoch: int, delay: int = 3, deadline_s: int = 30):
    """Verify a command against the car, polling until it confirms or we give up.

    The Leapmotor cloud often hasn't ingested the new state a few seconds after a
    command (it reflects what the car last uploaded, not what we just asked for), so a
    single early sample would wrongly "un-confirm" a command that actually worked — and,
    worse, persist that stale sample as the newest row, poisoning every later refetch
    until the cloud catches up. That was the real cause of GitHub #34 (tiles staying
    stale, 2nd/3rd tap "fixing" it). Instead we keep the optimistic overlay alive and
    retry until the cloud agrees, only persisting a sample once it confirms — or, on
    timeout, accept reality (the command most likely didn't take).
    """
    start = time.time()
    time.sleep(delay)
    while True:
        if _command_epoch != epoch:          # a newer command owns the state now
            return
        signals = command_client.get_fresh_signals()
        if signals and _command_confirmed(expected, signals):
            db_reader.save_fresh_signals(signals)     # truth matches the expectation
            return
        if time.time() - start >= deadline_s:
            # Gave up waiting: show reality rather than a stuck optimistic overlay.
            if _command_epoch == epoch:
                db_reader.clear_optimistic_status()
                if signals:
                    db_reader.save_fresh_signals(signals)
            return
        db_reader.extend_optimistic_status()  # keep the overlay alive across the wait
        time.sleep(4)


# Commands the car refuses while in motion (the official app shows the same notice) —
# mapped to the i18n key for the per-control warning. These are locked out at speed for
# safety; firing them just bounces off the car, and their signals are unreliable while
# moving — so we intercept the press and show the warning instead of sending it. Climate
# and comfort controls are NOT here: they work fine while driving.
_DRIVE_LOCKED = {
    "open_sunshade": "sunshade_moving", "close_sunshade": "sunshade_moving",
    "open_trunk":    "trunk_moving",    "close_trunk":    "trunk_moving",
    "open_windows":  "windows_moving",  "close_windows":  "windows_moving",
    "lock":          "lock_moving",     "unlock":         "lock_moving",
}


def _blocked_while_driving(name: str, status: dict, t) -> "str | None":
    """Warning text if this command can't run because the car is moving, else None."""
    key = _DRIVE_LOCKED.get(name)
    if key and _driving(status or {}):
        return t(key)
    return None


_CMD_COOLDOWN_S = 10     # match the HA integration's remote-action cooldown
_last_command_at = 0.0


def _wants_json(request: Request) -> bool:
    """A caller that sends `Accept: application/json` gets structured JSON back from the command
    endpoint instead of the HTML fragment the web UI (HTMX) uses — so a script, an iOS Shortcut, a
    smartwatch HTTP button or a Home Assistant `rest_command` can fire a command and parse the
    result. (Idea from irek's fork branch `fix/json-api-response`.)"""
    return "application/json" in request.headers.get("accept", "")


def _cmd_response(request: Request, *, html: str, payload: dict, status: int = 200):
    """Content-negotiated command result: JSON when the client asked for it, else the HTML
    fragment the browser uses — same status code either way."""
    if _wants_json(request):
        return JSONResponse(payload, status_code=status)
    return HTMLResponse(html, status_code=status)


@app.post("/api/command/{name}", response_class=HTMLResponse)
async def run_command(name: str, request: Request, background_tasks: BackgroundTasks):
    fn = _COMMANDS.get(name)
    if not fn:
        return _cmd_response(request, status=400,
                             html='<span style="color:#ef4444">Unknown command</span>',
                             payload={"ok": False, "error": "unknown_command"})

    # Ability gate (defence in depth, mirrors the hidden button): refuse a command the car doesn't
    # declare it can do — e.g. unlock-charge-cable on a T03, which never declares code 53 (#142) —
    # instead of bouncing a no-op off the car. Only the ability-gated commands (COMMAND_ABILITY keys)
    # look up the vehicle, so every other command's path is untouched. None abilities → allowed.
    if name in capability_profile.COMMAND_ABILITY:
        _veh, _ = db_reader.get_vehicle()
        if not capability_profile.command_shown(
                (_veh or {}).get("vin", ""), name,
                abilities=capability_profile.parse_abilities((_veh or {}).get("abilities"))):
            _msg = {"it": "Non supportato su questo modello", "fr": "Non pris en charge sur ce modèle",
                    "de": "Von diesem Modell nicht unterstützt"}.get(
                        db_reader.get_language(), "Not supported on this model")
            return _cmd_response(request, status=400,
                                 payload={"ok": False, "error": _msg, "unsupported": True},
                                 html=f'<span data-warn="1" style="color:#fbbf24">⚠️ {_msg}</span>')

    # The car locks some controls (sunshade, trunk, windows, lock) while moving —
    # intercept the press and show the same notice the official app does, instead of
    # bouncing it off the car. data-warn tells the Commands page to leave the message
    # up (no grid refetch).
    warn = _blocked_while_driving(name, db_reader.get_latest_status() or {},
                                  i18n.get_t(db_reader.get_language()))
    if warn:
        return _cmd_response(request, payload={"ok": False, "error": warn, "blocked": True},
                             html=f'<span data-warn="1" style="color:#fbbf24">⚠️ {warn}</span>')

    # Remote-action cooldown (like the HA integration's 10s): don't fire commands too
    # close together — the previous one may still be completing on the car.
    global _last_command_at
    import time
    remaining = _CMD_COOLDOWN_S - (time.time() - _last_command_at)
    if remaining > 0:
        wait = int(remaining) + 1
        # Say explicitly the command did NOT go through. A bare "Wait Ns" reads as "it's queued, it'll
        # fire in Ns" — the opposite of what happened (the cooldown blocked it; the previous command may
        # still be completing on the car). "Not sent — retry in Ns" makes the rejection unambiguous.
        _cooldown_msg = {"it": "Non inviato — riprova tra {n}s",
                         "fr": "Non envoyé — réessayez dans {n}s",
                         "de": "Nicht gesendet — in {n}s erneut"}
        msg = _cooldown_msg.get(db_reader.get_language(), "Not sent — retry in {n}s").format(n=wait)
        # data-warn → the front-end leaves the notice up (and does NOT refresh the card, which would
        # wipe it in a flash and make the user think the command was sent).
        return _cmd_response(request,
                             payload={"ok": False, "error": msg, "cooldown": True, "retry_in": wait},
                             html=f'<span data-warn="1" data-cooldown="1" style="color:#fbbf24">⏳ {msg}</span>')
    _last_command_at = time.time()
    # Boost the poller so the car's REAL state is re-polled within a few seconds (not up to 30s).
    # We no longer fake an optimistic state, so the UI must catch up to reality quickly.
    db_reader.set_setting("boost_until", str(time.time() + 60))
    global _command_epoch
    _command_epoch += 1
    epoch = _command_epoch

    # Climate: decide direction from the real state. The master A/C tile that's on →
    # ac_off (ac_switch operate=off — real full-off, drives 1938→0, B10-confirmed);
    # a mode tile that's on → ac_switch toggle (stops that mode); a tile that's off →
    # its own command. No optimistic overlay, but we DO verify the real signal flips.
    overrides = dict(_OPTIMISTIC.get(name) or {})
    expected = dict(overrides)                  # what the post-command check waits for
    field = _CLIMATE_TILES.get(name)
    if field:
        cur = db_reader.get_latest_status() or {}
        if name == "ac_on":
            # "A/C AUTO" counts as ON only when the car is REALLY in AUTO (mode 0). From OFF or from a
            # manual mode (cool/heat/vent), pressing it ENGAGES auto (ac_on) — it must not "turn off".
            turning_off = bool(cur.get("climate_on")) and cur.get("climate_mode") == 0 and not cur.get("climate_defrost")
        elif name in ("quick_cool", "quick_heat"):
            # Cool/Heat tiles light from the MODE (3713=1/3) to match the sliders; the off-press must use
            # the SAME basis, or a lit tile (mode on but compressor idle → cooling/heating signal 0)
            # wouldn't flip off — it would re-send the mode instead.
            _want = 1 if name == "quick_cool" else 3
            turning_off = bool(cur.get("climate_on")) and cur.get("climate_mode") == _want and not cur.get("climate_defrost")
        else:
            turning_off = bool(cur.get(field))
        if turning_off:                         # currently on → turn EVERYTHING off (full A/C off)
            # ANY climate tile, when on, is switched fully OFF — same behaviour as the A/C AUTO off,
            # NEVER fall back to AUTO. ac_off = ac_switch operate=off (drives 1938→0, B10-confirmed).
            fn = command_client.ac_off
        expected = {field: (not turning_off)}   # verify the tile actually flipped
        overrides = {}                          # never fake climate state in the DB

    if _IS_DEMO:
        # Demo mode: reflect the command in the demo's own state (reusing the
        # optimistic overlay) and skip the cloud entirely — keeps the demo interactive.
        if expected:
            db_reader.write_optimistic_status(expected)
        if name in _COMFORT_CMD_OPTIMISTIC:
            _veh, _ = db_reader.get_vehicle()
            _optimistic_comfort(_veh.get("vin") if _veh else None, _COMFORT_CMD_OPTIMISTIC[name])
        return _cmd_response(request, payload={"ok": True, "status": "done"},
                             html='<span style="color:#22c55e">✓ Done</span>')

    import asyncio
    ok, msg = await asyncio.get_event_loop().run_in_executor(None, fn)
    if ok:
        # No optimistic overlay: the UI shows ONLY the real signal state, never a faked "done" before the
        # car actually reports it (avoids "Mate says closed, the car is open"). The post-command verify
        # loop + the boost above bring the real state in within a few seconds.
        if name in ("open_windows", "close_windows"):   # keep the slider's last-commanded % (shown only
            db_reader.set_setting("windows_cmd_pct", "20" if name == "open_windows" else "0")  # for a really-open window)
        # Climate commands take several seconds to reflect in signals → show the
        # spinner and refresh from real signals after a delay (like slow commands).
        slow = name in _SLOW_COMMANDS or field is not None
        background_tasks.add_task(_post_command_refresh, expected, epoch, 12 if slow else 3)
        if slow:
            return _cmd_response(request, payload={"ok": True, "status": "pending"},
                html='<span data-slow="1" style="color:#60a5fa;display:inline-flex;align-items:center;gap:4px"><svg style="animation:spin 1s linear infinite;width:14px;height:14px" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/></svg></span><style>@keyframes spin{to{transform:rotate(360deg)}}</style>')
        return _cmd_response(request, payload={"ok": True, "status": "done"},
                             html='<span style="color:#22c55e">✓ Done</span>')
    return _cmd_response(request, payload={"ok": False, "error": msg},
                         html=_cmd_error_html(msg))


# ── Battery options — European models only (verified specs) ──────────────────
# The packs themselves live in battery_packs.py — a plain table, importable without fastapi,
# so the minimal CI environment can protect the figures (see
# tests/test_the_wizard_hint_matches_the_packs_mate_uses.py).
_EU_BATTERY_MAP = battery_packs.EU_BATTERY_MAP


def battery_options_for_build() -> dict[str, list[dict]]:
    """The wizard's battery list, as the build that is serving it is allowed to show it.

    REEV packs belong to MateBetaTesterOnly ONLY. The official Mate has no REEV support — its energy
    pipeline assumes SoC-down = energy-used, which a generator refilling the pack mid-drive breaks —
    so offering the pack would promise a support that isn't there, and whoever picked it would end up
    with statistics that quietly don't add up. Both builds ship from this one image, so the split is
    made here, at runtime, and NOT by removing the packs (that would blind the beta too).

    Single source of truth on purpose: the wizard page and the detect-vehicle endpoint both render
    from this. They used to keep separate copies, which is exactly how the REEV packs stayed in the
    official wizard for ten days after the gate that was supposed to remove them (#141)."""
    if research.research_enabled():
        return _EU_BATTERY_MAP
    return {ct: [o for o in opts if not o.get("reev")] for ct, opts in _EU_BATTERY_MAP.items()}


# ── Setup wizard ─────────────────────────────────────────────────────────────

@app.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    # The battery list is rendered from the server's map (already filtered for this build), so the
    # official wizard's HTML never even contains the REEV packs — and the page can't drift from what
    # detect-vehicle answers, since both read battery_options_for_build().
    return templates.TemplateResponse(request, "setup.html", {
        "battery_options": battery_options_for_build(),
        "research": research.research_enabled(),
        # Asked here, pre-filled with what Mate detected — see the note in setup_submit. The full
        # grouped list is the same one Settings uses, so the two pickers can never diverge.
        "tz_options": db_reader.timezone_options(),
        "tz_detected": db_reader.detected_tz_name(),
    })


def _restart_container() -> None:
    """Ask run.sh to relaunch Mate by exiting with the agreed RELAUNCH code (42). run.sh
    re-exec's itself in-process (re-reading the demo flag), so this works even with NO
    container restart policy — standalone `docker run` / Docker Desktop "Run" don't set one
    (with a policy / HA add-on it still relaunches cleanly). The poller uses the same code on
    an account switch (poller/main.py). Delayed briefly so the HTTP response reaches the browser."""
    threading.Timer(1.2, lambda: os._exit(42)).start()


@app.post("/api/demo/enable")
async def demo_enable():
    """Enter demo mode from inside Mate (the setup screen's 'Try the demo' button), then
    restart into it. Guarded to a not-yet-configured install and a non-demo process, so it
    can never replace a real, set-up dashboard by accident."""
    if _IS_DEMO or db_reader.is_setup_complete():
        return JSONResponse({"ok": False, "restarting": False})
    demo.set_flag(True)
    _restart_container()
    return JSONResponse({"ok": True, "restarting": True})


@app.post("/api/demo/disable")
async def demo_disable():
    """Leave demo mode (the in-demo exit banner) and restart into the normal app."""
    demo.set_flag(False)
    _restart_container()
    return JSONResponse({"ok": True, "restarting": True})


@app.get("/api/demo/status")
async def demo_status():
    """Current mode of THIS process. The browser polls it after enable/disable to know the
    container has come back in the target mode — robust regardless of how fast the restart is
    (polling for an up/down transition can miss a sub-second restart)."""
    return JSONResponse({"demo": _IS_DEMO})


_DATA_CERT_DIR = os.environ.get("DATA_CERT_DIR", "/data/certs")


@app.get("/api/setup/cert-status")
async def cert_status_api():
    """Whether the app certificate is already available (wizard can skip the cert step)."""
    return JSONResponse({"present": command_client.certs_present()})


@app.post("/api/setup/cert")
async def setup_cert_api(request: Request):
    """Receive the Leapmotor app certificate + key (file upload or pasted PEM) and store
    them in the persistent /data/certs dir. The cert is the same for everyone — users get
    it from github.com/markoceri/leapmotor-certs (documented in the wizard/README)."""
    form = await request.form()

    async def _read(field_file: str, field_text: str) -> str:
        f = form.get(field_file)
        if f is not None and hasattr(f, "read"):
            return (await f.read()).decode("utf-8", "replace").strip()
        return (form.get(field_text) or "").strip()

    crt = await _read("crt_file", "crt_pem")
    key = await _read("key_file", "key_pem")

    if not crt or not key:
        return JSONResponse({"error": "Both the certificate and the key are required."}, status_code=400)
    if "-----BEGIN CERTIFICATE-----" not in crt:
        return JSONResponse({"error": "The certificate file is not a valid PEM (app.crt)."}, status_code=400)
    if "-----BEGIN" not in key or "PRIVATE KEY" not in key:
        return JSONResponse({"error": "The key file is not a valid PEM private key (app.key)."}, status_code=400)

    try:
        os.makedirs(_DATA_CERT_DIR, exist_ok=True)
        with open(os.path.join(_DATA_CERT_DIR, "app.crt"), "w") as fh:
            fh.write(crt + "\n")
        with open(os.path.join(_DATA_CERT_DIR, "app.key"), "w") as fh:
            fh.write(key + "\n")
    except OSError as e:
        return JSONResponse({"error": f"Could not save the certificate: {e}"}, status_code=500)

    # Drop any half-built session so the next call picks up the new cert
    command_client._session._reset()
    return JSONResponse({"ok": True})


@app.post("/api/setup/detect-vehicle")
async def detect_vehicle_api(request: Request):
    import asyncio
    body = await request.json()
    user = (body.get("user") or "").strip()
    pwd  = (body.get("password") or "").strip()
    pin  = (body.get("pin") or "").strip()
    if not user or not pwd or not pin:
        return JSONResponse({"error": "Missing credentials"}, status_code=400)
    result = await asyncio.get_event_loop().run_in_executor(
        None, lambda: command_client.detect_vehicle(user, pwd, pin)
    )
    if "error" in result:
        return JSONResponse(result, status_code=400)
    options = battery_options_for_build().get(result["car_type"], [])
    if len(options) == 1:
        # Single EU variant — tell the frontend to auto-set, no selector needed
        result["battery_kwh"]   = options[0]["v"]
        result["battery_label"] = options[0]["label"]
    elif options:
        result["battery_options"] = options
    # else: unknown/non-EU model → frontend falls back to manual input
    return JSONResponse(result)


def apply_setup_vehicles(cars: list[dict]) -> int:
    """Save the wizard's answers for EVERY car it found. Returns how many were configured.

    One car is not a special case here, it is a list of one — which is the point: the wizard used to
    write a single pack and a single PIN, so on a two-car account the second car was left to the
    poller, which gives it its MODEL's default. For a C10 that default is the RWD, so an AWD ran 20%
    out and a REEV 2.4 times out, and nobody was ever asked. The pack is not derivable — the cloud
    says only "C10" — which is why the wizard shows a list, per car.

    Each car takes away its own pack, its own range-extender answer and its own PIN, and is marked
    as configured: that mark is what later separates a car a human has seen from one that walked in
    on its own and took a default."""
    done = 0
    for car in cars:
        vin = (car.get("vin") or "").strip()
        if not vin:
            continue
        key = vin.lower()
        car_type = (car.get("car_type") or "").strip().upper()
        if car_type:
            db_reader.upsert_vehicle(vin, car_type)
        try:
            kwh = float(car.get("battery") or 0)
        except (TypeError, ValueError):
            kwh = 0.0
        if kwh > 0:
            db_reader.set_vehicle_capacity(vin, kwh)
        pin = (car.get("pin") or "").strip()
        if pin:
            db_reader.set_secret(f"leapmotor_pin_{key}", pin)
        db_reader.set_setting(f"is_reev_{key}",
                              "1" if str(car.get("is_reev")) in ("1", "on", "true") else "0")
        db_reader.set_setting(f"vehicle_setup_done_{key}", "1")
        done += 1
    return done


@app.post("/setup", response_class=HTMLResponse)
async def setup_submit(request: Request):
    form = await request.form()
    user     = (form.get("user", "") or "").strip()
    pwd      = (form.get("password", "") or "").strip()
    pin      = (form.get("pin", "") or "").strip()
    battery  = (form.get("battery", "65.0") or "65.0").strip()
    lang     = form.get("language", "en")
    car_type = (form.get("car_type", "") or "").strip().upper()
    is_reev  = "1" if form.get("is_reev") in ("1", "on", "true") else "0"
    vin      = (form.get("vin", "") or "").strip()
    # The zone is asked here, pre-filled with the one detected, because leaving it implicit is what
    # cost @ghuaywen-ai 150 charges seven hours out (#181): a time you type is anchored to whatever
    # clock Mate happens to be running on, and on a bare container that is UTC. On Home Assistant
    # the detected value is already right and this is one more Next; on plain Docker the field reads
    # "UTC" and says so, which is the whole point of showing it.
    tz       = (form.get("timezone", "") or "").strip()

    if not user or not pwd or not pin:
        t = i18n.get_t(lang)
        _req_errors = {
            "it": "Email, password e PIN sono obbligatori.",
            "fr": "E-mail, mot de passe et PIN sont obligatoires.",
            "de": "E-Mail, Passwort und PIN sind erforderlich.",
        }
        return templates.TemplateResponse(request, "setup.html", {
            "error": _req_errors.get(lang, "Email, password and PIN are required."),
            "prefill": dict(form),
        }, status_code=400)

    try:
        battery_kwh = float(battery)
    except ValueError:
        battery_kwh = 65.0

    db_reader.set_setting("leapmotor_user", user)
    db_reader.set_secret("leapmotor_pass", pwd)
    db_reader.set_secret("leapmotor_pin", pin)
    db_reader.set_setting("battery_capacity_kwh", str(battery_kwh))
    db_reader.set_setting("is_reev", is_reev)   # REEV variant selected in the wizard → gates fuel features
    db_reader.set_setting("language", lang if lang in ("en", "it", "fr", "de", "pl", "pt-PT", "nl", "es") else "en")
    # set_timezone validates against the tz database and falls back to Auto on anything unknown, so
    # a tampered field cannot wedge every date render. Marked as pinned either way, so the startup
    # migration never comes back and overwrites a fresh answer with the container's clock.
    db_reader.set_timezone(tz or db_reader.detected_tz_name())
    db_reader.set_setting(db_reader.TZ_PINNED_KEY, "1")

    # Every car the wizard found, each with its OWN pack, range-extender answer and PIN. The
    # account-wide values written above stay for the single-car install and as the fallback the rest
    # of the code already knows; these are what a two-car account needs, and what it never had —
    # before this, car number two was left to the poller and took its model's default.
    try:
        _cars = json.loads(form.get("vehicles_json") or "[]")
    except (TypeError, ValueError):
        _cars = []
    if not _cars and vin and car_type:      # one car, or a client that posts the old shape
        _cars = [{"vin": vin, "car_type": car_type, "battery": str(battery_kwh),
                  "is_reev": is_reev, "pin": pin}]
    apply_setup_vehicles(_cars)

    # Completing setup cancels any pending factory reset: the user has intentionally configured
    # this install, so a stray marker (e.g. if a reset's relaunch never fired) must never wipe it.
    db_reader.set_setting("factory_reset_pending", "0")
    db_reader.set_setting("setup_complete", "1")

    # Reset the command session so it picks up new credentials
    command_client._session._reset()

    return RedirectResponse(request.headers.get("x-ingress-path", "") + "/", status_code=303)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("WEB_PORT", 4000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
