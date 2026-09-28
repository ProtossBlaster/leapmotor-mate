"""One read connection per thread, not one per read.

`db_reader._get()` opened a brand-new SQLite connection on every call, and the small reads are
called inside loops. Profiled on the lab's real database on 28/09/2026:

    get_vampire_drain   1610 connections opened for one card
    polling_summary       579
    get_battery_health    110

That is the single defect behind every performance fix of 27–28/09: `get_setting` costs ~124 µs
because the connection is new and its page cache is cold, not because the query is hard. 119 queries
in this module read through that same path.

The connection is READ-ONLY (`mode=ro`) and SQLite gives each statement its own read transaction, so
sharing one holds nothing back: a commit from the poller — a different process — is visible on the
very next read. That is what `test_a_write_from_elsewhere_is_seen_at_once` exists to prove, because
the previous attempt at this (a two-second cache on the timezone VALUE) was wrong for exactly that
reason and ten tests said so.

Three things must keep working, and each has its own test here:
  * the path can change (every test in this suite monkeypatches `DB_PATH`),
  * the FILE can be replaced under the path (a restore from backup, a migration),
  * a caller may still call `close()` on what it was handed — about a hundred of them do.
"""
import os
import pathlib
import sqlite3
import threading

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
# sys.path is conftest.py's job — it puts web/ before poller/, and BOTH hold a main.py.

import db as D  # noqa: E402
import db_reader  # noqa: E402


def _make(path):
    database = D.Database(str(path))
    conn = database._conn
    conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LVIN0000000000001','B10')")
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('timezone','Europe/Rome')")
    conn.commit()
    conn.close()
    return str(path)


@pytest.fixture
def counted(tmp_path, monkeypatch):
    """A database, and a count of how many real connections get opened."""
    path = _make(tmp_path / "one.db")
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    db_reader._drop_read_connection()
    opened = []
    real = db_reader._conn

    def counting(db_path):
        opened.append(db_path)
        return real(db_path)

    monkeypatch.setattr(db_reader, "_conn", counting)
    yield path, opened
    db_reader._drop_read_connection()


def test_many_reads_share_one_connection(counted):
    path, opened = counted
    for _ in range(50):
        db_reader.get_setting("timezone", "")
    assert len(opened) == 1, f"{len(opened)} connections opened for 50 reads of one setting"


def test_a_write_from_elsewhere_is_seen_at_once(counted):
    """The poller writes from another process. A shared connection must not hold a stale answer."""
    path, _ = counted
    assert db_reader.get_setting("timezone", "") == "Europe/Rome"
    writer = sqlite3.connect(path)
    writer.execute("UPDATE settings SET value = 'Europe/Paris' WHERE key = 'timezone'")
    writer.commit()
    writer.close()
    assert db_reader.get_setting("timezone", "") == "Europe/Paris", (
        "the shared connection served an answer from before someone else's commit"
    )


def test_changing_the_path_is_not_ignored(tmp_path, monkeypatch):
    """Every test in this suite points DB_PATH at its own file; a cache must follow it."""
    first = _make(tmp_path / "first.db")
    second = _make(tmp_path / "second.db")
    writer = sqlite3.connect(second)
    writer.execute("UPDATE settings SET value = 'Asia/Tokyo' WHERE key = 'timezone'")
    writer.commit(); writer.close()

    monkeypatch.setattr(db_reader, "DB_PATH", first)
    db_reader._drop_read_connection()
    assert db_reader.get_setting("timezone", "") == "Europe/Rome"
    monkeypatch.setattr(db_reader, "DB_PATH", second)
    assert db_reader.get_setting("timezone", "") == "Asia/Tokyo", (
        "the reader kept talking to the previous database after DB_PATH moved"
    )
    db_reader._drop_read_connection()


def test_a_replaced_file_is_reopened(tmp_path, monkeypatch):
    """A restore from backup swaps the file under the same name. The old inode must be let go."""
    path = _make(tmp_path / "live.db")
    other = _make(tmp_path / "restored.db")
    writer = sqlite3.connect(other)
    writer.execute("UPDATE settings SET value = 'America/Denver' WHERE key = 'timezone'")
    writer.commit(); writer.close()

    monkeypatch.setattr(db_reader, "DB_PATH", path)
    db_reader._drop_read_connection()
    assert db_reader.get_setting("timezone", "") == "Europe/Rome"
    os.replace(other, path)                       # same path, different file
    assert db_reader.get_setting("timezone", "") == "America/Denver", (
        "the reader is still holding the file that was replaced under it"
    )
    db_reader._drop_read_connection()


def test_each_thread_holds_its_own(counted):
    """A connection is not safe to hand between threads, so each one opens its own."""
    path, opened = counted
    seen = {}

    def read(name):
        seen[name] = db_reader._get()
        db_reader.get_setting("timezone", "")

    threads = [threading.Thread(target=read, args=(f"t{i}",)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    inner = {id(getattr(c, "_inner", c)) for c in seen.values()}
    assert len(inner) == 3, f"three threads shared {len(inner)} connection(s)"
    assert len(opened) == 3


def test_a_caller_closing_it_does_not_break_the_next_read(counted):
    """About a hundred call sites close what they were handed, and were right to when each got
    its own. Closing the shared one must not take the thread's reads down with it."""
    path, opened = counted
    db = db_reader._get()
    db.close()
    assert db_reader.get_setting("timezone", "") == "Europe/Rome", (
        "a caller's close() broke every later read on this thread"
    )
    assert len(opened) == 1, "close() threw the shared connection away"


def test_a_table_created_after_the_connection_does_not_eat_the_read(tmp_path, monkeypatch):
    """The one real defect sharing the connection exposed, and it was there all along.

    Refuels live in a table made on first use, and thirteen read paths call the create-if-missing
    helper on a READ-ONLY connection, most of them wrapping that call and their own query in one
    `except sqlite3.Error`. A fresh reader never raised: `CREATE TABLE IF NOT EXISTS` is a no-op
    against a connection whose cached schema already holds the table. A connection opened BEFORE the
    first refuel does try to write, raises "attempt to write a readonly database" — and the query
    behind it was skipped, so the total silently dropped every litre of petrol ever entered.
    """
    path = _make(tmp_path / "fuel.db")
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    db_reader._drop_read_connection()
    reader = db_reader._get()                  # opened while the table does not exist yet
    assert reader.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0] == 1

    writer = sqlite3.connect(path)
    db_reader._ensure_fuel_purchases(writer)
    writer.execute("INSERT INTO fuel_purchases (vehicle_id, ts, liters, price_per_l, total_cost)"
                   " VALUES (1,'2026-07-01T12:00:00+00:00',20,1.8,36)")
    writer.commit(); writer.close()

    db_reader._ensure_fuel_purchases(reader)   # must not raise on a read-only connection
    row = reader.execute("SELECT COALESCE(SUM(liters),0) FROM fuel_purchases").fetchone()
    assert row[0] == 20, f"the refuel is not visible to the connection that predates its table: {row}"
    db_reader._drop_read_connection()


def test_a_missing_database_still_raises_and_holds_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "absent.db"))
    db_reader._drop_read_connection()
    with pytest.raises(sqlite3.Error):
        db_reader._get().execute("SELECT 1").fetchone()
    path = _make(tmp_path / "absent.db")          # it appears later, as on a fresh install
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    assert db_reader.get_setting("timezone", "") == "Europe/Rome"
    db_reader._drop_read_connection()
