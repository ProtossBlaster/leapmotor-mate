"""LeapMotor Mate — web server."""
import os
import sys
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

sys.path.insert(0, str(Path(__file__).parent))
import db_reader
import command_client
import i18n

app = FastAPI(title="LeapMotor Mate")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


# ── Setup check middleware ────────────────────────────────────────────────────

@app.middleware("http")
async def setup_check(request: Request, call_next):
    path = request.url.path
    if path.startswith("/setup") or path.startswith("/api/") or path.startswith("/static/"):
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

def _state_color(pos: dict) -> str:
    if pos.get("charging"): return "text-yellow-400"
    if pos.get("speed_kmh", 0) > 1: return "text-blue-400"
    return "text-green-400"

def _ctx(**kwargs):
    """Add shared helpers + i18n to every template context."""
    lang = db_reader.get_language()
    t = i18n.get_t(lang)
    def state_label(pos: dict) -> str:
        if pos.get("charging"): return t("state_charging")
        if pos.get("speed_kmh", 0) > 1: return t("state_driving")
        return t("state_parked")
    return {**kwargs, "lang": lang, "t": t,
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
    return templates.TemplateResponse(request, "trips.html", _ctx(
        page="trips", vehicle=vehicle, grouped=grouped,
        total=total, highlight=highlight,
    ))


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
            "fl": {"bar": bar("2646"), "low": i("2641") == 1},
            "fr": {"bar": bar("2653"), "low": i("2648") == 1},
            "rl": {"bar": bar("2660"), "low": i("2655") == 1},
            "rr": {"bar": bar("2667"), "low": i("2662") == 1},
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
        "temps": {"battery": f("1182"), "cabin": f("1349"), "outside": f("2101")},
    }


@app.get("/vehicle", response_class=HTMLResponse)
async def vehicle_page(request: Request):
    vehicle, _ = db_reader.get_vehicle()
    return templates.TemplateResponse(request, "vehicle.html", _ctx(
        page="vehicle", vehicle=vehicle,
    ))


@app.get("/api/vehicle-status", response_class=HTMLResponse)
async def vehicle_status_api(request: Request):
    import asyncio
    signals = await asyncio.get_event_loop().run_in_executor(None, command_client.get_fresh_signals)
    vs = _parse_vehicle_status(signals) if signals else None
    if vs:
        # Signal 1724 on the B10 is the fixed panoramic glass (always non-zero), not the
        # shade — use the state tracked from commands instead (None if never set).
        shade = db_reader.get_setting("sunshade_last_state", "")
        vs["windows"]["sunshade"] = (shade == "1") if shade != "" else None
    return templates.TemplateResponse(request, "partials/vehicle_status.html", _ctx(vs=vs))


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    vehicle, settings = db_reader.get_vehicle()
    prices = db_reader.get_charge_prices()
    settings = {**settings, **prices}
    return templates.TemplateResponse(request, "settings.html", _ctx(
        page="settings", vehicle=vehicle, settings=settings,
        charge_types=db_reader.CHARGE_TYPES,
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


@app.get("/api/charge/{charge_id}/power-chart", response_class=HTMLResponse)
async def charge_power_chart(request: Request, charge_id: int):
    """Lazy-loaded power-over-time chart for one charge session (expandable in the list)."""
    curve = db_reader.get_charge_power_curve(charge_id)
    return templates.TemplateResponse(request, "partials/charge_power_chart.html", _ctx(
        cid=charge_id,
        labels=curve["labels"], power=curve["power"], soc=curve["soc"],
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
    return HTMLResponse('<span style="color:#22c55e;font-size:13px">✓ Saved — costs recalculated</span>')
    
    
@app.post("/api/settings/mqtt", response_class=HTMLResponse)
async def save_mqtt_settings(request: Request):
    form = await request.form()
    db_reader.set_setting("mqtt_enabled", "1" if form.get("mqtt_enabled") else "0")
    db_reader.set_setting("mqtt_discovery", "1" if form.get("mqtt_discovery") else "0")
    db_reader.set_setting("mqtt_tls", "1" if form.get("mqtt_tls") else "0")
    db_reader.set_setting("mqtt_tls_insecure", "1" if form.get("mqtt_tls_insecure") else "0")
    for key in ["mqtt_broker", "mqtt_port", "mqtt_user", "mqtt_pass", "mqtt_prefix"]:
        val = form.get(key, "").strip()
        db_reader.set_setting(key, val)
    return HTMLResponse('<span style="color:#22c55e">✓ Settings saved</span>')


@app.post("/api/settings/homeassistant", response_class=HTMLResponse)
async def save_ha(request: Request):
    form = await request.form()
    url = (form.get("ha_webhook_url", "") or "").strip()
    db_reader.set_setting("ha_webhook_url", url)
    t = i18n.get_t(db_reader.get_language())
    return HTMLResponse(f'<span style="color:#22c55e">✓ {t("saved")}</span>')


@app.post("/api/settings/language")
async def set_language(request: Request):
    """Change the UI language after setup. Saved to the DB, then the page is reloaded
    (HX-Refresh) so every server-rendered string switches to the new language."""
    form = await request.form()
    lang = form.get("language", "en")
    db_reader.set_setting("language", lang if lang in ("en", "it", "fr") else "en")
    return Response(status_code=204, headers={"HX-Refresh": "true"})


@app.post("/api/settings/abrp", response_class=HTMLResponse)
async def save_abrp(request: Request):
    """Save the optional ABRP live-data settings (per-user token + enable flag).
    The poller reads these from the DB each cycle and pushes telemetry when enabled."""
    form = await request.form()
    token = (form.get("abrp_token", "") or "").strip()
    enabled = "1" if form.get("abrp_enabled") == "1" else "0"
    db_reader.set_setting("abrp_token", token)
    db_reader.set_setting("abrp_enabled", enabled)
    t = i18n.get_t(db_reader.get_language())
    return HTMLResponse(f'<span style="color:#22c55e">✓ {t("saved")}</span>')


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
    import time
    import asyncio
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
    import time
    import asyncio
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
    # sunshade_open intentionally omitted: signal 1724 is panoramic glass (always non-zero on B10)
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
        _wait_labels = {"it": "Attendi", "fr": "Patientez"}
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
        if name == "open_sunshade":
            db_reader.set_sunshade_state(1)
        elif name == "close_sunshade":
            db_reader.set_sunshade_state(0)
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
    db_reader.set_setting("language", lang if lang in ("en", "it", "fr") else "en")

    # Optional ABRP live-data: token is per-user, enabled implicitly when one is given.
    abrp_token = (form.get("abrp_token", "") or "").strip()
    db_reader.set_setting("abrp_token", abrp_token)
    db_reader.set_setting("abrp_enabled", "1" if abrp_token else "0")

    # Pre-populate vehicles table so the UI shows model info before the first poller run
    if vin and car_type:
        db_reader.upsert_vehicle(vin, car_type)

    db_reader.set_setting("setup_complete", "1")

    # Reset the command session so it picks up new credentials
    command_client._session._reset()

    return RedirectResponse(request.headers.get("x-ingress-path", "") + "/", status_code=303)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("WEB_PORT", 4000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
