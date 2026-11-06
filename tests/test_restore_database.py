"""Restore-database (Settings -> Export/Backup -> Restore): a full DB backup restored across a
reinstall loses NO data and KEEPS the freshly-entered login.

The tester's flow it enables: download the DB backup -> reinstall the add-on -> sign in -> upload
the backup here. The backup's own secrets were sealed with a different /data/secret.key (never
exported), so restore_database splices the CURRENT encrypted secrets (the login just entered) into
the restored DB; everything else comes from the backup byte-for-byte. CI-safe (no fastapi)."""
import os
import sqlite3

import pytest


def _point(monkeypatch, path):
    """Point DB_PATH + crypto secret.key + db_reader at `path`, fresh caches (auto-reverts)."""
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


def _raw(path, key):
    con = sqlite3.connect(path)
    r = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    con.close()
    return r[0] if r else None


def _count(path, table):
    con = sqlite3.connect(path)
    n = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    con.close()
    return n


def test_restore_keeps_all_data_and_fresh_login(tmp_path, monkeypatch):
    import db as poller_db

    # OLD install: data + a non-secret preference + OLD login (encrypted with OLD secret.key)
    old = str(tmp_path / "old.db")
    crypto, dbr = _point(monkeypatch, old)
    db = poller_db.Database(old)
    db._conn.execute("INSERT OR IGNORE INTO vehicles (id, vin) VALUES (1,'V')")
    for i in range(50):
        db.insert_raw_signal_changes(1, 1_700_000_000_000 + i, {"3235": str(i)})
    db._conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('charge_price_home','OLD-PREF')")
    db._conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('leapmotor_user',?)",
                     (crypto.encrypt("old-user"),))
    db._conn.commit()
    db.close()
    dbr.checkpoint()
    backup = open(old, "rb").read()
    old_signals = _count(old, "raw_signals_log")
    assert old_signals == 50

    # NEW install + a fresh login (encrypted with the NEW secret.key)
    new = str(tmp_path / "new.db")
    crypto, dbr = _point(monkeypatch, new)
    fresh = poller_db.Database(new)
    fresh._conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('leapmotor_user',?)",
                        (crypto.encrypt("new-user"),))
    fresh._conn.commit()
    fresh.close()
    dbr.checkpoint()

    # RESTORE the backup
    res = dbr.restore_database(backup)
    poller_db.Database(new).close()   # reopen = restart + migrations

    assert _count(new, "raw_signals_log") == old_signals              # 0 rows lost
    assert crypto.decrypt(_raw(new, "leapmotor_user")) == "new-user"  # login survived
    assert _raw(new, "charge_price_home") == "OLD-PREF"               # preference from backup
    assert res["secrets_preserved"] == 1
    assert not os.path.exists(new + "-wal") and not os.path.exists(new + "-shm")


def test_restore_rejects_garbage_and_foreign_db(tmp_path, monkeypatch):
    import db as poller_db

    new = str(tmp_path / "n.db")
    _crypto, dbr = _point(monkeypatch, new)
    poller_db.Database(new).close()
    before = _count(new, "settings")

    with pytest.raises(ValueError):
        dbr.restore_database(b"this is not a sqlite database")

    foreign = str(tmp_path / "foreign.db")
    con = sqlite3.connect(foreign)
    con.execute("CREATE TABLE x(a)")
    con.commit()
    con.close()
    with pytest.raises(ValueError):
        dbr.restore_database(open(foreign, "rb").read())

    # the live DB was NOT touched by the refused restores, and no temp file was left behind
    assert _count(new, "settings") == before
    assert not os.path.exists(new + ".restore.tmp")


def _gz(data: bytes) -> bytes:
    import gzip
    return gzip.compress(data)


def test_restore_reads_a_gzip_backup_as_the_export_writes_it(tmp_path, monkeypatch):
    """The export is a .db.gz (gzip_db_stream); what it wrote is what comes back."""
    import db as poller_db

    old = str(tmp_path / "old.db")
    _crypto, dbr = _point(monkeypatch, old)
    db = poller_db.Database(old)
    db._conn.execute("INSERT OR IGNORE INTO vehicles (id, vin) VALUES (1,'V')")
    for i in range(50):
        db.insert_raw_signal_changes(1, 1_700_000_000_000 + i, {"3235": str(i)})
    db._conn.commit()
    db.close()
    dbr.checkpoint()
    backup = b"".join(dbr.gzip_db_stream(old))
    assert backup[:2] == b"\x1f\x8b"

    new = str(tmp_path / "new.db")
    _crypto, dbr = _point(monkeypatch, new)
    poller_db.Database(new).close()
    # a chunk far smaller than the backup: the database is put together from many pieces
    monkeypatch.setattr(dbr._gunzip_backup, "__defaults__", (1024,))
    assert dbr.restore_database(backup)["counts"]["raw_signals_log"] == 50
    assert _count(new, "raw_signals_log") == 50
    assert not os.path.exists(new + ".restore.tmp")


def test_restore_refuses_a_gzip_that_grows_past_the_limit(tmp_path, monkeypatch):
    """A few kilobytes of gzip can hold gigabytes of zeros, and the whole of it used to be inflated in
    memory before anything looked at it. The backup is written a chunk at a time and refused at the
    limit, with the live database untouched and nothing left behind."""
    import db as poller_db

    new = str(tmp_path / "n.db")
    _crypto, dbr = _point(monkeypatch, new)
    poller_db.Database(new).close()
    before = _count(new, "settings")
    monkeypatch.setattr(dbr, "RESTORE_MAX_BYTES", 1024 * 1024)

    bomb = _gz(b"SQLite format 3\x00" + b"\x00" * (8 * 1024 * 1024))
    assert len(bomb) < 16 * 1024
    with pytest.raises(ValueError, match="too large"):
        dbr.restore_database(bomb)

    assert _count(new, "settings") == before
    assert not os.path.exists(new + ".restore.tmp")


@pytest.mark.parametrize("damage", [lambda gz: gz[:len(gz) // 2],           # cut short
                                    lambda gz: gz[:10] + b"\xff" * 64])     # not deflate at all
def test_restore_refuses_a_damaged_gzip(tmp_path, monkeypatch, damage):
    import db as poller_db

    new = str(tmp_path / "n.db")
    _crypto, dbr = _point(monkeypatch, new)
    poller_db.Database(new).close()

    with pytest.raises(ValueError, match="gzip"):
        dbr.restore_database(damage(_gz(b"SQLite format 3\x00" + os.urandom(64 * 1024))))
    assert not os.path.exists(new + ".restore.tmp")
