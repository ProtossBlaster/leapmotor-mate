"""The four sources sort by the clock, not by the text of their timestamps.

The signal rows and the command log carry UTC; `get_charges` and the trip readers hand back times
already in the reader's zone, with its offset. Sorted as text, "23:00:00+02:00" comes after
"22:00:00+00:00" although it is the earlier moment. In Warsaw, in summer, a charge begun at 21:00 UTC
is a 23:00 charge, and a lock seen at 22:00 UTC came an hour later; across midnight and across the
change to winter time the order is the clock's.
"""
from datetime import date, datetime, timedelta, timezone

from events_fixture import Car, event_row, grouped, rows, web


def _charge(car, start, end):
    car.db._conn.execute(
        "INSERT INTO charges (vehicle_id, started_at, ended_at, energy_added_kwh) VALUES (?, ?, ?, 6.0)",
        (car.vid, start.isoformat(), end.isoformat()))
    car.db._conn.commit()


def test_a_charge_at_23_local_sorts_before_a_signal_at_22_utc(tmp_path, monkeypatch):
    car = Car(tmp_path)
    web(car, monkeypatch, zone="Europe/Warsaw")
    day = datetime(2026, 7, 15, tzinfo=timezone.utc)
    _charge(car, day.replace(hour=21), day.replace(hour=21, minute=30))         # 23:00 – 23:30 +02:00
    event_row(car, "unlocked", day.replace(hour=22))                              # 00:00 +02:00 next day
    event_row(car, "unlocked", day.replace(hour=22, minute=5), state=0)
    ev = grouped(date_from="2026-07-15", date_to="2026-07-16")
    assert [(d["date"], [r["hms"] for r in d["items"]]) for d in ev["days"]] == [
        (date(2026, 7, 16), ["00:05:00", "00:00:00"]), (date(2026, 7, 15), ["23:30:00", "23:00:00"])]
    assert [r["source"] for r in rows(ev)] == ["signal", "signal", "charge", "charge"]


def test_the_order_holds_across_the_change_to_winter_time(tmp_path, monkeypatch):
    """25 October 2026, 01:00 UTC: Warsaw goes from +02:00 to +01:00. A charge that began at 00:30
    UTC (02:30 +02:00) and a signal at 01:30 UTC (02:30 +01:00) print the same local time; the
    signal is the later one."""
    car = Car(tmp_path)
    web(car, monkeypatch, zone="Europe/Warsaw")
    day = datetime(2026, 10, 25, tzinfo=timezone.utc)
    _charge(car, day.replace(minute=30), day.replace(hour=2))
    event_row(car, "unlocked", day.replace(hour=1, minute=30))
    event_row(car, "unlocked", day.replace(hour=1, minute=40), state=0)
    listed = rows(grouped(date_from="2026-10-25", date_to="2026-10-25"))
    assert [(r["source"], r["on"], r["hms"]) for r in listed] == [
        ("charge", False, "03:00:00"), ("signal", False, "02:40:00"), ("signal", True, "02:30:00"), ("charge", True, "02:30:00")]
    assert listed[0]["duration_min"] == 90, "the charge's length is the clock's, not the printed hours'"
    assert day + timedelta(hours=1) < day.replace(hour=1, minute=30)
