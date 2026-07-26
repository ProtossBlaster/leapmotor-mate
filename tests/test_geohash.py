"""Pure-stdlib geohash encoder (web/geohash.py, poller/geohash.py — two small self-contained
copies, see poller/geohash.py's own docstring for why they aren't a shared import) — the
building block behind the similar-trips comparator's candidate pre-filter and route-overlap
check (see test_similar_trips.py)."""
import geohash as web_geohash


def test_encode_matches_known_reference_value():
    """geohash.org's own worked example: (37.8324, 112.5584) -> ww8p1r4t8."""
    assert web_geohash.encode(37.8324, 112.5584, 9) == "ww8p1r4t8"


def test_encode_san_francisco_reference():
    assert web_geohash.encode(37.7749, -122.4194, 5) == "9q8yy"


def test_encode_is_deterministic_and_precision_prefixes():
    """A shorter precision is always the PREFIX of a longer one at the same point —
    that's what makes the neighbor-cell pre-filter meaningful at a fixed precision."""
    long = web_geohash.encode(45.4642, 9.1900, 9)
    short = web_geohash.encode(45.4642, 9.1900, 5)
    assert long.startswith(short)


def _load_poller_geohash():
    """Load poller/geohash.py under a name distinct from the web copy already cached in
    sys.modules as "geohash" (conftest.py puts both dirs on sys.path, web last/winning) —
    a plain `import geohash` here would just return the SAME web module, silently testing
    it against itself instead of poller's independent copy."""
    import importlib.util
    import pathlib
    path = pathlib.Path(__file__).resolve().parent.parent / "poller" / "geohash.py"
    spec = importlib.util.spec_from_file_location("poller_geohash_module", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_poller_and_web_geohash_agree():
    """The stored trips.start_geohash/end_geohash (written poller-side) must be encoded
    IDENTICALLY to what the web side computes when querying for candidates — otherwise the
    fast pre-filter would silently never match anything."""
    poller_geohash = _load_poller_geohash()
    for lat, lon in [(37.7749, -122.4194), (45.4642, 9.19), (-33.8688, 151.2093), (0.0, 0.0)]:
        assert poller_geohash.encode(lat, lon) == web_geohash.encode(lat, lon)


def test_neighbors_returns_eight_distinct_cells_excluding_self():
    gh = web_geohash.encode(45.4642, 9.1900, 7)
    n = web_geohash.neighbors(gh)
    assert len(n) == 8
    assert gh not in n


def test_neighbors_are_actually_adjacent():
    """A point just over the boundary of a cell must land in one of that cell's
    neighbors — this is the whole point of the neighbor pre-filter (a route sitting right
    at a cell edge in one trip and a few metres over it in another must still match). Uses
    a high precision (8, ≈19m cells) so a ~1m nudge is guaranteed to stay within one hop,
    regardless of where in the cell the base point happens to sit."""
    gh = web_geohash.encode(45.0, 9.0, 8)
    n = web_geohash.neighbors(gh)
    for dlat, dlon in [(0.00001, 0), (-0.00001, 0), (0, 0.00001), (0, -0.00001)]:
        nearby = web_geohash.encode(45.0 + dlat, 9.0 + dlon, 8)
        assert nearby == gh or nearby in n


def test_neighbors_wraps_the_antimeridian():
    """Shouldn't raise or produce an out-of-range longitude near ±180°."""
    gh = web_geohash.encode(35.0, 179.99, 6)
    n = web_geohash.neighbors(gh)
    assert len(n) == 8
