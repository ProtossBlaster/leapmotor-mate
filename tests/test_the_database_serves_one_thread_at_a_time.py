"""The poller's one SQLite connection is used by one thread at a time (#495, @chengler).

The poller opens a single connection with `check_same_thread=False` and two threads use it: the
poll loop, which writes every frame, and paho's network thread, which runs a Home Assistant command
and reads the car's PIN for it. `_API_LOCK` covers only the HTTP call of the poll loop, not its
database work, so the two met on the connection: SQLite answered `bad parameter or other API misuse`
to both, the PIN lookup swallowed it and sent the command with no PIN ("Vehicle PIN is missing"),
and the poll loop lost the frame ("Frame processing failed") in the same second.

What holds: two threads calling the database never have a statement on the connection at once, and
a PIN read while the other thread writes is always the car's PIN.
"""
import threading
import time

import client
import db as D


VIN = "LFZ00000000000495"


class _WatchedConnection:
    """The real connection, with a note of when two threads are inside `execute` together."""

    def __init__(self, conn):
        self._conn = conn
        self._inside = 0
        self._guard = threading.Lock()
        self.overlaps = 0

    def execute(self, *args, **kwargs):
        with self._guard:
            self._inside += 1
            if self._inside > 1:
                self.overlaps += 1
        try:
            time.sleep(0.0005)  # widen the window a real statement leaves open
            return self._conn.execute(*args, **kwargs)
        finally:
            with self._guard:
                self._inside -= 1

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _run_together(*targets):
    threads = [threading.Thread(target=t) for t in targets]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def test_two_threads_never_share_the_connection_at_once(tmp_path):
    database = D.Database(str(tmp_path / "m.db"))
    database.set_secret(f"leapmotor_pin_{VIN.lower()}", "1234")
    watched = _WatchedConnection(database._conn)
    database._conn = watched

    def reader():
        for _ in range(200):
            database.get_operate_pin(VIN)

    def writer():
        for i in range(200):
            database.set_setting("poll_marker", str(i))

    _run_together(reader, writer)

    assert watched.overlaps == 0


def test_a_command_gets_the_pin_while_the_poll_loop_writes(tmp_path):
    database = D.Database(str(tmp_path / "p.db"))
    database.set_secret(f"leapmotor_pin_{VIN.lower()}", "1234")
    empty, errors = [], []

    class _Api:
        operation_password = ""

    def command_thread():
        api = _Api()
        for _ in range(3000):
            client.use_pin_of(api, database, VIN)
            if api.operation_password != "1234":
                empty.append(api.operation_password)

    def poll_thread():
        for i in range(3000):
            try:
                database.set_setting("poll_marker", str(i))
                database.get_setting("poll_marker")
            except Exception as exc:  # noqa: BLE001 — every failure is counted, none is expected
                errors.append(repr(exc))

    _run_together(command_thread, poll_thread)

    assert (empty, errors[:3]) == ([], [])
