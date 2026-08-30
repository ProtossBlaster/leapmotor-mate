"""Shared Leapmotor session between the poller and the web process.

Both run in the same container with the same account + device_id + cert, so two
independent logins evict each other (Leapmotor allows one session per device). Here
the auth state (token + sign material + account-cert file paths) is persisted to the
DB (settings['shared_session']) and `api.login` is monkey-patched to RESTORE that
shared token before ever doing a real login. The pip client's internal token-expiry
retry also calls api.login, so this intercepts every login path.

Fully defensive: any failure falls back to a normal login — the app never breaks.
"""
import base64
import json
import logging
import os
import sqlite3
import time
import types

import crypto

log = logging.getLogger("session_share")

_TTL = 45 * 60   # only restore a session blob younger than this (token lifetime margin)
_GUARD_S = 10    # don't re-attempt restore within this window (breaks retry recursion)

_ATTRS = ("user_id", "token", "refresh_token", "device_id",
          "sign_ikm", "sign_salt", "sign_info", "account_cert_file", "account_key_file")


def _db_path() -> str:
    return os.environ.get("DB_PATH", "leapmotor_mate.db")


def _stable_paths():
    """Fixed-name copies of the account cert/key on the persistent volume. The API writes
    them as PER-LOGIN tempfiles that later get cleaned up — once gone, session reuse used to
    fail and both processes re-logged in forever (GitHub #54). A fixed name we own survives,
    so the other process always finds a valid cert without a fresh login."""
    d = os.environ.get("TMPDIR") or "/tmp"
    return os.path.join(d, "mate-account-cert.pem"), os.path.join(d, "mate-account-key.pem")


def _read_bytes(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except Exception:  # noqa: BLE001
        return None


def _write_bytes(path, data) -> None:
    with open(path, "wb") as f:
        f.write(data)
    try:
        os.chmod(path, 0o600)
    except Exception:  # noqa: BLE001
        pass


def _save(api) -> None:
    try:
        blob = {a: getattr(api, a, None) for a in _ATTRS}
        # Copy the API's per-login account cert/key to stable, fixed-name files we own and
        # re-point the live api at them; also stash the bytes so a vanished file can be
        # re-created on restore. Without this the tempfiles get cleaned up and reuse breaks,
        # forcing a re-login every cycle -> token-eviction storm + cloud throttling (#54).
        cert_b = _read_bytes(getattr(api, "account_cert_file", None) or "")
        key_b = _read_bytes(getattr(api, "account_key_file", None) or "")
        if cert_b and key_b:
            sc, sk = _stable_paths()
            try:
                _write_bytes(sc, cert_b)
                _write_bytes(sk, key_b)
                api.account_cert_file = sc
                api.account_key_file = sk
                blob["account_cert_file"] = sc
                blob["account_key_file"] = sk
            except Exception as e:  # noqa: BLE001
                log.debug("stable cert copy failed: %s", e)
            blob["account_cert_b64"] = base64.b64encode(cert_b).decode()
            blob["account_key_b64"] = base64.b64encode(key_b).decode()
        blob["ts"] = time.time()
        # CIFRATO a riposo, come ogni altro segreto (set_secret fa lo stesso per token HA, ABRP,
        # chiavi dei geocodificatori, PIN). Questo modulo scrive con una connessione sua, saltando
        # db_reader, ed era rimasto l'unico posto in chiaro: dentro ci sono il token di sessione, il
        # token di rinnovo e la CHIAVE PRIVATA dell'account. Il backup del database è una copia del
        # file, e la pagina dell'esportazione promette all'utente credenziali «cifrate».
        c = sqlite3.connect(_db_path(), timeout=5)
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('shared_session', ?)",
                  (crypto.encrypt(json.dumps(blob)),))
        c.commit()
        c.close()
    except Exception as e:  # noqa: BLE001
        log.debug("shared session save failed: %s", e)


def _restore(api) -> bool:
    try:
        c = sqlite3.connect(_db_path(), timeout=5)
        row = c.execute("SELECT value FROM settings WHERE key='shared_session'").fetchone()
        c.close()
        if not row:
            return False
        # `decrypt` lascia passare il testo in chiaro apposta, quindi un blob scritto da una versione
        # precedente si legge ancora e viene ricifrato al primo salvataggio: nessuna migrazione,
        # nessun accesso nuovo da fare. Con la chiave persa torna stringa VUOTA, `json.loads` alza e
        # si finisce qui sotto: nessuna sessione → accesso normale. È la scelta della #227 — meglio
        # rifare l'accesso che passare al cloud del testo cifrato scambiandolo per una credenziale.
        b = json.loads(crypto.decrypt(row[0]))
    except Exception:  # noqa: BLE001
        return False
    if not b.get("token") or time.time() - b.get("ts", 0) > _TTL:
        return False
    acf, akf = b.get("account_cert_file"), b.get("account_key_file")
    if not acf or not akf:
        return False
    # Re-create the cert/key if they vanished — the root cause of the re-login storm (#54):
    # the API's per-login tempfile gets cleaned up, so reuse used to bail out to a full login.
    if not os.path.exists(acf) or not os.path.exists(akf):
        cb, kb = b.get("account_cert_b64"), b.get("account_key_b64")
        if not cb or not kb:
            return False
        try:
            _write_bytes(acf, base64.b64decode(cb))
            _write_bytes(akf, base64.b64decode(kb))
        except Exception:  # noqa: BLE001
            return False
    try:
        for a in _ATTRS:
            setattr(api, a, b.get(a))
        api.remote_cert_synced = False
        return True
    except Exception:  # noqa: BLE001
        return False


def ensure_account_cert(api) -> bool:
    """Best-effort: make sure the account cert/key files the API points at still exist on disk,
    re-creating them from the base64 stashed in the shared-session blob if they vanished — WITHOUT
    a full re-login. The per-login files can be cleaned up mid-session (often on a weak-signal /
    asleep car that poll-fails a lot), which otherwise surfaces as "Could not find the TLS
    certificate file" and forces an unnecessary, rate-limit-risky re-login (#64). Returns True if
    the files are present (or were just restored), False if we couldn't materialize them."""
    acf = getattr(api, "account_cert_file", None)
    akf = getattr(api, "account_key_file", None)
    if acf and akf and os.path.exists(acf) and os.path.exists(akf):
        return True
    try:
        c = sqlite3.connect(_db_path(), timeout=5)
        row = c.execute("SELECT value FROM settings WHERE key='shared_session'").fetchone()
        c.close()
        b = json.loads(crypto.decrypt(row[0])) if row else None    # stesso blob, stessa chiave
    except Exception:  # noqa: BLE001
        return False
    if not b:
        return False
    acf = acf or b.get("account_cert_file")
    akf = akf or b.get("account_key_file")
    cb, kb = b.get("account_cert_b64"), b.get("account_key_b64")
    if not (acf and akf and cb and kb):
        return False
    try:
        restored = False
        if not os.path.exists(acf):
            _write_bytes(acf, base64.b64decode(cb)); restored = True
        if not os.path.exists(akf):
            _write_bytes(akf, base64.b64decode(kb)); restored = True
        api.account_cert_file = acf
        api.account_key_file = akf
        if restored:
            log.info("Re-materialized the account TLS cert (was missing) — no re-login needed")
        return True
    except Exception:  # noqa: BLE001
        return False


def _shared_login(self) -> None:
    """Replacement for api.login: restore the shared token first; do a real login only
    when there is no recent shared session (or a just-restored one failed within the
    guard window). After a real login, persist the new session for the other process."""
    if time.time() - getattr(self, "_mate_restore_at", 0) > _GUARD_S:
        self._mate_restore_at = time.time()
        if _restore(self):
            log.info("Reusing shared session token (no login)")
            return
    type(self).login(self)   # original, unpatched class login
    _save(self)
    log.info("New login — shared session saved")


def _shared_token_refresh(self) -> None:
    """Persist the refreshed token too, so the other process picks it up."""
    type(self).token_refresh(self)
    _save(self)


def install(api):
    """Route every login / token-refresh through the shared-session logic."""
    try:
        api.login = types.MethodType(_shared_login, api)
        api.token_refresh = types.MethodType(_shared_token_refresh, api)
    except Exception as e:  # noqa: BLE001
        log.warning("Could not install shared session: %s", e)
    return api
