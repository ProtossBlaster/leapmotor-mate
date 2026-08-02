"""The Monthly Report always opens on the CURRENT month, even before its first trip.

It used to default to the newest month that HAD data. On the 2nd of a month, with nothing driven
yet, that meant opening on the previous month and showing its totals under this month's page —
right numbers, wrong month, and nothing on screen saying so. An empty month is an answer.
"""
from datetime import datetime, timedelta

import db as D            # poller schema
import db_reader


def _setup(tmp_path, monkeypatch):
    pdb = D.Database(str(tmp_path / "t.db"))
    pdb.set_battery_capacity(65.0)
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    return pdb


def _month_key(dt):
    return dt.strftime("%Y-%m")


def _trip(pdb, tid, when, *, dist=100.0, eff=20.0):
    ts = when.strftime("%Y-%m-%dT12:00:00+00:00")
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km,"
        " start_soc, end_soc, efficiency_kwh_100km, regen_kwh, duration_min)"
        " VALUES (?,1,?,?,?,60,50,?,0,60)",
        (tid, ts, ts, dist, eff))
    pdb._conn.commit()


def _last_month_day15():
    now = datetime.now(db_reader._local_tz())
    return (now.replace(day=1) - timedelta(days=1)).replace(day=15)


def test_opens_on_the_current_month_when_it_has_no_data(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _trip(pdb, 1, _last_month_day15())          # only the previous month has anything

    r = db_reader.get_monthly_report()
    now_key = _month_key(datetime.now(db_reader._local_tz()))

    assert r["has_data"] is True
    assert r["month"] == now_key                # not last month
    assert r["month_empty"] is True
    assert r["cur"]["trip_count"] == 0
    assert now_key in [m["key"] for m in r["months"]]   # selectable in the dropdown


def test_an_empty_month_shows_no_deltas(tmp_path, monkeypatch):
    # Every card would otherwise read −100 % against last month, which describes the calendar
    # rather than the driving.
    pdb = _setup(tmp_path, monkeypatch)
    _trip(pdb, 1, _last_month_day15(), dist=500.0)

    assert db_reader.get_monthly_report()["deltas"] is None


def test_the_previous_month_is_still_reachable_and_intact(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    prev = _last_month_day15()
    _trip(pdb, 1, prev, dist=500.0, eff=18.0)

    r = db_reader.get_monthly_report(_month_key(prev))
    assert r["month"] == _month_key(prev)
    assert r["month_empty"] is False
    assert r["cur"]["total_km"] == 500.0
    assert r["cur"]["trip_count"] == 1


def test_a_current_month_with_data_is_not_flagged_empty(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _trip(pdb, 1, datetime.now(db_reader._local_tz()).replace(day=1), dist=221.0, eff=16.4)

    r = db_reader.get_monthly_report()
    assert r["month"] == _month_key(datetime.now(db_reader._local_tz()))
    assert r["month_empty"] is False
    assert r["cur"]["total_km"] == 221.0


def test_no_data_at_all_still_says_no_data(tmp_path, monkeypatch):
    # The empty-database case must not be turned into "an empty current month" — the page has a
    # different, friendlier answer for a Mate that has never seen a trip.
    _setup(tmp_path, monkeypatch)
    r = db_reader.get_monthly_report()
    assert r["has_data"] is False
    assert r["month"] is None
    assert r["months"] == []
