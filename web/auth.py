"""Optional standalone authentication.

A single shared password gates the whole UI when Mate runs as standalone Docker exposed
beyond localhost. It is intentionally a NO-OP when running as a Home Assistant add-on:
there the Supervisor ingress already authenticates every request, so a second login would
just get in the way (and break ingress).

The password can come from two places, and BOTH exist for a reason:

  * env MATE_AUTH_PASSWORD — the original. Kept working unchanged, and it WINS, because an
    operator who set it in their compose file expects that file to be the source of truth.
  * set from the Settings page, stored as a salted hash — added because the env var was the
    whole problem: protecting your car meant editing a compose file and restarting, so
    virtually nobody did it, and a Mate reachable on the LAN can unlock the doors.

Setting it from the page has an obvious hole: while no password exists the page is open, so
whoever reaches it first chooses. That is the same trade every self-hosted first-run makes,
and it is bounded — once set, changing it goes through the authenticated API like anything
else. The alternative (a password printed in a log nobody reads) locks the owner out of their
own data on update, which is worse.

The session is a Fernet token (signed + encrypted with the same per-install key as the
credential encryption, /data/secret.key) carried in an HttpOnly, SameSite=Strict cookie
— so it survives restarts, can't be read by JS, and isn't sent on cross-site requests
(a solid CSRF defense for the authenticated app).
"""
import hashlib
import hmac
import os
import secrets
import time

from cryptography.fernet import Fernet, InvalidToken

import crypto

COOKIE = "mate_session"
TTL = 30 * 86400          # 30 days
_MARK = b"mate-auth-v1"
_fernet = None

# Stored form: pbkdf2$<iterations>$<salt-hex>$<hash-hex>. A hash rather than the encrypted
# plaintext because nothing ever needs to read this password back — only compare against it —
# and a DB copy (backup, bundle, support request) then leaks nothing usable.
PASSWORD_SETTING = "auth_password_hash"
_KDF_ITERATIONS = 200_000


def _f() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(crypto.load_or_create_key())
    return _fernet


def password() -> str:
    """The env-var password, '' when unset. Env wins over the stored one."""
    return os.environ.get("MATE_AUTH_PASSWORD", "")


def _stored() -> str:
    # Imported lazily: db_reader imports crypto/i18n at module scope and auth is imported from
    # main before the DB path is settled — a top-level import here would fix the order by luck.
    import db_reader
    try:
        return db_reader.get_setting(PASSWORD_SETTING, "") or ""
    except Exception:                      # noqa: BLE001 — no DB yet (first boot) is not an error
        return ""


def is_addon() -> bool:
    return bool(os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HASSIO_TOKEN"))


def enabled() -> bool:
    """On only for standalone with a password set, from either source. Add-on mode (Supervisor
    ingress already authenticates) is always exempt."""
    if is_addon():
        return False
    return bool(password()) or bool(_stored())


def unprotected() -> bool:
    """Standalone, reachable, and no password anywhere — what the Settings banner warns about.
    Deliberately NOT the negation of enabled(): under the add-on there is nothing to warn about,
    since ingress already authenticates."""
    return not is_addon() and not password() and not _stored()


def env_password_wins() -> bool:
    """True when MATE_AUTH_PASSWORD is set, so the page can say the compose file is in charge
    rather than silently ignoring what the user types into it."""
    return bool(password())


def _hash(pw: str, salt: bytes, iterations: int = _KDF_ITERATIONS) -> str:
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, iterations).hex()


def set_password(pw: str) -> bool:
    """Store a new password (min 8 chars). Empty/short is refused rather than silently leaving
    the UI open with a password the owner believes is protecting it."""
    import db_reader
    if len(pw or "") < 8:
        return False
    salt = secrets.token_bytes(16)
    db_reader.set_setting(PASSWORD_SETTING,
                          f"pbkdf2${_KDF_ITERATIONS}${salt.hex()}${_hash(pw, salt)}")
    return True


def clear_password() -> None:
    """Turn the login back off — only reachable from inside an authenticated session."""
    import db_reader
    db_reader.set_setting(PASSWORD_SETTING, "")


def check_password(pw: str) -> bool:
    if not pw:
        return False
    if password():                                     # env wins
        return hmac.compare_digest(pw, password())
    stored = _stored()
    if not stored:
        return False
    try:
        scheme, iters, salt_hex, want = stored.split("$")
        if scheme != "pbkdf2":
            return False
        return hmac.compare_digest(_hash(pw, bytes.fromhex(salt_hex), int(iters)), want)
    except (ValueError, TypeError):                    # malformed row → fail closed
        return False


# ── Login throttle ───────────────────────────────────────────────────────────
# The password is a single shared secret with no account to lock, so without a brake anything
# on the LAN can try a wordlist at network speed. Per-client-IP, in memory: this is one process
# with no shared state, and a restart clearing the counters is not a weakness worth a table —
# the attacker cannot cause the restart.
FAIL_LIMIT = 5            # consecutive misses before the door closes
LOCKOUT = 300             # seconds it stays closed, then one more try is allowed
_fails: dict = {}         # ip → [consecutive failures, timestamp of the last one]


def attempt_allowed(ip: str) -> bool:
    n, last = _fails.get(ip, (0, 0.0))
    if n < FAIL_LIMIT:
        return True
    if time.time() - last >= LOCKOUT:     # window elapsed → let exactly one more through
        _fails[ip] = (FAIL_LIMIT - 1, last)
        return True
    return False


def note_failure(ip: str) -> None:
    n, _ = _fails.get(ip, (0, 0.0))
    _fails[ip] = (n + 1, time.time())


def note_success(ip: str) -> None:
    _fails.pop(ip, None)


def make_token() -> str:
    return _f().encrypt(_MARK).decode()


def valid(token: str) -> bool:
    if not token:
        return False
    try:
        return _f().decrypt(token.encode(), ttl=TTL) == _MARK
    except (InvalidToken, Exception):  # noqa: BLE001
        return False
