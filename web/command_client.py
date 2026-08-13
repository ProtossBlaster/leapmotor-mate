"""Persistent Leapmotor session — login once, reuse for all commands and status fetches."""
import os
import json
import time
import logging
import threading

from leapmotor_api import LeapmotorApiClient

log = logging.getLogger(__name__)


def _classify_outcome(ok: bool, msg: str) -> str:
    """Bucket a command result for the responsiveness log:
      confirmed         — the car acknowledged in time
      timeout_car       — cloud accepted ('Request successful') but the car didn't confirm in 5s
      cloud_unreachable — couldn't even reach the Leapmotor cloud (network)
      rejected          — auth/PIN/other refusal (not a reachability issue)
    """
    if ok:
        return "confirmed"
    low = (msg or "").lower()
    if "remote control result" in low or ("timed out" in low and "remote" in low):
        return "timeout_car"
    if any(k in low for k in ("timed out", "connection", "max retries", "unreachable",
                              "remotedisconnected", "broken pipe", "ssl", "temporarily")):
        return "cloud_unreachable"
    return "rejected"

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
        user = _dr.get_account_user()   # same precedence, one definition (Settings shows it too)
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


def _classify_ec_response(j) -> tuple[str, dict | None]:
    """Classify a getLastweekEC (getEC) response body:
      - ('data', {driverEC,...}) → a real driving-energy split,
      - ('empty', None)          → genuine 'no driving in this window' (result 0/100 / 'No data found'),
      - ('auth',  None)          → an auth/token failure or other non-data error → refresh & retry.
    Verified shapes: success = {result:0,code:0,data:{...}}; no-data = {result:100,code:100,
    message:'No data found'}. Splitting these out fixes the bug where an expired-token response (no
    'data' key) was misread as 'no data', so the Convert button / background sweep silently reported
    'no official data' for trips that DO have it (a long-lived web session's token had expired)."""
    if not isinstance(j, dict):
        return "auth", None
    d = j.get("data") or {}
    if d:
        return "data", d
    result = j.get("result")
    msg = str(j.get("message") or "").lower()
    if result in (0, 100) or "no data" in msg:
        return "empty", None
    return "auth", None


def _parse_plugin_consumption(raw) -> dict | None:
    """REEV Phase B — pull the current-period consumption out of a getPlugInLastNweeks100kmEC body.
    The exact nesting isn't documented, so we grab the first object carrying `oc100km` (the current
    block: fuel L/100km `oc100km` + electric kWh/100km `ec100km`; the per-week series has only
    `ec100km`). Values may be numbers or numeric strings. Returns {fuel_l_100km, elec_kwh_100km,
    fuel_mpg} or None."""
    def _find(obj):
        if isinstance(obj, dict):
            if "oc100km" in obj:
                return obj
            for v in obj.values():
                r = _find(v)
                if r is not None:
                    return r
        elif isinstance(obj, list):
            for v in obj:
                r = _find(v)
                if r is not None:
                    return r
        return None

    def _f(v):
        try:
            return round(float(v), 1)
        except (TypeError, ValueError):
            return None

    block = _find(raw)
    if not block:
        return None
    return {
        "fuel_l_100km":   _f(block.get("oc100km")),
        "elec_kwh_100km": _f(block.get("ec100km")),
        "fuel_mpg":       _f(block.get("ocMpg")),
    }


class LeapmotorSession:
    """Login once, reuse token for all subsequent API calls."""

    def __init__(self):
        self._api: LeapmotorApiClient | None = None
        self._vehicle = None
        self._lock = threading.Lock()

    def _use_pin_of(self, vin):
        """Authorise this command with the PIN of the car it is going to (#186).

        Set per command rather than once at login: the session is built once and the user can change
        the selected car under it, so a PIN frozen at connection time would send car A's four digits
        with car B's command. Silent by design when there is nothing per-car stored — the reader
        falls back to the install-wide PIN, which is what every install has today.
        """
        try:
            import db_reader as _dr
            pin = _dr.get_operate_pin(vin)
        except Exception:                     # noqa: BLE001 — never let this break a command
            return
        if pin:
            self._api.operation_password = pin

    def _connect(self):
        if self._api is not None:
            # Already authenticated — just make sure the account TLS cert is still on disk (it can be
            # cleaned up mid-session), re-creating it from the saved bytes instead of failing a
            # command with "Could not find the TLS certificate file" (#64).
            try:
                import session_share
                session_share.ensure_account_cert(self._api)
            except Exception:  # noqa: BLE001
                pass
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
        # "certificate"/"cert" = the account TLS cert temp file vanished from /tmp ("Could not find
        # the TLS certificate file") — NOT token-refreshable; it must fall through to the full
        # re-login, which re-creates the cert (mirrors the poller's relogin self-heal).
        return any(k in low for k in ("token", "verification", "unauthori", "login", "certificate", "cert"))

    def _is_token_error(self, err: str) -> bool:
        """A genuine access-token expiry/invalidity — refreshable WITHOUT a full re-login. Narrower
        than _is_auth_error: a 'verification'/cert failure is NOT refreshable and must re-login."""
        low = err.lower()
        return "token" in low and any(k in low for k in
                                      ("expire", "invalid", "unauthor", "not valid"))

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
        """Run a command, then log its outcome + round-trip latency — the car-responsiveness
        signal. A command is the only time Mate reaches the car in real time (polls read the
        cloud cache), so this is our one window into how well the car answers."""
        import sys
        t0 = time.monotonic()
        try:
            action = sys._getframe(1).f_code.co_name
        except Exception:
            action = "command"
        ok, msg = self._execute_inner(action_fn)
        try:
            import db_reader as _dr
            _dr.log_command(action, _classify_outcome(ok, msg),
                            int((time.monotonic() - t0) * 1000),
                            vin=getattr(getattr(self, "_vehicle", None), "vin", "") or "")
        except Exception:
            pass
        return ok, msg

    def _execute_inner(self, action_fn) -> tuple[bool, str]:
        with self._lock:
            refreshed = False
            for attempt in range(3):
                try:
                    self._connect()
                    self._use_pin_of(self._vehicle.vin)
                    action_fn(self._api, self._vehicle.vin)
                    return True, "OK"
                except Exception as e:
                    err = str(e)
                    # Car-confirm timeout: the cloud accepted the command (HTTP 200) but the car
                    # didn't acknowledge within the cloud's poll window. This is NOT a network fault
                    # and NOT fixable by retrying — a resend just fires the command at the car a
                    # second time, and the reset would force a needless re-login. Stop here,
                    # best-effort. (riri19/#73: his car returns data:0 for the whole window — even
                    # the cloud's 30s grants time out — so neither a resend nor a longer wait helps.)
                    # The same message is mapped to 'timeout_car' by _classify_outcome for the log.
                    if "remote control result" in err.lower():
                        log.warning("Command not confirmed by the car in time (best-effort): %s", err)
                        return False, err
                    # A first failed attempt is usually transient (stale keep-alive or an expired
                    # token) and recovers on retry — log those at warning/info, and reserve ERROR
                    # for a command that actually gives up, so the diagnostics aren't alarming.
                    if self._is_connection_error(err):
                        # Stale keep-alive connection — reset and retry immediately
                        self._reset()
                        if attempt >= 1:
                            log.error("Command failed (connection): %s", err)
                            return False, err
                        log.warning("Command hit a stale connection (attempt %d) — retrying", attempt + 1)
                        continue
                    if not self._is_auth_error(err):
                        log.error("Command failed: %s", err)
                        return False, err
                    # Genuine token expiry → refresh the token (keeps the same session) BEFORE any
                    # full re-login. login() evicts the user's official-app session on a shared
                    # account; token_refresh() does not. session_share patches token_refresh to
                    # persist the new token, so the poller picks it up too (no divergent login).
                    if self._is_token_error(err) and not refreshed:
                        refreshed = True
                        try:
                            self._api.token_refresh()
                            log.info("Command auth: token expired → refreshed (no re-login), retrying")
                            continue
                        except Exception as re_err:  # noqa: BLE001
                            log.warning("token refresh failed (%s) — falling back to re-login", re_err)
                    # Fallback: full re-login (also heals a vanished cert / 'verification' errors)
                    self._reset()
                    if attempt >= 1:
                        log.error("Command failed (auth): %s", err)
                        return False, err
                    log.warning("Command auth issue (attempt %d) — re-login then retry", attempt + 1)
                    time.sleep(3)  # avoid rate limit before re-login
            log.error("Command failed after all retries")
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

    def get_prepare_car_schedule(self) -> list | None:
        """Native one-touch prepare-car schedule (cmd 361) — list of entry dicts. Read-only, safe.
        Each entry (decoded on the B10, PrepareCarT01 2026-06-08):
            {"set_id": "ios_<32hex><epoch>", "start_time": "YYYY-MM-DD HH:MM:SS", "enable": bool,
             "days": [int]  # 0=Sun..6=Sat ([]=once),
             "datacontent": {"air_condition": {...climate vocab...}, "seat_setting": {...},
                             "steeringWheelHeatCtrl": {...}, "rearMirrorHeating": {...}, "syn_path": {...}}}
        Only the ENABLED dimensions are present in datacontent."""
        with self._lock:
            for attempt in range(2):
                try:
                    self._connect()
                    return self._api.get_prepare_car_schedule(self._vehicle.vin)
                except Exception as e:
                    log.warning("Prepare-car schedule fetch (attempt %d): %s", attempt + 1, e)
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


    def get_car_picture_package(self) -> bytes | None:
        """Raw per-vehicle car-picture package ZIP (all layers + the static render). Rarely changes
        → caller should cache. The web layer composes the LIVE image from it (car_image.compose),
        reflecting the charge cable / charging animation / trunk."""
        with self._lock:
            for attempt in range(2):
                try:
                    self._connect()
                    meta = self._api.get_car_picture(self._vehicle)
                    key = (meta.get("data") or {}).get("key") if isinstance(meta, dict) else None
                    if not key:
                        return None
                    pkg = self._api.download_car_picture_package(picture_key=key)
                    # The cloud can answer the download with a tiny error JSON body instead of the
                    # ZIP (e.g. the transient `{"code":39,...}` seen right after a freshly-accepted
                    # car share) under an HTTP 200, so it slips past the library. Returning those
                    # bytes as "the package" makes the caller cache an error blob as
                    # car_picture_pkg.zip and then serve no image until a manual ?refresh=1. A real
                    # ZIP always starts with the "PK" magic; treat anything else as a transient
                    # failure → reset + retry, and None after the attempts (caller falls back).
                    if not (pkg and pkg[:2] == b"PK"):
                        log.warning("Car picture package was not a ZIP (%d bytes, attempt %d) — "
                                    "transient cloud error, not caching", len(pkg or b""), attempt + 1)
                        self._reset()
                        continue
                    return pkg
                except Exception as e:
                    log.warning("Car picture package fetch (attempt %d): %s", attempt + 1, e)
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


    def get_energy_breakdown_range(self, begin_ts: int, end_ts: int) -> dict | None:
        """Energy split (driving / A/C / other) for an ARBITRARY interval, via the same cloud
        endpoint as last-week (getEC) but with custom epoch-second bounds — the library only
        exposes the fixed previous-week window, so we sign+POST it directly. The cloud resolves
        sub-day windows (≥ ~15 min); a window with no driving returns None. NB: this is the
        DRIVING-session split (parked/standby energy is not included). Same dict shape as
        get_energy_breakdown()."""
        import json as _json
        from urllib.parse import quote
        try:
            from leapmotor_api.crypto import build_consumption_last_week_headers
        except Exception:  # noqa: BLE001
            return None
        with self._lock:
            refreshed = False
            for attempt in range(3):
                try:
                    self._connect()
                    api, vin = self._api, self._vehicle.vin
                    headers = build_consumption_last_week_headers(
                        sign_key=api.sign_key, device_id=api.device_id, carvin=vin,
                        begintime=str(begin_ts), endtime=str(end_ts), language=api.language,
                    ).to_dict()
                    headers.update(api._auth_headers())
                    body = f"endtime={end_ts}&begintime={begin_ts}&carvin={quote(vin, safe='')}"
                    resp = api._post(
                        path="/carownerservice/oversea/drivingRecord/v1/getLastweekEC",
                        headers=headers, data=body, cert=api.account_cert,
                    )
                    raw = resp.get("body")
                    j = _json.loads(raw) if isinstance(raw, str) else (raw or {})
                    kind, d = _classify_ec_response(j)
                    if kind == "empty":
                        return None  # genuine: no driving recorded in this window
                    if kind == "auth":
                        # A long-lived web session's token expires; the cloud then returns a non-data
                        # response that USED to be misread as "no data" (the Convert-button / sweep
                        # false negative on trips that DO have cloud data). Refresh the token once —
                        # safe on a shared account, unlike a full re-login — and retry.
                        log.info("Energy range: non-data response %s — token refresh & retry", j)
                        if not refreshed:
                            refreshed = True
                            try:
                                api.token_refresh()
                            except Exception:  # noqa: BLE001
                                self._reset()
                            continue
                        return None
                    drv = float(d.get("driverEC") or 0)
                    ac = float(d.get("acEC") or 0)
                    oth = float(d.get("otherEC") or 0)
                    total = drv + ac + oth
                    if total <= 0:
                        return None
                    pct = lambda v: round(v / total * 100, 1)  # noqa: E731
                    return {
                        "driving_kwh": round(drv, 1), "ac_kwh": round(ac, 1), "other_kwh": round(oth, 1),
                        "total_kwh": round(total, 1),
                        "driving_pct": pct(drv), "ac_pct": pct(ac), "other_pct": pct(oth),
                    }
                except Exception as e:  # noqa: BLE001
                    log.warning("Energy range fetch (attempt %d): %s", attempt + 1, e)
                    self._reset()
            return None


    def _raw_getec(self, begin_ts: int, end_ts: int):
        """Raw, UNMAPPED getEC response for [begin,end] (research probe helper). No lock/connect of
        its own — the caller holds the lock and has connected."""
        import json as _json
        from urllib.parse import quote
        from leapmotor_api.crypto import build_consumption_last_week_headers
        api, vin = self._api, self._vehicle.vin
        headers = build_consumption_last_week_headers(
            sign_key=api.sign_key, device_id=api.device_id, carvin=vin,
            begintime=str(begin_ts), endtime=str(end_ts), language=api.language).to_dict()
        headers.update(api._auth_headers())
        body = f"endtime={end_ts}&begintime={begin_ts}&carvin={quote(vin, safe='')}"
        resp = api._post(path="/carownerservice/oversea/drivingRecord/v1/getLastweekEC",
                         headers=headers, data=body, cert=api.account_cert)
        raw = resp.get("body")
        return _json.loads(raw) if isinstance(raw, str) else (raw or {})

    def _raw_weekly_rank(self):
        """Raw, UNMAPPED 6-week 100km-EC + rank response (research probe helper)."""
        import json as _json
        from urllib.parse import quote
        from leapmotor_api.crypto import build_consumption_weekly_rank_headers
        api, vin = self._api, self._vehicle.vin
        headers = build_consumption_weekly_rank_headers(
            sign_key=api.sign_key, device_id=api.device_id, carvin=vin, language=api.language).to_dict()
        headers.update(api._auth_headers())
        resp = api._post(path="/carownerservice/oversea/drivingRecord/v1/getLastNweeks100kmECAndRank",
                         headers=headers, data=f"carvin={quote(vin, safe='')}", cert=api.account_cert)
        raw = resp.get("body")
        return _json.loads(raw) if isinstance(raw, str) else (raw or {})

    def _raw_getplugin(self):
        """Raw, UNMAPPED getPlugInLastNweeks100kmEC response (research probe helper) — the REEV twin
        of the weekly rank: same drivingRecord/v1 family + weekly-rank signature, but the payload
        also carries the FUEL split (oc100km per 100km / ocMpg) alongside the electric one. On our
        BEV every fuel field is 0; on a range-extender this is the cloud's own L/100km, which is what
        we're here to CONFIRM. Raw on purpose — captures any fuel field the BEV mapping would drop."""
        import json as _json
        from urllib.parse import quote
        from leapmotor_api.crypto import build_consumption_weekly_rank_headers
        api, vin = self._api, self._vehicle.vin
        headers = build_consumption_weekly_rank_headers(
            sign_key=api.sign_key, device_id=api.device_id, carvin=vin, language=api.language).to_dict()
        headers.update(api._auth_headers())
        resp = api._post(path="/carownerservice/oversea/drivingRecord/v1/getPlugInLastNweeks100kmEC",
                         headers=headers, data=f"carvin={quote(vin, safe='')}", cert=api.account_cert)
        raw = resp.get("body")
        return _json.loads(raw) if isinstance(raw, str) else (raw or {})

    def _raw_mileage_energy(self, begin_ms: int, end_ms: int):
        """Raw, UNMAPPED mileage/energy/detail response (research probe helper).

        The one endpoint whose full reply nobody has ever seen: get_cumulative_summary calls it live,
        reads `totalEnergy` and `totalmileage`, and drops the rest of `data` on the spot. That matters
        on a range-extender, where the two figures it does read produce a misleading average — the
        electricity divided by a distance that was partly driven on petrol. @michapr's car shows
        12.6 kWh/100 km over its electric kilometres where Mate shows 8.9 over all of them, and the
        cloud's own weekly `ec100km` collapses the same way when fuel use rises, so the split the car
        has (58 % electric / 42 % fuel) is not in any endpoint we have captured. If it exists at all
        it is in here, in a field we throw away — hence this probe. Needs the SIGNED begin/end window
        (body_params) like the live call, or the reply carries mileage only."""
        import json as _json
        from urllib.parse import quote
        from leapmotor_api.crypto import build_signed_headers
        api, vin = self._api, self._vehicle.vin
        headers = build_signed_headers(
            sign_key=api.sign_key, device_id=api.device_id, vin=vin, language=api.language,
            body_params={"begintime": str(begin_ms), "endtime": str(end_ms)}).to_dict()
        headers.update(api._auth_headers())
        body = f"endtime={end_ms}&begintime={begin_ms}&vin={quote(vin, safe='')}"
        resp = api._post(path="/carownerservice/oversea/drivingRecord/v1/mileage/energy/detail",
                         headers=headers, data=body, cert=api.account_cert)
        raw = resp.get("body")
        return _json.loads(raw) if isinstance(raw, str) else (raw or {})

    def get_consumption_probe_raw(self) -> dict | None:
        """Research/beta ONLY: UNMAPPED raw JSON of the cloud consumption endpoints, for REEV field
        discovery — e.g. a fuel L/100km field the BEV mapping (driverEC/acEC/otherEC, hundredKmEC)
        would silently drop. Captures getEC over the last 24h and last 7d, the 6-week 100km rank,
        the REEV plug-in split (getPlugInLastNweeks100kmEC — carries oc100km fuel), and the whole
        mileage/energy/detail reply, of which the live call keeps only two fields. Returns
        {label: raw_json} or None. Called only from the research-gated export."""
        import time as _time
        with self._lock:
            try:
                self._connect()
                now = int(_time.time())
                return {
                    "captured_at_ms": now * 1000,
                    "getEC_last24h":  self._raw_getec(now - 86400, now),
                    "getEC_last7d":   self._raw_getec(now - 7 * 86400, now),
                    "weekly_rank_6w": self._raw_weekly_rank(),
                    "getplugin_100km_6w": self._raw_getplugin(),
                    # Whole reply, not the two fields the live call keeps — see _raw_mileage_energy.
                    "mileage_energy_detail": self._raw_mileage_energy(
                        (now - 7 * 86400) * 1000, now * 1000),
                }
            except Exception as e:  # noqa: BLE001
                log.warning("consumption probe (research) failed: %s", e)
                self._reset()
                return None

    def get_plugin_consumption(self) -> dict | None:
        """REEV Phase B — the cloud's OWN consumption from getPlugInLastNweeks100kmEC. The current-period
        block carries `oc100km` (fuel L/100km) next to `ec100km` (electric kWh/100km). On a BEV oc100km is
        0; on a range-extender it's the real fuel figure the car computed. Best-effort live read for the
        REEV dashboard — returns {fuel_l_100km, elec_kwh_100km, fuel_mpg} or None."""
        with self._lock:
            for attempt in range(2):
                try:
                    self._connect()
                    return _parse_plugin_consumption(self._raw_getplugin())
                except Exception as e:  # noqa: BLE001
                    log.warning("getPlugIn consumption (attempt %d): %s", attempt + 1, e)
                    self._reset()
            return None


    def get_cumulative_summary(self) -> dict | None:
        """Since-delivery cumulative totals from the cloud, via mileage/energy/detail — which needs a
        SIGNED begin/end window (body_params) or it returns mileage only. Gives total energy (incl.
        parked/standby), total mileage and lifetime kWh/100km; plus driving (getEC over the car's
        life) and the derived parked share (total − driving). Returns a dict or None."""
        import json as _json, time as _time
        from urllib.parse import quote
        from leapmotor_api.crypto import build_signed_headers
        with self._lock:
            for attempt in range(2):
                try:
                    self._connect()
                    api, vin = self._api, self._vehicle.vin
                    now_ms = int(_time.time() * 1000); b_ms = now_ms - 7 * 86400 * 1000
                    h = build_signed_headers(
                        sign_key=api.sign_key, device_id=api.device_id, vin=vin, language=api.language,
                        body_params={"begintime": str(b_ms), "endtime": str(now_ms)}).to_dict()
                    h.update(api._auth_headers())
                    body = f"endtime={now_ms}&begintime={b_ms}&vin={quote(vin, safe='')}"
                    resp = api._post(path="/carownerservice/oversea/drivingRecord/v1/mileage/energy/detail",
                                     headers=h, data=body, cert=api.account_cert)
                    raw = resp.get("body"); j = _json.loads(raw) if isinstance(raw, str) else (raw or {})
                    d = (j.get("data") or {}) if isinstance(j, dict) else {}
                    te, km = d.get("totalEnergy"), d.get("totalmileage")
                    if te is None or not km:
                        return None
                    total, mileage = float(te), float(km)
                    out = {
                        "total_energy_kwh": round(total, 1),
                        "total_mileage_km": round(mileage, 1),
                        "lifetime_eff_kwh_100km": round(total / mileage * 100, 1) if mileage > 0 else None,
                        "ec_driving_kwh": None, "ec_ac_kwh": None, "ec_other_kwh": None,
                        "driving_kwh": None, "parked_kwh": None,
                    }
                    # Driving split (driver / A·C / other) over the car's whole life via getEC (caps at
                    # delivery); parked/standby = total − driving.
                    now_s = int(_time.time())
                    ecj = self._raw_getec(now_s - 800 * 86400, now_s)
                    ecd = (ecj.get("data") or {}) if isinstance(ecj, dict) else {}
                    if ecd:
                        drv = round(float(ecd.get("driverEC") or 0), 1)
                        ac = round(float(ecd.get("acEC") or 0), 1)
                        oth = round(float(ecd.get("otherEC") or 0), 1)
                        driving = drv + ac + oth
                        if driving > 0:
                            out["ec_driving_kwh"] = drv
                            out["ec_ac_kwh"] = ac
                            out["ec_other_kwh"] = oth
                            out["driving_kwh"] = round(driving, 1)
                            out["parked_kwh"] = round(max(total - driving, 0), 1)
                    return out
                except Exception as e:  # noqa: BLE001
                    log.warning("Cumulative summary fetch (attempt %d): %s", attempt + 1, e)
                    self._reset()
            return None


_session = LeapmotorSession()


def get_car_picture() -> bytes | None:
    return _session.get_car_picture()


def get_car_picture_package() -> bytes | None:
    return _session.get_car_picture_package()


def get_energy_breakdown() -> dict | None:
    return _session.get_energy_breakdown()


def get_consumption_probe_raw() -> dict | None:
    return _session.get_consumption_probe_raw()


def get_plugin_consumption() -> dict | None:
    return _session.get_plugin_consumption()


def get_cumulative_summary() -> dict | None:
    return _session.get_cumulative_summary()


def get_consumption_rank() -> dict | None:
    return _session.get_consumption_rank()


def get_energy_breakdown_range(begin_ts: int, end_ts: int) -> dict | None:
    return _session.get_energy_breakdown_range(begin_ts, end_ts)


def detect_vehicle(user: str, pwd: str, pin: str) -> dict:
    """Login with the given credentials and return EVERY car on the account. Does NOT save to DB.

    🔴 This used to keep `vehicles[0]` and drop the rest, which is where two-car accounts lost their
    second car before the wizard ever saw it: you configured one, and the other was registered later
    by the poller with its model's DEFAULT pack — for a C10 that is the RWD, so an AWD read 20% out
    and a REEV 2.4 times out, silently. The pack cannot be derived: the cloud says only "C10", and
    C10/B10 each have several variants (plus the range-extenders). That is why the wizard shows a
    list — and why every car needs its own.

    `vin`/`car_type` stay at the top level for the first car so nothing that reads the old shape has
    to change; `vehicles` is the list the wizard walks."""
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
        try:
            api.close()
        except Exception:
            pass
        from main import battery_options_for_build          # the build's own list (REEV filtered)
        opts = battery_options_for_build()
        cars = [{"vin": v.vin,
                 "car_type": (v.car_type or "").upper(),
                 "battery_options": opts.get((v.car_type or "").upper(), [])}
                for v in vehicles]
        return {"vin": cars[0]["vin"], "car_type": cars[0]["car_type"], "vehicles": cars}
    except Exception as e:
        return {"error": str(e)}


def get_fresh_signals() -> dict | None:
    return _session.get_fresh_signals()

def get_charge_plan() -> dict | None:
    return _session.get_charge_plan()

def set_charge_limit(percent: int):
    """Change ONLY the charge-limit SoC, preserving the car's existing charge plan.

    NOT the lib's api.set_charge_limit (0.3.1): that guards on `cycles`, so for an ENABLED
    start-time-only plan — where the cloud omits cycles/endtime/recharge — it falls into an
    all-defaults branch that silently resets starttime to 00:00 and DISABLES the schedule
    (leapmotor-api #18). We round-trip the current plan through save_charge_schedule, which
    preserves cycles/circulation/recharge, and keep the plan's own enable state + start/end
    window — touching only the SoC."""
    cur = _session.get_charge_schedule() or {}
    return save_charge_schedule(
        enabled=bool(int(cur.get("chargeEnable", 0) or 0)),
        soc_limit=int(percent),
        start_time=cur.get("starttime") or "00:00",
        end_time=cur.get("endtime") or "08:00",
        cycles=cur.get("cycles"),   # None → save_charge_schedule re-reads & defaults to all-days
    )


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


# ── One-touch prepare-car (cmd 360 immediate / 361 schedule) ──────────────────────────────────
# Decoded from the official app via read-after-app-change (PrepareCarT01, 2026-06-08). The bundle
# (`datacontent`) carries ONLY the enabled dimensions; air_condition reuses the validated climate
# vocabulary (CLIMATE_PRESETS). Seat per-seat value: off="0", heat="3", vent="13" (the app's level-3
# defaults). steering/mirror appear only when on (default level/value "2"). syn_path = nav destination.
PREPARE_SEAT_CODES = {"off": "0", "heat": "3", "vent": "13"}


def build_prepare_bundle(*, ac_preset=None, ac_temperature=None,
                         seats=None, steering=False, mirror=False, dest=None) -> dict:
    """Build the prepare-car `datacontent` bundle. Omits any dimension that is off (matching the app).
    `seats` = {"driver"|"copilot"|"left_rear"|"right_rear": "off"|"heat"|"vent"}. `dest` = None or
    {"lat","lon","address","name","key"}."""
    bundle: dict = {}
    if ac_preset:
        spec = CLIMATE_PRESETS.get(ac_preset)
        if spec is None:
            raise ValueError(f"unknown ac preset: {ac_preset}")
        temp = spec["temperature"] or str(int(ac_temperature if ac_temperature is not None else 25))
        bundle["air_condition"] = {
            "circle": spec["circle"], "enable": True, "mode": spec["mode"],
            "operate": spec["operate"], "position": "all", "temperature": temp,
            "windlevel": spec["windlevel"], "wshld": spec["wshld"],
        }
    seats = seats or {}
    if any(seats.get(s, "off") != "off" for s in ("driver", "copilot", "left_rear", "right_rear")):
        bundle["seat_setting"] = {
            s: PREPARE_SEAT_CODES.get(seats.get(s, "off"), "0")
            for s in ("driver", "copilot", "left_rear", "right_rear")
        }
        bundle["seat_setting"]["enable"] = True
    if steering:
        bundle["steeringWheelHeatCtrl"] = {"enable": True, "level": "2"}
    if mirror:
        bundle["rearMirrorHeating"] = {"enable": True, "value": "2"}
    if dest and dest.get("lat") is not None and dest.get("lon") is not None:
        bundle["syn_path"] = {
            "address": str(dest.get("address", "")), "addressname": str(dest.get("name", "")),
            "addresskey": str(dest.get("key", "")), "config": "0110",
            "latitude": str(dest["lat"]), "longitude": str(dest["lon"]),
            "linenum": "0", "enable": True,
        }
    return bundle


def build_prepare_entry(*, bundle: dict, start_hhmm: str, day_positions, set_id=None) -> dict:
    """One cmd-361 schedule entry wrapping a bundle. start_time anchored to the next future
    occurrence (avoids the -2 expired-appointment rejection, same lesson as the climate schedule)."""
    import time as _t
    days = sorted({int(d) for d in (day_positions or [])})
    return {
        "datacontent": bundle,
        "days": days,                                    # 0=Sun..6=Sat
        "enable": True,
        "set_id": set_id or _mint_climate_set_id(),      # same ios_<32hex><epoch> shape (cloud-opaque)
        "start_time": _next_climate_start(days, start_hhmm),
        "update_time": str(int(_t.time() * 1000)),
    }


def get_prepare_car_schedule() -> list | None:
    """Current one-touch prepare-car schedule (cmd 361), list of entries. Read-only, safe."""
    return _session.get_prepare_car_schedule()


def _drop_past_oneshots(controls: list) -> list:
    """Drop spent one-shot entries before a full-state cmd-361 write, so one of them can't
    -2-reject the whole batch (#116).

    A 361 write ships EVERY entry (the save/delete routes read the current list, change one, and
    write them ALL back). Isolated on-car, exactly ONE combination makes the cloud reject the batch
    with -2 ("Request failed"): a ONE-SHOT entry (no weekday, days=[]) whose start_time has already
    passed — a "once" appointment that already fired and can't be re-registered. Recurring entries
    (weekday set) save fine even with a stale anchor (the cloud recomputes the next occurrence from
    the weekday), and still-pending one-shots are fine too — so we touch NOTHING else. Omitting the
    spent one-shots from the full-state write both unblocks the save AND clears them from the cloud.
    These stale one-shots are typically inherited from the official Leapmotor app (schedules live on
    the shared cloud), not created by Mate. Model-agnostic — only the 361 payload is filtered, never
    a live command path, so B10/C10/B05/T03 behaviour is unchanged."""
    import datetime
    now = datetime.datetime.now()
    out: list = []
    for e in controls:
        if isinstance(e, dict) and not (e.get("days") or []):        # one-shot: no weekday set
            try:
                if datetime.datetime.strptime(str(e.get("start_time") or ""),
                                              "%Y-%m-%d %H:%M:%S") <= now:
                    log.info("prepare 361: dropping spent one-shot %s (fired at %s)",
                             e.get("set_id"), e.get("start_time"))
                    continue                                         # already fired → drop
            except ValueError:
                pass                                                 # unparseable → keep verbatim
        out.append(e)
    return out


def set_prepare_car_schedule(controls: list) -> tuple:
    """Write the FULL prepare-car schedule list (cmd 361, full-state replacement — pass every entry
    you want to keep; [] cancels all). The lib has no setter for 361, so we drive the proven lower-level
    PIN/signing path directly with cmd_id=361 and the same {"controls":[...]} envelope as the climate
    schedule (verified gateway-recognised; PrepareCarT01). Spent one-shot entries are dropped first so
    one already-fired 'once' appointment can't -2-reject the whole batch (#116)."""
    controls = _drop_past_oneshots(controls)
    body = json.dumps({"controls": controls}, separators=(",", ":"))
    return _session.execute(lambda api, vin: api._remote_control_raw(
        vin=vin, cmd_id="361", cmd_content=body, action_label="prepare_car_alarm"))


def cancel_prepare_car_schedule() -> tuple:
    """Remove all prepare-car schedules (empty controls list)."""
    return set_prepare_car_schedule([])


def prepare_car_now(bundle: dict) -> tuple:
    """Trigger an IMMEDIATE one-touch preparation (cmd 360). ⚠️ ACTUATES the car now. `bundle` is the
    same datacontent shape as a schedule entry's (air_condition/seat_setting/steering/mirror/syn_path)."""
    return _session.execute(lambda api, vin: api.prepare_car(vin, params=bundle))


def prepare_car_off() -> tuple:
    """Cancel an active preparation — turn A/C + both front seats (heat & vent) + steering + mirror OFF.
    Uses Mate's individually-validated B10 off-commands (no untested 360 'off' payload). Best-effort:
    runs every step and reports any that failed (turning off something already off is a harmless no-op).
    (ac_off / seat_comfort / steering_heat_off / mirror_heat_off are defined below; resolved at call time.)"""
    steps = [
        ("A/C", ac_off),
        ("seat vent driver", lambda: seat_comfort("vent", "driver", 0)),
        ("seat vent copilot", lambda: seat_comfort("vent", "copilot", 0)),
        ("seat heat driver", lambda: seat_comfort("heat", "driver", 0)),
        ("seat heat copilot", lambda: seat_comfort("heat", "copilot", 0)),
        ("steering", steering_heat_off),
        ("mirror", mirror_heat_off),
    ]
    failed = []
    for name, fn in steps:
        try:
            ok, msg = fn()
            if not ok:
                failed.append(f"{name}: {msg}")
        except Exception as e:
            failed.append(f"{name}: {e}")
    return (False, "; ".join(failed)) if failed else (True, "OK")


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
def ac_on():
    """A/C ON in AUTO — like the official app's A/C button. operate=auto + mode=nohotcold tells the
    car to self-manage cool/heat (and recirc) toward the target temp → climate mode 3713=0 (AUTO),
    so NO manual mode (cool/heat/vent) is engaged. Target temp kept from the last reading (def 24).
    The OLD ac_switch() merely toggled power and RESUMED the last manual mode (e.g. cool), which made
    the car cool and wrongly lit the Quick-Cool tile (reported on-car 2026-06-21)."""
    import db_reader as _dr
    st = _dr.get_latest_status() or {}
    try: temp = max(18, min(int(float(st.get("climate_target_temp") or 24)), 32))
    except (TypeError, ValueError): temp = 24
    operate, mode = _t03_force_manual("auto", "nohotcold")   # T03 ignores operate=auto (#67) → manual+cold
    body = json.dumps({"circle": "in", "mode": mode, "operate": operate, "position": "all",
                       "temperature": str(temp), "windlevel": "5", "wshld": "0"}, separators=(",", ":"))
    return _session.execute(lambda api, vin: api._remote_control(vin=vin, action="ac_on", cmd_content=body))
# A/C full-OFF is MODEL-SPECIFIC — the B10 and T03 want OPPOSITE payloads:
#  • B10/C10: ac_switch with operate=off — drives acSwitch signal 1938→0, confirmed on-car
#    2026-06-06. The B10 IGNORES operate=close (tested live: it applies the setpoint and stays on),
#    so ac_switch operate=off is its only real full-off. Left exactly as-is.
#  • T03: operate=off inside the FULL seven-field body. Solved and verified on-car by **@derekzoli**
#    (06-07/08/2026, markoceri/leapmotor-api#9 — the report we opened): *"on the T03 it's not the
#    value of operate that matters, it's the shape of the payload: it needs off, but only with the
#    other fields present"*. He confirmed it the only way that counts — re-reading the vehicle status
#    ~8 s later and watching acSwitch go false with the A/C actually stopping, not an ACK: the cloud
#    answers code:0 to every one of these, which is why nobody could tell them apart from a log.
#    He measured the two near-misses too: `operate=close` in the same full body (what
#    REMOTE_CTL_AC_OFF / api.ac_off sends, i.e. what Mate used to send here) and bare
#    `{"operate":"off"}` (the B10's form) are both accepted and ignored. This closes a defect open
#    across the whole ecosystem — kerniger/leapmotor-ha#28, markoceri/leapmotor-api#9 — and our own
#    seven candidates (the retired web/t03_offtest.py) all missed it: we varied mode, fan, recirc and
#    invented keys while holding operate at close/manual. He held the body still and moved operate.
#  • B05: stays on the B10/C10 path (no data either way — don't change what we can't verify).
#
# ⚠️ poller/main.py sends this same body for the Home Assistant button. The two literals are held
# character-for-character identical by a test, because the page and HA must not come to disagree
# about the same car in silence.
T03_AC_OFF_BODY = ('{"circle":"out","mode":"wind","operate":"off","position":"all",'
                   '"temperature":"26","windlevel":"3","wshld":"0"}')


def ac_off():
    def _do(api, vin):
        # ONLY the T03 diverges. B10/C10/B05 keep the EXACT original behaviour below.
        if _session_car_type() == "T03":
            log.info("A/C-off (web, T03) → cmd 170 full body, operate=off [@derekzoli, #67]")
            return api._remote_control(vin=vin, action="ac_on", cmd_content=T03_AC_OFF_BODY)
        return api.ac_switch(vin, params={"operate": "off"})
    return _session.execute(_do)
def quick_cool():        return _session.execute(lambda api, vin: api.quick_cool(vin))
def quick_heat():        return _session.execute(lambda api, vin: api.quick_heat(vin))
def windshield_defrost():
    # Windshield defrost = cmd 170 with wshld=2 (reads back as signal 1945=2). Matches the official
    # app EXACTLY (captured on-car 2026-06-21: on=1, mode=0, defr/1945=2, fresh air, fan 7). The lib's
    # dedicated windshield_defrost ACTION (no params) wrongly engaged plain HEAT (mode 3, defr stayed 0).
    body = json.dumps({"circle": "out", "mode": "nohotcold", "operate": "auto", "position": "all",
                       "temperature": "26", "windlevel": "7", "wshld": "2"}, separators=(",", ":"))
    return _session.execute(lambda api, vin: api._remote_control(vin=vin, action="ac_on", cmd_content=body))
# Rapid ventilation + temperature: the whole climate is cmd 170 (kerniger payload). "ac_on"
# maps to cmd 170. mode wind = pure ventilation; temperature field sets the target & starts the climate.
def quick_vent():
    # Pure ventilation = mode "wind" (reads back as signal 3713=4) + FRESH AIR (circle=out, recirc off)
    # + fan 4. CRITICAL: the target temperature must NOT sit below the cabin, or the car engages COOLING
    # instead of plain vent — seen on-car 2026-06-21: a hardcoded temp=26 with a ~28° cabin came back as
    # mode 1 (cool), never 4 (vent). So we send the CURRENT CABIN temp as the target → zero heat/cool
    # delta → the car just blows air. (The lib's HvacMode enum 0/1/3 is incomplete; the cloud accepts
    # "wind" — ClimateMode.WIND confirms it.)
    # Pure ventilation via operate=MANUAL + mode=NOHOTCOLD — the recipe proven on-car (2026-06-21 12:57:
    # the experimental "A/C MANUAL" button, from off/mode-3 → on=1, 3713=4 vent) and the one the OFFICIAL
    # app effectively uses: it engages mode 4 from ANY starting state. The old mode="wind" did NOT engage
    # from a persisted manual mode (12:51 on-car: from mode-3 it stayed mode-3 — "quick_vent NON ingrana").
    # nohotcold = neither hot nor cold, so the target temp can't trigger cooling/heating; circle=out =
    # fresh air + fan 4, matching the app's captured vent (on=1, mode=4, recirc=0, fan=4).
    body = json.dumps({"circle": "out", "mode": "nohotcold", "operate": "manual", "position": "all",
                       "temperature": "26", "windlevel": "4", "wshld": "0"}, separators=(",", ":"))
    return _session.execute(lambda api, vin: api._remote_control(vin=vin, action="ac_on", cmd_content=body))
def set_climate_temp(temp, inside=None):
    """Set the target temp 18–32 °C while PRESERVING the current climate mode (so changing the target
    NEVER switches the mode): in AUTO (3713=0) it stays AUTO (operate=auto → the car keeps managing
    cool/heat itself toward the new target); in a manual mode (cool/heat/vent) it keeps that mode.
    Recirc + fan preserved from the last reading. `inside` is unused now (the car decides) but kept
    for call-site compatibility. Confirmed on-car 2026-06-21: operate=auto → mode 0, no manual tile."""
    try:
        t = max(18, min(int(round(float(temp))), 32))
    except (TypeError, ValueError):
        return False, "bad temp"
    import db_reader as _dr
    st = _dr.get_latest_status() or {}
    mode_tok = {1: "cold", 3: "hot", 4: "wind"}.get(st.get("climate_mode"))
    operate, mode = ("manual", mode_tok) if mode_tok else ("auto", "nohotcold")   # AUTO(0)/unknown → AUTO
    operate, mode = _t03_force_manual(operate, mode)   # T03 ignores operate=auto (#67) → manual+cold
    circle = "in" if st.get("recirculation") else "out"
    try:
        fan = max(1, min(int(st.get("fan_level") or 5), 7))
    except (TypeError, ValueError):
        fan = 5
    body = json.dumps({"circle": circle, "mode": mode, "operate": operate, "position": "all",
                       "temperature": str(t), "windlevel": str(fan), "wshld": "0"}, separators=(",", ":"))
    return _session.execute(lambda api, vin: api._remote_control(vin=vin, action="ac_on", cmd_content=body))

# ── Fan level (signal 1941) + recirculation (signal 1943) — both validated on-car 2026-06-20 ──
# Change ONE field while PRESERVING the rest of the panel (mode/recirc/temp/fan), read from the last
# stored position so there's no extra cloud round-trip. ac_on (cmd 170) with mode cold/hot/wind +
# windlevel is the SAME path set_climate_temp already ships, so cool/heat keep working at the chosen
# fan. The car rejects windlevel 0 (verified on-car) → clamp to 1-7.
_MODE_TOKEN = {1: "cold", 3: "hot", 4: "wind"}     # signal 3713 → ac_on MANUAL mode token
def _climate_ctx():
    """(operate, mode_token, circle, fan, temp) from the latest stored position — PRESERVES the panel
    on a single-field change without a cloud fetch. AUTO (3713=0) / unknown → operate=auto+nohotcold,
    so a fan/recirc tweak NEVER kicks the car out of AUTO (the old default 'wind' did → it went vent)."""
    import db_reader as _dr
    st = _dr.get_latest_status() or {}
    tok = _MODE_TOKEN.get(st.get("climate_mode"))
    operate, mode = ("manual", tok) if tok else ("auto", "nohotcold")
    operate, mode = _t03_force_manual(operate, mode)   # T03 ignores operate=auto (#67) → manual+cold
    circle = "in" if st.get("recirculation") else "out"
    try: fan = max(1, min(int(st.get("fan_level") or 3), 7))
    except (TypeError, ValueError): fan = 3
    try: temp = max(18, min(int(float(st.get("climate_target_temp") or 26)), 32))
    except (TypeError, ValueError): temp = 26
    return operate, mode, circle, fan, temp
def _send_ac_on(operate, mode, circle, windlevel, temp):
    body = json.dumps({"circle": circle, "mode": mode, "operate": operate, "position": "all",
                       "temperature": str(temp), "windlevel": str(windlevel), "wshld": "0"},
                      separators=(",", ":"))
    return _session.execute(lambda api, vin: api._remote_control(vin=vin, action="ac_on", cmd_content=body))
def set_fan_level(level):
    """Set the A/C fan to a 1-7 level (signal 1941), preserving mode/recirc/temp. 0 → clamped to 1."""
    try: lvl = max(1, min(int(level), 7))
    except (TypeError, ValueError): return False, "bad fan level"
    operate, mode, circle, _fan, temp = _climate_ctx()
    return _send_ac_on(operate, mode, circle, lvl, temp)
def set_recirc(on):
    """Toggle air recirculation (circle in=recirc / out=fresh, signal 1943), preserving mode/fan/temp."""
    operate, mode, _circle, fan, temp = _climate_ctx()
    return _send_ac_on(operate, mode, "in" if on else "out", fan, temp)
def recirc_toggle():
    """Flip recirculation vs the last stored state — lets the climate_tile button (single cmd with an
    on/off label) toggle it like the other climate tiles."""
    import db_reader as _dr
    cur = (_dr.get_latest_status() or {}).get("recirculation")
    return set_recirc(not cur)

# Window position is a 0–100% in the UI, but cmd 230's native range is model-specific: the LEAP-platform
# cars (B10, C10, B05) use 0–10 (10 = fully open, >10 is silently ignored), while the older T03 uses 0–100
# (#62). On-car confirmations: B10 (us) and C10 (kerniger / leapmotor-ha — same 0–10, sending 50 or 100 is
# ignored by the car); B05 shares the B10 platform + pack so it follows the same scale. Map the UI % to the
# model's native value via its full-open scale. cmd 230 is GLOBAL — all four windows move together (the API
# has no per-window control). Quick button = 20% vent; slider = 0 (closed) → 100.
_WINDOWS_SCALE = {"B10": 10, "C10": 10, "B05": 10}   # car_type → native value for "fully open"; default 100 (T03)
def _session_car_type() -> str:
    v = getattr(_session, "_vehicle", None)
    return (getattr(v, "car_type", "") or "").upper() if v else ""
def _t03_force_manual(operate: str, mode: str) -> tuple[str, str]:
    """T03-ONLY climate fix (#67). The T03 firmware silently IGNORES operate=auto climate writes — the
    A/C button, temperature, fan and recirc all no-op (the cloud still returns code:0, so the ACK is
    meaningless; the car simply does nothing) — while it HONORS operate=manual (the Raffredda/quick_cool
    path is the one that works on-car, confirmed in rossiadobe + Gr1m214 logs). So on the T03 ONLY, rewrite
    an auto write to manual. A bare 'nohotcold' has no manual equivalent, so fall back to 'cold' — the one
    mode confirmed to start the T03's A/C on-car (quick_cool). B10/C10/B05 are UNAFFECTED: they keep
    operate=auto, which they honor (deliberately unchanged). NOTE: not verified on-car by us (we have no
    T03); it is the direct consequence of the works-vs-fails diff in the #67 logs. Off is separate (neither
    operate=off nor operate=close switches the T03 off — needs the real T03 off cmd, markoceri #9)."""
    if operate == "auto" and _session_car_type() == "T03":
        return "manual", ("cold" if mode == "nohotcold" else mode)
    return operate, mode
def _windows_native(pct) -> str:
    try: pct = max(0, min(int(pct), 100))
    except (TypeError, ValueError): pct = 0
    full = _WINDOWS_SCALE.get(_session_car_type(), 100)
    return str(round(pct / 100 * full))
def set_windows(pct):
    """Open all windows to a 0–100% position (mapped to the car's native scale)."""
    return _session.execute(lambda api, vin: api.windows(vin, value=_windows_native(pct)))
def open_windows():      return set_windows(20)   # quick "vent" — air passage
def close_windows():     return set_windows(0)
def battery_preheat():   return _session.execute(lambda api, vin: api.battery_preheat(vin))
def battery_preheat_off():return _session.execute(lambda api, vin: api.battery_preheat_off(vin))
def open_sunshade():     return _session.execute(lambda api, vin: api.open_sunshade(vin))
def close_sunshade():    return _session.execute(lambda api, vin: api.close_sunshade(vin))
# Charge-port cable unlock (right 192; promised on mate#19). Exposed on the Charges page
# (charge-limit card) and over MQTT. Confirmed actuating on a real B10 (2026-06-08).
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
