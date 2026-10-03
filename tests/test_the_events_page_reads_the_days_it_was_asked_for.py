"""The Events page composes the window it was asked for, once — not the whole history, per part.

Point 2 of the review of #385. Three of the four sources read everything and let the range be
applied afterwards, in Python: `_trip_moments` and `_charge_moments` composed every trip and every
charge ever recorded, and `_command_moments` had no bound at all. Composing a session is not free —
its group, its source, its fuel, its place — so the page's cost followed the length of the history
instead of the days asked for. And every scroll paid it again, because a part is a slice of a
composition that was thrown away as soon as it was made.

Measured back to back in a container limited to a quarter of a core, on a database of 634 trips, 35
charges and 4,516 events, best of three:

                    before      after
    3 days          0.108 s     0.097 s
    30 days         0.110 s     0.101 s
    all             0.595 s     0.594 s
    all, part 1     0.595 s     0.001 s
    all, part 2     0.795 s     0.001 s

So be exact about which half does what. The **window** removes the composing of sessions outside it:
on this database that is 19 % of the default view, because 634 trips are cheap — it is the share
that grows with the history, and it was the whole history every time. The **cache** removes the
composition itself from a later part, which is the figure that was embarrassing.

What neither of them touches, profiled on the same run: `_signal_moments` is 77 % of the default
view, because the pairing reads the whole `events` table in id order whatever the window — an end
inside it can have its start outside, and that is how it is found. For the `all` range the cost is
instead spread over the rows themselves: laying out the tracks 27 %, composing each row most of the
rest.

A window keeps what OVERLAPS it, not what begins in it: a charge that began the evening before and
ended in the morning belongs to the morning too, and its end row is what the reader sees there.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import db_reader
from events_fixture import Car, event_row, grouped, rows, web

ZONE = ZoneInfo("Europe/Warsaw")
DAY = datetime.now(ZONE).date() - timedelta(days=1)


def _at(day, hh, mm=0):
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=ZONE).astimezone(timezone.utc)


def a_car(tmp_path, monkeypatch):
    car = Car(tmp_path, name="window.db")
    web(car, monkeypatch)
    return car


def trip(car, start, end, **cols):
    keys = "".join(f", {k}" for k in cols)
    marks = "".join(", ?" for _ in cols)
    car.db._conn.execute(
        f"INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km{keys})"
        f" VALUES (?, ?, ?, 10.0{marks})",
        (car.vid, start.isoformat(), end.isoformat(), *cols.values()))
    car.db._conn.commit()


def charge(car, start, end):
    car.db._conn.execute(
        "INSERT INTO charges (vehicle_id, started_at, ended_at, start_soc, end_soc,"
        " energy_added_kwh) VALUES (?, ?, ?, 40, 60, 13.0)",
        (car.vid, start.isoformat(), end.isoformat()))
    car.db._conn.commit()


def command(car, at):
    car.db._conn.execute(
        "CREATE TABLE IF NOT EXISTS command_log (id INTEGER PRIMARY KEY, vin TEXT, ts TEXT,"
        " action TEXT, outcome TEXT)")
    car.db._conn.execute(
        "INSERT INTO command_log (vin, ts, action, outcome) VALUES (?, ?, 'lock', 'ok')",
        ("VINEVENTS00000001", at.isoformat()))
    car.db._conn.commit()


def ids(day=DAY, **query):
    return [m["anchor"] for m in rows(grouped(date_from=day.isoformat(), date_to=day.isoformat(), **query))]


# ── the window ───────────────────────────────────────────────────────────────

def test_a_session_of_another_day_is_not_composed_at_all(tmp_path, monkeypatch):
    car = a_car(tmp_path, monkeypatch)
    trip(car, _at(DAY, 9), _at(DAY, 9, 30))
    trip(car, _at(DAY - timedelta(days=40), 9), _at(DAY - timedelta(days=40), 9, 30))
    charge(car, _at(DAY - timedelta(days=40), 20), _at(DAY - timedelta(days=40), 23))
    command(car, _at(DAY - timedelta(days=40), 21))
    assert ids() == ["ev-trip-1-off", "ev-trip-1-on"]


def test_a_session_that_crosses_into_the_window_is_kept(tmp_path, monkeypatch):
    """The reason the bound is an overlap and not a start: the reader of this day must see the
    charge end on it, and the row carries the whole session's figures."""
    car = a_car(tmp_path, monkeypatch)
    charge(car, _at(DAY - timedelta(days=1), 22), _at(DAY, 7))
    out = rows(grouped(date_from=DAY.isoformat(), date_to=DAY.isoformat()))
    assert [m["anchor"] for m in out] == ["ev-charge-1-off"]
    assert out[0]["energy_kwh"] == 13.0


def test_a_session_that_crosses_out_of_the_window_is_kept(tmp_path, monkeypatch):
    car = a_car(tmp_path, monkeypatch)
    trip(car, _at(DAY, 23, 30), _at(DAY + timedelta(days=1), 0, 20))
    assert ids() == ["ev-trip-1-on"]


def test_a_command_outside_the_window_is_left_out(tmp_path, monkeypatch):
    car = a_car(tmp_path, monkeypatch)
    command(car, _at(DAY, 10))
    command(car, _at(DAY - timedelta(days=5), 10))
    assert ids() == ["ev-command-1"]


def test_the_whole_history_still_comes_back_when_it_is_asked_for(tmp_path, monkeypatch):
    car = a_car(tmp_path, monkeypatch)
    trip(car, _at(DAY, 9), _at(DAY, 9, 30))
    trip(car, _at(DAY - timedelta(days=200), 9), _at(DAY - timedelta(days=200), 9, 30))
    assert len(rows(grouped(range="all"))) == 4


def test_a_reader_bounded_on_its_own_keeps_the_overlap(tmp_path, monkeypatch):
    """`get_trips` and `get_charges` grew the window, so their own rule is worth stating: the
    sessions that overlap it, newest first, and nothing else."""
    car = a_car(tmp_path, monkeypatch)
    trip(car, _at(DAY, 9), _at(DAY, 10))
    trip(car, _at(DAY - timedelta(days=1), 23), _at(DAY, 1))
    trip(car, _at(DAY - timedelta(days=9), 9), _at(DAY - timedelta(days=9), 10))
    lo = _at(DAY, 0).isoformat()
    hi = _at(DAY + timedelta(days=1), 0).isoformat()
    assert [t["id"] for t in db_reader.get_trips(since=lo, until=hi)] == [1, 2]
    assert len(db_reader.get_trips()) == 3


def test_the_window_reaches_the_readers_and_not_only_the_result(tmp_path, monkeypatch):
    """The tests above pass either way, and that is the point: the old page composed everything and
    filtered afterwards, so it printed the same rows — it only paid for the whole history to do it.
    A test of a cost has to pin the mechanism, so this one watches what the readers are ASKED for.

    The day asked for is one local day, which is 22:00 to 22:00 UTC in this zone; a session is kept
    when it overlaps that, so the bounds are the window's own ends."""
    car = a_car(tmp_path, monkeypatch)
    trip(car, _at(DAY, 9), _at(DAY, 10))
    asked = {}

    def watch(name):
        real = getattr(db_reader, name)

        def reader(**kw):
            asked[name] = kw
            return real(**kw)
        monkeypatch.setattr(db_reader, name, reader)

    watch("get_trips")
    watch("get_charges")
    command(car, _at(DAY, 10))
    command(car, _at(DAY - timedelta(days=5), 10))
    ids()
    lo = _at(DAY, 0).isoformat()
    hi = _at(DAY + timedelta(days=1), 0).isoformat()
    assert asked["get_trips"]["since"] == lo and asked["get_trips"]["until"] == hi
    assert asked["get_charges"]["since"] == lo and asked["get_charges"]["until"] == hi
    # The commands have no reader of their own to watch, so the bound is read where it is written.
    assert [m["id"] for m in db_reader._command_moments(
        db_reader._get(), _at(DAY, 0), _at(DAY + timedelta(days=1), 0))] == [1]
    assert len(db_reader._command_moments(db_reader._get(), None,
                                          _at(DAY + timedelta(days=1), 0))) == 2


# ── the parts ────────────────────────────────────────────────────────────────

def a_long_day(car, n=40):
    for k in range(n):
        event_row(car, "unlocked", _at(DAY, 8) + timedelta(minutes=2 * k))
        event_row(car, "unlocked", _at(DAY, 8) + timedelta(minutes=2 * k + 1), state=0)


def test_a_later_part_is_sliced_from_the_list_already_composed(tmp_path, monkeypatch):
    """The whole point: asking for the next part does not read the four sources again."""
    car = a_car(tmp_path, monkeypatch)
    a_long_day(car)
    monkeypatch.setattr(db_reader, "EVENTS_PART_ROWS", 10)
    flt = db_reader.EventFilter.from_query(date_from=DAY.isoformat(), date_to=DAY.isoformat())
    t = db_reader.i18n.get_t("en")
    first = db_reader.get_events_grouped(flt, t, "en")

    composed = []
    real = db_reader._signal_moments
    monkeypatch.setattr(db_reader, "_signal_moments",
                        lambda *a, **k: composed.append(1) or real(*a, **k))
    second = db_reader.get_events_grouped(flt, t, "en", part=1, asked=first["version"])
    assert composed == [], "the sources were read again"
    assert second["part"] == 1 and second["version"] == first["version"]
    assert second["lines"] and second["lines"] != first["lines"]


def test_a_row_written_meanwhile_is_composed_again_and_resets_the_part(tmp_path, monkeypatch):
    """The guard that keeps the parts of one list consistent is untouched: when the list has
    changed, the part asked for comes back as the first one. The cache is validated by the
    database's own write counter, not by the version the client sends, so it cannot hide a write."""
    car = a_car(tmp_path, monkeypatch)
    a_long_day(car)
    monkeypatch.setattr(db_reader, "EVENTS_PART_ROWS", 10)
    flt = db_reader.EventFilter.from_query(date_from=DAY.isoformat(), date_to=DAY.isoformat())
    t = db_reader.i18n.get_t("en")
    first = db_reader.get_events_grouped(flt, t, "en")

    event_row(car, "trunk", _at(DAY, 7))                       # another connection commits
    again = db_reader.get_events_grouped(flt, t, "en", part=1, asked=first["version"])
    assert again["version"] != first["version"]
    assert again["part"] == 0, "the list changed, so the reader starts at the top of the new one"


def test_another_filter_is_composed_on_its_own(tmp_path, monkeypatch):
    car = a_car(tmp_path, monkeypatch)
    a_long_day(car)
    monkeypatch.setattr(db_reader, "EVENTS_PART_ROWS", 10)
    t = db_reader.i18n.get_t("en")
    whole = db_reader.EventFilter.from_query(date_from=DAY.isoformat(), date_to=DAY.isoformat())
    first = db_reader.get_events_grouped(whole, t, "en")
    narrow = db_reader.EventFilter.from_query(date_from=DAY.isoformat(), date_to=DAY.isoformat(),
                                              kind=("trunk",))
    out = db_reader.get_events_grouped(narrow, t, "en", part=1, asked=first["version"])
    assert out["count"] == 0 and out["part"] == 0, "a different question, answered from the database"


def test_the_write_counter_is_the_same_number_on_every_thread(tmp_path, monkeypatch):
    """What made the cache unsound at first, and the reason it reads the counter on a connection of
    its own. `PRAGMA data_version` is PER CONNECTION: it moves when another connection commits, and
    its value means nothing outside the connection that read it. The web answers requests from a
    thread pool with a read connection per thread, so a value taken on one thread compared with a
    value taken on another compares two unrelated counters — and they can match by accident.

    Here the same question is asked from several threads around one write, and the answers have to
    agree: the same before, the same after, and different across it."""
    import threading
    car = a_car(tmp_path, monkeypatch)
    trip(car, _at(DAY, 9), _at(DAY, 10))

    def asked_from_threads(n=4):
        seen, lock = [], threading.Lock()

        def ask():
            v = db_reader._data_version()
            with lock:
                seen.append(v)
        threads = [threading.Thread(target=ask) for _ in range(n)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        return set(seen)

    before = asked_from_threads()
    assert len(before) == 1, before
    event_row(car, "trunk", _at(DAY, 7))              # another connection commits
    after = asked_from_threads()
    assert len(after) == 1 and after != before
