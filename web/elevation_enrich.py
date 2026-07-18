"""Per-trip elevation gain/loss, from the GPS track looked up against Open-Elevation.

The Leapmotor cloud has no altitude signal — only lat/lon (see poller/db.py trip_positions).
A render-triggered background sweep (mirrors ec_enrich/charger_locator): finds recent trips
without a stored gain/loss, batches their (downsampled) GPS track into one Open-Elevation POST,
and stores the result. Unlike ec_enrich's cloud getEC, terrain is static — no convergence/
stability logic needed, a single successful lookup is final; a failed one just retries next
sweep, up to a small ceiling (see db_reader.get_trips_needing_elevation).
"""
import json
import logging
import threading
import time
import urllib.request

import db_reader

log = logging.getLogger("elevation_enrich")

_ELEV_URL = "https://api.open-elevation.com/api/v1/lookup"
_TIMEOUT_S = 20
_SWEEP_TTL_S = 5 * 60          # at most one sweep per 5 min (DB-coordinated)
_BATCH = 4                     # trips enriched per sweep
_NOISE_THRESHOLD_M = 3.0       # ignore point-to-point deltas below this (SRTM/GPS noise, ~10m
                                # vertical accuracy) so flat roads don't accumulate fake dislivello
_lock = threading.Lock()
_running = False
_bg_started = False


def fetch_elevations(points: list[dict]):
    """POST the lat/lon list to Open-Elevation, return elevations (metres) in the SAME order as
    `points`, or None on any failure (timeout/HTTP/malformed JSON) — never raises."""
    if not points:
        return None
    body = json.dumps({"locations": [
        {"latitude": p["latitude"], "longitude": p["longitude"]} for p in points
    ]}).encode()
    req = urllib.request.Request(_ELEV_URL, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            data = json.load(resp)
        results = data.get("results")
        if not results or len(results) != len(points):
            return None
        return [r["elevation"] for r in results]
    except Exception as e:  # noqa: BLE001 — timeout/HTTP/JSON, all treated as a retry-later miss
        log.warning("elevation_enrich: Open-Elevation error: %s", e)
        return None


def _store_point_elevations(points: list[dict], elevations: list) -> None:
    """Persist the per-point altitudes just fetched (keyed by trip_positions.id) — the sparse
    track the trip-profile chart interpolates between. Best-effort: never blocks the gain/loss
    that's the primary result of a sweep/recalc."""
    try:
        db_reader.store_point_elevations({p["id"]: e for p, e in zip(points, elevations)})
    except Exception as e:  # noqa: BLE001
        log.debug("elevation_enrich: per-point store skipped: %s", e)


def compute_gain_loss(elevations: list, noise_threshold_m: float = _NOISE_THRESHOLD_M):
    """Sum of positive / negative deltas between consecutive elevations, ignoring steps smaller
    than `noise_threshold_m`. Returns (gain_m, loss_m) rounded to whole metres, or (0, 0) for
    fewer than 2 points."""
    gain = loss = 0.0
    for prev, cur in zip(elevations, elevations[1:]):
        delta = cur - prev
        if delta >= noise_threshold_m:
            gain += delta
        elif delta <= -noise_threshold_m:
            loss += -delta
    return round(gain), round(loss)


def _enabled() -> bool:
    return db_reader.get_setting("elevation_enabled", "1") == "1"


def maybe_sweep() -> None:
    """Cheap: bail unless the feature is on and the TTL elapsed; then run in a daemon thread.
    The TTL lives in a DB setting (`elev_sweep_at`) so it coordinates across uvicorn workers."""
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
    """Start a daemon thread that triggers the sweep every `interval_s`, so enrichment runs even
    when no page is being rendered. Idempotent."""
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
    """Manual, on-demand elevation lookup for one trip GROUP (every merged segment) — the
    'Calculate elevation' button. Unlike the background sweep it ignores elev_done/elev_tried, so
    it also recovers trips recorded before this feature existed (their columns are simply NULL)
    and old trips the sweep already gave up on. Returns {ok, reason?}; reason is set only when
    every segment came back empty (too few GPS points or Open-Elevation had nothing usable)."""
    db = db_reader._get()
    any_ok = False
    for seg_id in db_reader._segment_ids(db, trip_id):
        points = db_reader.get_trip_points_for_elevation(seg_id)
        if len(points) < 2:
            continue
        elevations = fetch_elevations(points)
        if not elevations:
            continue
        gain, loss = compute_gain_loss(elevations)
        db_reader.store_trip_elevation(seg_id, gain, loss)
        _store_point_elevations(points, elevations)
        any_ok = True
    return {"ok": True} if any_ok else {"ok": False, "reason": "no_data"}


def _sweep_now() -> None:
    global _running
    try:
        if not _enabled():
            return
        for t in db_reader.get_trips_needing_elevation(limit=_BATCH):
            trip_id = t["id"]
            points = db_reader.get_trip_points_for_elevation(trip_id)
            if len(points) < 2:
                db_reader.store_trip_elevation(trip_id, None, None)
                continue
            elevations = fetch_elevations(points)
            if not elevations:
                db_reader.store_trip_elevation(trip_id, None, None)
                log.info("elevation trip %s: Open-Elevation miss — will retry", trip_id)
                continue
            gain, loss = compute_gain_loss(elevations)
            db_reader.store_trip_elevation(trip_id, gain, loss)
            _store_point_elevations(points, elevations)
            log.info("elevation trip %s: +%sm / -%sm over %d points", trip_id, gain, loss, len(points))
    except Exception as e:  # noqa: BLE001
        log.warning("elevation_enrich sweep error: %s", e)
    finally:
        _running = False
