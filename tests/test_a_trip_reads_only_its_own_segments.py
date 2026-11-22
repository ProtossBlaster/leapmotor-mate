"""A page that shows a few merged trips reads their segments, not every merged trip in the history.

Each case counts the segments of OTHER groups that SQLite hands back while one page is composed:
the archived trip's page, its group, a merge preview and its day's totals (later groups exist), and
Overview's newest trip and the temperature chart (only older groups exist). The count is zero
however long the history; the trips shown still have every segment, also one past their day.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import db as D
import db_reader as reader

OTHERS = 1000                                   # unrelated merged groups, ids from 100 up


def _trip(i, start, minutes, km, parent=None):
    return (i, start.isoformat(), (start + timedelta(minutes=minutes)).isoformat(), km, parent)


@pytest.fixture
def con(tmp_path, monkeypatch):
    database = D.Database(str(tmp_path / "segments.db"))
    con = database._conn
    for name in ("_get", "_conn_rw"):
        monkeypatch.setattr(reader, name, lambda: con)
    monkeypatch.setattr(reader, "_current_vehicle_id", lambda: 1)
    monkeypatch.setattr(reader, "is_reev_car", lambda: False)
    con.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1, 'LVIN0000000000001', 'B10')")
    con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('is_reev', '0')")
    archive, recent = datetime(2020, 3, 1, 23, 30, tzinfo=timezone.utc), datetime(2026, 11, 1, 8, tzinfo=timezone.utc)
    rows = [_trip(1, archive, 20, 10), _trip(2, archive + timedelta(minutes=35), 20, 12, parent=1),  # past midnight
            _trip(5, archive + timedelta(minutes=70), 10, 4),
            _trip(3, recent, 20, 8), _trip(4, recent + timedelta(minutes=30), 20, 9, parent=3)]
    other = datetime(2021, 1, 1, 7, tzinfo=timezone.utc)
    for k in range(OTHERS):
        at = other + timedelta(hours=12 * k)
        rows += [_trip(100 + 2 * k, at, 20, 10), _trip(101 + 2 * k, at + timedelta(minutes=30), 20, 10, 100 + 2 * k)]
    con.executemany(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, merged_into_id, start_soc, end_soc,"
        " efficiency_kwh_100km, outside_temp_start_c) VALUES (?, 1, ?, ?, ?, ?, 80, 76, 16, 10)", rows)
    con.commit()
    yield con
    con.close()


def _others_read(con):
    """Ids of other groups' segments that leave SQLite from now on."""
    seen = []

    def row(cursor, values):
        r = sqlite3.Row(cursor, values)
        if "merged_into_id" in r.keys() and (r["merged_into_id"] or 0) >= 100:
            seen.append(r["id"])
        return r
    con.row_factory = row
    return seen


def _day(start):
    t = int(datetime.fromisoformat(start + "T00:00:00+00:00").timestamp())
    return t, t + 86399


@pytest.mark.parametrize("page,read,km", [
    ("the archived trip's page", lambda: reader.get_trip_detail(1), lambda r: r["distance_km"]),
    ("the archived trip's group", lambda: reader.trip_group(2), lambda r: r[0]["distance_km"]),
    ("a merge preview", lambda: reader.preview_merge(1, 5), lambda r: r["distance_km"] - 4),
    ("its day's totals", lambda: reader.get_trip_totals_between(*_day("2020-03-01")), lambda r: r["distance_km"]),
    ("Overview's newest trip", lambda: reader.get_trips(1), lambda r: r[0]["distance_km"] + 5),
    ("the temperature chart", lambda: reader.get_efficiency_vs_temp(),
     lambda r: [p["km"] for p in r["points"]][-1] + 5),
])
def test_a_page_reads_no_other_trips_segments(con, page, read, km):
    seen = _others_read(con)
    assert km(read()) == 22, page
    assert seen == [], page


def test_the_helper_reads_what_it_is_asked_for(con):
    statements = []
    con.set_trace_callback(statements.append)
    assert reader._children_by_parent(con, []) == {}
    assert statements == []
    assert len(reader._children_by_parent(con)) == OTHERS + 2
    assert {c["id"] for c in reader._children_by_parent(con, [{"id": 1}])[1]} == {2}
