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

_API_URL = "https://api.iternio.com/1/tlm/send"
_API_KEY = "6f6a554f-d8c8-4c72-8914-d5895f58b1eb"  # public shared telemetry key
_TIMEOUT = 10


NOTHING_SENT = ("", 0)   # the (token, frame timestamp) pair of a car that has not sent a point yet


def is_new_point(token: str, data, last_sent: tuple) -> bool:
    """Whether this frame is worth a point for this token. A sleeping car repeats one frame for
    hours and ABRP counts every arrival as fresh contact, so a frame goes once per token: a token
    changed in Settings has seen nothing yet. A frame without a timestamp cannot be told apart."""
    return not data.timestamp_ms or (token, data.timestamp_ms) != last_sent


def send(token: str, data) -> bool:
    """Send one telemetry frame to ABRP; True once ABRP has taken it. No‑op without a token."""
    if not token:
        return False
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
            return False
        return True
    except Exception as exc:  # noqa: BLE001 — telemetry must never break polling
        log.warning("ABRP: send failed: %s", exc)
        return False


def _build_tlm(data) -> dict:
    """Map VehicleData → ABRP telemetry payload (null fields filtered out)."""
    tlm = {
        # the time of the reading (a sleeping car repeats one frame for hours), not of the send
        "utc": data.timestamp_ms // 1000 if data.timestamp_ms else int(time.time()),
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
    if data.charge_voltage_v and data.charge_voltage_v > 0:
        tlm["voltage"] = data.charge_voltage_v
        # signed as the spec wants (output +, charging/regen −), on every point that measured a
        # current, 0 kW included; a current the car did not send is no power, not 0 kW
        if data.charge_current_a is not None:
            tlm["power"] = round(data.charge_current_a * data.charge_voltage_v / 1000.0, 3)
    if data.charge_current_a is not None:
        tlm["current"] = data.charge_current_a
    if data.battery_min_temp:
        tlm["batt_temp"] = data.battery_min_temp
    if data.climate_target_temp and data.climate_target_temp > 0:
        tlm["hvac_setpoint"] = data.climate_target_temp
    return {k: v for k, v in tlm.items() if v is not None}
