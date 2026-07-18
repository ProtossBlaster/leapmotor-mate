"""Frozen-telemetry detection in the trip-profile chart data (_filter_frozen_telemetry).

poller/recorder.py's stale-frame guard (#128, see test_stale_frame_guard.py) catches the cloud
re-serving a byte-identical frame — same raw timestamp. But if the cloud instead re-serves a CACHED
snapshot wrapped in a fresh timestamp each poll (the payload underneath — speed/SoC/GPS — frozen,
only the wrapper timestamp moving), that guard never fires: every poll looks like a new frame. The
symptom users see is exactly this: a flat speed plateau for several real minutes, followed by an
abrupt SoC/speed snap back to the true value once the cloud catches up.

_filter_frozen_telemetry runs AFTER the fact (read time, in get_trip_detail), so it also cleans up
trips already recorded with the live hiccup, not just future ones.
"""
from datetime import datetime, timedelta, timezone

import db_reader as DR


def _pt(t, speed, soc, lat=45.0, lon=9.0):
    return {"recorded_at": t.isoformat(), "latitude": lat, "longitude": lon,
            "speed_kmh": speed, "soc": soc}


def _mins(base, m):
    return base + timedelta(minutes=m)


def test_normal_driving_is_untouched():
    base = datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)
    pts = [_pt(_mins(base, i), 60 + i, 80 - i * 0.1, lat=45.0 + i * 0.01) for i in range(10)]
    assert DR._filter_frozen_telemetry(pts) == pts


def test_long_frozen_run_is_dropped_keeping_anchors():
    base = datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)
    # The cache freeze re-serves the LAST real reading (100 km/h, 62%) byte-identical for 5 real
    # minutes, so the last genuine sample and every re-served copy read the same.
    good_before = _pt(base, 100.0, 62.0)
    frozen = [_pt(_mins(base, i), 100.0, 62.0) for i in range(1, 6)]
    good_after = _pt(_mins(base, 6), 95.0, 61.5, lat=45.02)
    pts = [good_before, *frozen, good_after]
    out = DR._filter_frozen_telemetry(pts)
    assert out == [good_before, good_after]


def test_short_frozen_run_is_kept():
    """Under the 60s floor — too likely a coincidence to risk dropping real data."""
    base = datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)
    pts = [
        _pt(base, 55.0, 80.0),
        _pt(base + timedelta(seconds=20), 100.0, 62.0),
        _pt(base + timedelta(seconds=40), 100.0, 62.0),
        _pt(base + timedelta(seconds=60), 95.0, 61.5, lat=45.02),
    ]
    assert DR._filter_frozen_telemetry(pts) == pts


def test_real_stop_at_low_speed_is_never_flagged():
    """Repeated identical soc/position at near-zero speed = a genuine stop (red light, traffic) —
    must never be treated as frozen telemetry, however long it lasts."""
    base = datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)
    pts = [_pt(_mins(base, i), 0.0, 70.0) for i in range(10)]
    assert DR._filter_frozen_telemetry(pts) == pts


def test_real_cruising_with_gps_movement_is_never_flagged():
    """Constant reported speed is fine on its own (cruise control) — only flagged when GPS ALSO
    proves the car never moved."""
    base = datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)
    pts = [_pt(_mins(base, i), 100.0, 80.0 - i * 0.2, lat=45.0 + i * 0.02) for i in range(10)]
    assert DR._filter_frozen_telemetry(pts) == pts


def test_fewer_than_three_points_returned_unchanged():
    base = datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)
    pts = [_pt(base, 50.0, 80.0), _pt(_mins(base, 1), 50.0, 80.0)]
    assert DR._filter_frozen_telemetry(pts) == pts


def test_telemetry_frozen_pairwise():
    base = datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)
    a = _pt(base, 90.0, 60.0)
    same = _pt(_mins(base, 1), 90.1, 60.05)
    moved = _pt(_mins(base, 1), 90.1, 60.05, lat=45.05)
    slow = _pt(_mins(base, 1), 5.0, 60.0)
    assert DR._telemetry_frozen(a, same) is True
    assert DR._telemetry_frozen(a, moved) is False
    assert DR._telemetry_frozen(a, slow) is False
