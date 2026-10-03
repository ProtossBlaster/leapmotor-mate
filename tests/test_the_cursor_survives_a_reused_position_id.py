"""The reading position survives the rows it points at being deleted.

`positions.id` is not AUTOINCREMENT: once the newest rows are gone, SQLite hands their ids out again,
and a cursor left where they were would skip every row written next. So each deletion of positions —
the retention, the one-time repairs at start-up — pulls the cursors back to the newest row that
remains, in the same transaction. The cursor is the row id and not the time because the web's write
of a frame can land later in id order than in time order, and must still be read.
"""
from datetime import timedelta

import db_reader
from events_fixture import Car, signal


def _door_opens_twice(car):
    """A door opened and closed, so the detector has a baseline and a confirmed state."""
    car.frame()
    car.frame()
    car.frame(**{"1277": 1})
    car.frame(**{"1277": 1})
    car.frame()
    car.frame()
    car.derive()
    assert [(k, s) for k, s, _ in car.events()] == [("door_driver", 1), ("door_driver", 0)]


def _age_everything(car):
    car.db._conn.execute("UPDATE positions SET recorded_at = '2020-01-01T00:00:00+00:00'")
    car.db._conn.commit()


def _still_detects(car, restart=False):
    highest_before = car.db._conn.execute("SELECT COALESCE(MAX(id), 0) FROM positions").fetchone()[0]
    if restart:
        car.restart()
    row = car.frame(**{"1277": 1})
    car.frame(**{"1277": 1})
    car.derive()
    assert [(k, s) for k, s, _ in car.events()][-1] == ("door_driver", 1)
    return row["id"], highest_before


def test_the_retention_deleting_the_newest_rows_does_not_blind_the_detector(tmp_path):
    car = Car(tmp_path)
    _door_opens_twice(car)
    _age_everything(car)
    assert car.db.prune_positions(30) == 6
    new_id, _ = _still_detects(car)
    assert new_id <= 6, "the id was reused, and the row still read"


def test_the_same_after_a_restart(tmp_path):
    car = Car(tmp_path)
    _door_opens_twice(car)
    _age_everything(car)
    car.db.prune_positions(30)
    _still_detects(car, restart=True)


def test_the_start_up_repairs_pull_the_cursor_back_too(tmp_path):
    """Two repairs delete positions when a process starts; the cursor may point past them."""
    car = Car(tmp_path)
    _door_opens_twice(car)
    car.db._conn.execute(
        "INSERT INTO positions (vehicle_id, recorded_at, soc, odometer_km) VALUES (?, ?, 0, NULL)",
        (car.vid, "2026-10-02T10:00:00+00:00"))                  # what the misread-map repair drops
    car.db._conn.execute(
        "INSERT INTO positions (vehicle_id, recorded_at, soc, range_km, odometer_km) VALUES (?, ?, 0, 100, 1)",
        (car.vid, "2026-10-02T10:01:00+00:00"))                  # what the zero-SoC repair drops
    car.db._conn.commit()
    car.derive()
    for marker in ("positions_misread_map_repair_v1", "positions_zero_soc_repair_v1"):
        car.db._conn.execute("DELETE FROM settings WHERE key = ?", (marker,))
    car.db._conn.commit()
    car.restart()                                                # the repairs run in the constructor
    assert car.db._conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 6
    _still_detects(car)


def test_a_write_that_landed_late_is_still_read(tmp_path, monkeypatch):
    """The web stores the frame it read after a command; delayed by a lock, its row has a later id
    and an earlier time than the poller's next row. Read in id order, nothing is skipped."""
    car = Car(tmp_path)
    car.frame()
    car.frame()
    car.derive()
    opened = car.frame(**{"1277": 1})
    monkeypatch.setattr(db_reader, "DB_PATH", car.path)
    db_reader.save_fresh_signals(signal(car.t + timedelta(seconds=5), **{"1277": 1}))
    car.db._conn.execute("UPDATE positions SET recorded_at = ? WHERE id = (SELECT MAX(id) FROM positions)",
                         ("2000-01-01T00:00:00+00:00",))     # written late: a later id, an earlier time
    car.db._conn.commit()
    car.derive()
    assert car.events() == [("door_driver", 1, opened["recorded_at"])]


def test_a_factory_reset_wipes_the_events_and_the_detectors_memory(tmp_path):
    car = Car(tmp_path)
    _door_opens_twice(car)
    car.db.factory_reset()
    assert car.db._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    assert car.db._conn.execute("SELECT COUNT(*) FROM settings WHERE key LIKE 'events_state_%'"
                                ).fetchone()[0] == 0
