"""Backup compression (disc #264, @joeyoong): the DB backup downloads gzip-compressed, and restore
transparently accepts BOTH the new .db.gz AND the old raw .db — so a backup already downloaded still
restores. stdlib gzip only, no format lock-in, no toggle. CI-safe: the export stream is a db_reader
generator exercised directly (no httpx/TestClient needed)."""
import gzip
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


def _make_backup(tmp_path, monkeypatch, n=40):
    """A valid Mate backup (raw SQLite bytes) with `n` signal rows, checkpointed (no WAL sidecar)."""
    import db as poller_db
    p = str(tmp_path / "src.db")
    _c, dbr = _point(monkeypatch, p)
    db = poller_db.Database(p)
    db._conn.execute("INSERT OR IGNORE INTO vehicles (id, vin) VALUES (1,'V')")
    for i in range(n):
        db.insert_raw_signal_changes(1, 1_700_000_000_000 + i, {"3235": str(i)})
    db._conn.commit()
    db.close()
    dbr.checkpoint()
    return open(p, "rb").read()


def _count(path, table):
    con = sqlite3.connect(path)
    n = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    con.close()
    return n


def test_export_stream_is_gzip_and_roundtrips(tmp_path, monkeypatch):
    """The export generator yields a valid gzip stream that gunzips back to the EXACT db bytes."""
    raw = _make_backup(tmp_path, monkeypatch)
    src = str(tmp_path / "export_src.db")
    with open(src, "wb") as f:
        f.write(raw)
    import db_reader
    blob = b"".join(db_reader.gzip_db_stream(src))
    assert blob[:2] == b"\x1f\x8b"            # gzip magic — the browser saves a real .gz
    assert gzip.decompress(blob) == raw       # exact round-trip, nothing lost
    assert len(blob) < len(raw)               # it actually got smaller


def test_restore_accepts_gzipped_backup(tmp_path, monkeypatch):
    raw = _make_backup(tmp_path, monkeypatch)
    new = str(tmp_path / "new.db")
    _c, dbr = _point(monkeypatch, new)
    import db as poller_db
    poller_db.Database(new).close()

    dbr.restore_database(gzip.compress(raw))           # the NEW .db.gz path
    poller_db.Database(new).close()                    # reopen = restart + migrations
    assert _count(new, "raw_signals_log") == 40


def test_restore_still_accepts_raw_backup(tmp_path, monkeypatch):
    """Backward compatibility: a raw .db downloaded before this feature still restores."""
    raw = _make_backup(tmp_path, monkeypatch)
    new = str(tmp_path / "new.db")
    _c, dbr = _point(monkeypatch, new)
    import db as poller_db
    poller_db.Database(new).close()

    dbr.restore_database(raw)
    poller_db.Database(new).close()
    assert _count(new, "raw_signals_log") == 40


def test_restore_file_input_accepts_the_compressed_backup():
    """The restore file-picker must accept the new .db.gz (not just .db) — otherwise the browser greys
    out the very file the Export button now produces."""
    import pathlib
    import db_reader
    tpl = (pathlib.Path(db_reader.__file__).resolve().parent / "templates" / "settings.html").read_text()
    line = next(l for l in tpl.splitlines() if 'name="file"' in l and "accept=" in l)
    assert ".gz" in line and ".db" in line, line.strip()


def test_restore_rejects_corrupt_gzip_without_touching_db(tmp_path, monkeypatch):
    """A gzip magic header with a garbage body must raise ValueError, leave the live DB untouched,
    and drop no temp file — same guarantee as a non-SQLite upload."""
    new = str(tmp_path / "n.db")
    _c, dbr = _point(monkeypatch, new)
    import db as poller_db
    poller_db.Database(new).close()
    before = _count(new, "settings")

    with pytest.raises(ValueError):
        dbr.restore_database(b"\x1f\x8b\x08" + b"this is not a real gzip stream")

    assert _count(new, "settings") == before
    assert not os.path.exists(new + ".restore.tmp")
