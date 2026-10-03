"""A poller restarted between any two frames derives the same events as one that never stopped.

The detector's memory — the reading position, the state each kind was last confirmed in, a change
waiting for its second frame — is written with the events it produced, in one transaction, so a
new process picks up exactly where the old one was. The sequence below has every place a restart
could do damage: before the first event, while a change waits with the frame served twice, on a
frame that says nothing about READY, and between two plug-ins of the cable.
"""
from events_fixture import Car

SEQUENCE = (
    {}, {},
    {"1277": 1},                      # the door seen open once…
    "repeat",                         # …the same frame again…
    {"1277": 1},                      # …and a second frame: the event
    {"1258": None}, {"1258": None},   # frames without READY
    {}, {},
    {"1149": 1}, {"1149": 1},         # the cable in
    {"1149": 0}, {"1149": 0},         # and out
    {"1149": 1},                      # and in again, half-way to counting
    {"1149": 1},
    {"1298": 0},
)


def _run(tmp_path, name, restart_after_each=False, max_rows=5000):
    car = Car(tmp_path, name)
    for step in SEQUENCE:
        if step == "repeat":
            car.repeat(**{"1277": 1})
        else:
            car.frame(**step)
        if restart_after_each:
            car.derive(max_rows)
            car.restart()
    car.derive(max_rows)
    return [(k, s) for k, s, _ in car.events()], car


def test_a_restart_after_every_frame_gives_the_same_events(tmp_path):
    straight, _ = _run(tmp_path, "straight.db")
    restarted, _ = _run(tmp_path, "restarted.db", restart_after_each=True)
    assert straight == [("door_driver", 1), ("door_driver", 0), ("cable", 1), ("cable", 0),
                        ("cable", 1)]
    assert restarted == straight


def test_reading_the_history_one_row_at_a_time_gives_the_same_events(tmp_path):
    straight, _ = _run(tmp_path, "straight.db")
    _, car = _run(tmp_path, "sliced.db", max_rows=1)
    while car.derive(1):
        pass
    assert [(k, s) for k, s, _ in car.events()] == straight
