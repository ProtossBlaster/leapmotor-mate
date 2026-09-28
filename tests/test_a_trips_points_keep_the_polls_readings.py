"""A trip's points keep the poll's battery power, coldest-cell temperature, range and outside air.

The trip detail shows these along the drive. They used to be read from `positions`, which the
retention setting prunes, while the trip's own points (`trip_positions`) are kept for good — so
after the retention ran the route stayed and the figures went. Each point now carries them itself,
written from the same frame as its `positions` row, and the points recorded before that get them
once from their poll's `positions` row (the two rows are milliseconds apart), before the retention
can remove it.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import db as D

START = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=40)


def _frame(**over):
    return SimpleNamespace(**{"latitude": 45.0, "longitude": 9.0, "speed_kmh": 60.0, "soc": 80.0,
                              "charge_voltage_v": 400.0, "charge_current_a": 423.5, "battery_min_temp": 17.0,
                              "range_km": 301.0, "outside_temp": 12.5, **over})


def _db(tmp_path):
    d = D.Database(str(tmp_path / "t.db"))
    d._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LVIN0000000000001','B10')")
    d._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (2,'LVIN0000000000002','B10')")
    d._conn.execute("INSERT INTO trips (id, vehicle_id, started_at, ended_at) VALUES (1,1,?,?)",
                    (START.isoformat(), (START + timedelta(minutes=10)).isoformat()))
    d._conn.commit()
    return d


def _readings(d, trip_id=1):
    return [tuple(r) for r in d._conn.execute(
        "SELECT power_kw, battery_temp_c, range_km, outside_temp_c FROM trip_positions"
        " WHERE trip_id = ? ORDER BY recorded_at", (trip_id,))]


def test_a_new_point_keeps_its_frames_readings(tmp_path):
    d = _db(tmp_path)
    d.add_trip_position(1, _frame())
    assert _readings(d) == [(169.4, 17.0, 301.0, 12.5)]


def test_what_the_frame_did_not_say_stays_empty(tmp_path):
    """A range of 0 is the parser's "not sent", like the missing current."""
    d = _db(tmp_path)
    d.add_trip_position(1, _frame(charge_current_a=None, battery_min_temp=None, range_km=0.0, outside_temp=None))
    assert _readings(d) == [(None, None, None, None)]


def _old_points(d):
    """Three points recorded before the columns: one with its poll's row 3 ms later, one whose only
    row is 5 s away (another poll), one matched only by another car's row."""
    for k in range(3):
        at = START + timedelta(minutes=k)
        d._conn.execute("INSERT INTO trip_positions (trip_id, recorded_at, latitude, longitude, speed_kmh, soc)"
                        " VALUES (1,?,45.0,9.0,60,80)", (at.isoformat(),))
    rows = [(1, START + timedelta(milliseconds=3), -198.25),
            (1, START + timedelta(minutes=1, seconds=5), 100.0),
            (2, START + timedelta(minutes=2, milliseconds=2), 50.0)]
    for vehicle, at, amps in rows:
        d._conn.execute("INSERT INTO positions (vehicle_id, recorded_at, latitude, longitude, speed_kmh,"
                        " charge_voltage_v, charge_current_a, battery_min_temp, range_km, outside_temp)"
                        " VALUES (?,?,45.0,9.0,60,400,?,16,290,8.5)", (vehicle, at.isoformat(), amps))
    d._conn.execute("DELETE FROM settings WHERE key = 'trip_readings_backfill_v1'")
    d._conn.commit()


def test_points_recorded_before_get_their_polls_readings_once(tmp_path):
    d = _db(tmp_path)
    _old_points(d)
    d._backfill_trip_readings()
    assert _readings(d) == [(-79.3, 16.0, 290.0, 8.5), (None, None, None, None), (None, None, None, None)]
    assert d.get_setting("trip_readings_backfill_v1") == "1"


def test_the_readings_outlive_the_positions_retention(tmp_path):
    d = _db(tmp_path)
    _old_points(d)
    d._backfill_trip_readings()
    d.add_trip_position(1, _frame())
    before = _readings(d)
    assert d.prune_positions(30) > 0
    assert d._conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0
    assert _readings(d) == before
