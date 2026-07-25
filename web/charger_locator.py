"""Public EV charging stations from OpenStreetMap (Overpass API).

Two consumers:
  - 📍 charge labels: a background sweep names the public charging station of each
    closed charge (shown on the Charges list and the Overview "last charge" card).
    Opt-in via the `charger_locator` setting — home charges are never looked up, and
    history backfills too (charges already store their GPS position since v1.0).
  - Navigation page: "Find charging stations" around the car, user-picked radius.

Keyless and free (no API key), same etiquette as geocode.py: proper User-Agent,
tiny volume, ~1 s spacing inside the sweep. OSM is a community map database — names
live in the name/operator/brand tags (often only `operator`) and there is NO live
free/busy state. Standard library only (urllib) — no new dependency.

Idea credit: @hubcasale (PR #48). Reimplemented web-side: the poller stays untouched
(no shared-connection threads) and old charges get their label retroactively.
"""
import json
import logging
import math
import re
import threading
import time
import urllib.parse
import urllib.request

import db_reader

log = logging.getLogger(__name__)

_UA = "LeapMotorMate/1.0 (https://github.com/ProtossBlaster/leapmotor-mate)"
_OVERPASS = "https://overpass-api.de/api/interpreter"
_NAME_TAGS = ("name", "operator", "brand", "network")
_LABEL_RADIUS_M = 150     # stall vs mapped point + GPS error — validated on real stations
_REUSE_RADIUS_M = 80      # same spot as an already-resolved charge → reuse, no network
_SITE_SAME_NAME_RADIUS_M = 80    # find_station_candidates clustering: same name, same site
_SITE_DIFF_NAME_RADIUS_M = 100   # different name, same site — widened from 25 m: a real service
                                  # area is routinely reported under two different names 70+ m
                                  # apart (PUN's generic site point vs OCM/OSM's own node — see
                                  # "Area di Servizio - Flaminia Ovest" / "Free To X ADS Flaminia
                                  # Ovest", 72 m apart, both genuinely the same station). Trade-off,
                                  # accepted: two truly distinct but nearby stations can now also
                                  # surface as a manual-recalc choice.
_SWEEP_TTL_S = 30 * 60    # at most one render-triggered sweep per half hour
_SWEEP_LIMIT = 40         # charges resolved per sweep run (any backlog continues later)
_sweeping = False
_lock = threading.Lock()


def _dist_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Equirectangular approximation — exact enough at station scale (< few km)."""
    dy = (lat2 - lat1) * 111_320.0
    dx = (lon2 - lon1) * 111_320.0 * math.cos(math.radians(lat1))
    return math.hypot(dx, dy)


def _coord(el: dict):
    """Element position: nodes carry lat/lon, ways/relations carry a `center`."""
    if "lat" in el:
        return el["lat"], el["lon"]
    c = el.get("center") or {}
    return c.get("lat"), c.get("lon")


def _label(tags: dict):
    """Most human-readable tag — stations are often tagged with `operator` only."""
    for key in _NAME_TAGS:
        val = (tags.get(key) or "").strip()
        if val:
            return val
    return None


_SOCKETS = (("socket:ccs", "CCS"), ("socket:type2_combo", "CCS"),
            ("socket:chademo", "CHAdeMO"),
            ("socket:type2", "Type 2"), ("socket:type2_cable", "Type 2"))


_DC_SOCKETS = ("socket:ccs", "socket:type2_combo", "socket:chademo")
_AC_SOCKETS = ("socket:type2", "socket:type2_cable")


def _has(tags: dict, key: str) -> bool:
    return str(tags.get(key, "")).strip() not in ("", "0", "no")


def _fmt_kw(kw: float) -> str:
    """Clean kW for display: fast/DC chargers are quoted as whole numbers (22, 50,
    150) — and registry values are noisy (22.144 → 22); AC levels below 20 keep one
    decimal so 3.7 / 7.4 / 11 stay meaningful."""
    return f"{round(kw)}" if kw >= 20 else f"{round(kw, 1):g}"


def _osm_address(tags: dict) -> "str | None":
    """Best-effort street address from OSM addr:* tags (sparsely mapped, like everything
    else community-sourced — empty when the mapper only set amenity/operator)."""
    street = tags.get("addr:street") or ""
    house = tags.get("addr:housenumber") or ""
    city = tags.get("addr:city") or ""
    line = (f"{street} {house}".strip())
    parts = [p for p in (line, city) if p]
    return ", ".join(parts) or None


def _socket_info(tags: dict) -> str:
    """Best-effort 'DC · CCS · 300 kW' summary from the community socket:* /
    maxoutput tags (sparsely mapped — empty string when nothing usable).
    AC/DC is inferred: CCS/CHAdeMO sockets → DC, Type 2 → AC, and a max output
    ≥ 50 kW implies DC even when the socket tags are missing."""
    kinds = []
    for key, label in _SOCKETS:
        if _has(tags, key) and label not in kinds:
            kinds.append(label)
    kw = 0.0
    for k, v in tags.items():
        if k == "maxoutput" or (k.startswith("socket:") and k.endswith(":output")):
            m = re.search(r"\d+(?:[.,]\d+)?", str(v))
            if m:
                kw = max(kw, float(m.group(0).replace(",", ".")))
    dc = any(_has(tags, k) for k in _DC_SOCKETS) or kw >= 50
    ac = any(_has(tags, k) for k in _AC_SOCKETS)
    current = "AC/DC" if (ac and dc) else "DC" if dc else "AC" if ac else ""
    parts = ([current] if current else []) + kinds
    if kw:
        parts.append(f"{_fmt_kw(kw)} kW")
    return " · ".join(parts)


def _query(lat: float, lon: float, radius_m: int, limit: int, tries: int = 2):
    """Overpass call → element list, or None when it genuinely failed (after a quick
    retry — the public instance rate-limits in short bursts). `nwr` (not just nodes —
    stations are mapped as areas too) + `out center`. A 200 with zero elements AND a
    `remark` means the query was aborted server-side (internal timeout / out of
    memory) — that is a failure to retry, NOT an empty area.
    Two tag schemes coexist in OSM: `amenity=charging_station` (the site) and
    `man_made=charge_point` (the single column, common in Italy) — query BOTH or
    nearby chargers go missing."""
    around = f"(around:{int(radius_m)},{lat},{lon})"
    q = (f"[out:json][timeout:10];("
         f'nwr["amenity"="charging_station"]{around};'
         f'nwr["man_made"="charge_point"]{around};'
         f");out center {int(limit)};")
    data = urllib.parse.urlencode({"data": q}).encode()
    for attempt in range(tries):
        if attempt:
            time.sleep(1.5)
        req = urllib.request.Request(_OVERPASS, data=data, headers={"User-Agent": _UA})
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                body = json.load(resp)
        except Exception as exc:  # noqa: BLE001 — timeout/429/5xx/bad JSON
            log.warning("charger locator: Overpass error (attempt %d): %s", attempt + 1, exc)
            continue
        els = body.get("elements", [])
        if not els and body.get("remark"):
            log.warning("charger locator: Overpass aborted: %s", body["remark"])
            continue
        return els
    return None


def find_station_candidates(lat: float, lon: float):
    """(options, ok) for BOTH the background sweep (📍 auto-labels a charge — takes
    options[0], the nearest, deterministically, since unattended automation can't ask
    a user to pick) and the manual recalc button (shows a picker when len(options) > 1).
    ok=False → transient error, worth retrying later; ok=True with [] → genuinely
    nothing usable within _LABEL_RADIUS_M.
    Distance decides first: the nearest PHYSICAL SITE wins outright — a different,
    farther site never creates a choice, no matter how many others also sit within
    label range. "Same site" is proximity-based (_SITE_SAME_NAME_RADIUS_M /
    _SITE_DIFF_NAME_RADIUS_M), NOT bare name equality: a real service area is
    routinely reported under two different names by two sources (OSM tags the
    specific network, e.g. 'Free To X'; PUN gives the generic site name, e.g. 'Area
    di Servizio - X Nord') tens of metres apart.
    Within that one site, sources are grouped by NAME: sources that agree on a name
    merge into one richer pin (_merge_richer — a bare pin never shadows one that also
    has address/live-status detail); sources under a DIFFERENT name, or agreeing
    sources whose own details genuinely conflict (e.g. one says 22 kW, another
    150 kW), each surface as their own option — the manual recalc button pops a
    picker, the unattended sweep just takes options[0] (nearest) deterministically.
    Every option gets a best-effort link even when its own source has none —
    borrowed from whichever OTHER candidate in the same site already has one
    (Open Charge Map preferred over OpenStreetMap, see _url_rank) — so choosing the
    PUN-only name still gets you a map link.
    TomTom stays excluded — its data can't be stored (see _tomtom_stations)."""
    if not lat or not lon:
        return [], True
    els = _query(lat, lon, _LABEL_RADIUS_M, 10)
    ocm = _ocm_stations(lat, lon, _LABEL_RADIUS_M, 5)
    pun = _pun_stations(lat, lon, _LABEL_RADIUS_M, 20)
    cands = []
    for e in (els or []):
        la, lo = _coord(e)
        if la is None:
            continue
        tags = e.get("tags", {})
        name = _label(tags)
        if name:
            url = (f"https://www.openstreetmap.org/{e['type']}/{e['id']}"
                   if e.get("type") and e.get("id") else None)
            cands.append({"name": name, "info": _socket_info(tags), "lat": la, "lon": lo,
                          "address": _osm_address(tags), "url": url,
                          "dist_m": int(_dist_m(lat, lon, la, lo))})
    for s in (ocm or []):
        if s.get("name"):
            cands.append({"name": s["name"], "info": s.get("info") or "",
                          "lat": s["lat"], "lon": s["lon"],
                          "address": s.get("address"), "url": s.get("url"),
                          "dist_m": s["dist_m"]})
    for s in (pun or []):
        if s.get("name"):
            cands.append({"name": s["name"], "info": s.get("info") or "",
                          "lat": s["lat"], "lon": s["lon"],
                          "address": None, "url": s.get("url"), "dist_m": s["dist_m"]})
    tried = [els, ocm if _ocm_key() else None, pun if _in_italy(lat, lon) else None]
    ok = any(r is not None for r in tried)
    if not cands:
        return [], ok
    cands.sort(key=lambda c: c["dist_m"])
    sites: list = []
    for c in cands:
        site = next((s for s in sites if any(
            _dist_m(c["lat"], c["lon"], o["lat"], o["lon"]) <=
            (_SITE_SAME_NAME_RADIUS_M if c["name"] == o["name"] else _SITE_DIFF_NAME_RADIUS_M)
            for o in s)), None)
        if site is None:
            sites.append([c])
        else:
            site.append(c)
    nearest = min(sites, key=lambda s: min(c["dist_m"] for c in s))
    nearest.sort(key=lambda c: c["dist_m"])
    by_name: dict = {}
    for c in nearest:
        by_name.setdefault(c["name"], []).append(c)
    options = []
    for group in by_name.values():
        infos = {c["info"] for c in group if c["info"]}
        if len(infos) <= 1:             # this name's sources agree (or only one has details)
            merged = group[0]
            for c in group[1:]:
                merged = _merge_richer(merged, c)
            options.append(merged)
        else:                           # this name's own sources genuinely conflict
            options.extend(next(c for c in group if c["info"] == info) for info in infos)
    # Best-effort link for whichever option(s) still lack one — borrowed from ANY other
    # candidate in the same site, regardless of name (e.g. PUN itself never has a url).
    best_url = max((c.get("url") for c in nearest if c.get("url")), key=_url_rank, default=None)
    for opt in options:
        if not opt.get("url") and best_url:
            opt["url"] = best_url
    options.sort(key=lambda c: c["dist_m"])
    return options, ok


def find_nearby(lat: float, lon: float, radius_m: int, limit: int = 25, name_filter: str = ""):
    """Stations around a point for the Navigation page, nearest first, or None on
    a transient error. Unnamed stations are kept (a real charger you can drive to —
    the UI shows a generic label). Overpass cannot sort by distance and `out N` caps
    in id order — fetch generously so the post-sort really holds the nearest ones
    (PUN does its own shrink-to-fit; OCM already returns nearest-first).
    A site's individual charge-point columns dedupe into one pin (same label within
    80 m, anonymous right next to a kept pin, or the same station from the other
    source under a slightly different name) — the source with MORE data becomes the
    pin, backfilled with whatever extra field the other source still has (see
    _merge_richer). TomTom pins additionally borrow a source link from OCM (checked
    first) or OSM when either covers the same spot — see _borrow_url."""
    els = _query(lat, lon, radius_m, 1000)
    ocm = _ocm_stations(lat, lon, radius_m)
    tom = _tomtom_stations(lat, lon, radius_m)
    pun = _pun_stations(lat, lon, radius_m,
                        op_codes=_pun_op_codes(name_filter) if name_filter else None)
    # Transient error only when EVERY source we actually tried failed (returned None).
    # Sources that don't apply return [] (keyless OCM/TomTom, non-Italian PUN) and don't
    # count as failures — an empty result from an applicable source is a real "none nearby".
    attempted = ([els] + ([ocm] if _ocm_key() else []) + ([tom] if _tomtom_key() else [])
                 + ([pun] if _in_italy(lat, lon) else []))
    if all(r is None for r in attempted):
        return None
    out = []
    for e in (els or []):
        la, lo = _coord(e)
        if la is None:
            continue
        tags = e.get("tags", {})
        url = (f"https://www.openstreetmap.org/{e['type']}/{e['id']}"
               if e.get("type") and e.get("id") else None)
        out.append({"name": _label(tags), "lat": la, "lon": lo,
                    "dist_m": int(_dist_m(lat, lon, la, lo)),
                    "info": _socket_info(tags),
                    "address": _osm_address(tags), "url": url})
    out.extend(ocm or [])
    for t in (tom or []):
        t["url"] = _borrow_url(t["lat"], t["lon"], ocm, els)
    out.extend(tom or [])
    out.extend(pun or [])
    if name_filter:   # operator filter: keep only matching names (PUN was already narrowed)
        nf = name_filter.strip().lower()
        out = [s for s in out if nf in (s["name"] or "").lower()]
    out.sort(key=lambda s: s["dist_m"])
    kept = []
    for s in out:
        idx = next((i for i, k in enumerate(kept)
                    if _dist_m(s["lat"], s["lon"], k["lat"], k["lon"]) <=
                    (80 if s["name"] == k["name"] else 40 if not s["name"] else 25)), None)
        if idx is None:
            kept.append(s)
        else:
            kept[idx] = _merge_richer(kept[idx], s)
        if len(kept) >= limit:
            break
    return kept


_TOMTOM_LINK_RADIUS_M = 60   # "same physical station" for borrowing a link — tighter than the
                             # generic dedupe radii (25-80 m) so a neighbouring site never gets
                             # mislinked; TomTom's own coordinates are precise enough for this.


def _borrow_url(lat: float, lon: float, ocm: list, els: list) -> "str | None":
    """TomTom has no public per-POI page (see _tomtom_stations) — so its pin borrows
    one from whichever OTHER source already answered for the same spot: Open Charge
    Map is checked FIRST (it already carries its own detail-page link — see
    _url_rank), OpenStreetMap only if OCM doesn't cover this station. None when
    neither source is within link range."""
    for s in (ocm or []):
        if s.get("url") and _dist_m(lat, lon, s["lat"], s["lon"]) <= _TOMTOM_LINK_RADIUS_M:
            return s["url"]
    for e in (els or []):
        la, lo = _coord(e)
        if la is None or not (e.get("type") and e.get("id")):
            continue
        if _dist_m(lat, lon, la, lo) <= _TOMTOM_LINK_RADIUS_M:
            return f"https://www.openstreetmap.org/{e['type']}/{e['id']}"
    return None


_MERGE_FIELDS = ("name", "info", "avail", "address", "url")


def _url_rank(url: "str | None") -> int:
    """When two sources both have a link for the same station, Open Charge Map's own
    EV-detail page is the more useful, internationally-working landing spot — so it
    wins the link even on a charge where OSM was picked as the overall richer source
    (e.g. it alone has the address). NB: the page is `openchargemap.org/poi/details/*`
    — NOT `/site/poi/details/*` (an old path that 404s and gets redirected into a
    forced sign-in, which is what looked like a login-gate — verified live)."""
    if not url:
        return 0
    return 2 if "openchargemap.org" in url else 1


def _merge_richer(a: dict, b: dict) -> dict:
    """Two sources agree on one physical station: rather than always trusting whichever
    was nearest (an artifact of query order, not data quality), the one carrying MORE
    information becomes the base — then any field it's still missing gets backfilled
    from the other. So a source with a bare pin never shadows a source that also has
    the socket/address/live-status detail, regardless of which is a hair closer.
    The link is the one exception to "base wins ties": it's picked independently by
    _url_rank, not by overall richness (see there)."""
    score = lambda s: sum(bool(s.get(f)) for f in _MERGE_FIELDS)  # noqa: E731
    base, other = (a, b) if score(a) >= score(b) else (b, a)
    for f in _MERGE_FIELDS:
        if f != "url" and not base.get(f) and other.get(f):
            base[f] = other[f]
    if _url_rank(other.get("url")) > _url_rank(base.get("url")):
        base["url"] = other["url"]
    return base


# ── Open Charge Map (optional, free per-user API key) ────────────────────────
# OSM and OCM COMPLEMENT each other (validated on real data: near Silvio's home OSM
# has the Enel X stations and misses Lidl/BeCharge, OCM the exact opposite) — so with
# a key configured both are queried and merged. OCM is the EV-dedicated registry:
# AC/DC and kW are first-class per-connection fields there, not optional tags.

_OCM_URL = "https://api.openchargemap.io/v3/poi/"
_OCM_CUR = {10: "AC", 20: "AC", 30: "DC"}  # CurrentTypeID: 10=AC 1-ph, 20=AC 3-ph, 30=DC


def _ocm_key() -> str:
    try:
        return db_reader.get_secret("ocm_key", "") or ""
    except Exception:  # noqa: BLE001 — fresh install / CI: no settings DB yet
        return ""


def _ocm_stations(lat: float, lon: float, radius_m: int, limit: int = 100):
    """OCM POIs → station dicts (same shape as the OSM ones). [] when no key is
    configured (feature stays keyless-OSM), None on a transient API error."""
    key = _ocm_key()
    if not key:
        return []
    params = urllib.parse.urlencode({
        "output": "json", "latitude": f"{lat:.6f}", "longitude": f"{lon:.6f}",
        "distance": max(radius_m / 1000.0, 0.2), "distanceunit": "km",
        "maxresults": limit, "compact": "false", "verbose": "false", "key": key})
    req = urllib.request.Request(f"{_OCM_URL}?{params}", headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            pois = json.load(resp)
    except Exception as exc:  # noqa: BLE001
        log.warning("charger locator: Open Charge Map error: %s", exc)
        return None
    out = []
    for p in pois:
        a = p.get("AddressInfo") or {}
        la, lo = a.get("Latitude"), a.get("Longitude")
        if la is None or lo is None:
            continue
        d = _dist_m(lat, lon, la, lo)
        if d > radius_m:
            continue
        conns = p.get("Connections") or []
        kw = max((c.get("PowerKW") or 0) for c in conns) if conns else 0
        cur = sorted({_OCM_CUR[c["CurrentTypeID"]] for c in conns
                      if _OCM_CUR.get(c.get("CurrentTypeID"))})
        parts = (["/".join(cur)] if cur else []) + ([f"{_fmt_kw(kw)} kW"] if kw else [])
        name = ((a.get("Title") or "").strip()
                or ((p.get("OperatorInfo") or {}).get("Title") or "").strip() or None)
        addr_parts = [(a.get("AddressLine1") or "").strip(), (a.get("Town") or "").strip()]
        address = ", ".join(p2 for p2 in addr_parts if p2) or None
        poi_id = p.get("ID")
        # NB: /poi/details/{id} — NOT /site/poi/details/{id}. The old /site/ prefix
        # 404s and gets redirected into a forced sign-in (looked exactly like a
        # login-gate on this route specifically); the plain path shows the station
        # publicly, no account needed — verified live against a real ID.
        url = f"https://openchargemap.org/poi/details/{poi_id}" if poi_id else None
        out.append({"name": name, "lat": la, "lon": lo, "dist_m": int(d),
                    "info": " · ".join(parts), "address": address, "url": url})
    return out


_KEY_TEST_POINT = (45.4642, 9.19)   # Milan — dense enough that a working key gets a real 200


def test_ocm_key(key: str):
    """Settings 'verify key' button: a tiny live probe with the key TYPED in the form
    (not necessarily saved yet), so the user finds out it's wrong before saving it.
    (ok, reason): reason is None on success, else 'invalid_key' (401/403) or 'error'
    (anything else — network, rate limit, OCM outage)."""
    key = (key or "").strip()
    if not key:
        return False, "missing_key"
    lat, lon = _KEY_TEST_POINT
    params = urllib.parse.urlencode({
        "output": "json", "latitude": f"{lat:.6f}", "longitude": f"{lon:.6f}",
        "distance": 1, "distanceunit": "km", "maxresults": 1,
        "compact": "true", "verbose": "false", "key": key})
    req = urllib.request.Request(f"{_OCM_URL}?{params}", headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            json.load(resp)
        return True, None
    except Exception as exc:  # noqa: BLE001 — HTTPError (401/403 = bad key) is an OSError subclass
        if getattr(exc, "code", None) in (401, 403):
            return False, "invalid_key"
        log.warning("charger locator: OCM key test error: %s", exc)
        return False, "error"


# ── TomTom (optional, free per-user API key) ────────────────────────────────
# Another opt-in keyed source, same shape as OCM. Good worldwide coverage incl. the
# brand-new sites the PUN hasn't registered yet. Uses the Nearby Search API with
# categorySet 7309 (EV charging station) — returns every charger in the radius,
# nearest-first, with a chargingPark per POI. (categorySearch needs a query term and
# returns nothing for a bare category — verified live; nearbySearch is the right one.)
# NB: TomTom forbids STORING its data, so it feeds only the live "find nearby" view —
# never the saved 📍 charge labels (those stay OSM/PUN/OCM, all reusable open data).

_TOMTOM_URL = "https://api.tomtom.com/search/2/nearbySearch/.json"
_TOMTOM_EV_CATEGORY = "7309"


def _tomtom_key() -> str:
    try:
        return db_reader.get_secret("tomtom_key", "") or ""
    except Exception:  # noqa: BLE001 — fresh install / CI: no settings DB yet
        return ""


def _tomtom_stations(lat: float, lon: float, radius_m: int, limit: int = 100):
    """TomTom EV stations near a point (Category Search). [] when no key is set
    (feature stays keyless), None on a transient API error. Connector currentType /
    ratedPowerKW give AC/DC and kW; TomTom returns `dist` (metres) directly."""
    key = _tomtom_key()
    if not key:
        return []
    params = urllib.parse.urlencode({
        "key": key, "lat": f"{lat:.6f}", "lon": f"{lon:.6f}",
        "radius": int(radius_m), "categorySet": _TOMTOM_EV_CATEGORY,
        "limit": min(int(limit), 100)})
    req = urllib.request.Request(f"{_TOMTOM_URL}?{params}", headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            body = json.load(resp)
    except Exception as exc:  # noqa: BLE001
        log.warning("charger locator: TomTom error: %s", exc)
        return None
    out = []
    for r in body.get("results", []):
        pos = r.get("position") or {}
        la, lo = pos.get("lat"), pos.get("lon")
        if la is None or lo is None:
            continue
        name = ((r.get("poi") or {}).get("name") or "").strip() or None
        kw = 0.0
        currents = set()
        for c in ((r.get("chargingPark") or {}).get("connectors") or []):
            try:
                kw = max(kw, float(c.get("ratedPowerKW") or 0))
            except (TypeError, ValueError):
                pass
            ct = (c.get("currentType") or "").upper()
            if "DC" in ct:
                currents.add("DC")
            elif "AC" in ct:
                currents.add("AC")
        cur = "AC/DC" if {"AC", "DC"} <= currents else next(iter(currents), "")
        info = " · ".join(([cur] if cur else []) + ([f"{_fmt_kw(kw)} kW"] if kw else []))
        dist = r.get("dist")
        dist_m = int(dist) if isinstance(dist, (int, float)) else int(_dist_m(lat, lon, la, lo))
        address = ((r.get("address") or {}).get("freeformAddress") or "").strip() or None
        out.append({"name": name, "lat": la, "lon": lo, "dist_m": dist_m, "info": info,
                    "address": address})
    return out


def test_tomtom_key(key: str):
    """Settings 'verify key' button — see test_ocm_key. TomTom's own error payload
    uses 403 for a bad/missing key."""
    key = (key or "").strip()
    if not key:
        return False, "missing_key"
    lat, lon = _KEY_TEST_POINT
    params = urllib.parse.urlencode({
        "key": key, "lat": f"{lat:.6f}", "lon": f"{lon:.6f}", "radius": 1000,
        "categorySet": _TOMTOM_EV_CATEGORY, "limit": 1})
    req = urllib.request.Request(f"{_TOMTOM_URL}?{params}", headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            json.load(resp)
        return True, None
    except Exception as exc:  # noqa: BLE001
        if getattr(exc, "code", None) in (401, 403):
            return False, "invalid_key"
        log.warning("charger locator: TomTom key test error: %s", exc)
        return False, "error"


# ── PUN — Piattaforma Unica Nazionale (Italy, official, keyless) ─────────────
# The national EV-charging registry (GSE/MASE), mandatory by law (AFIR) so it is the
# most complete source for Italy by far — and it carries AC/DC, kW and live AVAILABLE
# status per connector. Reached through the public ArcGIS FeatureServer that backs the
# official GSE map dashboard; that proxy is referer-locked to the dashboard, so the
# header below is required (no key). Italy-only → gated by a bounding box so a foreign
# car never wastes the call. Best-effort: any hiccup just drops PUN for that query.

_PUN_URL = ("https://utility.arcgis.com/usrsvcs/servers/"
            "0a7de59eac154f248408fd7a281b3611/rest/services/PdR_latest_new/FeatureServer/0/query")
_PUN_REFERER = "https://experience.arcgis.com/experience/495016e6ce9744f490dd2f1a43b7873f/"
_PUN_FIELDS = ("ID_location,ID_EVSE,Nome_location,Tipologia_di_alimentazione,"
               "Potenza_erogabile,Stato,Latitudine_EVSE,Longitudine_EVSE")
_IT_BBOX = (35.0, 47.6, 6.5, 19.0)  # lat_min, lat_max, lon_min, lon_max (incl. islands)

# CPO code (the IT*XXX* prefix of ID_EVSE) → recognisable brand. Unmapped codes fall
# back to a human-readable Nome_location, then to the raw code.
_PUN_OPERATORS = {
    "A2A": "A2A", "EMO": "E-Moving", "ENX": "Enel X Way", "BEC": "Be Charge",
    "PLN": "Plenitude", "ATE": "Atlante", "DKC": "Duferco", "F2M": "Free To X",
    "EVW": "EVway", "EVA": "EVway", "NEO": "Neogy", "REP": "Repower",
    "ION": "Ionity", "IOY": "Ionity", "BMP": "BeCharge", "ECP": "Ecogy",
    "ALE": "Alperia", "MBE": "Mercedes", "TSL": "Tesla", "VOL": "Volting",
    "SIL": "Silla", "ELC": "Electra", "EWI": "Ewiva", "PWY": "Powy",
}


def _in_italy(lat: float, lon: float) -> bool:
    a, b, c, d = _IT_BBOX
    return a <= lat <= b and c <= lon <= d


def _pun_label(op_code: str, nome: str) -> "str | None":
    brand = _PUN_OPERATORS.get(op_code)
    if brand:
        return brand
    nome = (nome or "").strip()
    # human-readable site name (has a space + a lowercase letter, not a bare code)?
    if nome and " " in nome and any(ch.islower() for ch in nome):
        return nome
    return f"({op_code})" if op_code else None


_PUN_MAX_RECORDS = 2000   # the service's maxRecordCount — the server-side fetch ceiling


# brand (lowercased) → the CPO prefix code(s) used in ID_EVSE, for the operator filter
_PUN_BRAND_CODES = {}
for _code, _brand in _PUN_OPERATORS.items():
    _PUN_BRAND_CODES.setdefault(_brand.lower(), []).append(_code)


def _pun_op_codes(name_filter: str):
    """Map a free-text operator filter to PUN CPO code(s) so the query can be narrowed
    server-side (a far but specific network like Electra would otherwise fall outside
    the nearest-N). None when the text matches no known brand → client-side filtering."""
    q = (name_filter or "").strip().lower()
    if not q:
        return None
    codes = []
    for brand, brand_codes in _PUN_BRAND_CODES.items():
        if q in brand or brand in q:
            codes.extend(brand_codes)
    up = q.upper()
    if up in _PUN_OPERATORS and up not in codes:   # user typed the raw code itself
        codes.append(up)
    return codes or None


def _pun_query(lat: float, lon: float, radius_m: int, max_fetch: int, where: str = "1=1"):
    """One PUN FeatureServer call → raw connector feature list, or None on error."""
    params = urllib.parse.urlencode({
        "f": "json", "where": where, "geometry": f"{lon:.6f},{lat:.6f}",
        "geometryType": "esriGeometryPoint", "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects", "distance": int(radius_m),
        "units": "esriSRUnit_Meter", "outFields": _PUN_FIELDS,
        "returnGeometry": "false", "resultRecordCount": int(max_fetch)})
    req = urllib.request.Request(f"{_PUN_URL}?{params}",
                                 headers={"User-Agent": _UA, "Referer": _PUN_REFERER})
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            body = json.load(resp)
    except Exception as exc:  # noqa: BLE001
        log.warning("charger locator: PUN error: %s", exc)
        return None
    if "error" in body:
        log.warning("charger locator: PUN error: %s", body["error"])
        return None
    return body.get("features", [])


def _pun_stations(lat: float, lon: float, radius_m: int, max_fetch: int = _PUN_MAX_RECORDS,
                  op_codes=None):
    """Italy PUN stations near a point, deduped to one entry per physical site
    (`ID_location`). [] outside Italy (no call), None on a transient error.
    Each entry: name, lat, lon, dist_m, info ('DC · 150 kW'), avail ('3/5').

    The FeatureServer truncates at maxRecordCount in OBJECTID order — NOT by distance
    — so a plain capped query in a dense area can drop the nearest stations entirely
    (the bug behind 'widening the radius hides the close ones'). When a fetch hits the
    ceiling we halve the radius and refetch until the whole set fits: the result is then
    complete, so a distance sort really yields the nearest. Shrinking loses nothing the
    user sees — only the nearest handful are ever shown, and those are close by.

    `op_codes` narrows the query to specific CPO prefixes server-side (operator filter):
    the set is then small and complete at the full radius, so a specific far network is
    found without truncation and no shrinking is needed."""
    if not _in_italy(lat, lon):
        return []
    if op_codes:
        where = " OR ".join(f"ID_EVSE LIKE 'IT*{c}*%'" for c in op_codes)
        feats = _pun_query(lat, lon, max(int(radius_m), 250), max_fetch, where)
        if feats is None:
            return None
        return _pun_parse(lat, lon, feats)
    eff = max(int(radius_m), 250)
    feats = None
    for _ in range(5):
        feats = _pun_query(lat, lon, eff, max_fetch)
        if feats is None:
            return None
        if len(feats) < max_fetch or eff <= 1200:
            break                       # complete set (or already tight) → stop shrinking
        eff //= 2
    return _pun_parse(lat, lon, feats)


def _pun_parse(lat: float, lon: float, feats: list) -> list:
    """Group raw PUN connector rows into one entry per site (`ID_location`)."""
    sites = {}
    for f in feats:
        a = f.get("attributes", {})
        la, lo = a.get("Latitudine_EVSE"), a.get("Longitudine_EVSE")
        if la is None or lo is None:
            continue
        sites.setdefault(a.get("ID_location"), []).append(a)
    out = []
    for conns in sites.values():
        a0 = conns[0]
        ev = a0.get("ID_EVSE") or ""
        op_code = ev.split("*")[1] if ev.count("*") >= 2 else ""
        name = _pun_label(op_code, a0.get("Nome_location"))
        currents = {(_a.get("Tipologia_di_alimentazione") or "").split("_")[0]
                    for _a in conns}
        currents.discard("")
        kw = max((_a.get("Potenza_erogabile") or 0) for _a in conns) / 1000.0
        cur = "AC/DC" if {"AC", "DC"} <= currents else next(iter(currents), "")
        info = " · ".join(([cur] if cur else []) + ([f"{_fmt_kw(kw)} kW"] if kw else []))
        avail = sum(1 for _a in conns if _a.get("Stato") == "AVAILABLE")
        la0, lo0 = a0["Latitudine_EVSE"], a0["Longitudine_EVSE"]
        out.append({"name": name, "lat": la0, "lon": lo0,
                    "dist_m": int(_dist_m(lat, lon, la0, lo0)),
                    "info": info, "avail": f"{avail}/{len(conns)}"})
    return out


# ── Background label sweep (📍 on charges) ────────────────────────────────────

def maybe_sweep() -> None:
    """Kick a background sweep when the toggle is on, unresolved public charges exist
    and the TTL elapsed. Mirrors update_check: a page render only pays a settings probe
    plus one tiny indexed SELECT — the network work happens off-thread. Never raises."""
    try:
        if db_reader.get_setting("charger_locator", "0") != "1":
            return
        last = int(db_reader.get_setting("charger_locator_swept_at", "0") or 0)
        if time.time() - last < _SWEEP_TTL_S:
            return
        if not db_reader.has_location_lookup_candidates():
            return
    except Exception:  # noqa: BLE001 — fresh install: tables/column not migrated yet
        return
    with _lock:
        if _sweeping:
            return
    threading.Thread(target=sweep_now, daemon=True).start()


def sweep_now(limit: int = _SWEEP_LIMIT) -> int:
    """Resolve up to `limit` unlabelled public charges; returns how many got a NAME.
    Single-flight (concurrent callers no-op) — called by the maybe_sweep() thread and
    by the settings endpoint when the toggle turns on."""
    global _sweeping
    with _lock:
        if _sweeping:
            return 0
        _sweeping = True
    try:
        return _sweep_body(limit)
    except Exception as exc:  # noqa: BLE001 — a sweep must never take a worker down
        log.warning("charger locator: sweep failed: %s", exc)
        return 0
    finally:
        with _lock:
            _sweeping = False


def _sweep_body(limit: int) -> int:
    db_reader.set_setting("charger_locator_swept_at", str(int(time.time())))
    cands = db_reader.get_location_lookup_candidates(limit)
    if not cands:
        return 0
    known = db_reader.get_labelled_locations()  # incl. '' sentinels (looked up, nothing)
    named = 0
    called = False
    for c in cands:
        lat, lon = c["latitude"], c["longitude"]
        reuse = next(((n, u) for la, lo, n, u in known
                      if _dist_m(lat, lon, la, lo) <= _REUSE_RADIUS_M), None)
        if reuse is not None:
            db_reader.set_charge_location_name(c["id"], reuse[0], reuse[1])
            named += bool(reuse[0])
            continue
        if called:
            time.sleep(1.0)  # Overpass etiquette between consecutive calls
        options, ok = find_station_candidates(lat, lon)
        called = True
        if not ok:  # Overpass down/rate-limited: stop here, leftovers retry next sweep
            log.info("charger locator: Overpass unreachable — will retry next sweep")
            break
        # Unattended automation can't pop up a picker — take the nearest option
        # deterministically (see find_station_candidates: options are always sorted
        # nearest-first, and every option already carries a best-effort link).
        best = options[0] if options else {}
        name, url = best.get("name") or "", best.get("url")
        db_reader.set_charge_location_name(c["id"], name, url)
        known.append((lat, lon, name, url))
        if name:
            named += 1
            log.info("charger locator: charge #%s → %s", c["id"], name)
    return named


# ── Backfill: recover a missing link on ALREADY-labelled charges ─────────────
# location_url didn't exist when older charges were labelled (and a name resolved
# from PUN alone never had one, PUN has no per-station page) — this is a one-time,
# user-triggered pass (Settings button), not part of the ongoing sweep/TTL. It NEVER
# touches location_name: only a name that still matches what's already saved gets a
# link attached, so a charge the user picked by hand from an ambiguity popup — or
# whose surroundings changed since — is never silently relabelled.

_BACKFILL_LIMIT = 200
_backfilling = False


def backfill_urls_now(limit: int = _BACKFILL_LIMIT) -> int:
    """Single-flight entry point for the Settings 'recover missing links' button."""
    global _backfilling
    with _lock:
        if _backfilling:
            return 0
        _backfilling = True
    try:
        return _backfill_body(limit)
    except Exception as exc:  # noqa: BLE001 — must never crash the background thread
        log.warning("charger locator: link backfill failed: %s", exc)
        return 0
    finally:
        with _lock:
            _backfilling = False


def _backfill_body(limit: int) -> int:
    cands = db_reader.get_labelled_charges_missing_url(limit)
    filled = 0
    called = False
    for c in cands:
        if called:
            time.sleep(1.0)  # Overpass etiquette between consecutive calls
        options, ok = find_station_candidates(c["latitude"], c["longitude"])
        called = True
        if not ok:  # Overpass down/rate-limited: stop here, leftovers retry next click
            log.info("charger locator: link backfill — Overpass unreachable, stopping")
            break
        match = next((o for o in options if o.get("name") == c["location_name"] and o.get("url")), None)
        if match:
            db_reader.set_charge_location_url(c["id"], match["url"])
            filled += 1
            log.info("charger locator: charge #%s → link recovered", c["id"])
    return filled
