"""Per-point altitude on the trip-profile chart: _interpolate_elevation (pure function) and the
end-to-end flow from a sparse enrichment sweep through get_trip_detail.

elevation_enrich only queries Open-Elevation for a DOWNSAMPLED subset of a trip's GPS points (see
test_elevation_enrich.py) — the chart needs the full-resolution track, so db_reader fills the gaps
between two known samples by linear interpolation over elapsed time. Altitude changes physically
continuously (unlike SoC/speed, which can have real jumps), so this is legitimate — the same
technique consumer route-elevation profiles use.
"""
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI test env)")
import db as D
import db_reader as DR


def _pt(t, elevation_m=None, speed=50.0, soc=70.0, lat=45.0, lon=9.0):
    return {"recorded_at": t.isoformat(), "elevation_m": elevation_m,
            "speed_kmh": speed, "soc": soc, "latitude": lat, "longitude": lon}


def _mins(base, m):
    return base + timedelta(minutes=m)


# ── _interpolate_elevation: pure function ───────────────────────────────────────────────────

def test_fills_gap_between_two_known_points():
    base = datetime(2026, 7, 19, 10, 0, tzinfo=timezone.utc)
    pts = [_pt(_mins(base, 0), 100.0), _pt(_mins(base, 1)), _pt(_mins(base, 2)), _pt(_mins(base, 4), 140.0)]
    out = DR._interpolate_elevation(pts)
    assert out[1]["elevation_m"] == pytest.approx(110.0)   # 1/4 of the way: 100 + 0.25*40
    assert out[2]["elevation_m"] == pytest.approx(120.0)   # 2/4 of the way


def test_leading_and_trailing_gaps_stay_none():
    """No known value on ONE side of the gap → never extrapolated."""
    base = datetime(2026, 7, 19, 10, 0, tzinfo=timezone.utc)
    pts = [_pt(_mins(base, 0)), _pt(_mins(base, 1), 100.0), _pt(_mins(base, 2))]
    out = DR._interpolate_elevation(pts)
    assert out[0]["elevation_m"] is None
    assert out[2]["elevation_m"] is None


def test_fewer_than_two_known_points_is_a_noop():
    base = datetime(2026, 7, 19, 10, 0, tzinfo=timezone.utc)
    pts = [_pt(_mins(base, 0)), _pt(_mins(base, 1), 100.0), _pt(_mins(base, 2))]
    out = DR._interpolate_elevation(pts[:2])   # only one known value in the whole list
    assert out[0]["elevation_m"] is None


def test_multiple_known_segments_interpolate_independently():
    base = datetime(2026, 7, 19, 10, 0, tzinfo=timezone.utc)
    pts = [_pt(_mins(base, 0), 100.0), _pt(_mins(base, 1)), _pt(_mins(base, 2), 120.0),
           _pt(_mins(base, 3)), _pt(_mins(base, 4), 80.0)]
    out = DR._interpolate_elevation(pts)
    assert out[1]["elevation_m"] == pytest.approx(110.0)
    assert out[3]["elevation_m"] == pytest.approx(100.0)


# ── end-to-end: sparse enrichment → get_trip_detail returns an interpolated track ───────────

def test_get_trip_detail_interpolates_the_sparse_track(tmp_path, monkeypatch):
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(DR, "DB_PATH", str(tmp_path / "t.db"))
    now = datetime.now(timezone.utc)
    started = now - timedelta(minutes=20)
    ended = now - timedelta(minutes=5)
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km) "
        "VALUES (1, 1, ?, ?, 10.0)", (started.isoformat(), ended.isoformat()))
    ids = []
    for i in range(5):
        cur = pdb._conn.execute(
            "INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude, speed_kmh, soc) "
            "VALUES (1, ?, ?, ?, ?, ?)",
            ((started + timedelta(minutes=i)).isoformat(), 45.0 + i * 0.01, 9.0 + i * 0.01, 50.0, 70.0 - i))
        ids.append(cur.lastrowid)
    pdb._conn.commit()

    # Only the enrichment sweep's downsampled subset (here: first and last point) gets a real
    # Open-Elevation value — mirrors what elevation_enrich.py actually persists.
    DR.store_point_elevations({ids[0]: 200.0, ids[4]: 240.0})

    d = DR.get_trip_detail(1)
    elevs = [p["elevation_m"] for p in d["positions"]]
    assert elevs[0] == 200.0
    assert elevs[4] == 240.0
    assert elevs[1] == pytest.approx(210.0)
    assert elevs[2] == pytest.approx(220.0)
    assert elevs[3] == pytest.approx(230.0)


def test_elevation_profile_available_true_once_any_point_has_it(tmp_path, monkeypatch):
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(DR, "DB_PATH", str(tmp_path / "t.db"))
    now = datetime.now(timezone.utc)
    started, ended = now - timedelta(minutes=10), now - timedelta(minutes=5)
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km) "
        "VALUES (1, 1, ?, ?, 5.0)", (started.isoformat(), ended.isoformat()))
    for i in range(3):
        pdb._conn.execute(
            "INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude, speed_kmh, soc, elevation_m) "
            "VALUES (1, ?, ?, ?, ?, ?, ?)",
            ((started + timedelta(minutes=i)).isoformat(), 45.0 + i * 0.01, 9.0, 50.0, 70.0,
             100.0 if i == 0 else None))
    pdb._conn.commit()
    assert DR.get_trip_detail(1)["elevation_profile_available"] is True


def test_elevation_profile_unavailable_when_trip_predates_per_point_storage(tmp_path, monkeypatch):
    """Regression: a trip enriched BEFORE per-point storage existed has elevation_gain_m/loss_m
    set but every trip_positions.elevation_m still NULL — the aggregate being present must not
    hide this from the chart (or the 'Calculate elevation' button, see trip_detail.html)."""
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(DR, "DB_PATH", str(tmp_path / "t.db"))
    now = datetime.now(timezone.utc)
    started, ended = now - timedelta(minutes=10), now - timedelta(minutes=5)
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, "
        "elevation_gain_m, elevation_loss_m, elev_tried, elev_done) "
        "VALUES (1, 1, ?, ?, 5.0, 685, 611, 1, 1)", (started.isoformat(), ended.isoformat()))
    for i in range(3):
        pdb._conn.execute(
            "INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude, speed_kmh, soc) "
            "VALUES (1, ?, ?, ?, ?, ?)",
            ((started + timedelta(minutes=i)).isoformat(), 45.0 + i * 0.01, 9.0, 50.0, 70.0))
    pdb._conn.commit()
    d = DR.get_trip_detail(1)
    assert d["elevation_gain_m"] == 685
    assert d["elevation_profile_available"] is False


def test_get_trip_detail_leaves_elevation_none_when_never_enriched(tmp_path, monkeypatch):
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(DR, "DB_PATH", str(tmp_path / "t.db"))
    now = datetime.now(timezone.utc)
    started = now - timedelta(minutes=10)
    ended = now - timedelta(minutes=5)
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km) "
        "VALUES (1, 1, ?, ?, 5.0)", (started.isoformat(), ended.isoformat()))
    for i in range(3):
        pdb._conn.execute(
            "INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude, speed_kmh, soc) "
            "VALUES (1, ?, ?, ?, ?, ?)",
            ((started + timedelta(minutes=i)).isoformat(), 45.0 + i * 0.01, 9.0, 50.0, 70.0))
    pdb._conn.commit()
    d = DR.get_trip_detail(1)
    assert all(p["elevation_m"] is None for p in d["positions"])
