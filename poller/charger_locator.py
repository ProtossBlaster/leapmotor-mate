"""Look up the nearest public EV charging station for a given GPS position.

Supported providers
-------------------
osm     OpenStreetMap / Overpass API — free, no key required (default)
google  Google Places Nearby Search — requires a Places API key

Returns the station name (str) or None when nothing is found within the
search radius or the request fails.
"""

import logging
import urllib.request
import urllib.parse
import urllib.error
import json

log = logging.getLogger(__name__)

_RADIUS_M = 150   # search radius in metres
_TIMEOUT_S = 8    # network timeout


# ── OSM / Overpass ────────────────────────────────────────────────────────────

_OVERPASS_URL = "https://overpass-api.de/api/interpreter"

_NAME_TAGS = ("name", "operator", "brand", "network")


def _osm_find(lat: float, lon: float) -> "str | None":
    query = (
        f"[out:json][timeout:10];"
        f'node["amenity"="charging_station"](around:{_RADIUS_M},{lat},{lon});'
        f"out 5;"
    )
    data = urllib.parse.urlencode({"data": query}).encode()
    req = urllib.request.Request(_OVERPASS_URL, data=data)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            body = json.load(resp)
    except Exception as exc:
        log.warning("charger_locator OSM error: %s", exc)
        return None

    elements = body.get("elements", [])
    if not elements:
        return None

    # Pick the first node; prefer the most human-readable tag.
    tags = elements[0].get("tags", {})
    for key in _NAME_TAGS:
        val = tags.get(key, "").strip()
        if val:
            return val

    return None


# ── Google Places ─────────────────────────────────────────────────────────────

_GOOGLE_URL = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"


def _google_find(lat: float, lon: float, api_key: str) -> "str | None":
    params = urllib.parse.urlencode({
        "location": f"{lat},{lon}",
        "radius": _RADIUS_M,
        "type": "electric_vehicle_charging_station",
        "key": api_key,
    })
    url = f"{_GOOGLE_URL}?{params}"
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT_S) as resp:
            body = json.load(resp)
    except Exception as exc:
        log.warning("charger_locator Google error: %s", exc)
        return None

    results = body.get("results", [])
    if not results:
        return None
    name = results[0].get("name", "").strip()
    return name or None


# ── Public API ────────────────────────────────────────────────────────────────

def find_charging_station(
    lat: float,
    lon: float,
    provider: str = "osm",
    api_key: "str | None" = None,
) -> "str | None":
    """Return the name of the nearest charging station or None."""
    if not lat or not lon:
        return None

    try:
        if provider == "google":
            if not api_key:
                log.warning("charger_locator: Google provider selected but no API key set")
                return None
            return _google_find(lat, lon, api_key)
        else:
            return _osm_find(lat, lon)
    except Exception as exc:
        log.warning("charger_locator unexpected error: %s", exc)
        return None
