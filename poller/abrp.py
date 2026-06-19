"""ABRP (A Better Route Planner) live telemetry — optional, opt‑in.

Sends the car's live data to ABRP so it can do live route planning. Off unless the
user enables it and provides their personal ABRP token. The integrator api_key is
the public shared telemetry key used by many community projects, so no per‑app
registration is needed. Stdlib only (urllib) — no extra dependency. Best‑effort:
never raises to the poller loop.
"""
import json
import logging
import time
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

_API_URL      = "https://api.iternio.com/1/tlm/send"
_NEXT_WP_URL  = "https://api.iternio.com/1/tlm/get_next_wp"
_API_KEY = "6f6a554f-d8c8-4c72-8914-d5895f58b1eb"  # public shared telemetry key
_TIMEOUT = 10


def send(token: str, data) -> None:
    """Send one telemetry frame to ABRP. No‑op without a token."""
    if not token:
        return
    tlm = _build_tlm(data)
    qs = urllib.parse.urlencode({
        "api_key": _API_KEY,
        "token": token,
        "tlm": json.dumps(tlm, separators=(",", ":")),
    })
    try:
        with urllib.request.urlopen(f"{_API_URL}?{qs}", timeout=_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
        if body.get("status") != "ok":
            log.warning("ABRP: %s", body)
    except Exception as exc:  # noqa: BLE001 — telemetry must never break polling
        log.warning("ABRP: send failed: %s", exc)


def get_next_wp(token: str) -> dict | None:
    """Poll ABRP for the next waypoint on the active plan.

    Returns a dict with at minimum the keys the caller needs:
      batt_heat (bool)  — ABRP's own signal to start battery preconditioning
      next_charge_wp    — dict with charger_type ("dc_fast"/"ac"), dist_m, name
    Returns None when there is no active plan, the token is missing, or the
    request fails. Best-effort: never raises to the caller."""
    if not token:
        return None
    qs = urllib.parse.urlencode({"api_key": _API_KEY, "token": token})
    try:
        with urllib.request.urlopen(f"{_NEXT_WP_URL}?{qs}", timeout=_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
        if body.get("status") == "ok":
            return body.get("result") or None
        log.debug("ABRP get_next_wp: %s", body)
    except Exception as exc:  # noqa: BLE001
        log.debug("ABRP: get_next_wp failed: %s", exc)
    return None


def _build_tlm(data) -> dict:
    """Map VehicleData → ABRP telemetry payload (null fields filtered out)."""
    tlm = {
        "utc": int(time.time()),
        "soc": data.soc,
        "speed": data.speed_kmh,
        "lat": data.latitude,
        "lon": data.longitude,
        "is_charging": data.charging_status > 0,
        "is_parked": data.vehicle_state == "parked",
        "odometer": data.odometer_km,
        "ext_temp": data.outside_temp,
        "cabin_temp": data.inside_temp,
    }
    if data.range_km and data.range_km > 0:
        tlm["est_battery_range"] = data.range_km
    if data.charge_power_kw and data.charge_power_kw > 0:
        tlm["power"] = data.charge_power_kw
    if data.charge_voltage_v and data.charge_voltage_v > 0:
        tlm["voltage"] = data.charge_voltage_v
    if data.charge_current_a:
        tlm["current"] = data.charge_current_a
    if data.battery_min_temp:
        tlm["batt_temp"] = data.battery_min_temp
    if data.climate_target_temp and data.climate_target_temp > 0:
        tlm["hvac_setpoint"] = data.climate_target_temp
    return {k: v for k, v in tlm.items() if v is not None}
