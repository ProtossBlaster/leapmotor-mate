"""Pure-stdlib geohash — no external dependency, consistent with web/geocode.py and
web/charger_locator.py's own stdlib-only HTTP calls. Standard 32-character base32
alphabet (excludes a, i, l, o — visually confusable with 1/0). Used by the similar-trips
comparator (db_reader.get_similar_trips) both for the stored start/end bucket (a fast,
indexable pre-filter) and for the route-overlap check (geohash cells along the resampled
GPS trace, compared between two trips)."""
_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"
_DECODE = {c: i for i, c in enumerate(_BASE32)}


def encode(lat: float, lon: float, precision: int = 7) -> str:
    """Interleave longitude/latitude bits (longitude first), narrowing a bounding box,
    5 bits per base32 character. Precision 7 ≈ a 150m × 150m cell — tight enough to tell
    apart two roads a block apart, loose enough to survive ordinary GPS jitter."""
    lat_lo, lat_hi = -90.0, 90.0
    lon_lo, lon_hi = -180.0, 180.0
    out = []
    bit = 0
    ch = 0
    even = True   # longitude bit first
    while len(out) < precision:
        if even:
            mid = (lon_lo + lon_hi) / 2
            if lon >= mid:
                ch |= 1 << (4 - bit)
                lon_lo = mid
            else:
                lon_hi = mid
        else:
            mid = (lat_lo + lat_hi) / 2
            if lat >= mid:
                ch |= 1 << (4 - bit)
                lat_lo = mid
            else:
                lat_hi = mid
        even = not even
        if bit < 4:
            bit += 1
        else:
            out.append(_BASE32[ch])
            bit = 0
            ch = 0
    return "".join(out)


def _bounds(gh: str):
    """(lat_range, lon_range) the geohash string covers — the decode half of encode()."""
    lat_lo, lat_hi = -90.0, 90.0
    lon_lo, lon_hi = -180.0, 180.0
    even = True
    for c in gh:
        bits = _DECODE[c]
        for mask in (16, 8, 4, 2, 1):
            set_bit = bool(bits & mask)
            if even:
                mid = (lon_lo + lon_hi) / 2
                if set_bit:
                    lon_lo = mid
                else:
                    lon_hi = mid
            else:
                mid = (lat_lo + lat_hi) / 2
                if set_bit:
                    lat_lo = mid
                else:
                    lat_hi = mid
            even = not even
    return (lat_lo, lat_hi), (lon_lo, lon_hi)


def neighbors(gh: str) -> set:
    """The 8 geohash cells adjacent to `gh`, at the same precision — so a route that sits
    right at a cell boundary in one trip but a few metres over it in another isn't missed
    by an exact-match `WHERE start_geohash = ...`."""
    precision = len(gh)
    (lat_lo, lat_hi), (lon_lo, lon_hi) = _bounds(gh)
    lat_c, lon_c = (lat_lo + lat_hi) / 2, (lon_lo + lon_hi) / 2
    lat_err, lon_err = (lat_hi - lat_lo), (lon_hi - lon_lo)
    out = set()
    for dlat in (-1, 0, 1):
        for dlon in (-1, 0, 1):
            if dlat == 0 and dlon == 0:
                continue
            nlat = max(-90.0, min(90.0, lat_c + dlat * lat_err))
            nlon = lon_c + dlon * lon_err
            nlon = ((nlon + 180.0) % 360.0) - 180.0   # wrap the antimeridian
            out.add(encode(nlat, nlon, precision))
    return out
