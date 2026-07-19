"""Public EV charging stations from OpenStreetMap (Overpass API).

Two consumers:
  - 📍 charge labels: a background sweep names the public charging station of each
    closed charge (shown on the Charges list and the Overview "last charge" card).
    Opt-in via the `charger_locator` setting — home charges are never looked up, and
    history backfills too (charges already store their GPS position since v1.0).
    When the winning station's own data unambiguously says AC or DC (+ kW), the sweep
    also auto-fills the charge's AC/FAST/HPC type — skipping the manual "confirm this
    charge" step — but only on a charge still unconfirmed (`location_type IS NULL`),
    and never on a mixed AC/DC site (knowing the site exists doesn't say which cable
    this session used).
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


def _socket_current_kw(tags: dict):
    """(current, kw) from the community socket:*/maxoutput tags (sparsely mapped —
    '' / 0.0 when nothing usable). AC/DC is inferred: CCS/CHAdeMO sockets → DC,
    Type 2 → AC, and a max output ≥ 50 kW implies DC even when the socket tags
    are missing."""
    kw = 0.0
    for k, v in tags.items():
        if k == "maxoutput" or (k.startswith("socket:") and k.endswith(":output")):
            m = re.search(r"\d+(?:[.,]\d+)?", str(v))
            if m:
                kw = max(kw, float(m.group(0).replace(",", ".")))
    dc = any(_has(tags, k) for k in _DC_SOCKETS) or kw >= 50
    ac = any(_has(tags, k) for k in _AC_SOCKETS)
    current = "AC/DC" if (ac and dc) else "DC" if dc else "AC" if ac else ""
    return current, kw


def _socket_info(tags: dict) -> str:
    """Best-effort 'DC · CCS · 300 kW' summary from the community socket:* /
    maxoutput tags (sparsely mapped — empty string when nothing usable)."""
    kinds = []
    for key, label in _SOCKETS:
        if _has(tags, key) and label not in kinds:
            kinds.append(label)
    current, kw = _socket_current_kw(tags)
    parts = ([current] if current else []) + kinds
    if kw:
        parts.append(f"{_fmt_kw(kw)} kW")
    return " · ".join(parts)


def _classify_current(current: str, kw: float):
    """Best-effort AC/FAST/HPC guess from a station's current type + kW, for the 📍
    sweep's auto-confirm — None when ambiguous (a mixed AC/DC site, or the source
    didn't say): the app's 4-way HOME/AC/FAST/HPC split needs to know which cable a
    session actually used, and a mixed site alone can't tell you that. The 80 kW
    DC/HPC cutoff mirrors `db_reader.auto_location_type`, for consistency."""
    if current == "AC":
        return "AC"
    if current == "DC":
        return "HPC" if kw and kw > 80 else "FAST"
    return None


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


def find_station_name(lat: float, lon: float):
    """(name | None, location_type | None, ok). ok=False → transient error, worth
    retrying later; ok=True with name=None → genuinely nothing usable within
    _LABEL_RADIUS_M. Union of OSM and (when a key is set) Open Charge Map, picking
    the NEAREST entry that HAS a name: Overpass returns by id (not distance) and
    unnamed stall nodes commonly sit next to the named site POI.
    location_type is `_classify_current`'s AC/FAST/HPC guess from that SAME winning
    candidate's own current/kW data — None when it didn't say, or said something
    ambiguous."""
    if not lat or not lon:
        return None, None, True
    els = _query(lat, lon, _LABEL_RADIUS_M, 10)
    ocm = _ocm_stations(lat, lon, _LABEL_RADIUS_M, 5)
    pun = _pun_stations(lat, lon, _LABEL_RADIUS_M, 20)
    cands = []
    for e in (els or []):
        la, lo = _coord(e)
        if la is None:
            continue
        tags = e.get("tags", {})
        current, kw = _socket_current_kw(tags)
        cands.append((_dist_m(lat, lon, la, lo), _label(tags), current, kw))
    cands.extend((s["dist_m"], s["name"], s.get("current", ""), s.get("kw", 0.0))
                 for s in (ocm or []))
    cands.extend((s["dist_m"], s["name"], s.get("current", ""), s.get("kw", 0.0))
                 for s in (pun or []))
    cands.sort(key=lambda c: c[0])
    for _d, label, current, kw in cands:
        if label:
            return label, _classify_current(current, kw), True
    # No name found: that verdict is final only if at least one source actually
    # answered — if every source we tried errored, stay NULL and retry next sweep.
    tried = [els, ocm if _ocm_key() else None, pun if _in_italy(lat, lon) else None]
    answered = any(r is not None for r in tried)
    return None, None, answered


def find_nearby(lat: float, lon: float, radius_m: int, limit: int = 25, name_filter: str = ""):
    """Stations around a point for the Navigation page, nearest first, or None on
    a transient error. Unnamed stations are kept (a real charger you can drive to —
    the UI shows a generic label). Overpass cannot sort by distance and `out N` caps
    in id order — fetch generously so the post-sort really holds the nearest ones
    (PUN does its own shrink-to-fit; OCM already returns nearest-first).
    A site's individual charge-point columns dedupe into one pin (same label within
    80 m, anonymous right next to a kept pin, or the same station from the other
    source under a slightly different name) — keeping the nearest, richest info."""
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
        out.append({"name": _label(tags), "lat": la, "lon": lo,
                    "dist_m": int(_dist_m(lat, lon, la, lo)),
                    "info": _socket_info(tags)})
    out.extend(ocm or [])
    out.extend(tom or [])
    out.extend(pun or [])
    if name_filter:   # operator filter: keep only matching names (PUN was already narrowed)
        nf = name_filter.strip().lower()
        out = [s for s in out if nf in (s["name"] or "").lower()]
    out.sort(key=lambda s: s["dist_m"])
    kept = []
    for s in out:
        dup = next((k for k in kept
                    if _dist_m(s["lat"], s["lon"], k["lat"], k["lon"]) <=
                    (80 if s["name"] == k["name"] else 40 if not s["name"] else 25)), None)
        if dup is None:
            kept.append(s)
        else:
            if s["info"] and not dup["info"]:
                dup["info"] = s["info"]   # stall/OCM/PUN often carry data the site lacks
            if s["name"] and not dup["name"]:
                dup["name"] = s["name"]
            if s.get("avail") and not dup.get("avail"):
                dup["avail"] = s["avail"]   # keep PUN live status even if it was the dup
        if len(kept) >= limit:
            break
    return kept


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
        current = "AC/DC" if len(cur) > 1 else (cur[0] if cur else "")
        parts = (["/".join(cur)] if cur else []) + ([f"{_fmt_kw(kw)} kW"] if kw else [])
        name = ((a.get("Title") or "").strip()
                or ((p.get("OperatorInfo") or {}).get("Title") or "").strip() or None)
        out.append({"name": name, "lat": la, "lon": lo, "dist_m": int(d),
                    "info": " · ".join(parts), "current": current, "kw": kw})
    return out


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
        out.append({"name": name, "lat": la, "lon": lo, "dist_m": dist_m, "info": info})
    return out


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
                    "info": info, "avail": f"{avail}/{len(conns)}",
                    "current": cur, "kw": kw})
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
        reuse = next((n for la, lo, n in known
                      if _dist_m(lat, lon, la, lo) <= _REUSE_RADIUS_M), None)
        if reuse is not None:
            db_reader.set_charge_location_name(c["id"], reuse)
            named += bool(reuse)
            continue
        if called:
            time.sleep(1.0)  # Overpass etiquette between consecutive calls
        name, ctype, ok = find_station_name(lat, lon)
        called = True
        if not ok:  # Overpass down/rate-limited: stop here, leftovers retry next sweep
            log.info("charger locator: Overpass unreachable — will retry next sweep")
            break
        value = name or ""  # '' = resolved as "no station here", never re-asked
        db_reader.set_charge_location_name(c["id"], value)
        known.append((lat, lon, value))
        if name:
            named += 1
            log.info("charger locator: charge #%s → %s", c["id"], name)
            # Auto-confirm AC/FAST/HPC from the station's own data, but only when the
            # charge is still unconfirmed — never override a manual pick (MANUAL/FREE/
            # a type the user already corrected), even though such charges can still be
            # 📍-lookup candidates (naming and confirming are independent).
            if ctype and c.get("location_type") is None:
                db_reader.update_charge_type(c["id"], ctype)
                log.info("charger locator: charge #%s auto-confirmed → %s", c["id"], ctype)
    return named
