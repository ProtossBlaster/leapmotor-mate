"""Geocoding helpers: the Navigation page, the 🧭 buttons and the addresses of trips (place_lookup).

Keyless by default (Photon forward / Nominatim reverse, both OpenStreetMap) so it
works out of the box. An optional provider + API key can be configured for better
street/house-number coverage — you are never locked to a single vendor:

    geocoder_provider = "geoapify" | "locationiq" | "tomtom" | ""   (empty = keyless)
    geocoder_key      = "<api key>"

Recommended: Geoapify (free, no credit card, includes OpenAddresses house numbers).
On any keyed-provider error reverse_geocode falls back to the keyless provider, because someone is
waiting for its answer; reverse_place asks the chosen provider alone, to be asked again later.
Standard library only (urllib) — no new dependency.
"""
import re
import json
import urllib.error
import urllib.request
import urllib.parse

_UA = "LeapMotorMate/1.0 (https://github.com/ProtossBlaster/leapmotor-mate)"

# Italian street/article noise words ignored when matching the street keywords.
_SKIP_WORDS = {
    "via", "corso", "piazza", "viale", "vicolo", "largo", "strada",
    "del", "della", "dei", "degli", "delle", "di", "il", "la", "lo",
    "le", "gli", "i", "un", "una", "al", "alla", "sul", "sulla",
}


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def _street_matches(input_address: str, result_street: str) -> bool:
    """At least every significant keyword of the input street must appear in the
    geocoded street, so 'Via Torino' doesn't match 'Via Torre'."""
    if not result_street:
        return False
    words = [w for w in re.findall(r"[a-zàèéìòùü]+", input_address.lower())
             if w not in _SKIP_WORDS and len(w) > 3]
    if not words:
        return True
    rl = result_street.lower()
    return all(w in rl for w in words)


# ── Keyed providers (forward) ────────────────────────────────────────────────

def _geoapify_geocode(query: str, key: str) -> dict | None:
    url = (f"https://api.geoapify.com/v1/geocode/search"
           f"?text={urllib.parse.quote(query)}&limit=5&apiKey={urllib.parse.quote(key)}")
    feats = _get(url).get("features", [])
    if not feats:
        return None
    p = feats[0]["properties"]
    return {"lat": p["lat"], "lon": p["lon"], "label": (p.get("formatted") or query)[:200]}


def _locationiq_geocode(query: str, key: str) -> dict | None:
    url = (f"https://us1.locationiq.com/v1/search"
           f"?key={urllib.parse.quote(key)}&q={urllib.parse.quote(query)}&format=json&limit=5")
    arr = _get(url)
    if not arr:
        return None
    r = arr[0]
    return {"lat": float(r["lat"]), "lon": float(r["lon"]), "label": (r.get("display_name") or query)[:200]}


def _tomtom_geocode(query: str, key: str) -> dict | None:
    url = (f"https://api.tomtom.com/search/2/geocode/{urllib.parse.quote(query)}.json"
           f"?key={urllib.parse.quote(key)}&limit=5")
    results = _get(url).get("results", [])
    if not results:
        return None
    pos = results[0]["position"]
    label = results[0].get("address", {}).get("freeformAddress", query)
    return {"lat": pos["lat"], "lon": pos["lon"], "label": label[:200]}


_FORWARD = {"geoapify": _geoapify_geocode, "locationiq": _locationiq_geocode, "tomtom": _tomtom_geocode}


# ── Keyed providers (reverse) ────────────────────────────────────────────────
# Each answers with the address in parts, under the same keys whoever the provider is (_place), or
# None when it has no address there.

def _first(d: dict, *keys):
    return next((d[k] for k in keys if d.get(k)), None)


def _place(name=None, house_number=None, road=None, suburb=None, locality=None, postcode=None,
           country_code=None, display_name=None) -> dict:
    """One address: the keys say what a value is, not which provider named it so. A name is the
    place's own (a shop, a station), never the street's."""
    return {"name": name if name != road else None, "house_number": house_number, "road": road,
            "suburb": suburb, "locality": locality, "postcode": postcode,
            "country_code": (country_code or "").lower() or None, "display_name": display_name}


# OpenStreetMap kinds whose name is the place a trip went to (a shop, a station), not a building or
# an area around it.
_OSM_NAMED = ("amenity", "shop", "tourism", "leisure", "office")


def _osm_place(data: dict) -> dict | None:
    """A Nominatim answer in parts (LocationIQ answers in the same shape); None for its "Unable to
    geocode". The address carries a named feature under its kind, e.g. "amenity": "Powerdot"."""
    if not data.get("display_name"):
        return None
    a = data.get("address") or {}
    return _place(name=_first(a, *_OSM_NAMED), house_number=a.get("house_number"), road=a.get("road"),
                  suburb=_first(a, "suburb", "neighbourhood", "quarter", "city_district"),
                  locality=_first(a, "city", "town", "village", "municipality", "hamlet"),
                  postcode=a.get("postcode"), country_code=a.get("country_code"),
                  display_name=data["display_name"])


def _geoapify_reverse(lat, lon, key) -> dict | None:
    url = (f"https://api.geoapify.com/v1/geocode/reverse"
           f"?lat={lat}&lon={lon}&apiKey={urllib.parse.quote(key)}")
    feats = _get(url).get("features", [])
    if not feats:
        return None
    p = feats[0]["properties"]
    return _place(name=p.get("name") if p.get("result_type") == "amenity" else None,
                  house_number=p.get("housenumber"), road=p.get("street"),
                  suburb=_first(p, "suburb", "district"),
                  locality=_first(p, "city", "town", "village", "municipality", "hamlet"),
                  postcode=p.get("postcode"), country_code=p.get("country_code"),
                  display_name=p.get("formatted"))


def _locationiq_reverse(lat, lon, key) -> dict | None:
    url = (f"https://us1.locationiq.com/v1/reverse"
           f"?key={urllib.parse.quote(key)}&lat={lat}&lon={lon}&format=json&addressdetails=1")
    try:
        return _osm_place(_get(url))
    except urllib.error.HTTPError as e:
        if e.code == 404:           # LocationIQ's "Unable to geocode": nothing there, not a failure
            return None
        raise


def _tomtom_reverse(lat, lon, key) -> dict | None:
    url = (f"https://api.tomtom.com/search/2/reverseGeocode/{lat},{lon}.json"
           f"?key={urllib.parse.quote(key)}")
    addrs = _get(url).get("addresses", [])
    if not addrs:
        return None
    a = addrs[0].get("address", {})
    return _place(house_number=a.get("streetNumber"), road=a.get("streetName"),
                  suburb=a.get("municipalitySubdivision"), locality=a.get("municipality"),
                  postcode=a.get("postalCode"), country_code=a.get("countryCode"),
                  display_name=a.get("freeformAddress"))


_REVERSE = {"geoapify": _geoapify_reverse, "locationiq": _locationiq_reverse, "tomtom": _tomtom_reverse}


# ── Keyless fallback (OpenStreetMap) ─────────────────────────────────────────

def _photon_geocode(address: str, city: str) -> dict | None:
    """Keyless forward-geocode via Photon (OSM). Ranks candidates and returns the
    best guess (confirmed by the user on the map before sending)."""
    query = " ".join(p for p in (address, city) if p)
    url = f"https://photon.komoot.io/api/?q={urllib.parse.quote_plus(query)}&limit=5"
    features = _get(url).get("features", [])
    if not features:
        return None
    cl = city.lower()

    def _score(f):
        p = f["properties"]
        s = 0
        if city and (p.get("city") or p.get("town") or "").lower() == cl:
            s += 2
        if _street_matches(address, p.get("street", "")):
            s += 1
        return s

    features.sort(key=_score, reverse=True)
    f = features[0]
    p = f["properties"]
    lon, lat = f["geometry"]["coordinates"][0], f["geometry"]["coordinates"][1]
    parts = [p.get("housenumber", ""),
             p.get("street", "") or p.get("name", ""),
             p.get("city", "") or p.get("town", "") or p.get("village", ""),
             p.get("state", ""), p.get("postcode", ""), p.get("country", "")]
    label = ", ".join(x for x in parts if x)
    return {"lat": lat, "lon": lon, "label": label[:200]}


def _nominatim_reverse(lat, lon) -> dict | None:
    url = (f"https://nominatim.openstreetmap.org/reverse?format=jsonv2"
           f"&lat={lat}&lon={lon}&zoom=18&addressdetails=1")
    return _osm_place(_get(url))


# ── Public API ───────────────────────────────────────────────────────────────

def geocode(address: str, city: str = "", provider: str = "", api_key: str | None = None) -> dict | None:
    """Forward-geocode 'address[, city]' → {"lat","lon","label"} or None.
    Uses the configured keyed provider when available (better coverage),
    otherwise the keyless Photon provider."""
    address = (address or "").strip()
    city = (city or "").strip()
    if not address:
        return None
    fn = _FORWARD.get((provider or "").lower())
    if fn and api_key:
        try:
            res = fn(", ".join(p for p in (address, city) if p), api_key)
            if res:
                return res
        except Exception:  # noqa: BLE001 — fall back to keyless on any provider error
            pass
    return _photon_geocode(address, city)


def reverse_geocode(lat: float, lon: float, provider: str = "", api_key: str | None = None) -> str | None:
    """Reverse-geocode coordinates to a human-readable address (keyed provider if
    configured, otherwise keyless Nominatim)."""
    if lat is None or lon is None:
        return None
    fn = _REVERSE.get((provider or "").lower())
    if fn and api_key:
        try:
            res = fn(lat, lon, api_key)
            if res and res["display_name"]:
                return res["display_name"]
        except Exception:  # noqa: BLE001
            pass
    res = _nominatim_reverse(lat, lon)
    return res["display_name"] if res else None


def lookup_provider(provider: str = "", api_key: str | None = None) -> str:
    """Who reverse_place asks: the keyed provider when it has its key, otherwise Nominatim."""
    p = (provider or "").lower()
    return p if p in _REVERSE and api_key else "nominatim"


def reverse_place(lat: float, lon: float, provider: str = "", api_key: str | None = None) -> dict | None:
    """The address at the coordinates in parts (see _place), from lookup_provider's provider and no
    other. None: it has nothing there. A network or HTTP error is raised, not answered by another."""
    who = lookup_provider(provider, api_key)
    return _nominatim_reverse(lat, lon) if who == "nominatim" else _REVERSE[who](lat, lon, api_key)
