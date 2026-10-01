"""The poller waits for the web to finish creating the schema instead of dying on it.

01/10/2026, the Desktop 1.2.0 build: the frozen app started both processes on an empty data
directory and the poller died on its first statement — `PRAGMA journal_mode=WAL` in
`Database.__init__` → `sqlite3.OperationalError: database is locked`, in the same second it started.
The web creates the schema at import (`web/main.py::_ensure_schema`) in a write transaction; asking
to change the journal mode while another connection writes is refused AT ONCE, without the busy
timeout, because waiting could deadlock. The race was there before 4.7.7; 4.7.7 made it likelier on
a new installation, since both processes now provision the application material behind one lock and
leave it at the same instant.

Only a new installation meets it: an existing database is already in WAL, where the pragma asks for
nothing. So the poller retries the switch for a bounded time — the schema takes milliseconds.
"""
import sqlite3
import threading
import time

import db as D
import pytest


def test_the_poller_opens_a_database_the_web_is_still_writing(tmp_path):
    path = str(tmp_path / "fresh.db")
    web = sqlite3.connect(path, check_same_thread=False)
    web.execute("BEGIN IMMEDIATE")                    # the web's schema transaction, still open
    web.execute("CREATE TABLE IF NOT EXISTS placeholder (id INTEGER)")
    released = threading.Event()

    def finish_the_schema():
        time.sleep(0.6)
        web.commit()
        released.set()

    threading.Thread(target=finish_the_schema, daemon=True).start()
    db = D.Database(path)                             # used to raise "database is locked" at once
    assert released.is_set()
    assert str(db.journal_mode).lower() == "wal"


def test_a_lock_that_never_goes_away_still_surfaces(tmp_path, monkeypatch):
    """Bounded: a database held for good is reported, not waited on for ever."""
    path = str(tmp_path / "held.db")
    holder = sqlite3.connect(path)
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("CREATE TABLE IF NOT EXISTS placeholder (id INTEGER)")
    monkeypatch.setattr(D, "_WAL_SWITCH_WAIT_S", 0.3, raising=False)
    started = time.monotonic()
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        D.Database(path)
    assert time.monotonic() - started < 5
    holder.rollback()
