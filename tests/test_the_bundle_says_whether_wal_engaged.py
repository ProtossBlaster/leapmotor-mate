"""A bundle must say which journal mode the database is really in.

`PRAGMA journal_mode=WAL` does not fail when it cannot be honoured — it falls back and reports the
mode it settled on, and we threw that answer away (`poller/db.py`, the result of the execute was
never read). Nothing logged it, and no bundle carried it, so the one fact that separates two
opposite diagnoses was unavailable on every installation.

Why it separates them: in WAL, readers do not block writers. Outside WAL they do. #338 (@dommi1966,
A10 on standalone Docker on a QNAP NAS, 28/09/2026) shows 1013 `database is locked` messages and 747
frames lost in four hours, the poller log ending mid-stream and the heartbeat three hours stale while
the web process still answered. If WAL is engaged there, the contention is writer-against-writer; if
it silently fell back — which is what a filesystem without proper shared-memory support does, and a
NAS share is exactly that — then a long-lived per-thread read connection can starve the writer, and
that is ours. The same day, #298 (@arzthilfe, add-on) showed the same error eight times.

We cannot ask them to run anything: a bundle is what a non-technical owner can produce, and it must
carry the answer by itself. So the mode is read from the database and printed, and it is printed as
a WARNING when it is not `wal`, because a reader who sees "delete" must not have to know what that
implies.
"""
import pytest

pytest.importorskip("cryptography", reason="db_reader needs cryptography")


def _db(tmp_path, monkeypatch):
    import db as D
    import db_reader
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    pdb._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LFZTEST0000000001','A10')")
    pdb._conn.commit()
    pdb._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    import diagnostics
    monkeypatch.setattr(diagnostics.db_reader, "DB_PATH", path)
    return path


def test_the_reader_reports_the_mode_the_database_is_actually_in(tmp_path, monkeypatch):
    import db_reader
    _db(tmp_path, monkeypatch)
    assert db_reader.journal_mode() == "wal", "a file database created by the poller runs in WAL"


def test_a_database_that_could_not_take_wal_reports_what_it_took(tmp_path, monkeypatch):
    """An in-memory database is a real case where WAL cannot engage — no mocking needed."""
    import db_reader
    monkeypatch.setattr(db_reader, "DB_PATH", ":memory:")
    assert db_reader.journal_mode() not in ("wal", None), \
        "the fallback mode must be reported, not hidden"


def test_the_bundle_header_carries_the_mode(tmp_path, monkeypatch):
    import diagnostics
    _db(tmp_path, monkeypatch)
    info = diagnostics.build_system_info("4.5.5")
    assert info.get("journal_mode") == "wal", f"no journal mode in the snapshot: {info.get('journal_mode')!r}"
    out = diagnostics.build_bundle("4.5.5", parts=("info",))
    assert "Journal mode" in out, "the bundle header does not name the journal mode"
    assert "wal" in out.lower()


def test_a_fallback_is_printed_as_a_warning_not_as_a_word(tmp_path, monkeypatch):
    """"delete" means nothing to the owner reading it; what it costs them has to be on the line."""
    import diagnostics
    _db(tmp_path, monkeypatch)
    line = diagnostics._journal_line("delete")
    assert "delete" in line
    assert "⚠️" in line, "a database out of WAL is a warning, not a fact among others"
    assert "block" in line.lower() or "bloc" in line.lower(), \
        f"the line must say what it costs — readers blocking writers: {line!r}"
    assert "⚠️" not in diagnostics._journal_line("wal"), "WAL is the normal case, not a warning"


def test_the_poller_keeps_the_mode_it_got(tmp_path):
    import db as D
    pdb = D.Database(str(tmp_path / "p.db"))
    try:
        assert pdb.journal_mode == "wal", \
            "the poller must keep the answer the pragma gave, so it can complain about it"
    finally:
        pdb._conn.close()
