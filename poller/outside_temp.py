"""Live outside-air temperature for the car's current spot, from Open-Meteo.

The Leapmotor cloud carries no ambient-temperature signal (poller/client.py: outside_temp is always
None — the one candidate, 2101, is seat ventilation), which is why it's missing from the official app
too. Open-Meteo gives the weather for a lat/lon, keyless and free (~10 000 requests/day PER IP, i.e.
per install). Opt-in, because the position leaves the device.

Cached hard: weather moves by the hour and a parked car doesn't move at all, so a fresh lookup only
happens when the last one is STALE (older than _MAX_AGE_S) or the car has MOVED more than
_MAX_MOVE_KM. A day of driving is then a few dozen calls, nowhere near the cap; a parked car makes
none. The live samples are stored on each position, so they also become the trip's temperature
(averaged along the real route) — elevation_enrich.fetch_trip_temperature stays as the fallback for
trips with no live sample.
"""
import json
import logging
import urllib.parse
import urllib.request
from typing import Callable, Optional

from db import haversine_km

log = logging.getLogger("leapmotor_mate")

_URL = "https://api.open-meteo.com/v1/forecast"
_UA = "leapmotor-mate"
_TIMEOUT_S = 8
_MAX_AGE_S = 20 * 60          # refetch after 20 minutes…
_MAX_MOVE_KM = 10.0          # …or after moving 10 km


def fetch_current_temp(lat: float, lon: float) -> Optional[float]:
    """Current outside air temperature (°C) at lat/lon from Open-Meteo's `current` field. None on any
    failure — a missing reading must never break a poll."""
    qs = urllib.parse.urlencode({"latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}",
                                 "current": "temperature_2m"})
    try:
        req = urllib.request.Request(f"{_URL}?{qs}", headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            data = json.load(resp)
        t = (data.get("current") or {}).get("temperature_2m")
        return float(t) if t is not None else None
    except Exception as e:  # noqa: BLE001
        log.warning("outside_temp: Open-Meteo error: %s", e)
        return None


def _should_refetch(last_ts, last_lat, last_lon, lat, lon, now_ts) -> bool:
    """Fetch again only when the cached reading is stale or the car has moved far — so a standing car
    makes no calls and a drive makes a handful."""
    if last_ts is None or last_lat is None or last_lon is None:
        return True
    if now_ts - last_ts >= _MAX_AGE_S:
        return True
    return haversine_km(last_lat, last_lon, lat, lon) >= _MAX_MOVE_KM


class OutsideTempSampler:
    """Per-poller cache of the last Open-Meteo lookup. `sample` returns the current outside temp,
    touching the network only when `_should_refetch` says so; otherwise the cached value. Injectable
    `fetch` for tests."""

    def __init__(self, fetch: Callable[[float, float], Optional[float]] = fetch_current_temp):
        self._fetch = fetch
        self._temp: Optional[float] = None
        self._ts: Optional[float] = None
        self._lat: Optional[float] = None
        self._lon: Optional[float] = None

    def sample(self, lat, lon, now_ts) -> Optional[float]:
        # `None` never arrives: `client._resolve_coord` returns 0.0 when no usable value exists, so
        # this guard tested for something the parser cannot produce and a frame without a fix asked
        # for the weather at 0,0 — Null Island, in the Gulf of Guinea — stored it as the car's
        # outside temperature, and moved the anchor there, which then suppressed the next real fetch
        # until the car had "moved" 10 km back. Exactly 0,0 is no fix: the same convention the rest
        # of the poller already uses (`if data.latitude and data.longitude` for geohashes,
        # `latitude != 0 AND longitude != 0` in the trip query).
        if not lat or not lon:
            return self._temp   # no fix → keep the last reading, don't guess a new place
        if _should_refetch(self._ts, self._lat, self._lon, lat, lon, now_ts):
            t = self._fetch(lat, lon)
            if t is not None:   # a failed lookup keeps the old value AND the old anchor → retry next poll
                self._temp, self._ts, self._lat, self._lon = t, now_ts, lat, lon
        return self._temp
