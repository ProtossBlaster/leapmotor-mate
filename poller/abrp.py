"""ABRP (A Better Route Planner) telemetry service."""
import json
import logging
import time
import requests

log = logging.getLogger(__name__)

ABRP_API_URL = "https://api.iternio.com/1/tlm/send"
ABRP_API_KEY = "6f6a554f-d8c8-4c72-8914-d5895f58b1eb"

class AbrpService:
    def __init__(self, user_token: str):
        self.user_token = user_token

    def send(self, data):
        if not self.user_token:
            return

        tlm = self._build_tlm(data)
        try:
            resp = requests.get(
                ABRP_API_URL,
                params={
                    "api_key": ABRP_API_KEY,
                    "token": self.user_token,
                    "tlm": json.dumps(tlm, separators=(",", ":")),
                },
                timeout=10
            )
            if resp.status_code != 200:
                log.warning("ABRP HTTP %d: %s", resp.status_code, resp.text[:200])
            else:
                body = resp.json()
                if body.get("status") != "ok":
                    log.warning("ABRP error: %s", body)
                else:
                    log.info("ABRP telemetry sent successfully")
        except Exception as exc:
            log.error("ABRP: failed to send: %s", exc)

    @staticmethod
    def _build_tlm(data) -> dict:
        tlm = {
            "utc": int(time.time()),
            "soc": data.soc,
            "odometer": data.odometer_km,
            "speed": data.speed_kmh,
            "lat": data.latitude,
            "lon": data.longitude,
            "ext_temp": data.outside_temp,
            "cabin_temp": data.inside_temp,
            "is_charging": data.charging_status == 1,
            "is_parked": data.vehicle_state == "parked",
        }

        if data.charge_power_kw > 0:
            tlm["power"] = data.charge_power_kw

        if data.charge_voltage_v > 0:
            tlm["voltage"] = data.charge_voltage_v

        if data.charge_current_a != 0:
            tlm["current"] = data.charge_current_a

        if data.range_km > 0:
            tlm["est_battery_range"] = data.range_km

        if data.climate_target_temp > 0:
            tlm["hvac_setpoint"] = data.climate_target_temp

        if data.battery_min_temp != 0:
            tlm["batt_temp"] = data.battery_min_temp

        return tlm
