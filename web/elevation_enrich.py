"""Per-trip elevation gain/loss + outside temperature, from the GPS track looked up against Open-Meteo.

The Leapmotor cloud exposes neither an altitude signal nor an ambient-temperature one — only lat/lon
and the cabin temp (see poller/db.py trip_positions / poller/client.py). A render-triggered background
sweep (mirrors ec_enrich / charger_locator) finds recent finalized trips without stored data, and for
each makes at most two Open-Meteo calls — both keyless and free (10 000 requests/day, no API key):

  1. /v1/elevation — the trip's downsampled GPS track in ONE batch → per-point altitude (for the
     trip-profile chart) + the total gain/loss (dislivello).
  2. /v1/forecast?hourly=temperature_2m&past_days=N — the outside temperature at the trip's start
     point, averaged over the hours it spanned. past_days reads real RECENT measurements, so a trip
     that just ended already has its weather — unlike the ~3-day-lagged archive API. Fills the #124
     "trip temperature" gap that had no answer while the cloud was the only source.

Unlike ec_enrich's cloud getEC, terrain and a past hour's weather are static — no convergence/
stability logic needed, a single successful lookup is final; a failed one just retries next sweep, up
to a small ceiling (see db_reader.get_trips_needing_elevation). Opt-in via the `elevation_enabled`
setting (the GPS track leaves the device). Temperature is best-effort alongside elevation in the same
pass: whatever comes back is stored, and elev_done is set on the elevation result (the primary datum).
"""
import json
import logging
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import db_reader

log = logging.getLogger("elevation_enrich")

_ELEV_URL = "https://api.open-meteo.com/v1/elevation"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_UA = "leapmotor-mate (+https://github.com/ProtossBlaster/leapmotor-mate)"
_TIMEOUT_S = 20
_SWEEP_TTL_S = 5 * 60          # at most one sweep per 5 min (DB-coordinated)
_BATCH = 4                     # trips enriched per sweep
_NOISE_THRESHOLD_M = 3.0       # ignore point-to-point deltas below this (DEM/GPS noise, ~10m vertical
                               # accuracy) so flat roads don't accumulate fake dislivello
_MAX_PAST_DAYS = 92            # Open-Meteo forecast's past-data window; older trips get no temperature
_lock = threading.Lock()
_running = False
_bg_started = False


def fetch_elevations(points: list[dict]):
    """GET the lat/lon list to Open-Meteo /v1/elevation as ONE batch, return elevations (metres) in
    the SAME order as `points`, or None on any failure (timeout/HTTP/malformed JSON). Never raises.
    Open-Meteo accepts up to 100 coordinates per call; the track is downsampled to 60 upstream."""
    if not points:
        return None
    qs = urllib.parse.urlencode({
        "latitude": ",".join(f"{p['latitude']:.5f}" for p in points),
        "longitude": ",".join(f"{p['longitude']:.5f}" for p in points),
    })
    try:
        req = urllib.request.Request(f"{_ELEV_URL}?{qs}", headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            data = json.load(resp)
        elevs = data.get("elevation")
        if not elevs or len(elevs) != len(points):
            return None
        return elevs
    except Exception as e:  # noqa: BLE001 — timeout/HTTP/JSON, all treated as a retry-later miss
        log.warning("elevation_enrich: Open-Meteo elevation error: %s", e)
        return None


def _parse_iso(s):
    """Parse a stored UTC ISO timestamp or an Open-Meteo hourly time ("YYYY-MM-DDTHH:MM"), always
    returning a tz-aware datetime (assume UTC when naive) or None."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _temp_at_hour(times: list, temps: list, dt):
    """The hourly temperature at `dt` floored to the hour. Pure/testable; None when that hour isn't in
    the data (trip older than the past_days window, or empty response)."""
    if dt is None:
        return None
    target = dt.replace(minute=0, second=0, microsecond=0)
    for ts, tv in zip(times, temps):
        if tv is None:
            continue
        t = _parse_iso(ts)
        if t is not None and t == target:
            return round(tv, 1)
    return None


def fetch_trip_temperature(lat_start, lon_start, lat_end, lon_end, started_at, ended_at):
    """Outside temperature (°C) at the START point (its start hour) and the END point (its end hour),
    from Open-Meteo's hourly forecast with past_days (real recent measurements — NOT the ~3-day-lagged
    archive, so a just-ended trip already has its weather). Both points go in ONE multi-coordinate call
    (the response is a list, one entry per point). Returns (start_temp, end_temp) — either can be None
    on failure or when the hour isn't covered (> _MAX_PAST_DAYS ago). Never raises. The two temps
    differ by both the hour AND the place, so a valley→pass climb shows the real drop, not an average."""
    if lat_start is None or lon_start is None:
        return (None, None)
    start_dt = _parse_iso(started_at)
    end_dt = _parse_iso(ended_at) or start_dt
    if start_dt is None:
        return (None, None)
    days_ago = (datetime.now(timezone.utc).date() - start_dt.date()).days
    if days_ago > _MAX_PAST_DAYS:
        return (None, None)
    past_days = max(1, min(days_ago + 1, _MAX_PAST_DAYS))
    if lat_end is None or lon_end is None:
        lat_end, lon_end = lat_start, lon_start
    qs = urllib.parse.urlencode({
        "latitude": f"{lat_start:.4f},{lat_end:.4f}", "longitude": f"{lon_start:.4f},{lon_end:.4f}",
        "hourly": "temperature_2m", "past_days": past_days,
        "forecast_days": 1, "timezone": "UTC",
    })
    try:
        req = urllib.request.Request(f"{_FORECAST_URL}?{qs}", headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            data = json.load(resp)
    except Exception as e:  # noqa: BLE001
        log.warning("elevation_enrich: Open-Meteo temperature error: %s", e)
        return (None, None)
    locs = data if isinstance(data, list) else [data]
    if not locs:
        return (None, None)
    start_loc = locs[0]
    end_loc = locs[1] if len(locs) > 1 else locs[0]

    def _pick(loc, dt):
        h = loc.get("hourly") or {}
        return _temp_at_hour(h.get("time") or [], h.get("temperature_2m") or [], dt)

    return (_pick(start_loc, start_dt), _pick(end_loc, end_dt))


def _store_point_elevations(points: list[dict], elevations: list) -> None:
    """Persist the per-point altitudes just fetched (keyed by trip_positions.id) — the sparse track
    the trip-profile chart interpolates between. Best-effort: never blocks the gain/loss + temp that
    are the primary result of a sweep/recalc."""
    try:
        db_reader.store_point_elevations({p["id"]: e for p, e in zip(points, elevations)})
    except Exception as e:  # noqa: BLE001
        log.debug("elevation_enrich: per-point store skipped: %s", e)


def compute_gain_loss(elevations: list, noise_threshold_m: float = _NOISE_THRESHOLD_M):
    """Sum of positive / negative deltas between consecutive elevations, ignoring steps smaller than
    `noise_threshold_m`. Returns (gain_m, loss_m) rounded to whole metres, or (0, 0) for < 2 points."""
    gain = loss = 0.0
    for prev, cur in zip(elevations, elevations[1:]):
        delta = cur - prev
        if delta >= noise_threshold_m:
            gain += delta
        elif delta <= -noise_threshold_m:
            loss += -delta
    return round(gain), round(loss)


def _enrich_segment(seg_id: int, count_miss: bool) -> bool:
    """Enrich one trip SEGMENT: elevation batch + outside temperature, storing whatever came back.
    Returns True when anything was stored (elevation and/or temperature). `count_miss` (background
    sweep) records a total miss via store_trip_elevation(None, None) so elev_tried advances toward
    the retry ceiling; recalc_trip passes False so a manual retry never burns that ceiling."""
    points = db_reader.get_trip_points_for_elevation(seg_id)
    if len(points) < 2:
        if count_miss:
            db_reader.store_trip_elevation(seg_id, None, None)
        return False
    elevations = fetch_elevations(points)
    # The trip's OWN temperature from the live samples the poller recorded along the route
    # (positions.outside_temp, opt-in) — real readings on the way, not two hourly endpoints. The
    # Open-Meteo lookup below is the fallback for trips with no live sample (the feature was off).
    temp_start, temp_end = db_reader.trip_outside_temp_samples(seg_id)
    if temp_start is None and temp_end is None:
        temp_start, temp_end = fetch_trip_temperature(
            points[0]["latitude"], points[0]["longitude"],
            points[-1]["latitude"], points[-1]["longitude"],
            points[0].get("recorded_at"), points[-1].get("recorded_at"))
    gain, loss = compute_gain_loss(elevations) if elevations else (None, None)
    if gain is None and temp_start is None and temp_end is None:
        if count_miss:
            db_reader.store_trip_elevation(seg_id, None, None)
            log.info("elevation trip %s: Open-Meteo miss — will retry", seg_id)
        return False
    db_reader.store_trip_elevation(seg_id, gain, loss,
                                   outside_temp_start_c=temp_start, outside_temp_end_c=temp_end)
    if elevations:
        _store_point_elevations(points, elevations)
    log.info("elevation trip %s: +%sm / -%sm, %s→%s°C over %d points",
             seg_id, gain, loss, temp_start, temp_end, len(points))
    return True


def _enabled() -> bool:
    return db_reader.get_setting("elevation_enabled", "1") == "1"


def maybe_sweep() -> None:
    """Cheap: bail unless the feature is on and the TTL elapsed; then run in a daemon thread. The TTL
    lives in a DB setting (`elev_sweep_at`) so it coordinates across uvicorn workers."""
    global _running
    try:
        if not _enabled():
            return
        last = float(db_reader.get_setting("elev_sweep_at", "0") or 0)
        now = time.time()
        with _lock:
            if _running or now - last < _SWEEP_TTL_S:
                return
            _running = True
            db_reader.set_setting("elev_sweep_at", str(now))
    except Exception as e:  # noqa: BLE001
        log.debug("elevation_enrich maybe_sweep skipped: %s", e)
        return
    threading.Thread(target=_sweep_now, daemon=True).start()


def start_background(interval_s: int = 300) -> None:
    """Start a daemon thread that triggers the sweep every `interval_s`, so enrichment runs even when
    no page is being rendered. Idempotent."""
    global _bg_started
    with _lock:
        if _bg_started:
            return
        _bg_started = True

    def _loop():
        while True:
            try:
                time.sleep(interval_s)
                maybe_sweep()
            except Exception:  # noqa: BLE001
                pass

    threading.Thread(target=_loop, daemon=True).start()
    log.info("elevation_enrich background sweeper started (every %ss)", interval_s)


def recalc_trip(trip_id: int) -> dict:
    """Manual, on-demand lookup for one trip GROUP (every merged segment) — the 'Calculate elevation'
    button. Unlike the background sweep it ignores elev_done/elev_tried, so it also recovers trips
    recorded before this feature existed (columns simply NULL) and ones the sweep already gave up on.
    Returns {ok, reason?}; reason is set only when every segment came back empty (too few GPS points,
    or Open-Meteo had nothing usable for either elevation or temperature)."""
    db = db_reader._get()
    any_ok = False
    for seg_id in db_reader._segment_ids(db, trip_id):
        if _enrich_segment(seg_id, count_miss=False):
            any_ok = True
    return {"ok": True} if any_ok else {"ok": False, "reason": "no_data"}


def _sweep_now() -> None:
    global _running
    try:
        if not _enabled():
            return
        for t in db_reader.get_trips_needing_elevation(limit=_BATCH):
            _enrich_segment(t["id"], count_miss=True)
    except Exception as e:  # noqa: BLE001
        log.warning("elevation_enrich sweep error: %s", e)
    finally:
        _running = False
