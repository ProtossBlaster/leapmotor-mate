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
    is_reev: bool = False     # car reports a fuel tank (signal 3235) → range-extender model
    fuel_level_pct: float = None  # REEV fuel tank level % (signal 3235); None on a BEV
    # Litres actually in the tank — signal 3263, reported in MILLILITRES. Decoded by @gm27271
    # (beta #10) and confirmed here across seven bundles from three owners: 3263 ÷ 3235 is constant
    # to ±0.05 L within a model, and the highest value ever seen on a full C10 tank is exactly
    # 47 500. It makes the tank-size constant unnecessary wherever it is present — the car counts
    # the litres itself, finely enough to argue with a pump (his fill: 34.416 L where the pump said
    # 33.84). None on a BEV.
    fuel_liters: float = None
    fuel_range_km: float = None       # REEV range on fuel alone (signal 3259); None on a BEV
    combined_range_km: float = None   # REEV total range = battery + fuel (signal 3261); None on a BEV
    raw_signals: dict = None  # full raw signal dict — attached in research/beta mode for full-PID logging
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
    # AC-port / V2L mode — signal 47 (acInputSlowCharge): 0 = AC port idle, 1 = AC charging (input;
    # LATCHES at 1 for ~5-10 min after a charge, see _is_plugged_in), 2 = V2L bidirectional discharge
    # active. Verified on-car 2026-06-19: V2L switch ON + adapter → 47=2, battery discharging (1178>0).
    ac_port_mode: int = 0

    # Climate detail (read+write validated on-car 2026-06-20): fan level (signal 1941 acAirVolume,
    # 1-7; HOLDS the last level even when A/C is off), recirculation (signal 1943: 1=recirc/in,
    # 0=fresh/out), base mode (signal 3713: 0=auto·1=cool·3=heat·4=vent). NB: markoceri's lib
    # mislabels 1941 as drive_status — the on-car diff proved it's the fan air-volume.
    fan_level: int = 0
    recirculation: bool = False
    climate_mode: int | None = None

    # Cable inserted AND the charge deliberately postponed to the programmed window (1149 == 4).
    # Kept apart from plug_connected because the two answer different questions: the Overview and
    # the MQTT sensor want "is the cable in", the session machinery wants "is a charge running".
    charge_deferred: bool = False

    @property
    def v2l_active(self) -> bool:
        """True when the AC port is in V2L / bidirectional-discharge mode (powering an external
        load). The DELIVERED power is gated separately on real discharge current (1178)."""
        return self.ac_port_mode == 2

    @property
    def climate_mode_label(self) -> str:
        """Human label for the base climate mode (signal 3713); '' if unknown / no data."""
        return {0: "auto", 1: "cool", 3: "heat", 4: "vent"}.get(self.climate_mode, "")

    def fingerprint(self) -> tuple:
        """Compact snapshot of signals that indicate car activity."""
        return (
            self.is_locked,
            round(self.soc),           # 1% granularity avoids noise
            # ⚠️ None-tolerant since #144: a car that never sends the cabin temperature now stores
            # None rather than a fabricated 0.0, and `round(None)` raised — from inside the activity
            # fingerprint, which every poll computes. The tests caught it; a T03 would have caught
            # it in production, by not polling at all.
            None if self.inside_temp is None else round(self.inside_temp),   # 1°C granularity
            self.any_door_open,
            self.charging_status,
            self.plug_connected,
            self.ac_port_mode,         # V2L/charge engaging is activity → triggers the fast (alert) cadence
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
        self._vehicles: list = []          # every car on the account, in the order the cloud lists them
        self._named_mode_logged = False    # log the T03/EU named-field path once
        # Which status path a model reads on, and whether the fallback has been tried — BOTH per VIN.
        # They describe a MODEL, and an account can hold two: a B10 that answers on its own path and
        # a model that only answers on the B-family one would otherwise teach each other the wrong
        # address, one poll after the other.
        self._status_car_type: dict = {}   # vin → proven status path (see _raw_status)
        self._status_fallback_tried: set = set()

    def login(self):
        self._api.login()
        vehicles = self._api.get_vehicle_list()
        if not vehicles:
            raise RuntimeError("No vehicles found on this account")
        # Every car on the account, and the first one as the default target. `_vehicle` stays what
        # it has always been so nothing that reads it has to change; `_vehicles` is what lets the
        # poller give each car its own recorder instead of only ever seeing the first.
        self._vehicles = list(vehicles)
        self._vehicle = vehicles[0]
        log.info("Authenticated — VIN: %s  model: %s  shared: %s",
                 self._vehicle.vin, self._vehicle.car_type,
                 getattr(self._vehicle, "is_shared", False))
        for extra in vehicles[1:]:
            log.info("Also on this account — VIN: %s  model: %s  shared: %s",
                     extra.vin, extra.car_type, getattr(extra, "is_shared", False))

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

    # The status endpoint is the ONE call in the whole flow with the model in its address:
    # …/vehicle/v1/status/get/{car_type}. The library maps B10 and B11 onto c10 and lets every other
    # model fall through to its own name — so a model nobody has added asks for an address the
    # backend does not serve, and answers "No message available" from the very first poll while
    # login, the vehicle list, the VIN, the abilities and the official app all work perfectly.
    # @arnolds77's B05 (#177) is exactly that: Mate already treats the B05 as a B10 everywhere else
    # — same pack, same window scale, same command path — and this is the one place it doesn't.
    #
    # So: if the model's own address fails, try the B-family one ONCE, and remember the answer.
    # Remembering is the point — a retry on every poll would double the load on the very session
    # v2.13.2 stopped us from exhausting. It costs one extra call, once, on an install that is
    # already getting nothing.
    _STATUS_PATH_FALLBACK = "c10"

    def _raw_status(self, vehicle=None):
        vehicle = vehicle or self._vehicle
        proven = self._status_car_type.get(vehicle.vin)
        if proven is not None:
            return self._api.get_vehicle_raw_status(self._vehicle_as(proven, vehicle))
        try:
            return self._api.get_vehicle_raw_status(vehicle)
        except Exception as first:                                  # noqa: BLE001
            own = (getattr(vehicle, "car_type", "") or "").lower()
            if vehicle.vin in self._status_fallback_tried or own == self._STATUS_PATH_FALLBACK:
                raise
            self._status_fallback_tried.add(vehicle.vin)
            log.warning("Status call failed for model %s (%s) — retrying once on the %s path",
                        own or "?", first, self._STATUS_PATH_FALLBACK)
            try:
                raw = self._api.get_vehicle_raw_status(
                    self._vehicle_as(self._STATUS_PATH_FALLBACK, vehicle))
            except Exception:                                       # noqa: BLE001
                raise first from None                               # report the REAL failure, not ours
            self._status_car_type[vehicle.vin] = self._STATUS_PATH_FALLBACK
            log.warning("Model %s reads its status on the %s path — using it from now on. "
                        "Please report this model so it can be mapped properly.",
                        own or "?", self._STATUS_PATH_FALLBACK)
            return raw

    def _vehicle_as(self, car_type: str, vehicle=None):
        """A copy of the vehicle with a different car_type, so ONLY the status path changes. The real
        vehicle is left alone: its car_type is read elsewhere for the pack size, the window scale and
        the command paths, and those are already right."""
        import dataclasses
        return dataclasses.replace(vehicle or self._vehicle, car_type=car_type)

    def get_status(self, vehicle=None) -> VehicleData:
        # Make sure the per-login account TLS cert still exists before calling the cloud — if it was
        # cleaned up mid-session, re-create it from the saved bytes instead of failing with "Could
        # not find the TLS certificate file" and forcing an unnecessary re-login (#64).
        try:
            import session_share
            session_share.ensure_account_cert(self._api)
        except Exception:  # noqa: BLE001
            pass
        vehicle = vehicle or self._vehicle
        raw = self._raw_status(vehicle)
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
        vd = _parse_signal(vehicle.vin, sig)
        vd.raw_signals = sig   # full dict for research-mode full-PID logging (ignored in normal builds)
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
        Returns {ok: bool (endpoint answered), scanned: int, ota: bool, title, time}.

        `ok` distinguishes the three states that all otherwise surface as a bare "None" on the
        Overview and used to be indistinguishable (issue #156, a Malaysia C10): the inbox is
        genuinely empty (ok=True, scanned=0), it has messages but none is an update (ok=True,
        scanned>0, ota=False), or we couldn't read the inbox at all for this account/region
        (ok=False). The caller logs each outcome so a diagnostics bundle can tell which it is.

        We match on the message title/body because the numeric `msg_type` is undocumented and was
        None on every message we've captured so far — so this keyword match is a deliberate STOPGAP:
        the moment a real OTA message is seen on-car, key off its exact msg_type instead and tighten
        this. Non-OTA messages (vehicle sharing, etc.) are intentionally ignored — not surfaced."""
        try:
            ml = self._api.get_message_list(page_no=1, page_size=20)
            msgs = getattr(ml, "messages", None) or []
        except Exception as e:  # noqa: BLE001 — strict lib parser can raise on odd payloads
            log.warning("OTA inbox scan: message endpoint failed (%s) — cannot check for updates", e)
            return {"ok": False}
        for m in msgs:
            hay = f"{getattr(m, 'title', '') or ''} {getattr(m, 'message', '') or ''}".lower()
            if any(k in hay for k in _OTA_KEYWORDS):
                st = getattr(m, "send_time", None)
                return {"ok": True, "scanned": len(msgs), "ota": True,
                        "title": getattr(m, "title", None),
                        "time": int(st) if st else None}
        return {"ok": True, "scanned": len(msgs), "ota": False}

    def get_charge_schedule(self) -> dict | None:
        """The car's own charge window (cmd 190) — flat dict: chargeEnable, starttime, endtime,
        chargesoc, cycles, circulation, recharge. Read-only, never raises.

        It lives in the CAR, not in any signal the poll loop already reads, so the only way to show
        it is to ask for it — which is why the caller does that rarely and caches the answer. A
        window is set once and then left alone for months; the Overview refreshes every 30 s."""
        try:
            return self._api.get_charge_schedule(self._vehicle.vin)
        except Exception as e:  # noqa: BLE001 — a schedule read must never disturb the poll
            log.debug("Charge schedule fetch failed: %s", e)
            return None

    def get_energy_counters(self, vehicle=None) -> dict | None:
        """The car's official lifetime counters from `mileage/energy/detail`: total consumed
        energy INCLUDING parked/standby (integer kWh — the finest the cloud serves, param-probed
        02/07) and total mileage (read from the 0.1-mile field ×1.609344, ~160 m resolution —
        finer than the integer-km twin). The endpoint needs a SIGNED begin/end window just to
        unlock totalEnergy; the counters themselves are window-independent, so any window works.
        Returns {"total_energy_kwh": int, "total_mileage_km": float} or None. Never raises."""
        import json as _json
        import time as _time
        from urllib.parse import quote
        try:
            from leapmotor_api.crypto import build_signed_headers
            api, vin = self._api, (vehicle or self._vehicle).vin
            now_ms = int(_time.time() * 1000)
            b_ms = now_ms - 7 * 86400 * 1000
            h = build_signed_headers(
                sign_key=api.sign_key, device_id=api.device_id, vin=vin, language=api.language,
                body_params={"begintime": str(b_ms), "endtime": str(now_ms)}).to_dict()
            h.update(api._auth_headers())
            body = f"endtime={now_ms}&begintime={b_ms}&vin={quote(vin, safe='')}"
            resp = api._post(path="/carownerservice/oversea/drivingRecord/v1/mileage/energy/detail",
                             headers=h, data=body, cert=api.account_cert)
            raw = resp.get("body")
            j = _json.loads(raw) if isinstance(raw, str) else (raw or {})
            d = (j.get("data") or {}) if isinstance(j, dict) else {}
            te = d.get("totalEnergy")
            if te is None:
                return None
            try:
                km = float(d.get("totalmileageMile")) * 1.609344
            except (TypeError, ValueError):
                km = float(d.get("totalmileage") or 0) or None
            return {"total_energy_kwh": int(te), "total_mileage_km": round(km, 1) if km else None}
        except Exception as e:  # noqa: BLE001
            log.debug("Energy counters fetch failed: %s", e)
            return None

    def get_ec_range(self, begin_ts: int, end_ts: int, vehicle=None) -> tuple:
        """Official driving-energy split (getEC) over [begin_ts, end_ts] epoch seconds.
        Returns ('ok', {driving,ac,other kWh}) | ('empty', None) — the cloud's genuine
        'no driving in this window' | ('miss', None) — auth/transport/odd payload; a miss is
        recoverable later (getEC is retro-queryable), so callers just record it. Never raises."""
        import json as _json
        from urllib.parse import quote
        try:
            from leapmotor_api.crypto import build_consumption_last_week_headers
            api, vin = self._api, (vehicle or self._vehicle).vin
            h = build_consumption_last_week_headers(
                sign_key=api.sign_key, device_id=api.device_id, carvin=vin,
                begintime=str(begin_ts), endtime=str(end_ts), language=api.language,
            ).to_dict()
            h.update(api._auth_headers())
            body = f"endtime={end_ts}&begintime={begin_ts}&carvin={quote(vin, safe='')}"
            resp = api._post(path="/carownerservice/oversea/drivingRecord/v1/getLastweekEC",
                             headers=h, data=body, cert=api.account_cert)
            raw = resp.get("body")
            j = _json.loads(raw) if isinstance(raw, str) else (raw or {})
            if not isinstance(j, dict):
                return "miss", None
            d = j.get("data") or {}
            if d:
                return "ok", {"driving": float(d.get("driverEC") or 0),
                              "ac": float(d.get("acEC") or 0),
                              "other": float(d.get("otherEC") or 0)}
            if j.get("result") in (0, 100) or "no data" in str(j.get("message") or "").lower():
                return "empty", None
            return "miss", None
        except Exception as e:  # noqa: BLE001
            log.debug("getEC range fetch failed: %s", e)
            return "miss", None

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
        # 1=connected, 2=charging, 3=a third connected state the REEVs cycle THROUGH mid-charge
        # (seen on the C10/B10 range-extenders alongside 1=2, always parked, current ~0). Treating
        # 3 as unplugged made the cable read as pulled every time it flickered 2→3→2, which closed
        # and reopened the session and shredded one slow AC charge into many empty fragments — each
        # then dropped as a phantom (beta #12/#13). Keep the session whole across the flicker; the
        # motion gate above still bars anything that could read 3 while driving. (5 = the drive-time
        # cable code, deliberately NOT here.)
        # 4 = connected, but the charge is DEFERRED to the programmed window: the cable is in and
        # the car is deliberately not drawing. Measured on Silvio's B10 on 09/08/26 (#243) — he
        # enabled the schedule mid-charge and the code went 2 → 4 with the cable untouched, while
        # the official app, reading that same frame, kept drawing the cable. It used to fall into
        # "unplugged" by exclusion, which is what blanked the cable on the Overview. See
        # `_is_deferred_charge`: 4 must never be read as charging.
        return conn in (1, 2, 3, 4)
    return _si(sig, "47") == 1                   # legacy fallback when 1149 is missing


def _is_deferred_charge(sig: dict) -> bool:
    """Cable connected but the charge is waiting for its programmed window (1149 == 4).

    Plugged and charging are two different questions, and this is the state where they part
    company: every consumer that turns "the cable is in" into "a charge is running" has to skip
    it, or the session the car just ended stays open until the window opens hours later."""
    return _si(sig, "1149") == 4


def _temp_or_none(raw):
    """A temperature reading, or None when the car did not send one (#144).

    Deliberately NOT `float(raw or 0)`: that is what turned a signal the car never emits into a
    perfectly plausible 0 °C, on a T03 whose owner saw it all summer. And a real 0.0 is kept — a
    battery pack genuinely at zero is a fact, not an absence, so only `None` and an unparseable
    value become None. → [[signal-absent-is-not-signal-zero]]
    """
    # ⚠️ No `if raw is None` branch: `float(None)` raises TypeError, which the guard below already
    # turns into None. It was there and a mutation proved no test could tell the difference — a line
    # nothing can distinguish is a line that is not doing anything.
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


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
    # 5 is the drive-time cable code, NOT a connection — _is_plugged_in leaves it out for the same
    # reason, and the two readers of 1149 must agree. The motion gate above does not always catch
    # it: on ebagnoli's C10 (beta #13) 1149 read 5 at 08:30 on 23/07 while the speed frame was
    # still the previous evening's 0 km/h, which opened a 0-minute session — harmless only because
    # it delivered nothing and the phantom guard dropped it.
    # ⚠️ 4 (charge postponed to the programmed window) is deliberately NOT excluded here, though it
    # is a "not drawing" code. We have measured 4 only at rest (0.1 A); nobody has watched a car
    # whose window OPENS while the code stays at 4. Excluding it would then drop the entire
    # scheduled charge, silently — the worse failure by far. Let the current decide, as it does for
    # every other code: parked with a real charge current IS a charge, whatever the cable says.
    if _si(sig, "1149") in (None, 0, 5):   # cable not connected → cannot be charging
        return False
    current   = _sf(sig, "1178")
    remaining = _si(sig, "1200")
    power     = _charge_power_kw(sig)

    if current is not None:
        if abs(current) < _CHARGE_CURRENT_MIN_A:   # resting/plugged-idle → not charging
            # ...EXCEPT a C10 REEV charging in AC: on that car the pack current (1178) reads ~0
            # during slow AC charging — the on-board charger feeds the pack by a path this sensor
            # doesn't see — so the BEV rule "current ≥ 2 A" never fires and the session is missed
            # (beta #13 ebagnoli; same signature back to the earliest 1.36.1 bundle). Trust the
            # cable's OWN state when it explicitly says "charging" (1149==2, not the
            # "connected/waiting" 1149==1 of a scheduled charge) AND a charge is in progress
            # (remaining minutes > 0). Driving is already gated out above, and a bare 0→2→0 cable
            # blip has no remaining-time so it still can't open a phantom.
            #
            # ⚠️ This is the C10 signature ONLY — NOT the B10 REEV, whatever this comment used to
            # say. @michapr (beta #12) was diagnosed as this case on 23/07 and it was wrong: his
            # pack current during AC charging reads −3.8 A, well above the floor. v2.8.4 shipped
            # this very rule for him and changed nothing, because his cable sits at 1149==1 and
            # only brushes 2 for a few seconds at a time — the test below essentially never fires
            # on that car. Retracted in the issue on 24/07 ("I had tuned it on a C10's signature
            # and wrongly assumed the two REEVs behaved the same"), and fixed for real in v2.8.6
            # by the SoC-rise branch in state_machine.py, which is what actually covers a B10 REEV.
            # Do not re-add michapr here: two REEV models, two different signatures, two rules.
            if _si(sig, "1149") == 2 and (_si(sig, "1200") or 0) > 0:
                return True
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

# #232: "written only by a signed read" is not the same as "written only by a SIGNED read". Signal
# 2 was believed whenever it was non-zero, with no sanity check, so one frame that arrived carrying
# the bare magnitude taught the wrong hemisphere — and poller/main.py then persisted it, so the
# mistake outlived the restart and both readers mirrored the car for good.
#
# The physics the guard was missing: a car cannot teleport across the line. Driving to the other
# side means passing through zero, so a real crossing is always observed NEAR the line; a dropped
# sign is observed at full magnitude (8.6° W becoming 8.6° E is 1720 km between two polls).
_MERIDIAN_NEAR_DEG = 1.0          # within ~85 km of the line a crossing is ordinary — believe it
_SIGN_FLIP_CONFIRMATIONS = 10     # far from it, the flip must be argued: 10 polls in a row
# Consecutive signed polls, per VIN and axis, asking for the opposite hemisphere. Kept OUT of
# _coord_sign so get_coord_signs() still returns only the two signs poller/main.py persists — a
# counter leaking in there would rewrite the setting on every poll.
_sign_flip_pending: dict[str, dict[str, int]] = {}


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
    the pre-#43 behaviour, so east-of-Greenwich cars are never affected.

    A signed read that contradicts a hemisphere we already know is only believed near the line, or
    after _SIGN_FLIP_CONFIRMATIONS polls in a row say the same thing (#232)."""
    mem = _coord_sign.setdefault(vin, {})
    s = _coerce_float(signed_raw)
    if s != 0.0:
        proposed = -1.0 if s < 0 else 1.0
        known = mem.get(axis)
        if known is None or known == proposed or s < 0.0 or abs(s) <= _MERIDIAN_NEAR_DEG:
            # Nothing to protect (fresh install), nothing to argue about, a value that proves its
            # own sign, or a car genuinely at the line. Any of the four also settles a pending flip.
            #
            # 🔑 `s < 0` is why the guard is one-directional: a LOST sign can only ever surface as
            # a positive number, because the signals it would be confused with are magnitudes and
            # have no minus to drop. A negative reading is therefore evidence, not noise — doubting
            # it would strand a car that moved west or south for ten polls and buy nothing.
            mem[axis] = proposed
            _sign_flip_pending.get(vin, {}).pop(axis, None)
            return s
        seen = _sign_flip_pending.setdefault(vin, {}).get(axis, 0) + 1
        _sign_flip_pending[vin][axis] = seen
        if seen >= _SIGN_FLIP_CONFIRMATIONS:
            # Argued for long enough to be real: a car shipped across the line with its SIM dark
            # never gets to be seen near it, and refusing forever would strand that owner.
            log.warning("GPS %s: the opposite hemisphere has now been reported %d polls running — "
                        "accepting it and re-learning the sign", axis, seen)
            mem[axis] = proposed
            _sign_flip_pending[vin].pop(axis, None)
            return s
        # NO coordinate in this line: the poller log ships inside the diagnostics bundle, which
        # strips the GPS signals precisely so it can be attached in public (#232 asked for it).
        log.warning("GPS %s: cloud reported the opposite hemisphere far from the line (%d/%d) — "
                    "a car cannot cross it in one poll, keeping the remembered sign",
                    axis, seen, _SIGN_FLIP_CONFIRMATIONS)
        return abs(s) * known
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
        is_reev=(sig.get("3235") is not None),   # fuel level present → range-extender variant
        fuel_level_pct=(float(sig["3235"]) if sig.get("3235") is not None else None),  # REEV tank %
        fuel_liters=(float(sig["3263"]) / 1000.0 if sig.get("3263") is not None else None),  # 3263 = mL
        fuel_range_km=(float(sig["3259"]) if sig.get("3259") is not None else None),       # REEV fuel range
        combined_range_km=(float(sig["3261"]) if sig.get("3261") is not None else None),   # REEV total range
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
        # 🔴 A temperature the car did not send is ABSENT, not zero. `or 0` turned silence into
        # 0.0 — a value that looks like a reading, and in August, at forty degrees outside, an
        # absurd one that Mate showed with a straight face (#144, @staffhotel-beep's European T03:
        # both of these permanently empty, and nothing in his bundle able to say why).
        #
        # A plausible-looking wrong number is worse than a hole: the hole says "unknown" and the
        # number says 0 °C to the pages, to A Better Route Planner, and to the ready-automation
        # gate that fires "only pre-heat below 5 °C". `climate_mode` two lines below has always
        # done this correctly. → [[signal-absent-is-not-signal-zero]]
        inside_temp=_temp_or_none(sig.get("1349")),
        climate_target_temp=_temp_or_none(sig.get("2183")),
        battery_min_temp=_temp_or_none(sig.get("1182")),
        is_locked=int(sig.get("1298") or 0) == 1,
        climate_on=int(sig.get("1938") or 0) == 1,
        climate_cooling=int(sig.get("2669") or 0) == 2,
        climate_heating=int(sig.get("2681") or 0) == 2,
        climate_defrost=int(sig.get("1945") or 0) == 2,
        fan_level=int(sig.get("1941") or 0),                        # 1941 acAirVolume: fan level 1-7
        recirculation=int(sig.get("1943") or 0) == 1,              # 1943: 1=recirc(in) / 0=fresh(out)
        climate_mode=int(sig["3713"]) if sig.get("3713") is not None else None,  # 3713: 0 auto/1 cool/3 heat/4 vent
        trunk_open=int(sig.get("1281") or 0) != 0,
        windows_open=any(bool(w) for w in win_states),
        sunshade_open=int(sig.get("1724") or 0) != 0,
        any_door_open=any(
            int(sig.get(k) or 0) != 0
            for k in ("1277", "1278", "1279", "1280", "1281")
        ),
        plug_connected=_is_plugged_in(sig),
        charge_deferred=_is_deferred_charge(sig),
        remaining_charge_min=int(sig.get("1200") or 0),
        charge_voltage_v=float(sig.get("1177") or 0),
        charge_current_a=float(sig.get("1178") or 0),
        ac_port_mode=int(sig.get("47") or 0),    # 47 acInputSlowCharge: 0 idle / 1 AC charge / 2 V2L
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
    )
