"""A restored backup goes into the live database, not over it.

On Windows a file another process holds open cannot be replaced: `os.replace` raises
PermissionError (measured on MateDesktop 1.2.0 with Mate 4.7.17: "[WinError 5] Accesso negato"),
and the poller holds the database open for as long as it runs. The restore swapped the file, so on
MateDesktop for Windows every restore answered HTTP 500 with that error (#383, @matttiaromano). The restore now copies the backup's pages into the live database with
SQLite's own backup API, which goes through SQLite's locks and never touches the file name.
"""
import os
import sqlite3
import sys

import pytest


def _point(monkeypatch, path):
    import crypto
    import db_reader
    monkeypatch.setenv("DB_PATH", path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setattr(crypto, "_fernet", None)
    try:
        db_reader._get.cache_clear()
    except Exception:  # noqa: BLE001
        pass
    return crypto, db_reader


def _backup_with_rows(tmp_path, monkeypatch, n):
    import db as poller_db
    old = str(tmp_path / "old.db")
    _point(monkeypatch, old)
    db = poller_db.Database(old)
    db._conn.execute("INSERT OR IGNORE INTO vehicles (id, vin) VALUES (1,'V')")
    for i in range(n):
        db.insert_raw_signal_changes(1, 1_700_000_000_000 + i, {"3235": str(i)})
    db._conn.commit()
    db.close()
    return open(old, "rb").read()


@pytest.fixture
def windows_file_lock(monkeypatch):
    """Windows refuses to replace a file another process holds open, and the poller always does.

    On Windows itself nothing is simulated: the held connection below is the real lock, and the CI
    job there runs this file. Elsewhere `os.replace` is made to answer as Windows does."""
    if sys.platform == "win32":
        return
    def refuse(src, dst):
        raise PermissionError(5, "Access is denied", dst)   # as measured on Windows
    monkeypatch.setattr(os, "replace", refuse)


def test_a_restore_succeeds_while_the_poller_holds_the_database(tmp_path, monkeypatch, windows_file_lock):
    import db as poller_db
    backup = _backup_with_rows(tmp_path, monkeypatch, 50)

    live = str(tmp_path / "live.db")
    _crypto, dbr = _point(monkeypatch, live)
    poller = poller_db.Database(live)              # the poller's connection, open as it always is
    assert poller._conn.execute("SELECT COUNT(*) FROM raw_signals_log").fetchone()[0] == 0

    res = dbr.restore_database(backup)

    assert res["counts"]["raw_signals_log"] == 50
    # The poller's own connection reads the restored data: the file was never swapped under it.
    assert poller._conn.execute("SELECT COUNT(*) FROM raw_signals_log").fetchone()[0] == 50
    poller.close()
    assert not os.path.exists(live + ".restore.tmp")


def test_a_restore_keeps_the_fresh_login_while_the_poller_holds_the_database(tmp_path, monkeypatch, windows_file_lock):
    import db as poller_db
    backup = _backup_with_rows(tmp_path, monkeypatch, 3)

    live = str(tmp_path / "live.db")
    crypto, dbr = _point(monkeypatch, live)
    poller = poller_db.Database(live)
    poller._conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('leapmotor_user',?)",
                         (crypto.encrypt("new-user"),))
    poller._conn.commit()

    res = dbr.restore_database(backup)
    poller.close()

    con = sqlite3.connect(live)
    stored = con.execute("SELECT value FROM settings WHERE key='leapmotor_user'").fetchone()[0]
    con.close()
    assert crypto.decrypt(stored) == "new-user"
    assert res["secrets_preserved"] == 1
