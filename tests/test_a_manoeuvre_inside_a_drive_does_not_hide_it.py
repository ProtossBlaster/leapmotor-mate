"""A parking manoeuvre the car files on its own does not stop its drive from matching.

The car's cloud history can hold, next to the record of a drive, a second record wholly inside it:
the few metres of a parking manoeuvre near the end, filed as 0 km and 0 kWh. Measured on a B10: a
20 km drive 16:36:00–17:04:08, and inside it 16:58:20–16:59:17, 0.0 km, 0.0 kWh.

The matcher sorts a trip's records by start and takes the LAST one's end as the end of the cloud's
drive, and it rejects records that overlap. The manoeuvre is both: it sorts last and it overlaps
the drive. So the drive's own record, the one with every kilometre and kilowatt-hour, was never
matched to the trip.

Only an unambiguous zero — 0 km AND 0 kWh, both present — wholly inside another record of the same
car that has a distance of its own is set aside, and only for the matching: the stored record is
not touched. A missing value is not a zero, a zero on its own goes through the usual checks, and
a record with conflicting versions stays in the way, as it did.
"""
import json
from datetime import datetime, timedelta, timezone

import db as D
import pytest
import trip_energy

VIN = "LVIN0000000000001"
START = datetime(2026, 1, 1, 6, 0, tzinfo=timezone.utc)


def _ms(minutes):
    return int((START + timedelta(minutes=minutes)).timestamp() * 1000)


def _record(start_min, end_min, kwh, km, **extra):
    return json.dumps({"vin": VIN, "routeStartTs": _ms(start_min), "routeEndTs": _ms(end_min),
                       "totalEnergy": kwh, "totalMileage": km, **extra})


@pytest.fixture
def db(tmp_path):
    database = D.Database(str(tmp_path / "history.db"))
    conn = database._conn
    conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,?,'B10')", (VIN,))
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('is_reev','0')")
    conn.execute("""CREATE TABLE IF NOT EXISTS api_lab_cloud_history_records
                    (id INTEGER PRIMARY KEY, kind TEXT, payload_json TEXT)""")
    yield conn
    conn.close()


def _matched(conn, trip_min, km, records):
    """One trip from minute 0 to `trip_min` over `km`, as the matcher annotates it."""
    conn.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, start_soc,"
                 " end_soc) VALUES (1, 1, ?, ?, ?, 80, 70)",
                 (START.isoformat(), (START + timedelta(minutes=trip_min)).isoformat(), km))
    conn.executemany("INSERT INTO api_lab_cloud_history_records (kind, payload_json)"
                     " VALUES ('mileage', ?)", [(r,) for r in records])
    conn.commit()
    trip = dict(conn.execute("SELECT * FROM trips WHERE id=1").fetchone())
    trip_energy.select_energy(conn, [trip])
    return trip


def _energy(conn, trip_min, km, records):
    return _matched(conn, trip_min, km, records).get("cloud_energy_kwh")


def test_a_manoeuvre_inside_the_drive_leaves_the_drive_matched(db):
    assert _energy(db, 28, 20.0, [_record(0, 28, 3.6, 20.0), _record(22, 23, 0.0, 0.0)]) == 3.6


def test_the_top_speed_is_the_drives_not_the_manoeuvres(db):
    trip = _matched(db, 28, 20.0, [_record(0, 28, 3.6, 20.0, maxSpeed=120),
                                   _record(22, 23, 0.0, 0.0, maxSpeed=200)])
    assert trip.get("cloud_max_speed_kmh") == 120


def test_two_records_of_one_drive_still_add_up(db):
    """The car cuts a drive in two when it is switched off for a moment; Mate keeps one trip."""
    records = [_record(0, 14, 2.0, 13.0), _record(14.8, 30, 2.3, 14.0)]
    assert _energy(db, 30, 27.0, records) == pytest.approx(4.3)


def test_two_records_of_one_drive_with_a_manoeuvre_inside_the_second(db):
    records = [_record(0, 14, 2.0, 13.0), _record(14.8, 30, 2.3, 14.0), _record(25, 26, 0.0, 0.0)]
    assert _energy(db, 30, 27.0, records) == pytest.approx(4.3)


@pytest.mark.parametrize("inside", [
    _record(22, 23, None, 0.0),                      # no energy: missing is not zero
    _record(22, 23, 0.0, None),                      # no distance
    _record(22, 23, 0.1, 0.0),                       # some energy
    _record(22, 23, 0.0, 1.0),                       # some distance: a real overlap
], ids=["no energy", "no distance", "energy", "distance"])
def test_anything_but_an_unambiguous_zero_still_blocks_the_match(db, inside):
    assert _energy(db, 28, 20.0, [_record(0, 28, 3.6, 20.0), inside]) is None


def test_a_zero_with_two_versions_still_blocks_the_match(db):
    """Stored last, the zero is the version the matcher keeps: the conflict alone must stop it."""
    assert _energy(db, 28, 20.0, [_record(0, 28, 3.6, 20.0), _record(22, 23, 0.0, 0.1),
                                  _record(22, 23, 0.0, 0.0)]) is None


def test_a_zero_reaching_past_the_drive_still_blocks_the_match(db):
    assert _energy(db, 28, 20.0, [_record(0, 28, 3.6, 20.0), _record(27.5, 29, 0.0, 0.0)]) is None


def test_a_record_without_distance_shelters_nothing(db):
    """A record with energy but no kilometres is no drive for a manoeuvre to sit in."""
    assert _energy(db, 28, 0.3, [_record(0, 28, 0.5, 0.0), _record(22, 23, 0.0, 0.0)]) is None


def test_a_zero_record_on_its_own_goes_through_the_usual_checks(db):
    """A short hop the car filed as 0 km is not inside anything, so it matches as it always did."""
    assert _energy(db, 3, 0.3, [_record(0, 3, 0.0, 0.0)]) == 0.0


def test_another_cars_drive_shelters_nothing(db):
    """Its VIN sorts first, so its drive is the one walked just before this car's records."""
    assert _energy(db, 3, 0.3, [_record(0, 60, 5.0, 40.0, vin="A" + VIN[1:]),
                                _record(0, 3, 0.0, 0.0)]) == 0.0
