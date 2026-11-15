"""Each tyre's lowest and highest pressure of the day, in the user's own days (D#483).

The Vehicle page draws them per day, beside the outside temperature where the user has it on. A
reading the car did not send is a hole, not a 0: it must not pull a day's minimum down to 0 bar,
and a day with nothing at all is absent, not a day at 0.
"""
from datetime import datetime, timezone

import db_reader
from test_missed_charge_scan import _seed

NOW = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)


def _db(tmp_path, monkeypatch, rows, tz="Europe/Rome"):
    """`rows` = [(recorded_at, fl, fr, rl, rr, outside_temp[, vehicle_id]), …]."""
    db = _seed(tmp_path, monkeypatch)
    db._conn.execute("INSERT INTO vehicles (id, vin) VALUES (2, 'VIN2')")
    db._conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('timezone', ?)", (tz,))
    for r in rows:
        vid = r[6] if len(r) > 6 else 1
        db._conn.execute(
            "INSERT INTO positions (vehicle_id, recorded_at, tire_fl_bar, tire_fr_bar, tire_rl_bar,"
            " tire_rr_bar, outside_temp) VALUES (?,?,?,?,?,?,?)", (vid, *r[:6]))
    db._conn.commit()
    return db


def _days(days=30):
    return db_reader.get_tyre_pressure_days(days, now=NOW)


def test_min_and_max_per_tyre_per_day(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, [
        ("2026-11-15T08:00:00+00:00", 2.40, 2.50, 2.45, 2.48, 3.0),
        ("2026-11-15T12:00:00+00:00", 2.55, 2.52, 2.47, 2.49, 9.5),
        ("2026-11-15T16:00:00+00:00", 2.50, 2.51, 2.46, 2.50, 6.0),
    ])
    (d,) = _days()
    assert d == {"day": "2026-11-15", "fl": (2.40, 2.55), "fr": (2.50, 2.52),
                 "rl": (2.45, 2.47), "rr": (2.48, 2.50), "temp": (3.0, 9.5)}


def test_a_null_reading_does_not_pull_the_minimum_to_zero(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, [
        ("2026-11-15T08:00:00+00:00", 2.50, 2.5, 2.5, 2.5, None),
        ("2026-11-15T09:00:00+00:00", None, 2.5, 2.5, 2.5, None),
        ("2026-11-15T10:00:00+00:00", 2.45, 2.5, 2.5, 2.5, None),
    ])
    assert _days()[0]["fl"] == (2.45, 2.50)


def test_a_tyre_never_sent_that_day_is_none(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, [("2026-11-15T08:00:00+00:00", 2.5, None, 2.5, 2.5, None)])
    assert _days()[0]["fr"] is None


def test_the_day_is_the_local_day(tmp_path, monkeypatch):
    """23:30 UTC on the 14th and 00:30 UTC on the 15th are both the 15th in Rome."""
    _db(tmp_path, monkeypatch, [
        ("2026-11-14T23:30:00+00:00", 2.40, 2.5, 2.5, 2.5, None),
        ("2026-11-15T00:30:00+00:00", 2.60, 2.5, 2.5, 2.5, None),
    ])
    (d,) = _days()
    assert d["day"] == "2026-11-15" and d["fl"] == (2.40, 2.60)


def test_only_the_selected_vehicle(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, [
        ("2026-11-15T08:00:00+00:00", 2.50, 2.5, 2.5, 2.5, None),
        ("2026-11-15T09:00:00+00:00", 1.90, 2.5, 2.5, 2.5, None, 2),
    ])
    assert _days()[0]["fl"] == (2.50, 2.50)


def test_no_temperature_means_temp_none(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, [("2026-11-15T08:00:00+00:00", 2.5, 2.5, 2.5, 2.5, None)])
    assert _days()[0]["temp"] is None


def test_a_day_with_nothing_is_absent(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, [
        ("2026-11-14T08:00:00+00:00", None, None, None, None, None),
        ("2026-11-15T08:00:00+00:00", 2.5, 2.5, 2.5, 2.5, None),
    ])
    assert [d["day"] for d in _days()] == ["2026-11-15"]


def test_the_window_counts_back_from_now(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, [
        ("2026-10-01T08:00:00+00:00", 2.5, 2.5, 2.5, 2.5, None),
        ("2026-11-15T08:00:00+00:00", 2.5, 2.5, 2.5, 2.5, None),
    ])
    assert [d["day"] for d in _days(30)] == ["2026-11-15"]
    assert [d["day"] for d in _days(90)] == ["2026-10-01", "2026-11-15"]


def test_the_day_the_clocks_go_back_has_its_own_midnights(tmp_path, monkeypatch):
    """25/10/2026 in Rome starts in summer time and ends in winter time: it runs from 22:00 UTC on
    the 24th to 23:00 UTC on the 25th, 25 hours. An offset taken from `now` (November, +1) would
    start it an hour late and push its first half hour onto the 24th."""
    _db(tmp_path, monkeypatch, [
        ("2026-10-24T22:30:00+00:00", 2.40, 2.5, 2.5, 2.5, None),   # 00:30 on the 25th, local
        ("2026-10-25T22:30:00+00:00", 2.60, 2.5, 2.5, 2.5, None),   # 23:30 on the 25th, local
    ])
    assert [(d["day"], d["fl"]) for d in _days(90)] == [("2026-10-25", (2.40, 2.60))]


def test_a_day_with_only_the_outside_temperature_is_absent(tmp_path, monkeypatch):
    """The chart is the tyres'. A day the car sent no pressure is not a day of their history, or an
    install with the outside temperature on would draw a month of it on release day, no tyre line,
    and never say there is no history yet (final review, #1)."""
    _db(tmp_path, monkeypatch, [
        ("2026-11-14T08:00:00+00:00", None, None, None, None, 4.0),
        ("2026-11-15T08:00:00+00:00", 2.5, 2.5, 2.5, 2.5, 6.0),
    ])
    assert [(d["day"], d["temp"]) for d in _days()] == [("2026-11-15", (6.0, 6.0))]


def test_the_day_the_clocks_go_forward_ends_an_hour_early(tmp_path, monkeypatch):
    """29/03/2026 in Rome is 23 hours long: it ends at 22:00 UTC, not 23:00. 22:30 UTC is already
    00:30 on the 30th; an offset taken from `now` (November, +1) would keep it on the 29th."""
    _db(tmp_path, monkeypatch, [
        ("2026-03-29T21:30:00+00:00", 2.40, 2.5, 2.5, 2.5, None),   # 23:30 on the 29th, local
        ("2026-03-29T22:30:00+00:00", 2.60, 2.5, 2.5, 2.5, None),   # 00:30 on the 30th, local
    ])
    assert [(d["day"], d["fl"]) for d in _days(240)] == [
        ("2026-03-29", (2.40, 2.40)), ("2026-03-30", (2.60, 2.60))]
