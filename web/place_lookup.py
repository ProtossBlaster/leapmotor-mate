"""Where a trip started and ended and where a charge happened, as an address: a background sweep filling
the `addresses` table.

A spot is looked up once per geohash-8 cell (19 m north to south; 38 m across at the equator, 23 m at
53° N), whichever car, trip or charge was there, and a trip or a charge finds its rows by its
coordinates when it is read (db_reader.trip_places, db_reader.charge_places). So a second trip from the
same driveway costs no request, and an older trip or charge in a cell a new one filled shows that
address too.

The provider is the one chosen in Settings ▸ Address lookup (geocode.reverse_place), and only it: a
failure leaves the cell to be asked again later, at the same provider. "Nothing here" is final for the
provider that said it, for the pass. Another one chosen later is asked at once where the first had
nothing or failed; an address found stays until it is ADDRESS_REFRESH_DAYS old, then the next trip or
charge there asks again, and only a new address replaces it. Only the ends of trips and the charges that
ended in the last RECENT_DAYS are looked up, and only while the switch in that card is on; the 🧭 on a
trip's page asks about that trip's ends whenever the user wants (look_up_now). The history is never
swept: Nominatim's usage policy counts the requests of every install of an application together and
treats an application's periodic requests as bulk. Its other rules are kept here too: at most one
request per second, from the pass and the 🧭 together, a few per sweep, and every answer stored.
"""
import contextlib
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

import db_reader
import geocode
import geohash

log = logging.getLogger("place_lookup")

RECENT_DAYS = 3             # a trip's ends and a charge are looked up until it ended this long ago
ADDRESS_REFRESH_DAYS = 90   # a found address is asked again once this old: names change at the provider
_SWEEP_TTL_S = 60           # at most one sweep a minute
_MAX_CALLS = 4              # requests per sweep
_GAP_S = 1.1                # between two requests, the pass's and the 🧭's alike
_RETRY_S = 15 * 60          # a failed cell waits this, doubled at each failure in a row…
_RETRY_MAX_S = 6 * 3600     # …up to this
_lock = threading.Lock()
_running = False
_bg_started = False
_asking = threading.Lock()  # one batch of requests at a time: a pass or a 🧭
_asked_at = float("-inf")   # time.monotonic() when the last request ended


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _recent_cells(since: datetime) -> list[tuple[str, float, float]]:
    """Every car's trip ends and charges with a GPS fix, of those that ended since `since`, a merged trip
    or charge as long as its last piece did: newest first, one entry per cell, with the first point seen
    in it. A merged charge is where its first piece was, as its card shows it."""
    db = db_reader._get()
    # as every query on charges.merged_into_id: the web's schema step at start is best-effort
    head = "COALESCE(merged_into_id, id)" if db_reader._charges_have_merge(db) else "id"
    trips = ("FROM trips t JOIN (SELECT COALESCE(merged_into_id, id) AS head, MAX(julianday(ended_at)) AS jd"
             " FROM trips GROUP BY 1 HAVING jd >= julianday(?)) g ON g.head = COALESCE(t.merged_into_id, t.id)"
             " WHERE t.ended_at IS NOT NULL")
    since_iso = _iso(since)
    rows = db.execute(
        f"SELECT g.jd AS jd, t.id AS id, 0 AS kind, t.end_lat AS lat, t.end_lon AS lon {trips}"
        f" UNION ALL SELECT g.jd, t.id, 1, t.start_lat, t.start_lon {trips}"
        f" UNION ALL SELECT g.jd, c.id, 2, c.latitude, c.longitude FROM charges c JOIN (SELECT {head} AS head,"
        " MAX(julianday(ended_at)) AS jd FROM charges GROUP BY 1 HAVING jd >= julianday(?)) g ON g.head = c.id"
        " ORDER BY jd DESC, id DESC, kind", (since_iso, since_iso, since_iso)).fetchall()
    cells: dict = {}
    for r in rows:
        if db_reader.has_gps_fix(r["lat"], r["lon"]):
            cells.setdefault(geohash.encode(r["lat"], r["lon"], 8), (r["lat"], r["lon"]))
    return [(gh, lat, lon) for gh, (lat, lon) in cells.items()]


def _waiting(row, provider: str, now: datetime) -> bool:
    """The provider now chosen failed here and its wait is not over; another provider's wait holds no one."""
    return (row["retry_provider"] == provider and row["retry_at"] is not None
            and datetime.fromisoformat(row["retry_at"]) > now)


def _due(row, provider: str, now: datetime) -> bool:
    """Whether a cell already in the table is asked again: never while the provider now chosen waits after
    its own failure there; else a found address once it is ADDRESS_REFRESH_DAYS old, a "nothing here" from
    another provider, and any failure."""
    if _waiting(row, provider, now):
        return False
    if row["status"] == "found":
        return datetime.fromisoformat(row["looked_up_at"]) <= now - timedelta(days=ADDRESS_REFRESH_DAYS)
    if row["status"] == "none":
        return row["provider"] != provider
    return True


def _store(gh: str, lat: float, lon: float, provider: str, now: datetime, status: str,
           place: dict | None = None, attempts: int = 0, retry_at: str | None = None,
           retry_provider: str | None = None) -> None:
    place = place or {}
    with contextlib.closing(db_reader._conn_rw()) as db:
        db.execute(
            "INSERT OR REPLACE INTO addresses (geohash, latitude, longitude, provider, status, attempts,"
            " retry_at, retry_provider, name, house_number, road, suburb, locality, postcode, country_code,"
            " display_name, looked_up_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (gh, lat, lon, provider, status, attempts, retry_at, retry_provider, place.get("name"),
             place.get("house_number"), place.get("road"), place.get("suburb"), place.get("locality"),
             place.get("postcode"), place.get("country_code"), place.get("display_name"), _iso(now)))
        db.commit()


def _keep(gh: str, **cols) -> None:
    """A refresh that brought no address: the one the cell has stays, with these columns changed."""
    with contextlib.closing(db_reader._conn_rw()) as db:
        db.execute(f"UPDATE addresses SET {', '.join(f'{c} = ?' for c in cols)} WHERE geohash = ?",
                   (*cols.values(), gh))
        db.commit()


def _known(cells) -> dict:
    """The table's rows of these cells, by geohash."""
    cells = list(cells)
    return {r["geohash"]: r for r in db_reader._get().execute(
        "SELECT geohash, provider, status, attempts, retry_at, retry_provider, looked_up_at FROM addresses"
        f" WHERE geohash IN ({', '.join('?' * len(cells))})", cells)}


def _ask(gh: str, lat: float, lon: float, row, chosen: str, key: str | None, provider: str,
         now: datetime) -> tuple[str, str | None]:
    """Ask the provider about one cell and keep the answer: "found", "none" or "failed" (asked again later), with
    what went wrong on a failure (geocode.failure)."""
    refresh = row is not None and row["status"] == "found"
    try:
        place = geocode.reverse_place(lat, lon, chosen, key)
    except Exception as e:  # noqa: BLE001 — timeout, HTTP, malformed answer: all asked again later
        why = geocode.failure(e)
        again = row is not None and row["retry_provider"] == provider
        attempts = (row["attempts"] if again else 0) + 1
        wait = min(_RETRY_S * 2 ** (attempts - 1), _RETRY_MAX_S)
        if refresh:
            _keep(gh, attempts=attempts, retry_at=_iso(now + timedelta(seconds=wait)), retry_provider=provider)
        else:
            _store(gh, lat, lon, provider, now, "failed", attempts=attempts,
                   retry_at=_iso(now + timedelta(seconds=wait)), retry_provider=provider)
        log.warning("%s failed (%s), asked again in %d min", provider, why, wait // 60)
        return "failed", why
    if refresh and not place:
        _keep(gh, looked_up_at=_iso(now), attempts=0, retry_at=None, retry_provider=None)
    else:
        _store(gh, lat, lon, provider, now, "found" if place else "none", place)
    return ("found" if place else "none"), None


def _ask_each(cells: list, wanted, chosen: str, key: str | None, now: datetime, pause,
              limit: int | None = None) -> dict:
    """Ask about each (geohash, lat, lon) whose row is `wanted(row, provider)`, at most `limit` of them, each
    _GAP_S after the request before it, whichever batch made that one. The rows are read once no other batch
    is asking, so a cell it just found is not asked again. The first failure ends the batch: the provider is
    most likely down, and the cell that failed waits without holding up the others. Returns the counts, the
    provider asked and, after a failure, what went wrong (`why`)."""
    global _asked_at
    provider = geocode.lookup_provider(chosen, key)
    n = {"known": 0, "calls": 0, "found": 0, "none": 0, "failed": 0, "provider": provider, "why": None}
    with _asking:
        known = _known(gh for gh, _, _ in cells)
        for gh, lat, lon in cells:
            row = known.get(gh)
            if not wanted(row, provider):
                n["known"] += 1
                continue
            if n["calls"] == limit:
                break
            wait = _GAP_S if n["calls"] else _asked_at + _GAP_S - time.monotonic()
            if wait > 0:
                pause(wait)
            n["calls"] += 1
            try:
                outcome, why = _ask(gh, lat, lon, row, chosen, key, provider, now)
            finally:
                _asked_at = time.monotonic()
            n[outcome] += 1
            if outcome == "failed":
                n["why"] = why
                break
    return n


def sweep(now: datetime | None = None, pause=time.sleep) -> dict:
    """One pass over the recent trips' ends and charges. Returns its counts: cells checked, cells the table
    already answers, requests made, and how they went."""
    now = now or datetime.now(timezone.utc)
    chosen = db_reader.get_setting("geocoder_provider", "")
    key = db_reader.get_secret("geocoder_key", "") or None
    cells = _recent_cells(now - timedelta(days=RECENT_DAYS))
    n = {"cells": len(cells), **_ask_each(cells, lambda row, provider: row is None or _due(row, provider, now),
                                         chosen, key, now, pause, _MAX_CALLS)}
    log.log(logging.INFO if n["calls"] else logging.DEBUG,
            "%(cells)d cells, %(known)d known, %(calls)d asked: %(found)d found, "
            "%(none)d with no address, %(failed)d failed", n)
    return n


def look_up_now(points, now: datetime | None = None, pause=time.sleep) -> dict:
    """The 🧭 on a trip's page: the points' cells without an address found are asked about now, whatever the
    switch says and however old the trip, since the user asked. Returns the counts of _ask_each."""
    now = now or datetime.now(timezone.utc)
    chosen = db_reader.get_setting("geocoder_provider", "")
    key = db_reader.get_secret("geocoder_key", "") or None
    cells = {geohash.encode(lat, lon, 8): (lat, lon) for lat, lon in points if db_reader.has_gps_fix(lat, lon)}
    return _ask_each([(gh, lat, lon) for gh, (lat, lon) in cells.items()],
                     lambda row, provider: row is None or row["status"] != "found", chosen, key, now, pause)


def maybe_sweep() -> None:
    """Cheap: bail unless the switch is on and the TTL (`place_sweep_at`, kept across restarts) elapsed, then
    sweep in a daemon thread. One web process runs per database (run.sh, the desktop app's lock): `_lock`
    keeps it to one pass."""
    global _running
    try:
        if db_reader.get_setting("place_lookup", "0") != "1":     # written by schema.ensure_schema
            return
        with _lock:
            last = float(db_reader.get_setting("place_sweep_at", "0") or 0)
            now = time.time()
            if _running or abs(now - last) < _SWEEP_TTL_S:      # abs: a clock stepped back does not stop it
                return
            db_reader.set_setting("place_sweep_at", str(now))   # before _running: a failed write must not leave it set
            _running = True
    except Exception as e:  # noqa: BLE001
        log.debug("maybe_sweep skipped: %s", e)
        return
    threading.Thread(target=_sweep_now, daemon=True).start()


def start_background(interval_s: int = _SWEEP_TTL_S) -> None:
    """A daemon thread that triggers the sweep every `interval_s`, so a trip or a charge gets its address
    even when no page is open. Idempotent."""
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
    log.info("background sweeper started (every %ss)", interval_s)


def _sweep_now() -> None:
    global _running
    try:
        sweep()
    except Exception as e:  # noqa: BLE001
        log.warning("sweep error: %s", e)
    finally:
        _running = False
