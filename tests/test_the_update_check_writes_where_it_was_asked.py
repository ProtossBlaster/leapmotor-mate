"""The update check must write its timestamp to the database it was asked about.

Surfaced on 28/09/2026 while making reads share one connection per thread. `get_update_status` reads
a cached version and, when the cache is stale, starts a background thread that calls GitHub with a
six-second timeout and then writes `update_checked_at`. The write resolves `db_reader.DB_PATH` when
the thread finally runs — which can be a different database by then.

In the suite that is visible: a thread started by one test wrote into another test's database and
failed with `no such table: settings`. The read getting cheaper did not create that; it moved the
timing so the thread landed on a different test. Nothing was corrupted, because the write failed.

In production DB_PATH never moves, so this guard changes nothing there. What it does is make the rule
explicit: a background job writes to the database it was scheduled for, or not at all. `_conn_rw`
opens the path with sqlite3's default flags, which CREATE an empty database when the file is not
there — so a thread pointed at the wrong path does not just fail, it can leave a stray database
behind that looks like a fresh install.
"""
import pathlib
import threading
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

import db as D  # noqa: E402
import db_reader  # noqa: E402
import update_check  # noqa: E402


def _make(path):
    database = D.Database(str(path))
    database._conn.commit()
    database._conn.close()
    return str(path)


def test_a_refresh_scheduled_for_one_database_does_not_write_to_another(tmp_path, monkeypatch):
    theirs = _make(tmp_path / "theirs.db")
    monkeypatch.setattr(db_reader, "DB_PATH", theirs)

    written = []
    monkeypatch.setattr(db_reader, "set_setting",
                        lambda key, value: written.append((db_reader.DB_PATH, key, value)))
    monkeypatch.setattr(update_check.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))

    # scheduled while DB_PATH names `theirs`, but the path moves before the thread runs
    update_check._refresh_for(theirs)
    assert written, "the refresh wrote nothing at all"
    assert written[0][0] == theirs

    written.clear()
    mine = _make(tmp_path / "mine.db")
    monkeypatch.setattr(db_reader, "DB_PATH", mine)
    update_check._refresh_for(theirs)
    assert written == [], (
        f"the refresh scheduled for one database wrote into the one that replaced it: {written}"
    )


def test_the_check_still_records_when_the_path_is_the_same(tmp_path, monkeypatch):
    """The guard must not turn the check off: same database, the timestamp is still written."""
    path = _make(tmp_path / "same.db")
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setattr(update_check.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    update_check._refresh_for(path)
    assert db_reader.get_setting("update_checked_at", "") not in ("", None), (
        "the check no longer records that it ran, so it would run again on every render"
    )


def test_the_thread_is_started_for_a_named_database(tmp_path, monkeypatch):
    """`_maybe_refresh` must capture the path, not leave the thread to resolve it later."""
    path = _make(tmp_path / "named.db")
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    started = []

    class _Recording:
        def __init__(self, target=None, args=(), daemon=None, **kwargs):
            started.append((target, args))

        def start(self):
            return None

    monkeypatch.setattr(update_check.threading, "Thread", _Recording)
    monkeypatch.setattr(update_check, "_checking", False)
    db_reader.set_setting("update_checked_at", "0")
    update_check._maybe_refresh()
    assert started, "no refresh was scheduled"
    target, args = started[0]
    assert args and args[0] == path, (
        f"the thread was started without the database it is meant to write to: {args}"
    )
