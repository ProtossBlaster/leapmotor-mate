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
import update_check

MATE_VERSION = "1.29.2"  # bump together with the git tag + add-on config.yaml at release

import diagnostics
import demo
import maintenance

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

app = FastAPI(title="LeapMotor Mate")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _nice(x) -> str:
    """Show a number with at most 2 decimals, stripping trailing zeros
    (e.g. 1.77 → "1.77", 310.027 → "310.03", 48 → "48")."""
    if x is None:
        return "—"
    return f"{float(x):.2f}".rstrip("0").rstrip(".")

templates.env.filters["nice"] = _nice


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


def _localdate(s) -> str:
    """ISO timestamp → date in the UI language's format (e.g. it '10 giu 2026',
    en '10 Jun 2026') instead of the raw ISO '2026-06-10'. Time stays separate."""
    dt = db_reader._local_dt(s)
    return i18n.fmt_day_month_year(db_reader.get_language(), dt) if dt else (s or "")

templates.env.filters["localdate"] = _localdate

# Display-time unit conversion (DB stays metric — see units.py). Filters format "<value> <unit>";
# the *_unit() / *_val() globals give a bare unit label or converted number (chart axes / JS data).
import units
for _name in ("dist", "speed", "temp", "pressure"):
    templates.env.filters[_name] = getattr(units, _name)
templates.env.filters["eff"] = units.efficiency
templates.env.globals.update(
    dist_unit=units.dist_unit, speed_unit=units.speed_unit, temp_unit=units.temp_unit,
    pressure_unit=units.pressure_unit, eff_unit=units.eff_unit,
    dist_val=units.dist_val, speed_val=units.speed_val, temp_val=units.temp_val,
    eff_val=units.eff_val, unit_system=units.get_unit_system,
)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


# ── Setup check middleware ────────────────────────────────────────────────────

@app.middleware("http")
async def setup_check(request: Request, call_next):
    path = request.url.path
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
    return await call_next(request)


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
    return {**kwargs, "lang": lang, "t": t, "version": MATE_VERSION, "demo": _IS_DEMO,
            "update": update_check.get_update_status(MATE_VERSION),
            "wallbox_enabled": wallbox_enabled,
            "wb_active_profile_name": wb_active_profile_name,
            "wb_active_profile_id":   wb_active_profile_id,
            "currency": db_reader.get_currency(), "auth_enabled": auth.enabled(),
            "soc_color": _soc_color, "state_label": state_label, "state_color": _state_color,
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
    if auth.check_password(form.get("password") or ""):
        resp = RedirectResponse(ingress + "/", status_code=303)
        resp.set_cookie(auth.COOKIE, auth.make_token(), max_age=auth.TTL,
                        httponly=True, samesite="strict", path="/")
        return resp
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
    ))


@app.get("/trips", response_class=HTMLResponse)
async def trips_page(request: Request, highlight: int = 0):
    vehicle, _ = db_reader.get_vehicle()
    grouped = db_reader.get_trips_grouped()
    total   = sum(y["count"] for y in grouped)
    summary = db_reader.get_trips_summary()
    # All eligible adjacent pairs at the WIDEST gap → drawn as connectors in the tree and filtered
    # live (client-side) by the gap slider. Keyed by the later trip's id (b_id = the row above a in
    # the newest-first list); value carries the earlier trip a_id + the actual stop gap in minutes.
    merge_pairs = {p["b_id"]: {"a_id": p["a_id"], "gap": p["gap_min"]}
                   for p in db_reader.get_mergeable_pairs(db_reader.TRIP_MERGE_GAP_MAX)}
    return templates.TemplateResponse(request, "trips.html", _ctx(
        page="trips", vehicle=vehicle, grouped=grouped,
        total=total, highlight=highlight, summary=summary,
        merge_pairs=merge_pairs, merge_gap_default=db_reader.TRIP_MERGE_GAP_DEFAULT,
        merge_gap_min=db_reader.TRIP_MERGE_GAP_MIN, merge_gap_max=db_reader.TRIP_MERGE_GAP_MAX,
    ))


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
    return templates.TemplateResponse(request, "trip_detail.html", _ctx(
        page="trips", vehicle=vehicle, trip=trip,
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
        return Response(status_code=200, headers={"HX-Refresh": "true"})
    t = i18n.get_t(db_reader.get_language())
    return HTMLResponse(f'<div style="color:#f87171;font-size:13px;padding:6px 0">⚠️ {t("merge_failed")}</div>')


@app.post("/api/trips/unmerge", response_class=HTMLResponse)
async def trips_unmerge(request: Request, parent: int):
    """Split a merged group back into its original trips (reversible — nothing was lost)."""
    db_reader.unmerge_trip(parent)
    return Response(status_code=200, headers={"HX-Refresh": "true"})


@app.delete("/api/charges/{charge_id}")
async def delete_charge(request: Request, charge_id: int):
    """Permanently delete one charge session (HTMX, confirmed in the UI). Reloads the charges list,
    so day/month/lifetime totals recompute."""
    db_reader.delete_charge(charge_id)
    base = request.headers.get("x-ingress-path", "")
    return Response(status_code=200, headers={"HX-Redirect": f"{base}/charges"})


@app.get("/charges", response_class=HTMLResponse)
async def charges_page(request: Request, highlight: int = 0):
    vehicle, _ = db_reader.get_vehicle()
    grouped = db_reader.get_charges_grouped()
    stats   = db_reader.get_charge_stats()
    prices  = db_reader.get_charge_prices()
    status  = db_reader.get_latest_status()
    total   = sum(y["count"] for y in grouped)
    return templates.TemplateResponse(request, "charges.html", _ctx(
        page="charges", vehicle=vehicle, grouped=grouped,
        stats=stats, total=total, highlight=highlight,
        charge_types=db_reader.CHARGE_TYPES, prices=prices,
        status=status, ac_dc=db_reader.get_ac_dc_stats(),
        unconfirmed=db_reader.unconfirmed_charges_count(),
    ))


@app.get("/statistics", response_class=HTMLResponse)
async def statistics(request: Request):
    vehicle, _ = db_reader.get_vehicle()
    grouped  = db_reader.get_stats_grouped()
    totals   = db_reader.get_stats_summary()
    totals["v2l_total_kwh"] = db_reader.get_v2l_total_kwh()
    return templates.TemplateResponse(request, "statistics.html", _ctx(
        page="statistics", vehicle=vehicle,
        grouped=grouped, totals=totals,
    ))


@app.get("/report", response_class=HTMLResponse)
async def report(request: Request, month: str | None = None):
    vehicle, _ = db_reader.get_vehicle()
    data = db_reader.get_monthly_report(month)
    track = db_reader.get_month_track(data["month"]) if data.get("has_data") else []
    return templates.TemplateResponse(request, "report.html", _ctx(
        page="report", vehicle=vehicle, r=data, track=track,
    ))


@app.get("/battery", response_class=HTMLResponse)
async def battery_page(request: Request):
    vehicle, _ = db_reader.get_vehicle()
    health = db_reader.get_battery_health()
    vampire = db_reader.get_vampire_drain(
        min_drop_pct=float(db_reader.get_setting("vampire_min_drop_pct", "0.2") or 0.2))
    return templates.TemplateResponse(request, "battery.html", _ctx(
        page="battery", vehicle=vehicle, health=health, vampire=vampire,
    ))


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
        _, bkm, _explicit = maintenance.get_baseline()   # anchor km = earliest odometer Mate saw
        maintenance.set_baseline(dt, bkm)
    return templates.TemplateResponse(request, "partials/maintenance_content.html", _maint_ctx(request))


@app.get("/map", response_class=HTMLResponse)
async def map_page(request: Request):
    vehicle, _ = db_reader.get_vehicle()
    track  = db_reader.get_all_track()
    places = db_reader.get_frequent_places()
    return templates.TemplateResponse(request, "map.html", _ctx(
        page="map", vehicle=vehicle, track=track, places=places,
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


def _comfort_rows(vin):
    """Read-only comfort STATE sensors for the Commands page. The poller writes the live
    values to settings as `comfort_state_<vin>`; we show only the ones not confirmed broken
    on this car (the remote command may be broken even when the state sensor works)."""
    if not vin:
        return []
    raw = db_reader.get_setting(f"comfort_state_{vin.lower()}", "")
    try:
        state = json.loads(raw) if raw else {}
    except ValueError:
        state = {}
    rows = []
    for skey, feat, label_key, icon, accent in _COMFORT_ROWS:
        if not capability_profile.is_shown(vin, feat):
            continue
        v = int(state.get(skey) or 0)
        row = {"icon": icon, "accent": accent, "label_key": label_key, "value": v,
               "on": v > 0, "control": None, "skey": skey}
        # Seats → level slider (0–3); steering/mirror → on/off toggle. Gated by the command capability.
        if skey.startswith("seat_"):
            _, func, side = skey.split("_", 2)        # func: heat|vent, side: driver|passenger
            if capability_profile.is_shown(vin, f"seat_{func}_cmd"):
                row.update(control="slider", func=func,
                           position=("driver" if side == "driver" else "copilot"))
        elif skey in _COMFORT_TOGGLE:
            cfeat, cmd_on, cmd_off = _COMFORT_TOGGLE[skey]
            if capability_profile.is_shown(vin, cfeat):
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
    comfort = _comfort_rows(vehicle.get("vin") if vehicle else None)
    return templates.TemplateResponse(request, "commands.html", _ctx(
        page="commands", vehicle=vehicle, status=status, comfort=comfort, **_wins_ctx(),
        ac_off_shown=capability_profile.command_shown(vehicle.get("vin") if vehicle else None, "climate_off"),
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
    return templates.TemplateResponse(request, "prepare_car.html", _ctx(
        page="prepare_car", vehicle=vehicle,
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
                "abrp_token_set": bool(db_reader.get_setting("abrp_token", "")),
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
                "charge_reconstruct_min_pct": db_reader.get_setting("charge_reconstruct_min_pct", "2.0"),
                "vampire_min_drop_pct": db_reader.get_setting("vampire_min_drop_pct", "0.2"),
                "charge_dc_min_kw": db_reader.get_setting("charge_dc_min_kw", "11"),
                "wallbox_auto_home": db_reader.get_setting("wallbox_auto_home", "0"),
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
        charge_types=db_reader.CHARGE_TYPES,
        ha_url=db_reader.get_setting("ha_url", ""),
        ha_has_token=bool(db_reader.get_setting("ha_token", "")),
        ha_supervisor=bool(os.environ.get("SUPERVISOR_TOKEN")),
        # Dev/env-var mode skips the wizard, so the credentials live in the environment,
        # not the DB — the Logout button (which clears DB creds) wouldn't apply there.
        env_login=bool(os.environ.get("LEAPMOTOR_USER")),
        wb_keywords=db_reader.get_setting("wb_keywords", ""),
        currencies=db_reader.CURRENCIES,
        currency_code=db_reader.get_currency_code(),
        diag=diagnostics.build_system_info(MATE_VERSION),
        measured_capacity=db_reader.get_battery_health().get("latest_capacity_kwh"),
    ))


@app.get("/costs", response_class=HTMLResponse)
async def costs_page(request: Request):
    """Charging-costs page: base per-type prices + time-of-use bands."""
    vehicle, settings = db_reader.get_vehicle()
    prices = db_reader.get_charge_prices()
    cfg = db_reader.get_cost_config()
    return templates.TemplateResponse(request, "costs.html", _ctx(
        page="costs", vehicle=vehicle,
        settings={**settings, **prices},
        charge_types=db_reader.CHARGE_TYPES,
        cost_mode=cfg["mode"], tou_method=cfg["method"],
        tou_bands_json=json.dumps(cfg["bands"]),
    ))


@app.get("/wallbox", response_class=HTMLResponse)
async def wallbox_page(request: Request):
    """Wallbox page — only reachable when enabled in Settings. Data wiring to
    Home Assistant comes next; for now this previews the intended layout."""
    if db_reader.get_setting("wallbox_enabled", "0") != "1":
        return RedirectResponse(request.headers.get("x-ingress-path", "") + "/settings")
    vehicle, _ = db_reader.get_vehicle()
    return templates.TemplateResponse(request, "wallbox.html", _ctx(
        page="wallbox", vehicle=vehicle,
        configured=ha_client.is_configured() and bool(ha_client.get_mapping()),
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
    ))


@app.post("/api/settings/wallbox-entities", response_class=HTMLResponse)
async def save_wallbox_entities(request: Request):
    form = await request.form()
    mapping = {role: form.get(role, "").strip()
               for role in ha_client.WB_ROLES if form.get(role, "").strip()}
    db_reader.set_setting("wallbox_entities", json.dumps(mapping))
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
    b10_plugged = bool(status and status.get("plug_connected"))
    return templates.TemplateResponse(request, "partials/wallbox_live.html", _ctx(
        wb=wb, b10_plugged=b10_plugged))


def _integrate_kwh(points: list) -> float:
    """Trapezoidal integral of (epoch_seconds, kW) points → kWh. Skips non-positive and
    >15min gaps so a charger pause / poll miss inside one window is never integrated as a
    phantom interval — keeps the AC/DC comparison energy (and the HOME cost billed on the AC
    energy) consistent with compute_cost's split and _integrate_charge_energy_kwh, which both
    already skip multi-hour gaps."""
    e = 0.0
    for i in range(1, len(points)):
        dt = (points[i][0] - points[i - 1][0]) / 3600.0
        if dt <= 0 or dt > 0.25:
            continue
        e += (points[i][1] + points[i - 1][1]) / 2 * dt
    return e


def _session_energy(curve: dict) -> dict:
    """Energy comparison for one charge: DC into battery vs AC from the wallbox,
    both integrated from real power (so AC ≥ DC and efficiency < 100%).
    
    For AC, resamples the HA history onto the car curve's timestamps using step-hold
    (same approach as the overlay chart) to ensure consistency between the visual
    chart and the calculated energy values."""
    times = curve.get("times") or []
    dc = ac = eff = None
    if times:
        dc_pts = [(ha_client.epoch(t), p) for t, p in zip(times, curve["power"])
                  if ha_client.epoch(t) is not None]
        if len(dc_pts) > 1:
            dc = round(_integrate_kwh(dc_pts), 2)
    mapping = ha_client.get_mapping()
    # Same gating as the overlay: feature flag + configured + a mapped power entity.
    if (db_reader.get_setting("wallbox_enabled", "0") == "1" and times
            and ha_client.is_configured() and mapping.get("power")):
        hist = ha_client.get_history(mapping["power"], times[0], times[-1])
        if hist:
            # Resample hist onto car curve's timestamps using step-hold (same as chart overlay)
            resampled_ac = []
            j, last = 0, None
            for t in times:
                e = ha_client.epoch(t)
                if e is None:
                    resampled_ac.append(None)
                    continue
                while j < len(hist) and hist[j][0] <= e:
                    last = hist[j][1]
                    j += 1
                resampled_ac.append(last)
            # Integrate only the resampled points that have values
            if resampled_ac and any(v is not None for v in resampled_ac):
                ac_pts = [(ha_client.epoch(t), p) for t, p in zip(times, resampled_ac)
                          if p is not None and ha_client.epoch(t) is not None]
                if len(ac_pts) > 1:
                    ac = round(_integrate_kwh(ac_pts), 2)
    if ac and ac > 0:
        # Defensive plausibility guard. AC from the wall must be ≥ DC into the battery, and a real
        # onboard charger is well above 50% efficient — so AC more than ~2× DC, OR any AC we cannot
        # validate because DC is zero/missing, is never physical. It means a leaked/over-wide window
        # OR a mis-mapped wallbox entity (e.g. a cumulative kWh meter mapped as the power sensor:
        # FB report — 10889 kWh AC, 0.1% efficiency). Keep AC only when a positive DC validates it;
        # otherwise discard it rather than show an absurd comparison or bill HOME cost on it
        # (compute_cost then falls back to the DC/SOC energy).
        if dc and ac <= dc * 2:
            eff = round(100 * dc / ac, 1)
        else:
            ac = None
    return {"dc_kwh": dc, "ac_kwh": ac, "eff": eff}


def _wallbox_sessions_grouped() -> list:
    """Charges-with-power nested year → month → day, each session carrying the
    AC-vs-DC kWh comparison; node totals + efficiency rolled up."""
    from collections import OrderedDict
    lang = db_reader.get_language()
    years: "OrderedDict" = OrderedDict()
    for r in db_reader.charges_with_power():
        dt = db_reader._local_dt(r["started_at"])
        if dt is None:
            continue
        e = _session_energy(db_reader.get_charge_power_curve(r["id"]))
        sess = {"id": r["id"], "time": dt.strftime("%H:%M"), **e}
        yr, mo, day = dt.strftime("%Y"), i18n.fmt_month_year(lang, dt), i18n.fmt_day_month_year(lang, dt)
        Y = years.setdefault(yr, {"label": yr, "ac": 0.0, "dc": 0.0, "months": OrderedDict()})
        M = Y["months"].setdefault(mo, {"label": mo, "ac": 0.0, "dc": 0.0, "days": OrderedDict()})
        D = M["days"].setdefault(day, {"label": day, "ac": 0.0, "dc": 0.0, "sessions": []})
        D["sessions"].append(sess)
        for node in (Y, M, D):
            if e["ac_kwh"]:
                node["ac"] = round(node["ac"] + e["ac_kwh"], 2)
            if e["dc_kwh"]:
                node["dc"] = round(node["dc"] + e["dc_kwh"], 2)

    def _eff(n):
        return round(100 * n["dc"] / n["ac"], 1) if n["ac"] else None
    trees = list(years.values())
    for Y in trees:
        Y["eff"] = _eff(Y)
        for M in Y["months"].values():
            M["eff"] = _eff(M)
            for D in M["days"].values():
                D["eff"] = _eff(D)
    return trees


@app.get("/api/wallbox/sessions", response_class=HTMLResponse)
async def wallbox_sessions(request: Request):
    """Year/month/day history tree with the AC-vs-DC kWh comparison per session."""
    return templates.TemplateResponse(request, "partials/wallbox_sessions.html", _ctx(
        tree=_wallbox_sessions_grouped(),
    ))


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
    """Lifetime AC delivered vs DC into battery across all sessions with data."""
    ac = dc = 0.0
    for r in db_reader.charges_with_power():
        e = _session_energy(db_reader.get_charge_power_curve(r["id"]))
        if e["ac_kwh"]:
            ac += e["ac_kwh"]
        if e["dc_kwh"]:
            dc += e["dc_kwh"]
    return {"ac": round(ac, 2) if ac else None,
            "dc": round(dc, 2) if dc else None,
            "eff": round(100 * dc / ac, 1) if ac else None}


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
        "charge_types": db_reader.CHARGE_TYPES,
        "cost_oob": True,         # also refresh the cost cell (it changes with the type/basis)
        "cost_title": cost_title,
    })


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
    db_reader.set_setting("wallbox_active_profile", "")  # prices edited directly → stale
    t = i18n.get_t(db_reader.get_language())
    return HTMLResponse(f'<span style="color:#22c55e;font-size:13px">{t("costs_saved")}</span>')


@app.post("/api/costs/mode", response_class=HTMLResponse)
async def save_cost_mode(request: Request):
    """Switch between flat (24h) and time-of-use pricing. Bands/method untouched."""
    form = await request.form()
    cfg = db_reader.get_cost_config()
    db_reader.save_cost_config(form.get("cost_mode", "flat"), cfg["method"], cfg["bands"])
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


@app.post("/api/settings/abrp", response_class=HTMLResponse)
async def save_abrp(request: Request):
    """Enable/disable ABRP live telemetry and store the user's personal token."""
    form = await request.form()
    db_reader.set_setting("abrp_enabled", "1" if form.get("abrp_enabled") in ("1", "on", "true") else "0")
    tok = (form.get("abrp_token") or "").strip()
    if tok:  # masked field: only overwrite on a non-empty submit (keep existing otherwise)
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
    import csv, io
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
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
    saving), so the user can verify host/port/credentials/TLS first."""
    form = await request.form()
    import asyncio
    ok, reason = await asyncio.get_event_loop().run_in_executor(
        None, lambda: mqtt_check.check_connection(
            form.get("mqtt_broker", ""),
            form.get("mqtt_port", "1883"),
            form.get("mqtt_user") or None,
            form.get("mqtt_pass") or None,
            form.get("mqtt_tls") in ("1", "on", "true"),
            form.get("mqtt_tls_insecure") in ("1", "on", "true"),
        ))
    t = i18n.get_t(db_reader.get_language())
    if ok:
        return HTMLResponse(f'<span style="color:#22c55e;font-size:13px">🟢 {t("mqtt_connected")}</span>')
    return HTMLResponse(f'<span style="color:#ef4444;font-size:13px">🔴 {t("mqtt_failed")}: {reason}</span>')


# Every collapsible card on the Settings accordion. Used both to build the initial
# open/collapsed map and as the allowlist for the ui-state save endpoint.
_UI_CARD_KEYS = {"locale", "vehicle", "battery", "polling", "charge_detect", "advanced",
                 "abrp", "geocoder", "charger_locator", "wallbox", "mqtt",
                 "database", "export", "diagnostics"}

# Settings sections flagged "new in a recent release": the section shows a NEW badge on its header
# until the user OPENS it (card_seen_<key>=1, badge never returns — so a new feature isn't missed if
# buried in the changelog). Maintenance: add a section's key here when you SHIP that new section,
# then drop it a release or two later. Empty = no section is "new" right now (the mechanism is idle).
_NEW_SETTINGS_SECTIONS: set[str] = set()


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
    if not db_reader.get_setting("abrp_token", ""):
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
    db_reader.set_setting("language", lang if lang in ("en", "it", "fr", "de") else "en")
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
    ))


@app.get("/api/v2l-card", response_class=HTMLResponse)
async def v2l_card(request: Request):
    """The Overview's V2L block, refreshed live (every 10 s, matching the V2L poll cadence) so the
    instantaneous power tracks the load during a session — the parent "Last charge" card is static."""
    return templates.TemplateResponse(request, "partials/v2l_card.html",
                                      _ctx(v2l=db_reader.get_v2l_status()))


def _configured_charge_limit() -> int | None:
    """The car's configured max-charge SoC (the % it stops charging at), persisted by the poller
    from each status read (and on a Mate set). Lets the Overview hero label the charge ETA with the
    real limit instead of a hardcoded 100. None if never seen → the template falls back to 100."""
    try:
        return int(db_reader.get_setting("charge_limit_percent", "") or 0) or None
    except (TypeError, ValueError):
        return None


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
    gps_ids = ("3724", "3725", "2190", "2191")   # 3724=longitude, 3725=latitude (2190/2191 fallbacks)
    return JSONResponse({
        "gps_signals": {k: sig.get(k) for k in gps_ids if k in sig},
        "parsed": {"latitude":  float(sig.get("3725") or sig.get("2190") or 0),
                   "longitude": float(sig.get("3724") or sig.get("2191") or 0)},
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
    return Response(content=body, media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=leapmotor-mate-diagnostics.txt"})


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
    comfort = _comfort_rows(vin)
    return templates.TemplateResponse(request, "partials/cmd_grid.html", _ctx(
        status=status, comfort=comfort, **_wins_ctx(),
        ac_off_shown=capability_profile.command_shown(vin, "climate_off"),
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
        db_reader.set_setting("boost_until", str(time.time() + 60))   # re-poll the car within seconds, not 30s
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
        db_reader.set_setting("boost_until", str(time.time() + 60))   # re-poll the car within seconds, not 30s
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
        db_reader.set_setting("boost_until", str(time.time() + 60))   # re-poll the car within seconds, not 30s
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
    if not db_reader.get_setting("battery_capacity_nominal_kwh", ""):
        db_reader.set_setting("battery_capacity_nominal_kwh",
                              db_reader.get_setting("battery_capacity_kwh", str(kwh)))
    db_reader.set_setting("battery_capacity_kwh", str(kwh))
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


def _car_picture_cache_path() -> str:
    db_path = os.environ.get("DB_PATH", "leapmotor_mate.db")
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), "car_picture.png")


def _car_picture_pkg_path() -> str:
    db_path = os.environ.get("DB_PATH", "leapmotor_mate.db")
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), "car_picture_pkg.zip")


# Composed images memoised by body-state signature so a 30s hero refresh on an unchanged state
# doesn't recomposite. Cleared when the package is re-downloaded.
_car_image_memo: dict = {}
# The hero <img> carries a ?v=<state> token, so each distinct state is a distinct URL → a state
# change is a fresh fetch while an unchanged state is served from the browser cache (no re-download
# on every 30s hero refresh).
_CAR_IMG_CACHE = {"Cache-Control": "max-age=300"}


@app.get("/api/car-picture")
async def car_picture(refresh: int = 0):
    """Serve the owner's vehicle image — composed LIVE from the per-vehicle layer package to reflect
    the current state (charge cable, charging animation, trunk), like the official app. The package
    ZIP is cached to disk (it changes only if the car/colour changes; ?refresh=1 re-downloads it).
    Falls back to the package's static render, then the legacy cached PNG, on any problem — so the
    Overview never breaks."""
    import asyncio
    pkg_path = _car_picture_pkg_path()
    pkg = None
    if not refresh and os.path.exists(pkg_path):
        try:
            with open(pkg_path, "rb") as f:
                pkg = f.read()
        except OSError:
            pkg = None
    if pkg is None:
        pkg = await asyncio.get_event_loop().run_in_executor(None, command_client.get_car_picture_package)
        if pkg:
            try:
                with open(pkg_path, "wb") as f:
                    f.write(pkg)
            except OSError:
                pass
            _car_image_memo.clear()
            car_image.clear_cache()
    if not pkg:
        legacy = _car_picture_cache_path()
        if os.path.exists(legacy):
            return FileResponse(legacy, media_type="image/png")
        return Response(status_code=404)

    status = db_reader.get_latest_status() or {}
    sig = tuple(bool(status.get(k)) for k in (
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


@app.post("/api/command/{name}", response_class=HTMLResponse)
async def run_command(name: str, background_tasks: BackgroundTasks):
    fn = _COMMANDS.get(name)
    if not fn:
        return HTMLResponse('<span style="color:#ef4444">Unknown command</span>', status_code=400)

    # The car locks some controls (sunshade, trunk, windows, lock) while moving —
    # intercept the press and show the same notice the official app does, instead of
    # bouncing it off the car. data-warn tells the Commands page to leave the message
    # up (no grid refetch).
    warn = _blocked_while_driving(name, db_reader.get_latest_status() or {},
                                  i18n.get_t(db_reader.get_language()))
    if warn:
        return HTMLResponse(f'<span data-warn="1" style="color:#fbbf24">⚠️ {warn}</span>')

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
        return HTMLResponse(f'<span data-warn="1" data-cooldown="1" style="color:#fbbf24">⏳ {msg}</span>')
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
        return HTMLResponse('<span style="color:#22c55e">✓ Done</span>')

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
            return HTMLResponse('<span data-slow="1" style="color:#60a5fa;display:inline-flex;align-items:center;gap:4px"><svg style="animation:spin 1s linear infinite;width:14px;height:14px" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/></svg></span><style>@keyframes spin{to{transform:rotate(360deg)}}</style>')
        return HTMLResponse('<span style="color:#22c55e">✓ Done</span>')
    return HTMLResponse(_cmd_error_html(msg))


# ── Battery options — European models only (verified specs) ──────────────────
# T03: single EU variant → auto-set (no user selection needed)
# C10/B10/B05: two EU variants → selector shown

# Per-variant USABLE (net) capacity, kWh — the energy between the BMS's protective
# limits, not the gross pack. Sourced from EV Database / manufacturer sheets (cross-checked):
#   T03   gross 37.3 → usable 36.0          C10 RWD gross 72.0 → usable 69.9
#   B10 Pro     56.2 → 55.0                 C10 AWD gross 84.0 → usable 81.9
#   B10 Pro Max 67.1 → 65.0 (2.1 kWh / 3.1% buffer, confirmed by 2 sources)
#   B05 Pro     56.2 → 55.0   ·  B05 Pro Max 67.1 → 65.0 (shares the B10 pack; WLTP 401 / 482)
# These are the DEFAULTS for new setups; existing installs keep whatever they configured
# (no silent migration of a calibrated value). NB on the B10 Pro Max: the car's DISPLAYED
# SoC 0–100% is calibrated close to the GROSS 67.1 — a real-car ∫V·I measurement matched
# ΔSoC×67.1 within ~1% on mid-SoC charges — so a B10 owner may see energy run ~3% low on
# the usable default; the Settings "use measured" button (from the SoH estimator) lets them
# self-correct toward the value their own car actually uses.
_EU_BATTERY_MAP: dict[str, list[dict]] = {
    "T03": [
        {"v": "36.0", "label": "36.0 kWh usable"},
    ],
    "C10": [
        {"v": "69.9", "label": "69.9 kWh usable — RWD"},
        {"v": "81.9", "label": "81.9 kWh usable — AWD"},
    ],
    "B10": [
        {"v": "55.0", "label": "55.0 kWh usable — Pro · 361 km WLTP"},
        {"v": "65.0", "label": "65.0 kWh usable — Pro Max · 434 km WLTP"},
    ],
    "B05": [
        {"v": "55.0", "label": "55.0 kWh usable — Pro · 401 km WLTP"},
        {"v": "65.0", "label": "65.0 kWh usable — Pro Max · 482 km WLTP"},
    ],
}


# ── Setup wizard ─────────────────────────────────────────────────────────────

@app.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    return templates.TemplateResponse(request, "setup.html", {})


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
    options = _EU_BATTERY_MAP.get(result["car_type"], [])
    if len(options) == 1:
        # Single EU variant — tell the frontend to auto-set, no selector needed
        result["battery_kwh"]   = options[0]["v"]
        result["battery_label"] = options[0]["label"]
    elif options:
        result["battery_options"] = options
    # else: unknown/non-EU model → frontend falls back to manual input
    return JSONResponse(result)


@app.post("/setup", response_class=HTMLResponse)
async def setup_submit(request: Request):
    form = await request.form()
    user     = (form.get("user", "") or "").strip()
    pwd      = (form.get("password", "") or "").strip()
    pin      = (form.get("pin", "") or "").strip()
    battery  = (form.get("battery", "65.0") or "65.0").strip()
    lang     = form.get("language", "en")
    car_type = (form.get("car_type", "") or "").strip().upper()
    vin      = (form.get("vin", "") or "").strip()

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
    db_reader.set_setting("language", lang if lang in ("en", "it", "fr", "de") else "en")

    # Pre-populate vehicles table so the UI shows model info before the first poller run
    if vin and car_type:
        db_reader.upsert_vehicle(vin, car_type)

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
