"""Pure-stdlib geohash encoder — poller-side twin of web/geohash.py (kept as two small,
self-contained copies rather than a cross-import: poller and web are separately
deployable process trees, and this is a stable, standard algorithm, not shared business
logic). Only `encode` is needed here — trips.start_geohash/end_geohash are WRITTEN at
trip creation/finalize (this module); the candidate lookup and the neighbor-cell
pre-filter that READ them live entirely on the web side (web/db_reader.py,
get_similar_trips), which is also where the route-overlap validation happens."""
_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def encode(lat: float, lon: float, precision: int = 7) -> str:
    """Interleave longitude/latitude bits (longitude first), narrowing a bounding box,
    5 bits per base32 character. Precision 7 ≈ a 150m × 150m cell."""
    lat_lo, lat_hi = -90.0, 90.0
    lon_lo, lon_hi = -180.0, 180.0
    out = []
    bit = 0
    ch = 0
    even = True
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
