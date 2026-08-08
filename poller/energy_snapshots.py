"""Phase-1 silent collector for the car's official energy counters (design agreed 2026-07-02).

Once a day, read the lifetime counters from the cloud (`totalEnergy` = ALL consumption incl.
parked/standby, integer kWh; `totalmileage` at 0.1-mile resolution) plus the official getEC
driving split over the window since the previous snapshot, and append one raw row to
`energy_counter_snapshots`. No UI consumes this yet — the ledger just accrues.

Why counter sampling: Δ between any two rows carries at most ±1 kWh of quantization error at the
window edges, REGARDLESS of the span (we subtract two meter readings, we never sum rounded
deltas) — noisy on a single day, solid on weeks/months. The getEC window uses the SAME bounds as
the Δ (previous row's taken_at → this row's), so `Δ − getEC = parked/standby share` holds by
construction even if a skipped day widens the window.

Ledger rules: store readings AS SERVED, never correct in place — counter resets/decreases and
cloud gaps are the reader's job (total_increasing-style). A getEC miss is recoverable later
(getEC is retro-queryable); a lost counter reading is not, so the row is written even when getEC
misses.
"""
import logging
import threading
import time
from datetime import datetime, timezone

log = logging.getLogger("leapmotor.energy_snapshots")

SNAPSHOT_INTERVAL_S = 24 * 3600
RETRY_AFTER_S = 1800          # a failed attempt retries in 30 min, not on every 30s poll
# When the last attempt failed, PER CAR. One value for the module meant one car's failing snapshot
# put every other car on the same 30-minute cooldown — and a car that is simply asleep fails here
# routinely, so the healthy one would have gone a day at a time without a ledger entry for reasons
# entirely to do with a different vehicle.
_failed_at: dict = {}
_NULL_LOCK = threading.Lock()


def maybe_sample(db, client, vin: str, api_lock=None, now: float = None, vehicle=None) -> bool:
    """Opportunistic per-poll hook: take today's snapshot if the last one is ≥24h old (or none
    exists). Best-effort — never raises, a failure can't disturb the poll. Returns True when a
    row was written."""
    lock = api_lock if api_lock is not None else _NULL_LOCK
    now = time.time() if now is None else now
    try:
        last = db.last_energy_snapshot(vin)
        prev_ts = None
        if last is not None:
            try:
                prev_ts = datetime.fromisoformat(last["taken_at"]).timestamp()
            except (TypeError, ValueError):
                prev_ts = None
        if prev_ts is not None and now - prev_ts < SNAPSHOT_INTERVAL_S:
            return False
        if now - _failed_at.get(vin, 0.0) < RETRY_AFTER_S:
            return False

        with lock:
            counters = client.get_energy_counters(vehicle)
        if not counters:
            _failed_at[vin] = now
            log.debug("Energy snapshot: counters unavailable — retry in %ds", RETRY_AFTER_S)
            return False

        # getEC over exactly [previous snapshot, now] — same bounds as the counter Δ. First-ever
        # row has no window (a Δ needs two readings), so there's nothing to query yet.
        ec_status, ec = "first", None
        if prev_ts is not None:
            with lock:
                ec_status, ec = client.get_ec_range(int(prev_ts), int(now), vehicle)

        taken_at = datetime.fromtimestamp(now, tz=timezone.utc).isoformat()
        db.insert_energy_snapshot(
            vin=vin, taken_at=taken_at,
            total_energy_kwh=counters.get("total_energy_kwh"),
            total_mileage_km=counters.get("total_mileage_km"),
            ec_driving_kwh=ec["driving"] if ec else (0.0 if ec_status == "empty" else None),
            ec_ac_kwh=ec["ac"] if ec else (0.0 if ec_status == "empty" else None),
            ec_other_kwh=ec["other"] if ec else (0.0 if ec_status == "empty" else None),
            ec_status=ec_status,
        )
        log.info("Energy snapshot: totalEnergy=%s kWh, mileage=%s km, ec=%s (%s)",
                 counters.get("total_energy_kwh"), counters.get("total_mileage_km"),
                 ec, ec_status)
        return True
    except Exception as e:  # noqa: BLE001
        _failed_at[vin] = now
        log.warning("Energy snapshot failed: %s", e)
        return False
