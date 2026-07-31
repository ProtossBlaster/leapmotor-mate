"""Phase 2 — per-trip EC (driving) energy enrichment.

A render-triggered background sweep (mirrors charger_locator.maybe_sweep): when the EC-trip-energy
feature is enabled, it finds recent NEW trips (started on/after the feature's cutoff) that still
lack the cloud getEC split, queries it per-trip on its exact window, and stores it — overriding the
trip's energy with the official EC figure (the SoC value is kept as backup, so it's reversible).
Old trips (before the cutoff) stay SoC.

Self-guarding: a page render only pays a settings read + a TTL check; the cloud calls run in a
daemon thread and never raise into the request path. Trips the cloud can't yet aggregate (just
ended) or can't isolate (very short) simply stay SoC after a few attempts (ec_tried).
"""
import logging
import threading
import time

import command_client
import db_reader

log = logging.getLogger("ec_enrich")

_SWEEP_TTL_S = 5 * 60          # at most one sweep per 5 min (DB-coordinated)
_BATCH = 4                     # trips enriched per sweep
_STABLE_MIN_AGE_S = 30 * 60    # don't lock EC as "stable" before the trip is this old (a held
                               # partial cloud value would otherwise freeze an incomplete figure)
_STABLE_TOL_ABS  = 0.15        # kWh — two reads this close (or within _STABLE_TOL_REL) count as
_STABLE_TOL_REL  = 0.05        # converged. The cloud quantizes EC to 0.1, so the old 0.05-abs
                               # tolerance could NEVER match a value wobbling one 0.1 step across a
                               # rounding boundary (1.9↔2.0) → it never locked. 0.15 fixes that.
_STABLE_BACKSTOP_AGE_S = 90 * 60  # hard backstop: a usable value seen at least twice and this old is
                                  # final — lock it even if the two reads keep wobbling or never
                                  # repeat, so enrichment ALWAYS completes on its own (no manual lock).
_MIN_PLAUSIBLE_EFF = 5         # kWh/100km — LOW floor: an EC implying less than this is suspect (no EV
                               # sustains it), but NOT proof alone — a long descent / heavy regen can be
                               # genuinely this low → must coincide with the SoC shortfall.
_MAX_EC_SOC_SHORTFALL = 0.5    # ...and: a real getEC ≈ the physical battery delta (empirically median
                               # getEC/SoC ≈ 0.93). Below HALF the SoC delta = the cloud captured only a
                               # fraction of the trip (riri19 #96: 0.5 vs 5.8 kWh).
_MAX_PLAUSIBLE_EFF = 60        # kWh/100km — HIGH ceiling: above this is suspect, but a short cold/hard
                               # burst can read high → must coincide with the SoC overshoot.
_MAX_EC_SOC_OVERSHOOT = 2.0    # ...and: getEC ABOVE this × the SoC delta = over-attributed (riri19 #98).
                               # Typical on VERY SHORT trips: the window's 2-min pre-pad (A/C, standby,
                               # pre-conditioning) dwarfs the tiny drive and inflates getEC several-fold
                               # past what actually left the battery (1 km: 1.2 kWh getEC vs 0.2 SoC).
_lock = threading.Lock()
_running = False
_bg_started = False


def _soc_energy_kwh(d: dict, dist: float):
    """The trip/group's physical battery-delta energy (the SoC reference for the #96 guard), kWh, or
    None. Primary = ΔSoC × usable battery capacity — the most fundamental value, computed straight from
    start/end SoC and pack size, so it works even when the stored efficiency was withheld (Mate omits it
    on net-≤0 or sub-0.5 km trips). Falls back to the SoC efficiency (efficiency_soc backup, else
    efficiency_kwh_100km = the SoC value before conversion)."""
    ss, es = d.get("start_soc"), d.get("end_soc")
    if ss is not None and es is not None and ss > es:
        return (ss - es) / 100.0 * db_reader.get_battery_capacity_kwh()
    soc_eff = d.get("efficiency_soc") or d.get("efficiency_kwh_100km")
    return (soc_eff / 100 * dist) if (soc_eff and dist and dist > 0) else None


def _ec_implausible(ec: dict, dist: float, soc_energy) -> bool:
    """True when a getEC reading is physically implausible vs the trip's SoC battery delta → keep the
    estimate instead. Each side needs BOTH an efficiency signal AND a SoC-mismatch signal (a genuinely
    low/high but SoC-consistent trip is accepted; with no SoC reference → accept):
      • LOW (#96): eff < _MIN_PLAUSIBLE_EFF AND total < _MAX_EC_SOC_SHORTFALL × SoC — the cloud only
        captured a fraction / genuinely lacks it (a real gap no window fixes).
      • HIGH (#98): eff > _MAX_PLAUSIBLE_EFF AND total > _MAX_EC_SOC_OVERSHOOT × SoC — the value
        over-states THIS drive (e.g. a single trip whose session swallowed pre-drive idle/climate; the
        drive-only SoC is the truer per-trip figure). The MULTI-trip session case is handled earlier by
        the shared-session block, so this fires only on single trips."""
    if not ec or not dist or dist <= 0 or not soc_energy:
        return False
    total = ec.get("total_kwh") or 0
    eff = total / dist * 100
    if eff < _MIN_PLAUSIBLE_EFF and total < soc_energy * _MAX_EC_SOC_SHORTFALL:
        return True                                # too low → incomplete / cloud gap (#96)
    if eff > _MAX_PLAUSIBLE_EFF and total > soc_energy * _MAX_EC_SOC_OVERSHOOT:
        return True                                # too high → over-attributed (#98)
    return False


def _enabled() -> bool:
    # Default ON: the feature is always-on (no UI toggle). It degrades gracefully to the SoC estimate
    # when the cloud lacks a trip's getEC, so being on is never worse than off. A latent DB override
    # (`ec_trip_energy_enabled=0`) stays available for support/debug, but isn't exposed in the UI.
    return db_reader.get_setting("ec_trip_energy_enabled", "1") == "1"


def maybe_sweep() -> None:
    """Cheap: bail unless the feature is on and the TTL elapsed; then run in a daemon thread.
    The TTL is kept in a DB setting (`ec_sweep_at`) so it coordinates across uvicorn workers and
    restarts — otherwise each worker would sweep independently and burn a fresh trip's retry budget
    before the cloud has had time to aggregate its EC."""
    global _running
    try:
        if not _enabled():
            return
        last = float(db_reader.get_setting("ec_sweep_at", "0") or 0)
        now = time.time()
        with _lock:
            if _running or now - last < _SWEEP_TTL_S:
                return
            _running = True
            db_reader.set_setting("ec_sweep_at", str(now))
    except Exception as e:  # noqa: BLE001
        log.debug("ec_enrich maybe_sweep skipped: %s", e)
        return
    threading.Thread(target=_sweep_now, daemon=True).start()


def start_background(interval_s: int = 300) -> None:
    """Start a daemon thread that triggers the sweep every `interval_s`, so enrichment runs even when
    no page is being rendered (the render trigger alone misses trips when the app isn't open). The
    DB-coordinated TTL still bounds how often a sweep actually fires. Idempotent."""
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
    log.info("ec_enrich background sweeper started (every %ss)", interval_s)


def _sweep_now() -> None:
    global _running
    try:
        if not _enabled():
            return
        cutoff = db_reader.get_setting("ec_trip_since", "")
        if not cutoff:
            # Always-on with no cutoff yet (fresh install) → start enriching from NOW, so going-forward
            # trips get the official figure; older trips stay SoC (still convertible by hand).
            from datetime import datetime, timezone
            cutoff = datetime.now(timezone.utc).isoformat()
            db_reader.set_setting("ec_trip_since", cutoff)
            log.info("ec_enrich: first run — cutoff set to %s (enrich from now)", cutoff)
        apply = True
        now = time.time()
        for t in db_reader.get_trips_needing_ec(cutoff, limit=_BATCH):
            b, e = db_reader.trip_epoch_window(t)   # exact span; e (end) used for the age check
            if not b or not e:
                db_reader.store_trip_ec(t["id"], None, t.get("distance_km"), apply)
                continue
            # Shared power-on session: if OTHER trips share this trip's Ready session (car never powered
            # off between them), DON'T auto-apply a per-trip value — it'd grab the whole session. Leave
            # it on SoC; the user merges them by hand (convert_trip then converts the combined group).
            sess = db_reader.ready_session(t)
            if sess and (set(sess["trip_ids"]) - {t["id"]}):
                db_reader.store_trip_ec(t["id"], None, t.get("distance_km"), apply)
                log.info("EC trip %s: shared Ready session %s — left on SoC (merge to convert)",
                         t["id"], sess["trip_ids"])
                continue
            bq, eq = db_reader.trip_ec_window(t)    # Ready-on→T1 (or fallback) → the exact window
            ec = command_client.get_energy_breakdown_range(bq or b, eq or e)  # can miss the bucket
            dist = t.get("distance_km") or 0
            age = now - e
            tried = (t.get("ec_tried") or 0) + 1
            # Plausibility net: a single trip whose getEC is implausible vs its own ΔSoC → discard, stay
            # SoC. LOW = cloud gap; HIGH = session swallowed pre-drive idle (drive-only SoC is truer).
            # The multi-trip shared-session case was already skipped above.
            soc_e = _soc_energy_kwh(t, dist)
            if _ec_implausible(ec, dist, soc_e):
                log.info("EC trip %s: implausible read %.2f kWh (SoC≈%.2f kWh) — discarded (age %.0fm, try %d)",
                         t["id"], ec.get("total_kwh") or 0, soc_e or 0, age / 60, tried)
                ec = None
            if not ec:
                # Full attempt logging (so the cloud-aggregation lag is observable, never a mystery).
                db_reader.store_trip_ec(t["id"], None, t.get("distance_km"), apply)
                log.info("EC trip %s: cloud no usable value (None) — age %.0fm, try %d",
                         t["id"], age / 60, tried)
                continue
            prev = t.get("ec_kwh")
            new = ec.get("total_kwh") or 0
            tol = max(_STABLE_TOL_ABS, new * _STABLE_TOL_REL)
            converged = prev is not None and abs(new - prev) <= tol
            # Lock via EITHER: (fast) two close reads once the cloud has surely finished aggregating,
            # OR (backstop) a 2nd usable read old enough to be final — even if the reads keep wobbling
            # or never exactly repeat. The backstop GUARANTEES the trip locks on its own (the failure
            # we hit: a value bouncing one 0.1 step across a rounding boundary never converged).
            if converged and age >= _STABLE_MIN_AGE_S:
                stable, how = True, "fast"
            elif prev is not None and age >= _STABLE_BACKSTOP_AGE_S:
                stable, how = True, "backstop"
            else:
                stable, how = False, "wait"
            db_reader.store_trip_ec(t["id"], ec, t.get("distance_km"), apply, stable=stable)
            log.info("EC %s trip %s: %.1f kWh (Guida %.1f / AC %.1f / Altro %.1f) "
                     "prev=%s age=%.0fm tol=%.2f try=%d [%s]",
                     "LOCKED" if stable else "upd", t["id"], new,
                     ec["driving_kwh"], ec["ac_kwh"], ec["other_kwh"],
                     prev, age / 60, tol, tried, how)
    except Exception as e:  # noqa: BLE001
        log.warning("ec_enrich sweep error: %s", e)
    finally:
        _running = False


def convert_trip(trip_id: int) -> dict:
    """Manual, on-demand getEC conversion of a SINGLE trip (the 'Convert with official data' button).
    Works on any trip, including old ones before the cutoff — it's an explicit user action, so it does
    NOT depend on the feature flag or the maturity/age gates the background sweep uses. It locks the
    result immediately (stable=1) and overrides the efficiency, keeping the SoC value as backup.

    Data is available far back (verified 39+ days — no short retention); a trip just returns no_data
    when the cloud has no separable record for it (e.g. trips too close together, merged by the cloud
    into one driving session). For a MERGED group it converts the COMBINED drive (one cloud session).
    Returns: {ok, reason?, ec?}."""
    db = db_reader._get()
    row = db.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone()
    if not row:
        return {"ok": False, "reason": "not_found"}
    t = dict(row)
    # A merged child converts its parent group (the row shown/aggregated).
    if t.get("merged_into_id"):
        parent = db.execute("SELECT * FROM trips WHERE id=?", (t["merged_into_id"],)).fetchone()
        if parent:
            t = dict(parent)
            trip_id = t["id"]
    # Merged groups: use the COMBINED span + distance so getEC covers the whole drive and the official
    # figure is attributed over the full distance (a merge of close trips = one cloud driving session).
    children = db_reader._children_by_parent(db).get(trip_id, [])
    grp = db_reader._trip_group_stats(t, children) if children else t
    dist = grp.get("distance_km") or 0
    if dist <= 0:
        return {"ok": False, "reason": "no_distance"}
    # Shared power-on SESSION guard (#98 root): the cloud's getEC runs Ready-ON→OFF and can span SEVERAL
    # Mate trips + idle. If OTHER finalized trips share this trip's Ready session (the car was never
    # powered off between them — read from positions.ready), converting this one alone would grab the
    # WHOLE session → tell the user to MERGE them (only the merged group attributes the figure over the
    # full distance). Detected from the real signal, so it works at ANY gap (not the 5-min heuristic).
    own_ids = {trip_id} | {c["id"] for c in children}
    sess = db_reader.ready_session(grp)
    if sess and (set(sess["trip_ids"]) - own_ids):
        # Hand back WHICH trips share it. "the adjacent one" told @michapr nothing — his was the
        # previous trip, and the word doesn't say previous, next, or how many (beta #19).
        return {"ok": False, "reason": "shared_session",
                "other_ids": sorted(set(sess["trip_ids"]) - own_ids)}
    bq, eq = db_reader.trip_ec_window(grp)
    if not bq or not eq:
        return {"ok": False, "reason": "no_window"}
    try:
        ec = command_client.get_energy_breakdown_range(bq, eq)
    except Exception as e:  # noqa: BLE001
        log.warning("convert_trip %s cloud error: %s", trip_id, e)
        return {"ok": False, "reason": "error"}
    if not ec or (ec.get("total_kwh") or 0) <= 0:
        # bump ec_tried so the row reflects the attempt, but change nothing else
        db_reader.store_trip_ec(trip_id, None, dist, apply_energy=False)
        # Distinguish "the cloud merged this with its neighbour" (→ tell the user to merge them, the
        # only way to recover it) from a plain "no official data". Use Mate's OWN merge eligibility as
        # the signal (adjacent, within the default gap, no charge between) — NOT a hard-coded number —
        # so it matches what the merge UI offers and generalises to any user's trip pattern.
        reason = "no_data"
        try:
            pairs = db_reader.get_mergeable_pairs(db_reader.TRIP_MERGE_GAP_DEFAULT)
            if any(p.get("b_id") == trip_id for p in pairs):
                reason = "merged_cloud"
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "reason": reason}
    # Plausibility guard (#96 low / #98 high) — mirrors the auto-sweep: refuse a getEC that is
    # physically implausible vs the SoC battery delta — too low (incomplete cloud aggregation) OR too
    # high (over-attributed, e.g. a very short trip whose 2-min window pre-pad dwarfs the drive).
    # Applying it would overwrite the reliable SoC estimate; a genuinely low/high but SoC-consistent
    # trip is still accepted. The manual button must not do what the background sweep refuses to.
    # Plausibility net (kept on the Ready path too — it's about correctness, not just the window): a
    # SINGLE trip whose getEC is implausible vs its own ΔSoC is rejected → keep the SoC estimate. LOW =
    # genuine cloud gap. HIGH = the session swallowed pre-drive idle/climate, so getEC over-states THIS
    # drive (the trip = the drive; drive-only SoC is the truer per-trip figure). The MULTI-trip case is
    # already handled above by the shared-session block, so here it only ever sees single trips.
    soc_e = _soc_energy_kwh(grp, dist)
    if _ec_implausible(ec, dist, soc_e):
        db_reader.store_trip_ec(trip_id, None, dist, apply_energy=False)  # record attempt, change nothing
        log.info("convert_trip %s: implausible getEC %.2f kWh over %.1f km (SoC≈%.2f kWh) — kept SoC estimate",
                 trip_id, ec.get("total_kwh") or 0, dist, soc_e or 0)
        return {"ok": False, "reason": "implausible"}
    # Over-attribution guard: when the cloud merged this trip with a CLOSE FOLLOWING trip, this trip's
    # own window returns the COMBINED session energy (the cloud anchors it on the EARLIER trip). Applying
    # it over only this trip's distance overstates it (e.g. 113: 1.6 kWh over 4 km → 40, instead of over
    # the 10 km of 113+114 → 16). If this is the earlier half of a mergeable pair, suggest merging
    # instead — only the merged group attributes the figure over the full distance. (A MERGED parent has
    # children, so it already uses the combined distance and skips this guard.)
    if not children:
        try:
            pairs = db_reader.get_mergeable_pairs(db_reader.TRIP_MERGE_GAP_DEFAULT)
            if any(p.get("a_id") == trip_id for p in pairs):
                return {"ok": False, "reason": "merged_cloud"}
        except Exception:  # noqa: BLE001
            pass
    db_reader.store_trip_ec(trip_id, ec, dist, apply_energy=True, stable=True)
    log.info("EC CONVERTED (manual) trip %s: %.1f kWh (Guida %.1f / AC %.1f / Altro %.1f)",
             trip_id, ec["total_kwh"], ec["driving_kwh"], ec["ac_kwh"], ec["other_kwh"])
    return {"ok": True, "ec": ec}
