"""GitHub #54 — the account TLS cert Leapmotor issues at login is a per-login temp file that
gets cleaned up. The shared session must survive a vanished cert by RE-CREATING it from the
saved bytes (reuse, no re-login), instead of bailing to a full login every cycle (which evicts
the shared session and triggers a token-eviction storm + cloud throttling)."""
import json
import os
import sqlite3

import crypto
import session_share


class _API:
    pass


def _fresh():
    a = _API()
    for attr in session_share._ATTRS:
        setattr(a, attr, None)
    return a


def _setup(tmp_path, monkeypatch):
    db = tmp_path / "s.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    con.commit()
    con.close()
    monkeypatch.setenv("DB_PATH", str(db))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    return db


def test_reuse_survives_vanished_cert(tmp_path, monkeypatch):
    db = _setup(tmp_path, monkeypatch)
    cert = tmp_path / "tmpX-leapmotor-cert.pem"
    key = tmp_path / "tmpX-leapmotor-key.pem"
    cert.write_bytes(b"CERTDATA")
    key.write_bytes(b"KEYDATA")

    a = _fresh()
    a.token = "TOK"
    a.user_id = "u"
    a.device_id = "D"
    a.account_cert_file = str(cert)
    a.account_key_file = str(key)
    session_share._save(a)

    # ⚠️ Il blob è CIFRATO a riposo (token, refresh e chiave privata dell'account non stanno in
    # chiaro nel backup). Questi due test lo ispezionano da fuori, quindi decifrano come fa il
    # codice; il comportamento provato — riuso della sessione, cert ricreato — non cambia.
    blob = json.loads(crypto.decrypt(sqlite3.connect(str(db)).execute(
        "SELECT value FROM settings WHERE key='shared_session'").fetchone()[0]))
    assert "account_cert_b64" in blob   # bytes stashed for re-materialization

    # the per-login tempfiles AND the stable copy both vanish
    for p in {str(cert), str(key), blob["account_cert_file"], blob["account_key_file"]}:
        if os.path.exists(p):
            os.remove(p)

    a2 = _fresh()
    assert session_share._restore(a2) is True       # reuse, NOT a fresh login
    assert a2.token == "TOK"
    assert os.path.exists(a2.account_cert_file)      # cert was re-created on the fly
    assert open(a2.account_cert_file, "rb").read() == b"CERTDATA"


def test_restore_without_session_returns_false(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert session_share._restore(_fresh()) is False


def test_ensure_account_cert_rematerializes_without_relogin(tmp_path, monkeypatch):
    # #64: the stable account cert can vanish mid-session (weak-signal/asleep car poll-failing).
    # ensure_account_cert must re-create it from the saved bytes WITHOUT a full login — just put
    # the files back, so the next request doesn't fail with "Could not find the TLS certificate".
    db = _setup(tmp_path, monkeypatch)
    cert = tmp_path / "tmpY-leapmotor-cert.pem"
    key = tmp_path / "tmpY-leapmotor-key.pem"
    cert.write_bytes(b"CERTDATA")
    key.write_bytes(b"KEYDATA")

    a = _fresh()
    a.token = "TOK"
    a.account_cert_file = str(cert)
    a.account_key_file = str(key)
    session_share._save(a)                       # stashes bytes + writes stable copies

    # ⚠️ Il blob è CIFRATO a riposo (token, refresh e chiave privata dell'account non stanno in
    # chiaro nel backup). Questi due test lo ispezionano da fuori, quindi decifrano come fa il
    # codice; il comportamento provato — riuso della sessione, cert ricreato — non cambia.
    blob = json.loads(crypto.decrypt(sqlite3.connect(str(db)).execute(
        "SELECT value FROM settings WHERE key='shared_session'").fetchone()[0]))
    stable_cert, stable_key = blob["account_cert_file"], blob["account_key_file"]
    for p in (stable_cert, stable_key):          # both stable files vanish mid-session
        if os.path.exists(p):
            os.remove(p)

    live = _fresh()                              # a live api still pointed at the missing files
    live.account_cert_file = stable_cert
    live.account_key_file = stable_key
    assert session_share.ensure_account_cert(live) is True
    assert os.path.exists(stable_cert) and os.path.exists(stable_key)
    assert open(stable_cert, "rb").read() == b"CERTDATA"


def test_ensure_account_cert_noop_when_present(tmp_path, monkeypatch):
    # Files already on disk → cheap True, no re-materialization.
    _setup(tmp_path, monkeypatch)
    cert = tmp_path / "c.pem"; cert.write_bytes(b"X")
    key = tmp_path / "k.pem"; key.write_bytes(b"Y")
    a = _fresh()
    a.account_cert_file = str(cert)
    a.account_key_file = str(key)
    assert session_share.ensure_account_cert(a) is True


def test_ensure_account_cert_false_without_blob(tmp_path, monkeypatch):
    # No saved session and the files are gone → nothing to restore from.
    _setup(tmp_path, monkeypatch)
    a = _fresh()
    a.account_cert_file = str(tmp_path / "missing.pem")
    a.account_key_file = str(tmp_path / "missing-key.pem")
    assert session_share.ensure_account_cert(a) is False
