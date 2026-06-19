"""
Leapmotor API client wrapper.
Uses leapmotor-api 0.3.1, which natively maps the B10/B11 status path to /c10 and
serves the T03 named-field status, so no endpoint patching is needed here. We still
parse the raw signal dict ourselves (_parse_signal) to stay independent of the
library's typed model and insulated from its enum changes.
"""
import logging
import os
from dataclasses import dataclass

from leapmotor_api import LeapmotorApiClient

import capability_profile

log = logging.getLogger(__name__)


@dataclass
class VehicleData:
    vin: str
    timestamp_ms: int
    soc: float
    range_km: float
    odometer_km: float
    speed_kmh: float
    gear: str            # P R N D
    vehicle_state: str   # parked driving
    charging_status: int
    charge_power_kw: float
    latitude: float
    longitude: float
    outside_temp: float
    inside_temp: float
    climate_target_temp: float
    battery_min_temp: float
    is_locked: bool
    climate_on: bool
    climate_cooling: bool     # quick-cool active (signal 2669 == 2)
    climate_heating: bool     # quick-heat active (signal 2681 == 2)
    climate_defrost: bool     # windshield defrost active (signal 1945 == 2)
    trunk_open: bool
    windows_open: bool
    sunshade_open: bool
    any_door_open: bool       # driver/passenger/rear doors or trunk
    plug_connected: bool      # cable inserted (signal 1149)
    remaining_charge_min: int # minutes to full (signal 1200), 0 when not charging
    charge_voltage_v: float   # charging voltage (signal 1177)
    charge_current_a: float   # charging current (signal 1178)
    # Individual doors / windows / tyres — used by the optional MQTT → HA bridge
    door_driver_open: bool = False
    door_passenger_open: bool = False
    door_rear_left_open: bool = False
    door_rear_right_open: bool = False
    window_fl_open: bool = False
    window_fr_open: bool = False
    window_rl_open: bool = False
    window_rr_open: bool = False
    tire_fl_bar: float = 0.0
    tire_fr_bar: float = 0.0
    tire_rl_bar: float = 0.0
    tire_rr_bar: float = 0.0
    # Comfort STATE sensors (read-only). They reflect reality on the B10 even though the
    # matching remote COMMANDS don't actuate (see capability_profile). 0 = off, >0 = level/on.
    seat_heat_driver: int = 0       # signal 2100 driverSeatHeating
    seat_heat_passenger: int = 0    # signal 2118 passengerSeatHeating
    seat_vent_driver: int = 0       # signal 2101 driverSeatVentilation
    seat_vent_passenger: int = 0    # signal 2119 passengerSeatVentilation
    steering_heat: int = 0          # signal 1816 steeringWheelHeating
    mirror_heat_left: int = 0       # signal 49 leftMirrorHeating
    mirror_heat_right: int = 0      # signal 50 rightMirrorHeating
    ready: bool = False             # signal 1258 bcmKeyPositionOn3 — faithful READY/ON3 (physical key only)
    charge_completed: bool = False  # signal 3736 chargeCompleted — true at full charge (validate on a real charge)
    security_active: bool = False   # signal 1255 vehicleSecurityActive — locked + alarm armed (validate on-car)
    charge_limit_percent: int | None = None  # configured max-charge SoC (from the config block, not a
                                             # signal); None if unknown / model doesn't report it
    # Battery thermal management state from the BMS (signal 1186 batteryThermalRequest).
    # 0 = none  1 = cooling requested  2 = heating requested (preconditioning active)
    # Used to confirm whether a battery_preheat() command actually took effect.
    battery_thermal_request: int = 0

    def fingerprint(self) -> tuple:
        """Compact snapshot of signals that indicate car activity."""
        return (
            self.is_locked,
            round(self.soc),           # 1% granularity avoids noise
            round(self.inside_temp),   # 1°C granularity
            self.any_door_open,
            self.charging_status,
            self.plug_connected,
        )


class EmptyStatusError(Exception):
    """The cloud returned a vehicle status with no live `signal` block — the car is
    asleep / not reporting, or the response was incomplete. Transient: back off and
    retry rather than treating it as a hard failure."""


# Title/body keywords that mark an inbox message as an OTA / software update (lowercase, substring),
# across the languages a Leapmotor account may use. STOPGAP until a real OTA message pins its
# msg_type — see LeapmotorMateClient.check_ota(). Kept broad enough to catch the notice, specific
# enough that everyday messages (vehicle sharing, etc.) don't match.
_OTA_KEYWORDS = (
    "ota", "fota", "firmware", "aggiorn", "software update", "software-update", "system update",
    "vehicle update", "update available", "mise à jour", "mise a jour", "logiciel",
    "aktualis", "software-aktualisierung", "upgrade",
)


class LeapmotorMateClient:
    def __init__(self, username: str, password: str, pin: str, cert_path: str, key_path: str,
                 device_id: str | None = None):
        self._api = LeapmotorApiClient(
            username=username,
            password=password,
            operation_password=pin,
            app_cert_path=cert_path,
            app_key_path=key_path,
            language="en-US",
            device_id=device_id,
        )
        import session_share
        session_share.install(self._api)   # share ONE token with the web (avoid mutual eviction)
        self._vehicle = None
        self._named_mode_logged = False    # log the T03/EU named-field path once

    def login(self):
        self._api.login()
        vehicles = self._api.get_vehicle_list()
        if not vehicles:
            raise RuntimeError("No vehicles found on this account")
        self._vehicle = vehicles[0]
        log.info("Authenticated — VIN: %s  model: %s  shared: %s",
                 self._vehicle.vin, self._vehicle.car_type,
                 getattr(self._vehicle, "is_shared", False))

    def relogin(self):
        """Force a fresh login to self-heal a broken session. The account TLS cert
        lives in a /tmp temp file; if it vanishes, every request fails forever with
        'Could not find the TLS certificate file'. Dropping the shared-session blob
        and re-logging in re-creates the cert. Also recovers auth/token drops."""
        try:
            import sqlite3
            c = sqlite3.connect(os.environ.get("DB_PATH", "leapmotor_mate.db"), timeout=5)
            c.execute("DELETE FROM settings WHERE key='shared_session'")
            c.commit()
            c.close()
        except Exception as e:  # noqa: BLE001
            log.debug("Could not clear shared session before relogin: %s", e)
        self.login()

    def get_status(self) -> VehicleData:
        # Make sure the per-login account TLS cert still exists before calling the cloud — if it was
        # cleaned up mid-session, re-create it from the saved bytes instead of failing with "Could
        # not find the TLS certificate file" and forcing an unnecessary re-login (#64).
        try:
            import session_share
            session_share.ensure_account_cert(self._api)
        except Exception:  # noqa: BLE001
            pass
        raw = self._api.get_vehicle_raw_status(self._vehicle)
        data = (raw or {}).get("data") or {}
        sig = data.get("signal")
        if not sig:
            # T03 / EU responses carry live data as NAMED fields at the top level of
            # `data` (e.g. "soc", "speed", "gearStatus") instead of a numeric-ID
            # `signal` sub-dict like C10/B10. Rebuild the signal dict our parser
            # expects from those named fields (id↔name map per leapmotor-api 0.3.1).
            sig = _named_fields_to_signal(data)
            if sig and not self._named_mode_logged:
                log.info("T03/EU named-field status detected — mapped %d live fields", len(sig))
                self._named_mode_logged = True
        if not sig:
            # Genuinely empty: car asleep / not reporting (or a brief cloud hiccup).
            # Surface a clear, transient error instead of a bare KeyError so the poller
            # can back off cleanly and retry.
            raise EmptyStatusError("vehicle status has no live signals (car asleep or not reporting)")
        # A status whose signal block is present but carries NO usable SoC (both 100003/1204
        # absent), or a SoC of 0 while the car clearly still has range, is a PARTIAL/glitch read —
        # often a poll perturbed by a just-issued command (e.g. changing the charge limit). Treat it
        # as "no live data" (like an asleep poll) so it can't be stored as a spurious soc=0 row that
        # then seeds a phantom "charged from 0%" reconstruction / "recover missed charges" hit.
        _soc_raw = sig.get("100003")
        if _soc_raw is None:
            _soc_raw = sig.get("1204")
        if _soc_raw is None or (float(_soc_raw or 0) == 0 and float(sig.get("3260") or 0) > 5):
            raise EmptyStatusError("vehicle status carries no usable SoC (partial/glitch read)")
        vd = _parse_signal(self._vehicle.vin, sig)
        # The configured charge limit (max-charge SoC) lives in the config block of this SAME raw
        # status — not in the signal dict — so capture it here, free of any extra cloud call, for
        # the Overview's "to X%" charge-ETA label instead of a hardcoded 100. It's the very field
        # the Charges page reads via get_charge_plan (config["3"]["percent"]). Absent on some models
        # (T03/EU named-field responses) → stays None and the UI falls back to 100.
        try:
            pct = ((data.get("config") or {}).get("3") or {}).get("percent")
            if pct is not None:
                vd.charge_limit_percent = int(pct)
        except (TypeError, ValueError):
            pass
        return vd

    def check_ota(self) -> dict:
        """Scan the account message inbox for an OTA / software-update notice. This is the ONLY
        automatic "update available" signal Leapmotor exposes — there is NO dedicated OTA-status
        endpoint (even the official-app flow / LeapConnect needs the FOTA task_id typed in by hand);
        the cloud delivers "update available" as an inbox MESSAGE. Best-effort, never raises.
        Returns {ota: bool, title: str|None, time: int|None (epoch ms)}.

        We match on the message title/body because the numeric `msg_type` is undocumented and was
        None on every message we've captured so far — so this keyword match is a deliberate STOPGAP:
        the moment a real OTA message is seen on-car, key off its exact msg_type instead and tighten
        this. Non-OTA messages (vehicle sharing, etc.) are intentionally ignored — not surfaced."""
        try:
            ml = self._api.get_message_list(page_no=1, page_size=20)
            msgs = getattr(ml, "messages", None) or []
        except Exception as e:  # noqa: BLE001 — strict lib parser can raise on odd payloads
            log.debug("OTA message scan failed: %s", e)
            return {}
        for m in msgs:
            hay = f"{getattr(m, 'title', '') or ''} {getattr(m, 'message', '') or ''}".lower()
            if any(k in hay for k in _OTA_KEYWORDS):
                st = getattr(m, "send_time", None)
                return {"ota": True, "title": getattr(m, "title", None),
                        "time": int(st) if st else None}
        return {"ota": False}

    def close(self):
        self._api.close()


# Numeric signal-id → T03 named-field map (verbatim from leapmotor-api 0.3.1's
# _SIGNAL_TO_NAMED). C10/B10 report these as numeric IDs inside `data["signal"]`;
# the T03 / EU API reports the SAME data as these named fields at the top level of
# `data`. We invert this to rebuild a numeric `signal` dict for the T03 so the shared
# _parse_signal() below works unchanged for every model.
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
    "3": "latitudeSigned", "2": "longitudeSigned",
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
    """Rebuild a numeric-id `signal` dict from a T03/EU response, whose live data is
    carried as named fields at the top level of `data`. Returns None when no known
    named field is present (genuinely empty / car asleep)."""
    if not isinstance(data, dict):
        return None
    sig = {sid: data[name] for sid, name in _SIGNAL_TO_NAMED.items()
           if data.get(name) is not None}
    return sig or None


_GEAR_MAP = {0: "P", 1: "R", 2: "N", 3: "D"}


def _sf(sig: dict, k: str):
    v = sig.get(k)
    try:    return float(v) if v is not None else None
    except (TypeError, ValueError): return None


def _si(sig: dict, k: str):
    v = sig.get(k)
    try:    return int(v) if v is not None else None
    except (TypeError, ValueError): return None


# Below this magnitude the charge current is just plugged-idle / sensor noise.
# Default 2.0 A so low-power home charges (and the tail of a charge) are still
# detected; user-tunable from Settings (the poller calls set_charge_current_min
# each cycle). NB: this is unrelated to the regen threshold (recorder.py, -3.0 A).
_CHARGE_CURRENT_MIN_A = 2.0


def set_charge_current_min(amps: float) -> None:
    """Update the charge-detection current floor (A) from the Settings value."""
    global _CHARGE_CURRENT_MIN_A
    if amps and amps > 0:
        _CHARGE_CURRENT_MIN_A = amps


def _charge_power_kw(sig: dict) -> float:
    """Charge/regen power from current (1178) × voltage (1177). Signal 49 is NOT a
    power — in the Leapmotor app it's the left-mirror-heating flag. Magnitude only;
    the recorder decides charge vs regen from the current sign."""
    current = _sf(sig, "1178")
    voltage = _sf(sig, "1177")
    if current is None or voltage is None or abs(current) < _CHARGE_CURRENT_MIN_A:
        return 0.0
    return round(abs(current * voltage) / 1000.0, 3)


def _is_plugged_in(sig: dict) -> bool:
    """Whether the charge cable is physically connected. Uses signal 1149 (charge
    connection status: 1=connected, 2=charging), gated by motion.

    Why 1149 and not 47: signal 47 (acInputSlowCharge) was the old primary, but on the
    B10 it LATCHES at 1 after an AC charge and only clears ~5 min later, when the car's
    charge controller tears down the AC subsystem — it does NOT drop on unplug. That kept
    a finished charge SESSION open long after the cable was pulled (the plug_connected
    OR-term in the state machine never went false), inflating the session window.
    Signal 1149 instead drops to 0 promptly when the charge SESSION ends — at completion
    (target SoC reached) or on unplug. Verified on-car twice: 1149 fell ~40s after the car
    hit its charge limit and the session closed at once, while 47 stayed latched at 1 for
    10+ minutes (right through the physical unplug). Its only flaw is reading 1 spuriously
    during regen at speed, so we suppress it while the car is moving (it can never be
    plugged in while driving anyway — same motion gate as _is_charging). Falls back to
    signal 47 only when 1149 is absent (other models)."""
    if _si(sig, "1010") not in (None, 0):       # gear R/N/D → moving, cannot be plugged
        return False
    if (_sf(sig, "1319") or 0) > 2.0:            # speed > 2 km/h → moving (gear may lag)
        return False
    conn = _si(sig, "1149")
    if conn is not None:
        return conn in (1, 2)
    return _si(sig, "47") == 1                   # legacy fallback when 1149 is missing


def _is_charging(sig: dict) -> bool:
    """Whether the car is actually charging. Charging only happens while PARKED, so the
    car must be stationary (gear P, speed ~0); plus the cable plugged in (1149) AND a
    meaningful charge current (1178). The motion gate is essential: during regen braking
    the pack current is strongly negative (same sign as charging) AND 1149 reads 1
    spuriously, so without it driving is mistaken for charging — fragmenting trips and
    creating phantom charge sessions. Signal 1939 (AC fan mode) is NOT used."""
    if _si(sig, "1010") not in (None, 0):   # gear R/N/D → moving, cannot be charging
        return False
    if (_sf(sig, "1319") or 0) > 2.0:       # speed > 2 km/h → moving (gear signal may lag)
        return False
    if _si(sig, "1149") in (None, 0):   # cable not connected → cannot be charging
        return False
    current   = _sf(sig, "1178")
    remaining = _si(sig, "1200")
    power     = _charge_power_kw(sig)

    if current is not None:
        if abs(current) < _CHARGE_CURRENT_MIN_A:   # resting/plugged-idle → not charging
            return False
        return remaining is not None or power >= 1.0
    if power >= 1.0:
        return remaining is not None
    return _si(sig, "1149") == 2                    # fallback: connection status "charging"


# ── GPS sign memory (GitHub #43) ────────────────────────────────────────────────
# The signed coordinate pair (signals 2/3) is authoritative, but some cars omit it in
# certain poll states and we then fall back to the UNSIGNED pair (3724/3725/219x), which is
# an absolute value — re-flipping west-of-Greenwich / southern-hemisphere cars into the sea
# (the #30 symptom returns; smalley1992 is the second UK car to hit it). A car can't cross
# the equator or prime meridian between polls, so we remember the last AUTHORITATIVE sign
# per VIN and re-apply it to the unsigned magnitude. The memory is only ever written by a
# signed read, never by the fallback, so it can't be polluted. seed_coord_signs() primes it
# from a persisted setting on poller startup, so an add-on update / restart doesn't plot the
# car in the sea until the next signed poll arrives.
_coord_sign: dict[str, dict[str, float]] = {}


def _coerce_float(raw) -> float:
    if raw in (None, ""):
        return 0.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def seed_coord_signs(vin: str, lat_sign: float = 0.0, lon_sign: float = 0.0) -> None:
    """Prime the per-VIN GPS sign memory at poller startup. 0 = unknown (leaves it unset)."""
    if not vin:
        return
    mem = _coord_sign.setdefault(vin, {})
    if lat_sign:
        mem["lat"] = -1.0 if lat_sign < 0 else 1.0
    if lon_sign:
        mem["lon"] = -1.0 if lon_sign < 0 else 1.0


def get_coord_signs(vin: str) -> dict:
    """Current remembered signs for this VIN (only updated by authoritative signed reads)."""
    return dict(_coord_sign.get(vin, {}))


def _resolve_coord(vin: str, axis: str, signed_raw, unsigned_raw) -> float:
    """Resolve one GPS axis. The signed signal (2/3) is authoritative and refreshes the
    remembered sign; when only the unsigned signal is present, re-apply the remembered sign
    to its magnitude (#43). Returns 0.0 when no usable value exists. With no memory yet (a
    fresh install before any signed poll) the unsigned value is used as-is — unchanged from
    the pre-#43 behaviour, so east-of-Greenwich cars are never affected."""
    mem = _coord_sign.setdefault(vin, {})
    s = _coerce_float(signed_raw)
    if s != 0.0:
        mem[axis] = -1.0 if s < 0 else 1.0
        return s
    u = _coerce_float(unsigned_raw)
    if u == 0.0:
        return 0.0
    return abs(u) * mem.get(axis, 1.0)


def _parse_signal(vin: str, sig: dict) -> VehicleData:
    gear      = _GEAR_MAP.get(int(sig.get("1010") or 0), "P")
    speed_kmh = float(sig.get("1319") or 0)
    # parked/driving from GEAR + SPEED (the trusted motion inputs the trip state machine also
    # uses), NOT signal 1941 — that's acAirVolume (AC fan speed): a fan level of 3 or 5 while the
    # car was parked used to be misread as "driving" and published to HA (MQTT) / ABRP.
    vehicle_state = "driving" if (gear in ("D", "R", "N") or speed_kmh > 1) else "parked"

    # Windows: flag OR position % (the T03 reports only the %, the B10 only the flag) — the same
    # shared helper the web uses, so the stored status matches the Vehicle page (#62). The poller
    # can't reach the per-VIN capability store from here, so use_pct = bool(vin); that is identical
    # to the web today (the windows_pct gate is never marked broken) and the B10 is safe because it
    # sends no % signals. window_open_states returns [FL, FR, RL, RR].
    win_states = capability_profile.window_open_states(sig, bool(vin))

    return VehicleData(
        vin=vin,
        timestamp_ms=int(sig.get("sts") or sig.get("1") or 0),
        soc=float(sig.get("100003") or sig.get("1204") or 0),
        range_km=float(sig.get("3260") or 0),
        odometer_km=float(sig.get("1318") or 0),
        speed_kmh=speed_kmh,
        gear=gear,
        vehicle_state=vehicle_state,
        charging_status=1 if _is_charging(sig) else 0,
        charge_power_kw=_charge_power_kw(sig),
        # Signals 2/3 carry the SIGNED coordinates; 3724/3725 (and 2190/2191) are unsigned —
        # west-of-Greenwich cars lost the longitude sign there (GitHub #30: Lichfield B10
        # reports 2=-1.915912 but 3724=+1.915912; on east-of-Greenwich cars 2 == 3724). When
        # a poll omits the signed pair, _resolve_coord re-applies the last known sign (#43).
        latitude=_resolve_coord(vin, "lat", sig.get("3"), sig.get("3725") or sig.get("2190")),
        longitude=_resolve_coord(vin, "lon", sig.get("2"), sig.get("3724") or sig.get("2191")),
        outside_temp=None,   # no ambient-temp signal exists (2101 = driverSeatVentilation)
        inside_temp=float(sig.get("1349") or 0),
        climate_target_temp=float(sig.get("2183") or 0),
        battery_min_temp=float(sig.get("1182") or 0),
        is_locked=int(sig.get("1298") or 0) == 1,
        climate_on=int(sig.get("1938") or 0) == 1,
        climate_cooling=int(sig.get("2669") or 0) == 2,
        climate_heating=int(sig.get("2681") or 0) == 2,
        climate_defrost=int(sig.get("1945") or 0) == 2,
        trunk_open=int(sig.get("1281") or 0) != 0,
        windows_open=any(bool(w) for w in win_states),
        sunshade_open=int(sig.get("1724") or 0) != 0,
        any_door_open=any(
            int(sig.get(k) or 0) != 0
            for k in ("1277", "1278", "1279", "1280", "1281")
        ),
        plug_connected=_is_plugged_in(sig),
        remaining_charge_min=int(sig.get("1200") or 0),
        charge_voltage_v=float(sig.get("1177") or 0),
        charge_current_a=float(sig.get("1178") or 0),
        seat_heat_driver=int(sig.get("2100") or 0),
        seat_heat_passenger=int(sig.get("2118") or 0),
        seat_vent_driver=int(sig.get("2101") or 0),
        seat_vent_passenger=int(sig.get("2119") or 0),
        steering_heat=int(sig.get("1816") or 0),
        # Mirror heat 49/50 are kept as separate left/right ON PURPOSE. On the B10 it's a UNIFIED
        # both-mirror control (verified on-car: 49 and 50 report the same value), but other models
        # may heat each mirror independently — so we read both rather than collapse to one. Don't
        # "simplify" these into a single sensor; that would lose per-side data on those models.
        mirror_heat_left=int(sig.get("49") or 0),
        mirror_heat_right=int(sig.get("50") or 0),
        door_driver_open=int(sig.get("1277") or 0) != 0,
        door_passenger_open=int(sig.get("1278") or 0) != 0,
        door_rear_left_open=int(sig.get("1279") or 0) != 0,
        door_rear_right_open=int(sig.get("1280") or 0) != 0,
        window_fl_open=bool(win_states[0]),
        window_fr_open=bool(win_states[1]),
        window_rl_open=bool(win_states[2]),
        window_rr_open=bool(win_states[3]),
        # Tyre signal→wheel mapping. The leapmotor-api docs label these LF=2667/RF=2653/
        # LR=2646/RR=2660, but that's WRONG: cross-checked on TWO real B10s against the official
        # app's per-wheel view — the #32 reporter's UK car AND Silvio's IT car, both with the
        # 280-kPa wheel at the REAR-RIGHT — the true order is the ascending-id one:
        # 2646=FL, 2653=FR, 2660=RL, 2667=RR. (State signals pair the same way:
        # FL=2655, FR=2648, RL=2662, RR=2641 — see _parse_vehicle_status.)
        tire_fl_bar=round(float(sig.get("2646") or 0) / 100.0, 2),
        tire_fr_bar=round(float(sig.get("2653") or 0) / 100.0, 2),
        tire_rl_bar=round(float(sig.get("2660") or 0) / 100.0, 2),
        tire_rr_bar=round(float(sig.get("2667") or 0) / 100.0, 2),
        ready=int(sig.get("1258") or 0) == 1,   # B10 faithful READY (ON3) sensor
        charge_completed=int(sig.get("3736") or 0) != 0,  # 3736 chargeCompleted — truthy (confirm value at a real full charge)
        security_active=int(sig.get("1255") or 0) != 0,   # 1255 vehicleSecurityActive — B10 reads 2 when armed; truthy, matches kerniger bool()
        battery_thermal_request=int(sig.get("1186") or 0),  # 0=none 1=cooling 2=heating (preconditioning)
    )
