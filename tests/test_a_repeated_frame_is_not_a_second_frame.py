"""A frame the cloud serves again is not a second frame.

While the car sleeps the cloud answers every poll with the last frame it holds, and the poller stores
it every time (one frame 433 times in a night). Those rows carry the same `frame_ts`, and a state
seen once in a frame served twice was seen once. A row without a car clock — the history before
`frame_ts` existed — has no way of being a repeat, so it counts.
"""
import db_reader
from events_fixture import Car, signal


def _pending(car):
    car.frame()
    car.frame()
    car.frame(**{"1277": 1})           # the door seen open once
    car.derive()
    assert car.events() == []


def test_the_same_frame_served_again_confirms_nothing(tmp_path):
    car = Car(tmp_path)
    _pending(car)
    car.repeat(**{"1277": 1})
    car.repeat(**{"1277": 1})
    car.derive()
    assert car.events() == [], "two copies of one frame are one frame"
    car.frame(**{"1277": 1})
    car.derive()
    assert [(k, s) for k, s, _ in car.events()] == [("door_driver", 1)]


def test_a_row_without_a_car_clock_always_counts(tmp_path):
    car = Car(tmp_path)
    _pending(car)
    car.repeat(**{"1277": 1})
    car.db._conn.execute("UPDATE positions SET frame_ts = NULL WHERE id = "
                         "(SELECT MAX(id) FROM positions)")
    car.db._conn.commit()
    car.derive()
    assert [(k, s) for k, s, _ in car.events()] == [("door_driver", 1)]


def test_the_webs_copy_of_the_frame_is_skipped_too(tmp_path, monkeypatch):
    """The web stores a row from the frame it reads after a command — the same frame the poller
    just stored, with the same car clock."""
    car = Car(tmp_path)
    _pending(car)
    monkeypatch.setattr(db_reader, "DB_PATH", car.path)
    db_reader.save_fresh_signals(signal(car.t, **{"1277": 1}))
    assert car.db._conn.execute("SELECT COUNT(*) FROM positions WHERE frame_ts = ?",
                                (int(car.t.timestamp() * 1000),)).fetchone()[0] == 2
    car.derive()
    assert car.events() == []
