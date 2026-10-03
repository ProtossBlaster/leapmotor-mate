"""A trip or a charge is two rows, its start and its end, each under its own local day.

A charge from 23:00 to 01:00 starts on one day and ends on the next; the start is listed under the
first, the end under the second with the figures the Charges page shows for the session (a merged
group as one), and the end links to its start. The session in progress is its start alone, and once
it closes it gains its end, under the same id.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from events_fixture import Car, grouped, row_text, rows, web

ZONE = ZoneInfo("Europe/Warsaw")
TODAY = datetime.now(ZONE).date()


def _local(day, hh, mm=0):
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=ZONE).astimezone(timezone.utc)


def _charge(car, start, end, kwh=10.0, parent=None):
    cur = car.db._conn.execute(
        "INSERT INTO charges (vehicle_id, started_at, ended_at, energy_added_kwh, merged_into_id) VALUES (?, ?, ?, ?, ?)",
        (car.vid, start.isoformat(), end.isoformat() if end else None, kwh, parent))
    car.db._conn.commit()
    return cur.lastrowid


def test_a_charge_over_midnight_starts_on_one_day_and_ends_on_the_next(tmp_path, monkeypatch):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    yesterday = TODAY - timedelta(days=1)
    parent = _charge(car, _local(yesterday, 23), _local(yesterday, 23, 50), 5.0)
    _charge(car, _local(TODAY, 0, 10), _local(TODAY, 1), 7.0, parent=parent)
    ev = grouped()
    assert [d["date"] for d in ev["days"]] == [TODAY, yesterday]
    (end,), (start,) = ev["days"][0]["items"], ev["days"][1]["items"]
    assert (end["id"], end["on"], end["hms"], end["label"]) == (parent, False, "01:00:00", "Charging ended")
    assert (start["id"], start["on"], start["hms"], start["label"]) == (parent, True, "23:00:00", "Charging started")
    assert end["energy_kwh"] == 12.0, "the merged group's figure, as on Charges"
    assert end["from_anchor"] == start["anchor"] and end["from_listed"] and end["from_day"]
    assert ev["count"] == 2
    html = client.get("/events").text
    assert html.count('<i class="dot d-charging j"></i>') == 2, "a line joins the two dots"
    assert "from" not in row_text(html, end["anchor"]), "a line joins them across the day's heading, no text"


def test_the_charge_in_progress_is_its_start_and_renders(tmp_path, monkeypatch):
    """The charge in progress comes from `open_charge()` with fewer fields than a finished one; the
    page must print it without reaching for a figure it does not carry."""
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    charge = _charge(car, datetime.now(timezone.utc) - timedelta(minutes=30), None)
    trip = car.db._conn.execute("INSERT INTO trips (vehicle_id, started_at) VALUES (?, ?)",
                                (car.vid, (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat())).lastrowid
    car.db._conn.commit()
    listed = rows(grouped())
    assert [(r["source"], r["id"], r["on"], r["open"]) for r in listed] == [
        ("trip", trip, True, True), ("charge", charge, True, True)]
    r = client.get("/events")
    assert r.status_code == 200 and f'id="ev-charge-{charge}-on"' in r.text and f'id="ev-trip-{trip}-on"' in r.text


def test_the_trip_in_progress_gains_its_end_when_it_closes(tmp_path, monkeypatch):
    car = Car(tmp_path)
    web(car, monkeypatch)
    started = datetime.now(timezone.utc) - timedelta(minutes=20)
    trip = car.db._conn.execute("INSERT INTO trips (vehicle_id, started_at) VALUES (?, ?)",
                                (car.vid, started.isoformat())).lastrowid
    car.db._conn.commit()
    assert [(r["id"], r["on"], r["open"]) for r in rows(grouped())] == [(trip, True, True)]
    car.db._conn.execute("UPDATE trips SET ended_at = ?, distance_km = 9.5, duration_min = 15, start_soc = 80,"
                         " end_soc = 77.5 WHERE id = ?", ((started + timedelta(minutes=15)).isoformat(), trip))
    car.db._conn.commit()
    end, start = rows(grouped())
    assert (end["id"], end["on"], start["id"], start["open"]) == (trip, False, trip, False)
    assert (end["distance_km"], end["duration_min"], end["soc_from"], end["soc_to"]) == (9.5, 15, 80, 77.5)
