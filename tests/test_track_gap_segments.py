"""Gap-aware map tracks (_split_track_gaps / _rows_to_segments).

The Map, the report's month map, and a single trip's own map used to draw one straight polyline
through every recorded point — including across a real signal-loss stretch (tunnel, dead zone, a
cloud hiccup), where the resulting straight line cuts through buildings/fields instead of
following the road the car was actually on. There's no way to tell "the car really drove this
straight line" from "we lost the signal here" once they're rendered identically.

_split_track_gaps marks the difference: a jump much bigger than the trip's own typical sampling
interval becomes its own 2-point "gap" segment (drawn dashed by the templates), while normal
consecutive samples stay a single solid run (drawn as the actually-recorded path). The threshold
is RELATIVE (median inter-sample delay × a multiplier, floored at a minute) so it self-adjusts to
whatever driving poll interval is configured, instead of assuming a fixed cadence.
"""
from datetime import datetime, timedelta, timezone

import db as D
import db_reader as DR


def _pt(t, lat, lon):
    return {"recorded_at": t.isoformat(), "latitude": lat, "longitude": lon}


def _secs(base, s):
    return base + timedelta(seconds=s)


def test_normal_driving_is_one_solid_run():
    """Points 10s apart (the default driving poll) — no gap, one run covering everything."""
    base = datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)
    pts = [_pt(_secs(base, i * 10), 45.0 + i * 0.001, 9.0) for i in range(10)]
    segs = DR._split_track_gaps(pts)
    assert len(segs) == 1
    assert segs[0]["gap"] is False
    assert len(segs[0]["points"]) == 10


def test_real_gap_becomes_a_bridge_segment():
    """A 10-minute hole in an otherwise 10s-cadence trip — e.g. a tunnel — must split into
    [solid run before, 2-point gap bridge, solid run after], not one uninterrupted solid line."""
    base = datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)
    before = [_pt(_secs(base, i * 10), 45.0 + i * 0.001, 9.0) for i in range(5)]
    after_start = _secs(base, 40 + 600)   # 10 real minutes after the last "before" sample
    after = [_pt(_secs(after_start, i * 10), 45.1 + i * 0.001, 9.1) for i in range(5)]
    segs = DR._split_track_gaps(before + after)

    assert [s["gap"] for s in segs] == [False, True, False]
    assert len(segs[0]["points"]) == 5
    assert len(segs[2]["points"]) == 5
    # The bridge is exactly the two real endpoints straddling the hole — nothing invented.
    assert segs[1]["points"] == [segs[0]["points"][-1], segs[2]["points"][0]]


def test_small_jump_at_fast_poll_rate_is_not_a_gap():
    """A 2-minute jump would be "3x the median" if the median were 10s (2 min = 12x — actually
    IS a gap there), but at a SLOWER configured poll rate (e.g. 90s) the same absolute jump is
    normal cadence and must not be flagged — the threshold is relative to the trip's own rate."""
    base = datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)
    pts = [_pt(_secs(base, i * 90), 45.0 + i * 0.001, 9.0) for i in range(6)]
    segs = DR._split_track_gaps(pts)
    assert len(segs) == 1 and segs[0]["gap"] is False


def test_absolute_floor_flags_a_relatively_small_but_real_gap():
    """At a very fast poll rate (2s), a 90s hole is "only" 45x the median — clearly a gap by the
    multiplier alone, but the point of the _TRACK_GAP_MIN_S floor is that even a SMALLER relative
    jump still gets flagged once it crosses an absolute minute, so a fast-polling trip can't have
    a real dropout hidden by its own tight cadence."""
    base = datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)
    before = [_pt(_secs(base, i * 2), 45.0, 9.0) for i in range(5)]
    after = [_pt(_secs(base, 8 + 65), 45.01, 9.01)]   # 65s gap: > _TRACK_GAP_MIN_S (60s)
    segs = DR._split_track_gaps(before + after)
    assert [s["gap"] for s in segs] == [False, True]


def test_empty_and_single_point_do_not_crash():
    assert DR._split_track_gaps([]) == []
    one = [_pt(datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc), 45.0, 9.0)]
    segs = DR._split_track_gaps(one)
    assert len(segs) == 1 and segs[0]["points"] == [[45.0, 9.0]]


def test_accepts_sqlite_row_not_just_dict():
    """get_all_track/get_month_track pass raw sqlite3.Row objects (never dict-ified) — Row
    supports r["col"] but not r.get(), so the gap detector must use indexing, not .get()."""
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE t (recorded_at TEXT, latitude REAL, longitude REAL)")
    conn.executemany("INSERT INTO t VALUES (?,?,?)", [
        ("2026-07-18T10:00:00+00:00", 45.0, 9.0),
        ("2026-07-18T10:00:10+00:00", 45.001, 9.0),
    ])
    rows = conn.execute("SELECT * FROM t").fetchall()
    segs = DR._split_track_gaps(rows)
    assert len(segs) == 1 and segs[0]["gap"] is False


def _setup(tmp_path, monkeypatch):
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(DR, "DB_PATH", str(tmp_path / "t.db"))
    return pdb


def test_get_all_track_marks_a_real_gap_end_to_end(tmp_path, monkeypatch):
    """Through the real DB path (get_all_track), not just the unit-level helper: a trip with a
    genuine mid-trip signal-loss gap comes back as solid/gap/solid, matching what the Map now
    renders as a dashed bridge."""
    pdb = _setup(tmp_path, monkeypatch)
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km)"
        " VALUES (1,1,'2026-07-18T10:00:00+00:00','2026-07-18T10:20:00+00:00',5.0)")
    rows = [
        (1, "2026-07-18T10:00:00+00:00", 45.40, 9.10),
        (1, "2026-07-18T10:00:10+00:00", 45.401, 9.101),
        (1, "2026-07-18T10:00:20+00:00", 45.402, 9.102),
        # a 12-minute tunnel/dead-zone hole
        (1, "2026-07-18T10:12:20+00:00", 45.500, 9.200),
        (1, "2026-07-18T10:12:30+00:00", 45.501, 9.201),
    ]
    pdb._conn.executemany(
        "INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude) VALUES (?,?,?,?)", rows)
    pdb._conn.commit()

    tracks = DR.get_all_track()
    assert len(tracks) == 1
    runs = tracks[0]
    assert [r["gap"] for r in runs] == [False, True, False]
    assert len(runs[0]["points"]) == 3 and len(runs[2]["points"]) == 2


def test_downsampling_never_thins_a_gap_bridge(tmp_path, monkeypatch):
    """max_points forces heavy downsampling of the solid runs, but a gap bridge is already just
    its 2 endpoints and must survive untouched — thinning it would risk losing the one honest
    signal (a dashed line) that a stretch wasn't really measured."""
    pdb = _setup(tmp_path, monkeypatch)
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km)"
        " VALUES (1,1,'2026-07-18T10:00:00+00:00','2026-07-18T11:00:00+00:00',50.0)")
    rows = [(1, f"2026-07-18T10:{i:02d}:00+00:00", 45.0 + i * 0.001, 9.0) for i in range(40)]
    # a 20-minute gap right after the 40 close-together samples
    rows.append((1, "2026-07-18T11:00:00+00:00", 45.5, 9.5))
    pdb._conn.executemany(
        "INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude) VALUES (?,?,?,?)", rows)
    pdb._conn.commit()

    tracks = DR.get_all_track(max_points=10)   # force heavy downsampling
    runs = tracks[0]
    assert runs[-1]["gap"] is True
    assert len(runs[-1]["points"]) == 2                  # never thinned
    assert len(runs[0]["points"]) < 40                   # the solid run WAS downsampled
    assert runs[0]["points"][0] == [45.0, 9.0]            # real first point kept
