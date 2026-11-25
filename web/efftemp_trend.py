"""Keeps each car's consumption-vs-temperature trend stored for the poller's MQTT bridge (D#498).

The trend changes when a trip gets its temperature (elevation_enrich), its cloud energy (ec_enrich),
is merged or deleted — all of them here, in the web. Rather than hook each of those, a daemon thread
recomputes it every few minutes: one query of two years of trips per car, and a write only when the
numbers moved (db_reader.store_efftemp_trends)."""
import logging
import threading
import time

import db_reader

log = logging.getLogger("efftemp_trend")

_lock = threading.Lock()
_bg_started = False


def start_background(interval_s: int = 300) -> None:
    """Start the refresher. Idempotent. The first pass runs at once, so an install that has just
    updated publishes its trend without waiting a full interval."""
    global _bg_started
    with _lock:
        if _bg_started:
            return
        _bg_started = True

    def _loop():
        while True:
            try:
                changed = db_reader.store_efftemp_trends()
                if changed:
                    log.info("efftemp trend: %d car(s) updated", changed)
            except Exception as e:  # noqa: BLE001 — a bad pass must never stop the next one
                log.debug("efftemp trend refresh skipped: %s", e)
            time.sleep(interval_s)

    threading.Thread(target=_loop, daemon=True).start()
