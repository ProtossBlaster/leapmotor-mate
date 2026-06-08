"""Persistent Leapmotor session — login once, reuse for all commands and status fetches."""
import os
import json
import time
import logging
import threading

from leapmotor_api import LeapmotorApiClient

log = logging.getLogger(__name__)

# App certificate location. The wizard writes the user-provided cert to /data/certs
# (persistent); fall back to the image-bundled CERT_DIR for local dev. Resolved at call
# time so certs uploaded mid-setup are picked up without a restart.
_DATA_CERT_DIR     = os.environ.get("DATA_CERT_DIR", "/data/certs")
_FALLBACK_CERT_DIR = os.environ.get("CERT_DIR", "certs")


def cert_dir() -> str:
    if (os.path.exists(os.path.join(_DATA_CERT_DIR, "app.crt"))
            and os.path.exists(os.path.join(_DATA_CERT_DIR, "app.key"))):
        return _DATA_CERT_DIR
    return _FALLBACK_CERT_DIR


def certs_present() -> bool:
    d = cert_dir()
    return (os.path.exists(os.path.join(d, "app.crt"))
            and os.path.exists(os.path.join(d, "app.key")))


# T03/EU status carries live data as named fields at the top level of `data` instead
# of a numeric-id `signal` sub-dict (C10/B10). Map per leapmotor-api 0.3.1; kept in
# sync with poller/client.py's copy.
_SIGNAL_TO_NAMED = {
    "47": "acInputSlowCharge", "1204": "soc", "100003": "preciseSoc",
    "1200": "chargeRemainTime", "1178": "batteryCurrent", "1177": "batteryVoltage",
    "1197": "dcInputFastCharge", "1149": "chargeState", "1182": "minBatteryTemp",
    "1186": "batteryThermalRequest", "3736": "chargeCompleted", "48": "healthyChargeEnabled",
    "3737": "chargeScheduleCancelledOnce",
    "3260": "expectedMileage", "2188": "liveRemainingRange", "3257": "maxRange", "3262": "rangeMode",
    "1319": "speed", "1318": "totalMileage", "1010": "gearStatus", "1944": "vehicleState",
    "1480": "parkingBrakeState", "6048": "speedLimit", "6047": "speedLimitUnit",
    "12054": "speedLimitActive",
    "3725": "latitude", "3724": "longitude",
    "1938": "acSwitch", "2183": "acSetting", "2184": "acSettingRight", "1349": "interiorTemp",
    "1943": "recirculationMode", "1945": "windshieldDefrost", "1946": "rearWindowHeating",
    "3713": "climateMode", "2669": "rapidCooling", "2681": "rapidHeating",
    "1939": "acOperateMode", "1941": "acAirVolume",
    "3727": "leftFrontWindowPercent", "3728": "rightFrontWindowPercent",
    "1879": "leftRearWindowPercent", "1880": "rightRearWindowPercent",
    "1693": "driverWindowStatus", "1694": "rightFrontWindowStatus",
    "1695": "leftRearWindowStatus", "1696": "rightRearWindowStatus",
    "1298": "driverDoorLockStatus", "1277": "lbcmDriverDoorStatus", "1278": "rbcmDriverDoorStatus",
    "1279": "lbcmLeftRearDoorStatus", "1280": "rbcmRightRearDoorStatus", "1281": "bbcmBackDoorStatus",
    "2667": "leftFrontTirePressure", "2653": "rightFrontTirePressure",
    "2646": "leftRearTirePressure", "2660": "rightRearTirePressure",
    "2641": "leftFrontTirePressureState", "2648": "rightFrontTirePressureState",
    "2655": "leftRearTirePressureState", "2662": "rightRearTirePressureState",
    "1256": "bcmKeyPositionOn1", "1257": "bcmKeyPositionOn2", "1258": "bcmKeyPositionOn3",
    "2100": "driverSeatHeating", "2101": "driverSeatVentilation",
    "2118": "passengerSeatHeating", "2119": "passengerSeatVentilation",
    "1816": "steeringWheelHeating", "1624": "steeringWheelHeaterMinutes",
    "1255": "vehicleSecurityActive", "3636": "sentryMode",
    "49": "leftMirrorHeating", "50": "rightMirrorHeating", "1724": "roofOpening",
}


def _named_fields_to_signal(data: dict) -> dict | None:
    """Rebuild a numeric-id `signal` dict from a T03/EU named-field response."""
    if not isinstance(data, dict):
        return None
    sig = {sid: data[name] for sid, name in _SIGNAL_TO_NAMED.items()
           if data.get(name) is not None}
    return sig or None


def _get_credentials() -> tuple[str, str, str]:
    """Read credentials from DB settings, falling back to env vars for dev."""
    try:
        import sys, pathlib
        sys.path.insert(0, str(pathlib.Path(__file__).parent))
        import db_reader as _dr
        user = _dr.get_setting("leapmotor_user") or os.environ.get("LEAPMOTOR_USER", "")
        pwd  = _dr.get_secret("leapmotor_pass") or os.environ.get("LEAPMOTOR_PASS", "")
        pin  = _dr.get_secret("leapmotor_pin")  or os.environ.get("LEAPMOTOR_PIN", "")
    except Exception:
        user = os.environ.get("LEAPMOTOR_USER", "")
        pwd  = os.environ.get("LEAPMOTOR_PASS", "")
        pin  = os.environ.get("LEAPMOTOR_PIN", "")
    return user, pwd, pin


def _make_client() -> LeapmotorApiClient:
    user, pwd, pin = _get_credentials()
    try:
        import sys, pathlib
        sys.path.insert(0, str(pathlib.Path(__file__).parent))
        import db_reader as _dr
        device_id = _dr.get_or_create_device_id()
    except Exception:
        device_id = None
    api = LeapmotorApiClient(
        username=user,
        password=pwd,
        operation_password=pin,
        app_cert_path=os.path.join(cert_dir(), "app.crt"),
        app_key_path=os.path.join(cert_dir(), "app.key"),
        language="en-US",
        device_id=device_id,
    )
    import session_share
    session_share.install(api)   # share ONE token with the poller (avoid mutual eviction)
    return api


class LeapmotorSession:
    """Login once, reuse token for all subsequent API calls."""

    def __init__(self):
        self._api: LeapmotorApiClient | None = None
        self._vehicle = None
        self._lock = threading.Lock()

    def _connect(self):
        if self._api is not None:
            return
        self._api = _make_client()
        self._api.login()
        vehicles = self._api.get_vehicle_list()
        if not vehicles:
            raise RuntimeError("No vehicle found on this account")
        self._vehicle = vehicles[0]
        log.info("Session started — VIN %s  model %s", self._vehicle.vin, self._vehicle.car_type)

    def _is_auth_error(self, err: str) -> bool:
        low = err.lower()
        return any(k in low for k in ("token", "verification", "unauthori", "login"))

    def _is_connection_error(self, err: str) -> bool:
        low = err.lower()
        return any(k in low for k in ("connection aborted", "remotedisconnected", "connectionerror",
                                       "connection reset", "broken pipe", "timed out"))

    def _reset(self):
        try:
            if self._api:
                self._api.close()
        except Exception:
            pass
        self._api = None
        self._vehicle = None
        log.info("Session reset — will re-login on next call")

    def execute(self, action_fn) -> tuple[bool, str]:
        with self._lock:
            for attempt in range(2):
                try:
                    self._connect()
                    action_fn(self._api, self._vehicle.vin)
                    return True, "OK"
                except Exception as e:
                    err = str(e)
                    log.error("Command error (attempt %d): %s", attempt + 1, err)
                    if self._is_connection_error(err):
                        # Stale keep-alive connection — reset and retry immediately
                        self._reset()
                        if attempt == 1:
                            return False, err
                        continue
                    if not self._is_auth_error(err):
                        return False, err
                    self._reset()
                    if attempt == 1:
                        return False, err
                    time.sleep(3)  # avoid rate limit before re-login
            return False, "Unknown error"

    def get_fresh_signals(self) -> dict | None:
        with self._lock:
            for attempt in range(2):
                try:
                    self._connect()
                    raw = self._api.get_vehicle_raw_status(self._vehicle)
                    data = (raw or {}).get("data") or {}
                    # C10/B10: numeric `signal` dict. T03/EU: named fields at top level.
                    return data.get("signal") or _named_fields_to_signal(data)
                except Exception as e:
                    log.warning("Status fetch (attempt %d): %s", attempt + 1, e)
                    self._reset()
            return None

    def get_charge_plan(self) -> dict | None:
        """Return charge plan dict with at least 'charge_limit_percent' key."""
        with self._lock:
            for attempt in range(2):
                try:
                    self._connect()
                    raw = self._api.get_vehicle_raw_status(self._vehicle)
                    plan = ((raw.get("data") or {}).get("config") or {}).get("3") or {}
                    return {
                        "charge_limit_percent": plan.get("percent"),
                        "charge_enabled": plan.get("isEnable"),
                        "start_time": plan.get("beginTime"),
                        "end_time": plan.get("endTime"),
                    }
                except Exception as e:
                    log.warning("Charge plan fetch (attempt %d): %s", attempt + 1, e)
                    self._reset()
            return None

    def get_charge_schedule(self) -> dict | None:
        """Native charge schedule (cmd 190) — flat dict: chargeEnable, chargesoc,
        circulation, cycles, starttime, endtime, recharge. Read-only, safe."""
        with self._lock:
            for attempt in range(2):
                try:
                    self._connect()
                    return self._api.get_charge_schedule(self._vehicle.vin)
                except Exception as e:
                    log.warning("Charge schedule fetch (attempt %d): %s", attempt + 1, e)
                    self._reset()
            return None

    def get_climate_schedule(self) -> list | None:
        """Native climate schedule (cmd 171) — list of control dicts. Read-only, safe.

        Control-dict shape, confirmed live on the B10 (2026-06-07) — the partial
        templates/partials/climate_schedule.html depends on these exact keys:
            {"on": "1"/"0", "mode": "cold"/"hot"/"wind"/"nohotcold", "operate": "auto"/"manual",
             "start_time": "YYYY-MM-DD HH:MM:SS", "temperature": "26", "windlevel": "7",
             "days": [int]  # [] = once; order per lib doc 0=Sun..6=Sat but UNCONFIRMED on B10
             #               (charge `cycles` proved Mon-first) — "position":"all","circle":"in",
             "set_id": "...", "wshld": "0"/"1"}
        NB `days` is a LIST OF INTS here (unlike the charge schedule's flag-string `cycles`)."""
        with self._lock:
            for attempt in range(2):
                try:
                    self._connect()
                    return self._api.get_climate_schedule(self._vehicle.vin)
                except Exception as e:
                    log.warning("Climate schedule fetch (attempt %d): %s", attempt + 1, e)
                    self._reset()
            return None

    def get_car_picture(self) -> bytes | None:
        """Static owner vehicle PNG, extracted from the car-picture package ZIP
        (android/xxhdpi/carpic_for_tripsum.png) — same as the HA integration's
        image.leapmotor_vehicle_picture. Rarely changes → caller should cache."""
        import io, zipfile
        with self._lock:
            for attempt in range(2):
                try:
                    self._connect()
                    meta = self._api.get_car_picture(self._vehicle)
                    key = (meta.get("data") or {}).get("key") if isinstance(meta, dict) else None
                    if not key:
                        return None
                    pkg = self._api.download_car_picture_package(picture_key=key)
                    with zipfile.ZipFile(io.BytesIO(pkg)) as z:
                        return z.read("android/xxhdpi/carpic_for_tripsum.png")
                except Exception as e:
                    log.warning("Car picture fetch (attempt %d): %s", attempt + 1, e)
                    self._reset()
            return None


    def get_energy_breakdown(self) -> dict | None:
        """Last-week energy split (driving / A/C / other), via the library's native
        get_consumption_last_week_breakdown() (0.3.x). Mapped to the dict shape the UI
        already consumes."""
        with self._lock:
            for attempt in range(2):
                try:
                    self._connect()
                    b = self._api.get_consumption_last_week_breakdown(self._vehicle)
                    drv, ac, oth = float(b.driver_ec or 0), float(b.ac_ec or 0), float(b.other_ec or 0)
                    total = drv + ac + oth
                    pct = (lambda v: round(v / total * 100, 1)) if total > 0 else (lambda v: 0)
                    return {
                        "driving_kwh": round(drv, 1), "ac_kwh": round(ac, 1), "other_kwh": round(oth, 1),
                        "total_kwh": round(total, 1),
                        "driving_pct": pct(drv), "ac_pct": pct(ac), "other_pct": pct(oth),
                    }
                except Exception as e:
                    log.warning("Energy breakdown fetch (attempt %d): %s", attempt + 1, e)
                    self._reset()
            return None


    def get_consumption_rank(self) -> dict | None:
        """6-week kWh/100km trend + driver ranking, via the library's native
        get_consumption_weekly_rank() (0.3.x). Mapped to the UI dict shape."""
        with self._lock:
            for attempt in range(2):
                try:
                    self._connect()
                    r = self._api.get_consumption_weekly_rank(self._vehicle)
                    weeks = [{"start": w.week_start, "end": w.week_end,
                              "ec": round(float(w.hundred_km_ec or 0), 1)}
                             for w in (r.weekly or [])]
                    rank = getattr(r, "rank", None)
                    return {
                        "rank": rank.rank if rank else None,
                        "current_ec": round(float(rank.hundred_km_ec or 0), 1) if rank else 0,
                        "weeks": weeks,
                    }
                except Exception as e:
                    log.warning("Consumption rank fetch (attempt %d): %s", attempt + 1, e)
                    self._reset()
            return None


_session = LeapmotorSession()


def get_car_picture() -> bytes | None:
    return _session.get_car_picture()


def get_energy_breakdown() -> dict | None:
    return _session.get_energy_breakdown()


def get_consumption_rank() -> dict | None:
    return _session.get_consumption_rank()


def detect_vehicle(user: str, pwd: str, pin: str) -> dict:
    """Login with provided credentials, return vehicle info. Does NOT save to DB."""
    try:
        api = LeapmotorApiClient(
            username=user,
            password=pwd,
            operation_password=pin,
            app_cert_path=os.path.join(cert_dir(), "app.crt"),
            app_key_path=os.path.join(cert_dir(), "app.key"),
            language="en-US",
        )
        api.login()
        vehicles = api.get_vehicle_list()
        if not vehicles:
            return {"error": "No vehicle found on this account"}
        v = vehicles[0]
        car_type = v.car_type.upper()
        try:
            api.close()
        except Exception:
            pass
        return {"vin": v.vin, "car_type": car_type}
    except Exception as e:
        return {"error": str(e)}


def get_fresh_signals() -> dict | None:
    return _session.get_fresh_signals()

def get_charge_plan() -> dict | None:
    return _session.get_charge_plan()

def set_charge_limit(percent: int):
    return _session.execute(lambda api, vin: api.set_charge_limit(vin, percent))


# ── Scheduling (native B10 support) ───────────────────────────────────────────
# Reads are safe. Both writes work on the B10: charge (cmd 190, flat object) and climate
# (cmd 171, full-state-replacement list). The climate write was thought blocked (code -2) until
# 2026-06-07, when it turned out the -2 was an EXPIRED start_time, not the endpoint — see
# ClimaSchedulerT01 + _next_climate_start / save_climate_schedule below.
def get_charge_schedule() -> dict | None:
    return _session.get_charge_schedule()


def get_climate_schedule() -> list | None:
    return _session.get_climate_schedule()


# Climate "quick" presets — the exact payloads the official app emits, validated on-car
# (ClimaSchedulerT01, 2026-06-07). ONLY cool/heat lock the temperature (18/32); vent/defrost/none
# leave it FREE (temperature=None → use the user's slider value). vent pulls fresh air (circle=out);
# defrost is the windshield-defrost flag wshld=2; none = the neutral nohotcold/auto state (no quick mode).
CLIMATE_PRESETS = {
    "cool":    {"mode": "cold",      "operate": "manual", "temperature": "18", "circle": "in",  "windlevel": "7", "wshld": "1"},
    "heat":    {"mode": "hot",       "operate": "manual", "temperature": "32", "circle": "in",  "windlevel": "7", "wshld": "1"},
    "vent":    {"mode": "nohotcold", "operate": "manual", "temperature": None, "circle": "out", "windlevel": "4", "wshld": "1"},
    "defrost": {"mode": "nohotcold", "operate": "auto",   "temperature": None, "circle": "in",  "windlevel": "7", "wshld": "2"},
    "none":    {"mode": "nohotcold", "operate": "auto",   "temperature": None, "circle": "in",  "windlevel": "7", "wshld": "1"},
}


def _next_climate_start(day_positions, hhmm) -> str:
    """Strictly-future "YYYY-MM-DD HH:MM:00" for the climate schedule. `day_positions`: ints
    0=Sun..6=Sat ([] = one-time). The B10 cloud rejects PAST appointments with code -2 (the
    historic "climate write blocked" was just an expired start_time, ClimaSchedulerT01), so we
    anchor start_time to the next upcoming occurrence; the `days` list drives the recurrence."""
    import datetime
    try:
        h, m = (int(x) for x in str(hhmm).split(":"))
    except Exception:
        h, m = 7, 0
    now = datetime.datetime.now()
    base = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if not day_positions:
        if base <= now:
            base += datetime.timedelta(days=1)
        return base.strftime("%Y-%m-%d %H:%M:00")
    want = {(int(p) - 1) % 7 for p in day_positions}     # our Sun=0..Sat=6 → py weekday Mon=0..Sun=6
    for add in range(0, 8):
        cand = base + datetime.timedelta(days=add)
        if cand > now and cand.weekday() in want:
            return cand.strftime("%Y-%m-%d %H:%M:00")
    return (base + datetime.timedelta(days=1)).strftime("%Y-%m-%d %H:%M:00")


def _mint_climate_set_id() -> str:
    """Fresh set_id in the cloud-accepted `ios_<32hex><epochSec>` shape (used only when there is no
    existing entry to edit in place)."""
    import time, hashlib
    try:
        import sys, pathlib
        sys.path.insert(0, str(pathlib.Path(__file__).parent))
        import db_reader as _dr
        seed = _dr.get_or_create_device_id()
    except Exception:
        seed = "mate"
    return "ios_%s%d" % (hashlib.md5(str(seed).encode()).hexdigest(), int(time.time()))


def save_climate_schedule(*, enabled: bool, preset: str, start_hhmm: str, day_positions,
                          temperature: int | None = None):
    """Write Mate's climate (pre-conditioning) schedule (cmd 171). WORKS on the B10 (verified
    on-car 2026-06-07, ClimaSchedulerT01): `set_climate_schedule` is fine — the historic code -2
    was an EXPIRED start_time, so we always anchor start_time to the next future occurrence.

    Mate manages the climate schedule as a SINGLE entry (full-state replacement; it edits the
    existing entry in place by reusing its `set_id`). `preset` ∈ cool|heat|vent|defrost|none — the
    validated app payloads (cool/heat LOCK temp 18/32; vent/defrost/none take a free `temperature`;
    vent = fresh-air intake, defrost = windshield-defrost flag wshld=2, none = neutral nohotcold/auto).
    `day_positions`: ints 0=Sun..6=Sat ([] = one-time). enabled=False cancels (sends an empty list)."""
    if not enabled:
        return _session.execute(lambda api, vin: api.set_climate_schedule(vin, controls=[]))
    spec = CLIMATE_PRESETS.get(preset)
    if spec is None:
        return False, f"unknown climate preset: {preset}"
    import time as _t
    cur = _session.get_climate_schedule() or []
    set_id = cur[0].get("set_id") if (cur and cur[0].get("set_id")) else _mint_climate_set_id()
    days = sorted({int(d) for d in day_positions})
    temp = spec["temperature"] or str(int(temperature if temperature is not None else 25))
    entry = {
        "circle": spec["circle"],
        "days": days,                                    # 0=Sun..6=Sat (confirmed on-car)
        "mode": spec["mode"],
        "on": "1",
        "operate": spec["operate"],
        "position": "all",
        "set_id": set_id,
        "start_time": _next_climate_start(days, start_hhmm),
        "temperature": temp,
        "update_time": str(int(_t.time() * 1000)),
        "windlevel": spec["windlevel"],
        "wshld": spec["wshld"],
    }
    return _session.execute(lambda api, vin: api.set_climate_schedule(vin, controls=[entry]))


# `cycles` is the charge schedule's per-weekday mask: a 7-field comma string where field i is
# "1" (charge that day) or "0". Position order is **MONDAY-first** (0=Mon, 1=Tue … 6=Sun) —
# confirmed on-car 2026-06-07: Mate sent "1,0,0,0,0,0,0" and the Leapmotor app showed Monday
# active. (The app DISPLAYS days Dom-first, but the stored mask is Mon-first.) These helpers are
# weekday-agnostic — they just map flags[i] ↔ position i; the UI maps each chip to its position.
def cycles_from_day_flags(flags) -> str:
    """7 truthy/falsy values in cycles-position order (Mon..Sun) → "1,0,1,…" mask. Empty
    selection → all-days (a charge window with no days would never fire)."""
    f = [bool(x) for x in list(flags)[:7]]
    f += [False] * (7 - len(f))
    if not any(f):
        f = [True] * 7
    return ",".join("1" if x else "0" for x in f)


def day_flags_from_cycles(cycles) -> list:
    """"1,0,1,…" → [bool x7] in cycles-position order (Mon..Sun). Tolerant of short/garbage
    input (missing → False)."""
    parts = (cycles or "").split(",")
    return [(parts[i].strip() == "1") if i < len(parts) else False for i in range(7)]


def save_charge_schedule(*, enabled: bool, soc_limit: int, start_time: str, end_time: str,
                         cycles: str | None = None):
    """Read-modify-write the charge schedule: change enable/SoC/window (+ days when `cycles` is
    given) and PRESERVE the car's `circulation` and `recharge`.

    `cycles` is the Monday-first 7-flag per-weekday mask, pos 0=Mon..6=Sun (see
    cycles_from_day_flags) — confirmed on-car 2026-06-07. When the caller passes None we round-trip
    the car's existing mask verbatim (or all-days if the car has no schedule yet). The live B10 GET
    returns "1,1,1,1,1,1,1"; upstream lib docs are inconsistent (some use day-NUMBER lists), so the
    mask format/order is anchored to the on-car confirmation (Mate sent pos0 → app showed Monday)."""
    cur = _session.get_charge_schedule() or {}
    if not cycles:
        cycles = cur.get("cycles") or "1,1,1,1,1,1,1"
    circulation = int(cur.get("circulation", 1) or 0)
    recharge    = int(cur.get("recharge", 0) or 0)
    return _session.execute(lambda api, vin: api.set_charge_schedule(
        vin, enabled=enabled, soc_limit=int(soc_limit),
        start_time=start_time, end_time=end_time,
        cycles=cycles, circulation=circulation, recharge=recharge))


def lock():              return _session.execute(lambda api, vin: api.lock_vehicle(vin))
def unlock():            return _session.execute(lambda api, vin: api.unlock_vehicle(vin))
def open_trunk():        return _session.execute(lambda api, vin: api.open_trunk(vin))
def close_trunk():       return _session.execute(lambda api, vin: api.close_trunk(vin))
def find_car():          return _session.execute(lambda api, vin: api.find_vehicle(vin))
def ac_on():             return _session.execute(lambda api, vin: api.ac_switch(vin))
# B10 A/C full-OFF: the working payload is ac_switch with operate=off (drives acSwitch
# signal 1938 → 0). Found empirically on-car 2026-06-06 — the lib's ac_off() sends
# operate=close, which on the B10 only flips the HVAC to AUTO (never off). Reported
# upstream (markoceri/leapmotor-api#3).
def ac_off():            return _session.execute(lambda api, vin: api.ac_switch(vin, params={"operate": "off"}))
def quick_cool():        return _session.execute(lambda api, vin: api.quick_cool(vin))
def quick_heat():        return _session.execute(lambda api, vin: api.quick_heat(vin))
def windshield_defrost():return _session.execute(lambda api, vin: api.windshield_defrost(vin))
# Rapid ventilation + temperature: the whole climate is cmd 170 (kerniger payload). "ac_on"
# maps to cmd 170. mode wind = pure ventilation; temperature field sets the target & starts the climate.
def quick_vent():
    body = json.dumps({"circle": "in", "mode": "wind", "operate": "manual", "position": "all",
                       "temperature": "26", "windlevel": "7", "wshld": "0"}, separators=(",", ":"))
    return _session.execute(lambda api, vin: api._remote_control(vin=vin, action="ac_on", cmd_content=body))
def set_climate_temp(temp, inside=None):
    """Set target temp 18–32 °C. Auto mode: cool if cabin warmer than target, heat if cooler, else vent."""
    try:
        t = max(18, min(int(round(float(temp))), 32))
    except (TypeError, ValueError):
        return False, "bad temp"
    mode = "cold" if (inside is not None and inside > t) else ("hot" if (inside is not None and inside < t) else "wind")
    body = json.dumps({"circle": "in", "mode": mode, "operate": "manual", "position": "all",
                       "temperature": str(t), "windlevel": "5", "wshld": "0"}, separators=(",", ":"))
    return _session.execute(lambda api, vin: api._remote_control(vin=vin, action="ac_on", cmd_content=body))
def open_windows():      return _session.execute(lambda api, vin: api.open_windows(vin, value="2"))
def close_windows():     return _session.execute(lambda api, vin: api.close_windows(vin, value="0"))
def battery_preheat():   return _session.execute(lambda api, vin: api.battery_preheat(vin))
def battery_preheat_off():return _session.execute(lambda api, vin: api.battery_preheat_off(vin))
def open_sunshade():     return _session.execute(lambda api, vin: api.open_sunshade(vin))
def close_sunshade():    return _session.execute(lambda api, vin: api.close_sunshade(vin))
# Charge-port cable unlock (right 192). Exposed on the battery card (promised on mate#19).
# NB: a 2026-Q2 staging note flagged this as "accepted but not actuated" on the B10 (same as
# the old A/C-off) — re-verify on-car before any release; pull the button if it's a no-op.
def unlock_charger():    return _session.execute(lambda api, vin: api.unlock_charger(vin))
# Staged but NOT exposed in any UI: live testing showed the B10 ACCEPTS these (cloud returns
# OK) but does NOT actuate them — so they'd be misleading "Done" buttons. Kept ready so they
# can be wired up instantly if a future leapmotor-api / vehicle update makes them work on the
# B10. (No sunroof: the existing open/close "sunshade" already operates the B10's panoramic roof.)
def sentry_on():         return _session.execute(lambda api, vin: api.sentry_mode_on(vin))
def sentry_off():        return _session.execute(lambda api, vin: api.sentry_mode_off(vin))
# Steering / mirror heat — kerniger 0.6.11 payloads (B10-verified): level/value 1=off, 2=on.
def steering_heat_on():  return _session.execute(lambda api, vin: api._remote_control(vin=vin, action="steering_wheel_heat", cmd_content='{"level":"2"}'))
def steering_heat_off(): return _session.execute(lambda api, vin: api._remote_control(vin=vin, action="steering_wheel_heat", cmd_content='{"level":"1"}'))
def mirror_heat_on():    return _session.execute(lambda api, vin: api._remote_control(vin=vin, action="rearview_mirror_heat", cmd_content='{"value":"2"}'))
def mirror_heat_off():   return _session.execute(lambda api, vin: api._remote_control(vin=vin, action="rearview_mirror_heat", cmd_content='{"value":"1"}'))
# Seats — kerniger/leapmotor-ha#41: the WORKING payload (B10+C10) is
# {"position":"driver"|"copilot","level":"0..3"}, NOT the lib's old {"value":"pos,level"}.
# Send it raw via _remote_control. level: 0=off, 1..3 = the three fan/heat speeds.
_SEAT_ACTION = {"heat": "seat_heat", "vent": "seat_ventilation"}
def seat_comfort(func, position, level):
    """func: 'heat'|'vent'; position: 'driver'|'copilot'; level: 0..3 (0=off)."""
    action = _SEAT_ACTION.get(func)
    if not action or position not in ("driver", "copilot"):
        return False, "bad seat args"
    lvl = max(0, min(int(level), 3))
    body = json.dumps({"position": position, "level": str(lvl)}, separators=(",", ":"))
    return _session.execute(lambda api, vin: api._remote_control(vin=vin, action=action, cmd_content=body))
def seat_heat_driver_on():   return seat_comfort("heat", "driver", 3)
def seat_heat_driver_off():  return seat_comfort("heat", "driver", 0)
def seat_vent_driver_on():   return seat_comfort("vent", "driver", 3)
def seat_vent_driver_off():  return seat_comfort("vent", "driver", 0)
def send_destination(name, address, lat, lon):
    """Push a navigation destination to the car (cmd_id 180, no PIN)."""
    return _session.execute(lambda api, vin: api.send_destination(
        vin, address=address, address_name=name,
        latitude=float(lat), longitude=float(lon)))
