"""A charge the user merged is one start and one end, with the figures the Charges page shows for it.

A plug-in the car reported in pieces comes back as one session once merged, and the Events page reads
it through the same reader as the Charges page: the end of the last piece, the energy, the cost and
the final charge level of the group. A merged trip the same, through the Trips reader.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import db_reader
from events_fixture import Car, grouped, rows, web

ZONE = ZoneInfo("Europe/Warsaw")
# Yesterday 20:00 local: every session seeded a few hours before it stays on one local day, at any hour the suite runs.
NOW = datetime.combine(datetime.now(ZONE).date() - timedelta(days=1), datetime.min.time().replace(hour=20),
                       ZONE).astimezone(timezone.utc)


def test_a_merged_charge_is_one_row_with_the_groups_figures(tmp_path, monkeypatch):
    car = Car(tmp_path)
    web(car, monkeypatch)
    t0 = NOW - timedelta(hours=6)
    parent = car.db._conn.execute(
        "INSERT INTO charges (vehicle_id, started_at, ended_at, start_soc, end_soc, energy_added_kwh, cost)"
        " VALUES (?, ?, ?, 40, 55, 9.0, 2.0)",
        (car.vid, t0.isoformat(), (t0 + timedelta(hours=1)).isoformat())).lastrowid
    car.db._conn.execute(
        "INSERT INTO charges (vehicle_id, started_at, ended_at, start_soc, end_soc, energy_added_kwh, cost,"
        " merged_into_id) VALUES (?, ?, ?, 55, 80, 15.0, 3.5, ?)",
        (car.vid, (t0 + timedelta(hours=1, minutes=10)).isoformat(), (t0 + timedelta(hours=3)).isoformat(), parent))
    car.db._conn.commit()
    end, start = rows(grouped())
    assert (end["id"], end["on"], start["id"], start["on"]) == (parent, False, parent, True)
    page = db_reader.search_charges(date_from=(NOW - timedelta(days=1)).astimezone(ZONE).date().isoformat())
    assert len(page) == 1
    for figure in ("energy_added_kwh", "cost", "end_soc", "ended_at"):
        assert end["session"][figure] == page[0][figure], figure
    assert (end["energy_kwh"], end["cost"], end["soc_to"], end["duration_min"]) == (24.0, 5.5, 80, 180)


def test_a_merged_trip_is_one_row_with_the_groups_distance(tmp_path, monkeypatch):
    car = Car(tmp_path)
    web(car, monkeypatch)
    t0 = NOW - timedelta(hours=6)
    parent = car.db._conn.execute(
        "INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km, duration_min, start_odometer_km,"
        " end_odometer_km) VALUES (?, ?, ?, 10.0, 15, 1000, 1010)",
        (car.vid, t0.isoformat(), (t0 + timedelta(minutes=15)).isoformat())).lastrowid
    car.db._conn.execute(
        "INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km, duration_min, start_odometer_km,"
        " end_odometer_km, merged_into_id) VALUES (?, ?, ?, 5.0, 10, 1010, 1015, ?)",
        (car.vid, (t0 + timedelta(minutes=20)).isoformat(), (t0 + timedelta(minutes=30)).isoformat(), parent))
    car.db._conn.commit()
    end, start = rows(grouped())
    assert (end["id"], end["on"], start["id"]) == (parent, False, parent)
    assert (end["distance_km"], end["duration_min"]) == (15.0, 25), "the group's distance and driving time"
    assert end["session"]["ended_at"] == db_reader.get_trips()[0]["ended_at"]


def test_a_merged_trip_whose_first_piece_ended_before_the_days_shown_ends_in_them(tmp_path, monkeypatch):
    """The days shown bound the sessions read, by the whole group: its last piece ends inside them."""
    car = Car(tmp_path)
    web(car, monkeypatch)
    first_day = datetime.combine(datetime.now(ZONE).date() - timedelta(days=2), datetime.min.time(), ZONE)
    t0 = (first_day - timedelta(minutes=20)).astimezone(timezone.utc)                # 23:40 the day before
    parent = car.db._conn.execute(
        "INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km, duration_min) VALUES (?, ?, ?, 4.0, 10)",
        (car.vid, t0.isoformat(), (t0 + timedelta(minutes=10)).isoformat())).lastrowid
    car.db._conn.execute(
        "INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km, duration_min, merged_into_id)"
        " VALUES (?, ?, ?, 6.0, 10, ?)",
        (car.vid, (t0 + timedelta(minutes=25)).isoformat(), (t0 + timedelta(minutes=35)).isoformat(), parent))
    car.db._conn.commit()
    (end,) = rows(grouped(range="3d"))
    assert (end["id"], end["on"], end["distance_km"]) == (parent, False, 10.0)
    assert end["from_day"] is not None, "its start, the day before, is off the list"
