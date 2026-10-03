"""A change of the car's state is an event once two consecutive frames hold it.

One frame saying a door is open, with the next saying it is closed again, is a blink the cloud sends
often (a sunshade "open" for one poll in hundreds); it makes no event. Two frames make one, and the
event's time is the FIRST frame's: the second confirms the change, it does not move it, and so are
the position, charge level, odometer and temperatures stored with it. Both ends are stored, state 1
when the state begins and 0 when it ends.
"""
from events_fixture import Car


def _settle(car):
    """Two frames of the default state: the baseline every kind is confirmed in."""
    car.frame()
    car.frame()
    car.derive()
    assert car.events() == []


def test_a_single_frame_is_a_blink_and_makes_no_event(tmp_path):
    car = Car(tmp_path)
    _settle(car)
    car.frame(**{"1277": 1})           # the driver door open for one poll
    car.frame()
    car.frame()
    car.derive()
    assert car.events() == []


def test_two_frames_make_the_event_at_the_first_frames_time(tmp_path):
    car = Car(tmp_path)
    _settle(car)
    first = car.frame(**{"1277": 1})
    car.frame(**{"1277": 1})
    car.derive()
    assert car.events() == [("door_driver", 1, first["recorded_at"])]


def test_the_state_going_back_closes_the_span(tmp_path):
    car = Car(tmp_path)
    _settle(car)
    opened = car.frame(**{"1277": 1})
    car.frame(**{"1277": 1})
    closed = car.frame()
    car.frame()
    car.derive()
    assert car.events() == [("door_driver", 1, opened["recorded_at"]),
                            ("door_driver", 0, closed["recorded_at"])]


def test_the_event_keeps_the_first_frames_readings(tmp_path):
    """The climate's end says what it did from these: the cabin when it started and when it ended."""
    car = Car(tmp_path)
    _settle(car)
    first = car.frame(**{"1938": 1, "1349": 14.0, "2183": 21.0})
    car.db._conn.execute("UPDATE positions SET outside_temp = 8.5 WHERE id = ?", (first["id"],))
    car.db._conn.commit()
    car.frame(**{"1938": 1, "1349": 15.0, "2183": 22.0})
    car.derive()
    (row,) = car.db._conn.execute("SELECT * FROM events").fetchall()
    assert (row["kind"], row["at"], row["soc"], row["odometer_km"]) == ("climate", first["recorded_at"], 80.0, 12345.0)
    assert (row["inside_temp"], row["climate_target_temp"], row["outside_temp"]) == (14.0, 21.0, 8.5)


def test_the_first_frame_of_a_kind_is_its_baseline_not_an_event(tmp_path):
    """Mate starting on a car with a door open learns that the door is open; it does not report
    the door opening at the moment it started."""
    car = Car(tmp_path)
    car.frame(**{"1277": 1})
    car.frame(**{"1277": 1})
    car.derive()
    assert car.events() == []


def test_every_kind_of_the_table_is_detected(tmp_path):
    """One frame pair per signal, so a rule that reads the wrong column shows up by name."""
    car = Car(tmp_path)
    _settle(car)
    opened = {"unlocked": {"1298": 0}, "door_driver": {"1277": 1}, "door_passenger": {"1278": 1},
              "door_rear_left": {"1279": 1}, "door_rear_right": {"1280": 1}, "trunk": {"1281": 1},
              "window_fl": {"1693": 2}, "window_rl": {"1695": 2}, "sunshade": {"1724": 40},
              "cable": {"1149": 1}, "v2l": {"47": 2}, "climate": {"1938": 1}, "defrost": {"1945": 2},
              "rapid_heat": {"2681": 2}, "rapid_cool": {"2669": 2}, "ready": {"1258": 1}}
    for kind, sig in opened.items():
        car.frame(**sig)
        car.frame(**sig)
        car.frame()
        car.frame()
        car.derive()
        assert [(k, s) for k, s, _ in car.events()][-2:] == [(kind, 1), (kind, 0)], kind
