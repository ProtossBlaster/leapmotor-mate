import json
import logging
import urllib.request
from dataclasses import asdict

log = logging.getLogger("leapmotor_mate.homeassistant")

def push_to_webhook(url: str, data) -> None:
    """Send vehicle data to a Home Assistant Webhook."""
    if not url or not url.startswith("http"):
        return
        
    try:
        # Convert dataclass to dict
        payload = asdict(data)
        
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status not in (200, 201):
                log.warning("HA Webhook push HTTP %s", resp.status)
    except Exception as exc:
        log.debug("HA Webhook push failed: %s", exc)
