"""🔎 'Compare similar trips' — trips on the SAME route as a given one, for comparing
efficiency/temperature/speed across the same commute over time (see the GitHub feature
request this answers: riri19's "Comparateur de trajets similaires"). Two-stage match: a
geohash bucket on start/end (fast, indexable) narrows candidates, then an ACTUAL route
overlap check (resampled-geohash Jaccard) confirms it's the same road — not just a trip
that happens to start/end nearby but took a different way (see test_excludes_a_detour_*).
"""
import asyncio

import pytest

import db as D
import db_reader


class _Req:
    """Minimal Starlette Request stand-in — the endpoint only reads the path param
    (already bound by FastAPI) and passes `request` straight to TemplateResponse."""
    headers: dict = {}


@pytest.fixture
def pdb(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    database = D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return database


def _line_points(lat1, lon1, lat2, lon2, n=20):
    return [(lat1 + (lat2 - lat1) * i / (n - 1), lon1 + (lon2 - lon1) * i / (n - 1)) for i in range(n)]


def _detour_points(lat1, lon1, lat2, lon2, n=20):
    """Same start/end as a straight line between the two points, but bulging out through a
    midpoint several km OFF to the side of the direct line (perpendicular offset, not just
    "further along the same bearing" — adding to both lat/lon naively can coincide with the
    endpoint itself when start/end differ by equal amounts, as A/B below do) — a real
    different road, not GPS jitter."""
    mid_lat, mid_lon = (lat1 + lat2) / 2, (lon1 + lon2) / 2
    dlat, dlon = lat2 - lat1, lon2 - lon1
    # Perpendicular to (dlat, dlon) is (-dlon, dlat), normalized-ish and scaled out ~0.05°.
    bulge_lat, bulge_lon = mid_lat - dlon * 0.5, mid_lon + dlat * 0.5
    half = n // 2
    return (_line_points(lat1, lon1, bulge_lat, bulge_lon, half) +
            _line_points(bulge_lat, bulge_lon, lat2, lon2, n - half))


def _seed_trip(pdb, tid, points, started="2026-07-01T10:00:00+00:00", ended="2026-07-01T10:30:00+00:00",
               efficiency=18.0, outside_temp_start_c=None, merged_into_id=None, reconstructed=0):
    import geohash
    start_lat, start_lon = points[0] if points else (None, None)
    end_lat, end_lon = points[-1] if points else (None, None)
    start_gh = geohash.encode(start_lat, start_lon) if start_lat is not None else None
    end_gh = geohash.encode(end_lat, end_lon) if end_lat is not None else None
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, start_lat, start_lon, end_lat, end_lon,"
        " start_geohash, end_geohash, efficiency_kwh_100km, outside_temp_start_c, merged_into_id,"
        " reconstructed, distance_km) VALUES (?,1,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, started, ended, start_lat, start_lon, end_lat, end_lon, start_gh, end_gh,
         efficiency, outside_temp_start_c, merged_into_id, reconstructed, 10.0))
    for i, (lat, lon) in enumerate(points):
        pdb._conn.execute(
            "INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude, speed_kmh)"
            " VALUES (?,?,?,?,?)",
            (tid, f"2026-07-01T10:{i:02d}:00+00:00", lat, lon, 50.0))
    pdb._conn.commit()


A = (45.000, 9.000)
B = (45.100, 9.100)   # ~11km away — same corner of the map either way


# ── _route_geohash_cells / _jaccard ──────────────────────────────────────────────

def test_route_geohash_cells_empty_for_no_positions(pdb):
    _seed_trip(pdb, 1, [])
    db = db_reader._get()
    assert db_reader._route_geohash_cells(db, 1) == set()


def test_jaccard_empty_sets_is_zero_not_a_crash(pdb):
    assert db_reader._jaccard(set(), set()) == 0.0
    assert db_reader._jaccard({"a"}, set()) == 0.0


def test_jaccard_identical_sets_is_one():
    s = {"u0n2h8", "u0n2h9"}
    assert db_reader._jaccard(s, s) == 1.0


# ── get_similar_trips ─────────────────────────────────────────────────────────────

def test_finds_a_trip_on_the_same_road(pdb):
    _seed_trip(pdb, 1, _line_points(*A, *B), started="2026-07-01T10:00:00+00:00")
    _seed_trip(pdb, 2, _line_points(*A, *B), started="2026-07-08T10:00:00+00:00")
    results = db_reader.get_similar_trips(1)
    assert [r["id"] for r in results] == [2]
    assert results[0]["overlap_pct"] > 90


def test_excludes_a_detour_with_the_same_start_and_end(pdb):
    """The core bug this whole feature has to avoid (riri19's own concern): two trips
    sharing start/end but a genuinely different road must NOT be reported as the same
    route just because a start/end + total-distance check alone would pass them."""
    _seed_trip(pdb, 1, _line_points(*A, *B))
    _seed_trip(pdb, 2, _detour_points(*A, *B))
    assert db_reader.get_similar_trips(1) == []


def test_excludes_reconstructed_trips_with_no_gps_trace(pdb):
    """A reconstructed trip (offline SoC/odometer jump) has start/end coords but no
    trip_positions — it can never be route-validated, so it must not show up as a
    confirmed match even if its endpoints happen to line up."""
    _seed_trip(pdb, 1, _line_points(*A, *B))
    _seed_trip(pdb, 2, [A, B], reconstructed=1)
    # Drop the seeded positions to simulate a real reconstructed trip's "no GPS trace":
    pdb._conn.execute("DELETE FROM trip_positions WHERE trip_id=2")
    pdb._conn.commit()
    assert db_reader.get_similar_trips(1) == []


def test_the_query_trip_itself_having_no_gps_trace_returns_nothing(pdb):
    _seed_trip(pdb, 1, _line_points(*A, *B))
    pdb._conn.execute("DELETE FROM trip_positions WHERE trip_id=1")
    pdb._conn.commit()
    assert db_reader.get_similar_trips(1) == []


def test_direction_matters_a_reversed_trip_does_not_match(pdb):
    """A return trip (same road, opposite direction) is a SEPARATE group by default —
    consumption/traffic often differ by direction (e.g. uphill vs downhill)."""
    _seed_trip(pdb, 1, _line_points(*A, *B))
    _seed_trip(pdb, 2, _line_points(*B, *A))   # same road, reversed
    assert db_reader.get_similar_trips(1) == []


def test_excludes_merged_children_only_parents_are_candidates(pdb):
    _seed_trip(pdb, 1, _line_points(*A, *B))
    _seed_trip(pdb, 2, _line_points(*A, *B), merged_into_id=99)   # a merged child, not a real candidate
    assert db_reader.get_similar_trips(1) == []


def test_result_carries_avg_speed_and_temperature_for_the_comparison_table(pdb):
    _seed_trip(pdb, 1, _line_points(*A, *B), outside_temp_start_c=18.0)
    _seed_trip(pdb, 2, _line_points(*A, *B), outside_temp_start_c=32.0)
    results = db_reader.get_similar_trips(1)
    assert results[0]["avg_speed_kmh"] == 50
    assert results[0]["outside_temp_start_c"] == 32.0


def test_sorted_oldest_first(pdb):
    _seed_trip(pdb, 1, _line_points(*A, *B), started="2026-07-15T10:00:00+00:00")
    _seed_trip(pdb, 2, _line_points(*A, *B), started="2026-07-01T10:00:00+00:00")
    _seed_trip(pdb, 3, _line_points(*A, *B), started="2026-07-08T10:00:00+00:00")
    results = db_reader.get_similar_trips(1)
    assert [r["id"] for r in results] == [2, 3]


def test_missing_trip_returns_empty_list(pdb):
    assert db_reader.get_similar_trips(999) == []


def test_a_trip_in_an_unrelated_corner_of_the_map_never_becomes_a_candidate(pdb):
    """Sanity check on the Stage-1 pre-filter itself: something hundreds of km away must
    never even be considered, regardless of the overlap threshold."""
    _seed_trip(pdb, 1, _line_points(*A, *B))
    far = (48.8566, 2.3522)   # Paris — nowhere near A/B
    far2 = (48.8600, 2.3600)
    _seed_trip(pdb, 2, _line_points(*far, *far2))
    assert db_reader.get_similar_trips(1) == []


# ── main.py endpoint ─────────────────────────────────────────────────────────────

def test_endpoint_renders_matches(pdb, monkeypatch):
    pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI test env)")
    import main
    monkeypatch.setattr(main.db_reader, "DB_PATH", db_reader.DB_PATH)
    monkeypatch.setattr(main.db_reader, "get_language", lambda: "en")
    _seed_trip(pdb, 1, _line_points(*A, *B), started="2026-07-01T10:00:00+00:00",
              ended="2026-07-01T10:30:00+00:00")
    _seed_trip(pdb, 2, _line_points(*A, *B), started="2026-07-08T10:00:00+00:00",
              ended="2026-07-08T10:30:00+00:00")

    resp = asyncio.run(main.trip_similar(_Req(), 1))

    body = resp.body.decode()
    assert 'href="trips/2"' in body
    assert "similar trips found" in body   # en.json's similar_count_suffix


def test_endpoint_empty_state_when_no_matches(pdb, monkeypatch):
    pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI test env)")
    import main
    monkeypatch.setattr(main.db_reader, "DB_PATH", db_reader.DB_PATH)
    monkeypatch.setattr(main.db_reader, "get_language", lambda: "en")
    _seed_trip(pdb, 1, _line_points(*A, *B))

    resp = asyncio.run(main.trip_similar(_Req(), 1))

    assert "No other trip found on this same route." in resp.body.decode()


def test_endpoint_redirects_for_a_missing_trip(pdb, monkeypatch):
    pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI test env)")
    import main
    monkeypatch.setattr(main.db_reader, "DB_PATH", db_reader.DB_PATH)

    resp = asyncio.run(main.trip_similar(_Req(), 999))

    assert resp.status_code in (302, 307)


# ── i18n coverage ────────────────────────────────────────────────────────────────

def test_similar_trip_strings_present_in_every_locale():
    import i18n
    for lang in ("en", "it", "de", "fr", "pl", "pt-PT"):
        t = i18n.get_t(lang)
        for key in ("similar_trips_btn", "similar_trips_title", "similar_count_suffix",
                    "similar_none", "similar_overlap", "back_to_trip"):
            assert t(key) != key, f"{lang} is missing {key}"
