"""Per-trip elevation gain/loss (dislivello), from the GPS track looked up against
Open-Elevation. No network in tests — `fetch_elevations` is monkeypatched to a scripted
sequence of altitudes.
"""
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI test env)")
import db as D            # poller schema (trips/trip_positions + migrations)
import db_reader
import elevation_enrich as EE


# ── compute_gain_loss: pure function ────────────────────────────────────────────────────────

def test_pure_climb():
    assert EE.compute_gain_loss([100, 110, 130]) == (30, 0)


def test_pure_descent():
    assert EE.compute_gain_loss([130, 110, 100]) == (0, 30)


def test_up_and_down():
    assert EE.compute_gain_loss([100, 150, 90]) == (50, 60)


def test_noise_below_threshold_ignored():
    # Each step is 2m, under the default 3m threshold — a flat road shouldn't accumulate dislivello.
    assert EE.compute_gain_loss([100, 102, 104, 102, 100]) == (0, 0)


def test_custom_threshold():
    assert EE.compute_gain_loss([100, 105, 100], noise_threshold_m=1.0) == (5, 5)


def test_fewer_than_two_points():
    assert EE.compute_gain_loss([]) == (0, 0)
    assert EE.compute_gain_loss([100]) == (0, 0)


# ── sweep: DB-backed, fetch_elevations stubbed ──────────────────────────────────────────────

def _setup(tmp_path, monkeypatch, n_points=5):
    """One finalized trip with a straight-line GPS track, feature enabled."""
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    now = datetime.now(timezone.utc)
    ended = now - timedelta(minutes=5)
    started = ended - timedelta(minutes=10)
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km) "
        "VALUES (1, 1, ?, ?, 5.0)",
        (started.isoformat(), ended.isoformat()))
    for i in range(n_points):
        pdb._conn.execute(
            "INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude) "
            "VALUES (1, ?, ?, ?)",
            ((started + timedelta(minutes=i)).isoformat(), 45.0 + i * 0.001, 9.0 + i * 0.001))
    pdb._conn.commit()
    db_reader.set_setting("elevation_enabled", "1")
    return pdb


def _row(pdb):
    return pdb._conn.execute(
        "SELECT elevation_gain_m, elevation_loss_m, elev_tried, elev_done FROM trips WHERE id=1"
    ).fetchone()


def test_sweep_stores_gain_loss(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(EE, "fetch_elevations", lambda pts: [100, 110, 120, 115, 105])
    EE._sweep_now()
    gain, loss, tried, done = _row(pdb)
    assert (gain, loss, tried, done) == (20, 15, 1, 1)


def test_sweep_also_persists_per_point_altitude(tmp_path, monkeypatch):
    """The trip-profile chart's altitude line (see test_elevation_profile.py) reads this back."""
    pdb = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(EE, "fetch_elevations", lambda pts: [100, 110, 120, 115, 105])
    EE._sweep_now()
    rows = pdb._conn.execute(
        "SELECT elevation_m FROM trip_positions WHERE trip_id=1 ORDER BY recorded_at").fetchall()
    assert [r[0] for r in rows] == [100, 110, 120, 115, 105]


def test_sweep_miss_bumps_tried_without_marking_done(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(EE, "fetch_elevations", lambda pts: None)
    EE._sweep_now()
    gain, loss, tried, done = _row(pdb)
    assert (gain, loss, done) == (None, None, 0)
    assert tried == 1


def test_sweep_gives_up_after_retry_ceiling(tmp_path, monkeypatch):
    """get_trips_needing_elevation caps retries at elev_tried < 3 — after 3 misses the trip is no
    longer selected, so a 4th sweep leaves elev_tried at 3 (enrichment abandoned, shows '—' forever)."""
    pdb = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(EE, "fetch_elevations", lambda pts: None)
    for _ in range(4):
        EE._sweep_now()
    gain, loss, tried, done = _row(pdb)
    assert (gain, loss, done, tried) == (None, None, 0, 3)


def test_sweep_skips_trip_with_single_point(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch, n_points=1)
    calls = []
    monkeypatch.setattr(EE, "fetch_elevations", lambda pts: calls.append(pts) or [100])
    EE._sweep_now()
    assert calls == []  # never even called Open-Elevation
    gain, loss, tried, done = _row(pdb)
    assert (gain, loss, tried, done) == (None, None, 1, 0)


# ── get_trip_points_for_elevation: shares the frozen-telemetry filter with the trip chart ──────
# (see tests/test_frozen_telemetry.py) — a long cloud-cache freeze must not waste the downsample
# budget on repeated duplicate coordinates before the GPS track reaches Open-Elevation.

def test_frozen_run_is_dropped_before_elevation_lookup(tmp_path, monkeypatch):
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    now = datetime.now(timezone.utc)
    started = now - timedelta(minutes=30)
    ended = now - timedelta(minutes=5)
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km) "
        "VALUES (1, 1, ?, ?, 20.0)", (started.isoformat(), ended.isoformat()))
    # 2 real points, then an 18-minute frozen plateau (identical speed/soc/position), then 2 more
    # real points — mirrors the field scenario (screenshot 2 of the frozen-telemetry fix).
    pdb._conn.execute(
        "INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude, speed_kmh, soc) "
        "VALUES (1, ?, 45.0, 9.0, 55.0, 60.0)", (started.isoformat(),))
    pdb._conn.execute(
        "INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude, speed_kmh, soc) "
        "VALUES (1, ?, 45.01, 9.01, 60.0, 59.5)", ((started + timedelta(minutes=1)).isoformat(),))
    frozen_start = started + timedelta(minutes=2)
    for i in range(18):
        pdb._conn.execute(
            "INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude, speed_kmh, soc) "
            "VALUES (1, ?, 45.05, 9.05, 100.0, 55.0)", ((frozen_start + timedelta(minutes=i)).isoformat(),))
    resume = frozen_start + timedelta(minutes=18)
    pdb._conn.execute(
        "INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude, speed_kmh, soc) "
        "VALUES (1, ?, 45.20, 9.20, 70.0, 48.0)", (resume.isoformat(),))
    pdb._conn.execute(
        "INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude, speed_kmh, soc) "
        "VALUES (1, ?, 45.21, 9.21, 71.0, 47.8)", ((resume + timedelta(minutes=1)).isoformat(),))
    pdb._conn.commit()
    pts = db_reader.get_trip_points_for_elevation(1)
    # 22 rows recorded, but 17 of the 18 frozen duplicates are dropped — only the anchor survives.
    assert len(pts) == 5
    coords = [(round(p["latitude"], 2), round(p["longitude"], 2)) for p in pts]
    assert coords == [(45.0, 9.0), (45.01, 9.01), (45.05, 9.05), (45.2, 9.2), (45.21, 9.21)]


# ── recalc_trip: the manual "Calculate elevation" button ────────────────────────────────────

def test_recalc_trip_ignores_the_retry_ceiling(tmp_path, monkeypatch):
    """A trip the background sweep already gave up on (elev_tried past the ceiling) is invisible
    to get_trips_needing_elevation, but the manual button must still be able to recover it."""
    pdb = _setup(tmp_path, monkeypatch)
    pdb._conn.execute("UPDATE trips SET elev_tried = 5 WHERE id=1")
    pdb._conn.commit()
    monkeypatch.setattr(EE, "fetch_elevations", lambda pts: [100, 110, 120, 115, 105])
    res = EE.recalc_trip(1)
    assert res == {"ok": True}
    gain, loss, tried, done = _row(pdb)
    assert (gain, loss, done) == (20, 15, 1)


def test_recalc_trip_recovers_pre_feature_trip(tmp_path, monkeypatch):
    """A trip recorded before this feature existed has elev_tried/elev_done at their column
    defaults (0) — recalc_trip must enrich it exactly like a fresh trip."""
    pdb = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(EE, "fetch_elevations", lambda pts: [200, 190])
    res = EE.recalc_trip(1)
    assert res == {"ok": True}
    gain, loss, tried, done = _row(pdb)
    assert (gain, loss, done) == (0, 10, 1)


def test_recalc_trip_also_persists_per_point_altitude(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(EE, "fetch_elevations", lambda pts: [100, 110, 120, 115, 105])
    EE.recalc_trip(1)
    rows = pdb._conn.execute(
        "SELECT elevation_m FROM trip_positions WHERE trip_id=1 ORDER BY recorded_at").fetchall()
    assert [r[0] for r in rows] == [100, 110, 120, 115, 105]


def test_recalc_trip_no_data_reports_reason(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(EE, "fetch_elevations", lambda pts: None)
    assert EE.recalc_trip(1) == {"ok": False, "reason": "no_data"}


def test_recalc_trip_covers_every_merged_segment(tmp_path, monkeypatch):
    """Elevation is per-segment (like regen_kwh) — recalculating a merged group's parent must
    also enrich its children, not just the parent row."""
    pdb = _setup(tmp_path, monkeypatch, n_points=3)
    now = datetime.now(timezone.utc)
    ended = now - timedelta(minutes=1)
    started = ended - timedelta(minutes=5)
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, merged_into_id) "
        "VALUES (2, 1, ?, ?, 3.0, 1)", (started.isoformat(), ended.isoformat()))
    for i in range(3):
        pdb._conn.execute(
            "INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude) VALUES (2, ?, ?, ?)",
            ((started + timedelta(minutes=i)).isoformat(), 46.0 + i * 0.001, 10.0 + i * 0.001))
    pdb._conn.commit()
    monkeypatch.setattr(EE, "fetch_elevations", lambda pts: [50, 60, 55])
    res = EE.recalc_trip(1)   # called on the parent
    assert res == {"ok": True}
    seg1 = tuple(pdb._conn.execute(
        "SELECT elevation_gain_m, elevation_loss_m FROM trips WHERE id=1").fetchone())
    seg2 = tuple(pdb._conn.execute(
        "SELECT elevation_gain_m, elevation_loss_m FROM trips WHERE id=2").fetchone())
    assert seg1 == (10, 5) and seg2 == (10, 5)
