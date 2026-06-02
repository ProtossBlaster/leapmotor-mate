"""Optional ABRP (A Better Routeplanner) live-data push.

Forwards each successful poll to the Iternio Generic Telemetry endpoint so ABRP can
use the live car state for range / charge planning. Inert unless the user enables it
and supplies their personal ABRP Generic Token (one token per vehicle).

Two distinct credentials:
- ``token``   — per-user, identifies the user's car in ABRP. Entered in the wizard /
                Settings, stored in the DB.
- ``API_KEY`` — identifies LeapMotor Mate as an integrator to Iternio, shared by all
                users. Register one for Mate at ABRP/Iternio and set it below.

Uses only the standard library (urllib) with a short timeout: the poll loop is
synchronous, so the push must never block it for long, and must never raise.
"""
import json
import logging
import os
import time
import urllib.parse
import urllib.request

log = logging.getLogger("leapmotor_mate.abrp")

_ENDPOINT = "https://api.iternio.com/1/tlm/send"

# LeapMotor Mate's Iternio integrator api_key. TODO: replace with our own registered
# key before releasing the ABRP feature. Overridable via the ABRP_API_KEY env var so
# it can be tested without committing a key to the repo. While empty, push() is a no-op.
_BUNDLED_API_KEY = ""
API_KEY = os.environ.get("ABRP_API_KEY", _BUNDLED_API_KEY)


def is_configured(token: str) -> bool:
    """True only when both the integrator key and the user's token are present."""
    return bool(API_KEY) and bool(token and token.strip())


def build_tlm(data) -> dict:
    """Map a VehicleData snapshot to ABRP's Generic Telemetry fields (null values dropped)."""
    is_charging = data.charging_status > 0 or data.plug_connected
    tlm = {
        "utc":               int(time.time()),
        "soc":               round(data.soc, 1),
        "speed":             round(data.speed_kmh, 1),
        "is_charging":       1 if is_charging else 0,
        "is_parked":         1 if data.gear == "P" else 0,
        "est_battery_range": round(data.range_km, 1),
        "odometer":          round(data.odometer_km, 1),
    }
    # Skip the (0, 0) "null island" when there is no GPS fix.
    if data.latitude and data.longitude:
        tlm["lat"] = round(data.latitude, 6)
        tlm["lon"] = round(data.longitude, 6)
    # Power/voltage/current are only meaningful (and correctly signed) while charging.
    if is_charging:
        if data.charge_voltage_v:
            tlm["voltage"] = round(data.charge_voltage_v, 1)
        if data.charge_current_a:
            tlm["current"] = round(data.charge_current_a, 2)
        if data.charge_power_kw:
            tlm["power"] = round(data.charge_power_kw, 2)
    return {k: v for k, v in tlm.items() if v is not None}


def push(data, token: str) -> None:
    """Fire-and-forget telemetry push. Swallows all errors — ABRP must not break polling."""
    if not is_configured(token):
        return
    try:
        qs = urllib.parse.urlencode({
            "api_key": API_KEY,
            "token":   token.strip(),
            "tlm":     json.dumps(build_tlm(data), separators=(",", ":")),
        })
        req = urllib.request.Request(f"{_ENDPOINT}?{qs}", method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status != 200:
                log.warning("ABRP push HTTP %s", resp.status)
    except Exception as exc:  # noqa: BLE001
        log.debug("ABRP push failed (ignored): %s", exc)
