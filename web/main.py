"""LeapMotor Mate — web server."""
import json
import os
import sys
import time
from pathlib import Path

from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

sys.path.insert(0, str(Path(__file__).parent))
import db_reader
import command_client
import i18n
import ha_client
import geocode
import mqtt_test
import auth

MATE_VERSION = "1.9.0"  # bump together with the git tag + add-on config.yaml at release

app = FastAPI(title="LeapMotor Mate")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
# Expose to templates so the sidebar can show the signed-in user + a logout link.
templates.env.globals["oidc_enabled"] = auth.is_active()


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
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


# ── Setup check middleware ────────────────────────────────────────────────────

@app.middleware("http")
async def setup_check(request: Request, call_next):
    path = request.url.path
    if (path.startswith("/setup") or path.startswith("/api/")
            or path.startswith("/static/") or path.startswith("/auth")):
        return await call_next(request)
    # If env vars are set, skip wizard (dev mode)
    if os.environ.get("LEAPMOTOR_USER"):
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
    return "text-green-400"

def _ctx(**kwargs):
    """Add shared helpers + i18n to every template context."""
    lang = db_reader.get_language()
    t = i18n.get_t(lang)
    def state_label(pos: dict) -> str:
        if pos.get("charging"): return t("state_charging")
        if _driving(pos): return t("state_driving")
        return t("state_parked")
    return {**kwargs, "lang": lang, "t": t, "version": MATE_VERSION,
            "wallbox_enabled": db_reader.get_setting("wallbox_enabled", "0") == "1",
            "currency": db_reader.get_currency(),
            "soc_color": _soc_color, "state_label": state_label, "state_color": _state_color}


# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def overview(request: Request):
    vehicle, settings = db_reader.get_vehicle()
    status = db_reader.get_latest_status()
    trips = db_reader.get_trips(limit=3)
    charges = db_reader.get_charges(limit=1)
    return templates.TemplateResponse(request, "overview.html", _ctx(
        page="overview", vehicle=vehicle, settings=settings,
        status=status, recent_trips=trips,
        last_charge=charges[0] if charges else None,
    ))


@app.get("/trips", response_class=HTMLResponse)
async def trips_page(request: Request, highlight: int = 0):
    vehicle, _ = db_reader.get_vehicle()
    grouped = db_reader.get_trips_grouped()
    total   = sum(y["count"] for y in grouped)
    summary = db_reader.get_trips_summary()
    return templates.TemplateResponse(request, "trips.html", _ctx(
        page="trips", vehicle=vehicle, grouped=grouped,
        total=total, highlight=highlight,
        summary=summary,
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
    return templates.TemplateResponse(request, "statistics.html", _ctx(
        page="statistics", vehicle=vehicle,
        grouped=grouped, totals=totals,
    ))


@app.get("/commands", response_class=HTMLResponse)
async def commands(request: Request):
    vehicle, _ = db_reader.get_vehicle()
    status = db_reader.get_latest_status()
    return templates.TemplateResponse(request, "commands.html", _ctx(
        page="commands", vehicle=vehicle, status=status,
    ))


def _parse_vehicle_status(sig: dict) -> dict:
    """Parse tyres / doors / windows / temps from a fresh signal dict (live, not DB)."""
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
    return {
        "tyres": {
            "fl": {"bar": bar("2667"), "low": i("2641") == 1},
            "fr": {"bar": bar("2653"), "low": i("2648") == 1},
            "rl": {"bar": bar("2646"), "low": i("2655") == 1},
            "rr": {"bar": bar("2660"), "low": i("2662") == 1},
        },
        "doors": {
            "driver":     is_open("1277"), "passenger": is_open("1278"),
            "rear_left":  is_open("1279"), "rear_right": is_open("1280"),
            "trunk":      is_open("1281"),
        },
        "windows": {
            "fl": is_open("1693"), "fr": is_open("1694"),
            "rl": is_open("1695"), "rr": is_open("1696"),
            "sunshade": is_open("1724"),
        },
        "temps": {"battery": f("1182"), "cabin": f("1349")},  # no ambient-temp signal exists
    }


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
    key = db_reader.get_setting("geocoder_key", "") or None
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
    key = db_reader.get_setting("geocoder_key", "") or None
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
    return HTMLResponse(f'<span style="color:#ef4444">✗ {msg}</span>')


@app.get("/api/vehicle-status", response_class=HTMLResponse)
async def vehicle_status_api(request: Request):
    import asyncio
    signals = await asyncio.get_event_loop().run_in_executor(None, command_client.get_fresh_signals)
    vs = _parse_vehicle_status(signals) if signals else None
    return templates.TemplateResponse(request, "partials/vehicle_status.html", _ctx(vs=vs))


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    vehicle, settings = db_reader.get_vehicle()
    prices = db_reader.get_charge_prices()
    settings = {**settings, **prices,
                "abrp_enabled": db_reader.get_setting("abrp_enabled", "0"),
                "abrp_token": db_reader.get_setting("abrp_token", ""),
                "mqtt_enabled": db_reader.get_setting("mqtt_enabled", "0"),
                "mqtt_broker": db_reader.get_setting("mqtt_broker", ""),
                "mqtt_port": db_reader.get_setting("mqtt_port", "1883"),
                "mqtt_user": db_reader.get_setting("mqtt_user", ""),
                "mqtt_pass": db_reader.get_setting("mqtt_pass", ""),
                "mqtt_prefix": db_reader.get_setting("mqtt_prefix", "leapmotor"),
                "mqtt_tls": db_reader.get_setting("mqtt_tls", "0"),
                "mqtt_tls_insecure": db_reader.get_setting("mqtt_tls_insecure", "0"),
                "mqtt_discovery": db_reader.get_setting("mqtt_discovery", "1"),
                "geocoder_provider": db_reader.get_setting("geocoder_provider", ""),
                "geocoder_key": db_reader.get_setting("geocoder_key", "")}
    return templates.TemplateResponse(request, "settings.html", _ctx(
        page="settings", vehicle=vehicle, settings=settings,
        charge_types=db_reader.CHARGE_TYPES,
        ha_url=db_reader.get_setting("ha_url", ""),
        ha_has_token=bool(db_reader.get_setting("ha_token", "")),
        ha_supervisor=bool(os.environ.get("SUPERVISOR_TOKEN")),
        currencies=db_reader.CURRENCIES,
        currency_code=db_reader.get_currency_code(),
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
        db_reader.set_setting("ha_token", form.get("ha_token").strip())
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
async def wallbox_entities(request: Request):
    """Lazy-loaded entity picker: discovered HA entities + role selects,
    pre-filled with the saved mapping or an auto-detected best guess."""
    all_entities = ha_client.list_entities(only_wallbox=True)
    # Auto-detected defaults for any role, overridden by what the user saved →
    # new roles get a sensible pre-fill while saved choices are preserved.
    mapping = {**ha_client.auto_map(all_entities), **ha_client.get_mapping()}
    # Offer only the wallbox device's own sensors in the dropdowns (not every HA entity).
    entities = ha_client.filter_device_entities(all_entities, mapping)
    return templates.TemplateResponse(request, "partials/wallbox_entities.html", _ctx(
        entities=entities, mapping=mapping, roles=ha_client.WB_ROLES,
    ))


@app.post("/api/settings/wallbox-entities", response_class=HTMLResponse)
async def save_wallbox_entities(request: Request):
    form = await request.form()
    mapping = {role: form.get(role, "").strip()
               for role in ha_client.WB_ROLES if form.get(role, "").strip()}
    db_reader.set_setting("wallbox_entities", json.dumps(mapping))
    t = i18n.get_t(db_reader.get_language())
    return HTMLResponse(f'<span style="color:#22c55e;font-size:13px">{t("wallbox_saved")}</span>')


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
    """Trapezoidal integral of (epoch_seconds, kW) points → kWh."""
    e = 0.0
    for i in range(1, len(points)):
        dt = (points[i][0] - points[i - 1][0]) / 3600.0
        e += (points[i][1] + points[i - 1][1]) / 2 * dt
    return e


def _session_energy(curve: dict) -> dict:
    """Energy comparison for one charge: DC into battery vs AC from the wallbox,
    both integrated from real power (so AC ≥ DC and efficiency < 100%)."""
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
        if len(hist) > 1:
            ac = round(_integrate_kwh(hist), 2)
    if dc and ac and ac > 0:
        eff = round(100 * dc / ac, 1)
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
    return templates.TemplateResponse(request, "partials/wallbox_control.html", _ctx(
        cfg=ha_client.get_max_current_config(), applied=ok,
    ))


# ── Charge type update (HTMX) ────────────────────────────────────────────────

@app.post("/api/charges/{charge_id}/type", response_class=HTMLResponse)
async def set_charge_type(request: Request, charge_id: int):
    form = await request.form()
    location_type = form.get("location_type", "HOME")
    charge = db_reader.update_charge_type(charge_id, location_type)
    return templates.TemplateResponse(request, "partials/charge_type_badge.html", {
        "charge": charge,
        "charge_types": db_reader.CHARGE_TYPES,
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
    if "abrp_token" in form:
        db_reader.set_setting("abrp_token", (form.get("abrp_token") or "").strip())
    t = i18n.get_t(db_reader.get_language())
    return HTMLResponse(f'<span style="color:#22c55e;font-size:13px">{t("abrp_saved")}</span>')


@app.post("/api/settings/geocoder", response_class=HTMLResponse)
async def save_geocoder(request: Request):
    """Store the optional TomTom API key used for better address/house-number
    coverage on the Navigation page. Empty = keyless Photon/Nominatim."""
    form = await request.form()
    if "geocoder_provider" in form:
        db_reader.set_setting("geocoder_provider", (form.get("geocoder_provider") or "").strip())
    if "geocoder_key" in form:
        db_reader.set_setting("geocoder_key", (form.get("geocoder_key") or "").strip())
    t = i18n.get_t(db_reader.get_language())
    return HTMLResponse(f'<span style="color:#22c55e;font-size:13px">{t("geocoder_saved")}</span>')


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
            db_reader.set_setting(key, (form.get(key) or "").strip())
    t = i18n.get_t(db_reader.get_language())
    return HTMLResponse(f'<span style="color:#22c55e;font-size:13px">{t("mqtt_saved")}</span>')


@app.post("/api/settings/mqtt/test", response_class=HTMLResponse)
async def test_mqtt(request: Request):
    """Try to connect to the broker with the values currently in the form (before
    saving), so the user can verify host/port/credentials/TLS first."""
    form = await request.form()
    import asyncio
    ok, reason = await asyncio.get_event_loop().run_in_executor(
        None, lambda: mqtt_test.test_connection(
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


# ── HTMX partial ─────────────────────────────────────────────────────────────

@app.get("/api/charging-live", response_class=HTMLResponse)
async def charging_live(request: Request):
    status = db_reader.get_latest_status()
    return templates.TemplateResponse(request, "partials/charging_live.html", _ctx(status=status))


@app.get("/api/status-card", response_class=HTMLResponse)
async def status_card(request: Request):
    status = db_reader.get_latest_status()
    vehicle, _ = db_reader.get_vehicle()
    return templates.TemplateResponse(request, "partials/status_card.html", _ctx(
        status=status, vehicle=vehicle,
    ))


# ── Command routes ────────────────────────────────────────────────────────────

_COMMANDS = {
    "lock":              command_client.lock,
    "unlock":            command_client.unlock,
    "open_trunk":        command_client.open_trunk,
    "close_trunk":       command_client.close_trunk,
    "find_car":          command_client.find_car,
    "ac_on":             command_client.ac_on,
    "quick_cool":        command_client.quick_cool,
    "quick_heat":        command_client.quick_heat,
    "windshield_defrost":command_client.windshield_defrost,
    "open_windows":      command_client.open_windows,
    "close_windows":     command_client.close_windows,
    "battery_preheat":   command_client.battery_preheat,
    "open_sunshade":     command_client.open_sunshade,
    "close_sunshade":    command_client.close_sunshade,
}

@app.get("/api/cmd-grid", response_class=HTMLResponse)
async def cmd_grid(request: Request):
    status = db_reader.get_latest_status()
    return templates.TemplateResponse(request, "partials/cmd_grid.html", _ctx(status=status))


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


@app.get("/api/car-picture")
async def car_picture(refresh: int = 0):
    """Serve the owner's vehicle PNG. Cached to disk (picture changes rarely) so the
    overview doesn't trigger an API call + ZIP download on every page load.
    Use ?refresh=1 to force a re-download."""
    cache = _car_picture_cache_path()
    if not refresh and os.path.exists(cache):
        return FileResponse(cache, media_type="image/png")
    import asyncio
    data = await asyncio.get_event_loop().run_in_executor(None, command_client.get_car_picture)
    if not data:
        if os.path.exists(cache):
            return FileResponse(cache, media_type="image/png")
        return Response(status_code=404)
    try:
        with open(cache, "wb") as f:
            f.write(data)
    except OSError:
        pass
    return Response(content=data, media_type="image/png")


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
        return HTMLResponse(f'<span style="color:#22c55e">✓ Limit set to {percent}%</span>')
    return HTMLResponse(f'<span style="color:#ef4444">✗ {msg}</span>')


_OPTIMISTIC = {
    "lock":          {"is_locked": 1},
    "unlock":        {"is_locked": 0},
    "open_trunk":    {"trunk_open": 1},
    "close_trunk":   {"trunk_open": 0},
    "open_windows":  {"windows_open": 1},
    "close_windows": {"windows_open": 0},
    "open_sunshade": {"sunshade_open": 1},
    "close_sunshade":{"sunshade_open": 0},
}

# Climate tiles: a tile that's ON is turned off by sending ac_switch (the only
# command that deactivates climate); a tile that's OFF sends its own mode command.
# Direction is decided from the real signal state. NO optimistic overlay — climate
# state is read from signals (2669 cool / 2681 heat / 1945 defrost / 1938 on), so the
# UI never shows a fake value. Frontend is unchanged; this is backend logic only.
_CLIMATE_TILES = {
    "ac_on":              "climate_on",
    "quick_cool":         "climate_cooling",
    "quick_heat":         "climate_heating",
    "windshield_defrost": "climate_defrost",
}


_FIELD_CHECK = {
    "is_locked":       lambda sig: int(sig.get("1298") or 0) == 1,
    "trunk_open":      lambda sig: int(sig.get("1281") or 0) != 0,
    "windows_open":    lambda sig: any(int(sig.get(k) or 0) != 0 for k in ("1693","1694","1695","1696")),
    "sunshade_open":   lambda sig: int(sig.get("1724") or 0) != 0,   # 1724 = shade opening % (0 = closed)
    "climate_on":      lambda sig: int(sig.get("1938") or 0) == 1,
    "climate_cooling": lambda sig: int(sig.get("2669") or 0) == 2,
    "climate_heating": lambda sig: int(sig.get("2681") or 0) == 2,
    "climate_defrost": lambda sig: int(sig.get("1945") or 0) == 2,
}

# Commands that trigger slow physical movement — UI shows ⏳ until confirmed
_SLOW_COMMANDS = {"open_sunshade", "close_sunshade", "open_trunk", "close_trunk"}


def _post_command_refresh(optimistic: dict, delay: int = 3):
    """Fetch fresh status after a command.
    If API confirms the expected state: write real data.
    If API does NOT confirm: clear the optimistic overlay so the UI shows the real state.
    """
    time.sleep(delay)
    signals = command_client.get_fresh_signals()
    if not signals:
        return
    for field, expected in optimistic.items():
        checker = _FIELD_CHECK.get(field)
        if checker and bool(checker(signals)) != bool(expected):
            db_reader.clear_optimistic_status()
            db_reader.save_fresh_signals(signals)
            return
    db_reader.save_fresh_signals(signals)


_CMD_COOLDOWN_S = 10     # match the HA integration's remote-action cooldown
_last_command_at = 0.0


@app.post("/api/command/{name}", response_class=HTMLResponse)
async def run_command(name: str, background_tasks: BackgroundTasks):
    fn = _COMMANDS.get(name)
    if not fn:
        return HTMLResponse('<span style="color:#ef4444">Unknown command</span>', status_code=400)

    # Remote-action cooldown (like the HA integration's 10s): don't fire commands too
    # close together — the previous one may still be completing on the car.
    global _last_command_at
    import time
    remaining = _CMD_COOLDOWN_S - (time.time() - _last_command_at)
    if remaining > 0:
        wait = int(remaining) + 1
        _wait_labels = {"it": "Attendi", "fr": "Patientez", "de": "Warten"}
        label = _wait_labels.get(db_reader.get_language(), "Wait")
        return HTMLResponse(f'<span style="color:#fbbf24">⏳ {label} {wait}s</span>')
    _last_command_at = time.time()

    # Climate: decide direction from the real state. A tile that's on → ac_switch
    # (deactivate); a tile that's off → its own command. No optimistic overlay.
    overrides = dict(_OPTIMISTIC.get(name) or {})
    field = _CLIMATE_TILES.get(name)
    if field:
        cur = db_reader.get_latest_status() or {}
        if cur.get(field):                      # currently on → turn off
            fn = command_client.ac_on           # ac_switch = deactivate climate
        overrides = {}                          # never fake climate state

    import asyncio
    ok, msg = await asyncio.get_event_loop().run_in_executor(None, fn)
    if ok:
        if overrides:
            db_reader.write_optimistic_status(overrides)
        # Climate commands take several seconds to reflect in signals → show the
        # spinner and refresh from real signals after a delay (like slow commands).
        slow = name in _SLOW_COMMANDS or field is not None
        background_tasks.add_task(_post_command_refresh, overrides or {}, 12 if slow else 3)
        if slow:
            return HTMLResponse('<span data-slow="1" style="color:#60a5fa;display:inline-flex;align-items:center;gap:4px"><svg style="animation:spin 1s linear infinite;width:14px;height:14px" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/></svg></span><style>@keyframes spin{to{transform:rotate(360deg)}}</style>')
        return HTMLResponse('<span style="color:#22c55e">✓ Done</span>')
    return HTMLResponse(f'<span style="color:#ef4444">✗ {msg}</span>')


# ── Battery options — European models only (verified specs) ──────────────────
# T03: single EU variant → auto-set (no user selection needed)
# C10/B10: two EU variants → selector shown

_EU_BATTERY_MAP: dict[str, list[dict]] = {
    "T03": [
        {"v": "37.3", "label": "37.3 kWh"},
    ],
    "C10": [
        {"v": "69.9", "label": "69.9 kWh — RWD"},
        {"v": "81.9", "label": "81.9 kWh — AWD"},
    ],
    "B10": [
        {"v": "56.2", "label": "56.2 kWh — Pro · 361 km WLTP"},
        {"v": "67.1", "label": "67.1 kWh — Pro Max · 434 km WLTP"},
    ],
}


# ── Setup wizard ─────────────────────────────────────────────────────────────

@app.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    return templates.TemplateResponse(request, "setup.html", {})


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
    battery  = (form.get("battery", "67.1") or "67.1").strip()
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
        battery_kwh = 67.1

    db_reader.set_setting("leapmotor_user", user)
    db_reader.set_setting("leapmotor_pass", pwd)
    db_reader.set_setting("leapmotor_pin", pin)
    db_reader.set_setting("battery_capacity_kwh", str(battery_kwh))
    db_reader.set_setting("language", lang if lang in ("en", "it", "fr", "de") else "en")

    # Pre-populate vehicles table so the UI shows model info before the first poller run
    if vin and car_type:
        db_reader.upsert_vehicle(vin, car_type)

    db_reader.set_setting("setup_complete", "1")

    # Reset the command session so it picks up new credentials
    command_client._session._reset()

    return RedirectResponse(request.headers.get("x-ingress-path", "") + "/", status_code=303)


# Install CSRF + OIDC auth. Registered here (after setup_check above) so the auth guard
# and SessionMiddleware nest OUTSIDE setup_check — an unauthenticated user is sent to the
# OIDC login before the setup wizard, closing the previously-open wizard too.
auth.register(app, templates)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("WEB_PORT", 4000))
    # Default to loopback so a bare run isn't LAN/internet-open. The container sets
    # WEB_HOST=0.0.0.0 (Docker/ingress controls real exposure); proxy_headers lets the
    # OIDC redirect_uri reflect X-Forwarded-Proto/Host behind a TLS-terminating proxy.
    host = os.environ.get("WEB_HOST", "127.0.0.1")
    uvicorn.run("main:app", host=host, port=port, reload=False, proxy_headers=True,
                forwarded_allow_ips="*")
