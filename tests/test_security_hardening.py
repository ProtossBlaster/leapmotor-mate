"""Cross-site write guard + login throttle.

The install being protected is the one with NO password — the default for standalone Docker —
because Mate's API opens the doors and a page in the owner's own browser is already inside the
network. See web/security.py for the two attack shapes and why add-on mode is exempt.
"""
import time

import auth
import security

BASE = "http://192.168.1.50:4000/"
EVIL = "https://evil.example"


def _hdr(**kw):
    return {k.replace("_", "-"): v for k, v in kw.items()}


# ── the guard ────────────────────────────────────────────────────────────────

def test_a_foreign_origin_cannot_write(monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    monkeypatch.delenv("MATE_TRUSTED_ORIGINS", raising=False)
    assert security.origin_allowed("POST", "/api/command/unlock",
                                   _hdr(origin=EVIL), BASE) is False


def test_our_own_pages_can_write(monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    assert security.origin_allowed("POST", "/api/command/unlock",
                                   _hdr(origin="http://192.168.1.50:4000"), BASE) is True


def test_a_request_with_no_origin_still_works(monkeypatch):
    """curl, a script, an HA automation. Browsers always declare an origin on a cross-origin
    submission — which is the attack — so refusing these would break automation and stop
    nothing."""
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    assert security.origin_allowed("POST", "/api/command/unlock", _hdr(), BASE) is True


def test_referer_is_used_when_origin_is_missing(monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    assert security.origin_allowed("POST", "/api/x",
                                   _hdr(referer=EVIL + "/trap.html"), BASE) is False


def test_reads_are_never_blocked(monkeypatch):
    """A GET must render even framed/embedded — Mate has no state-changing GET, and blocking
    reads would break the page instead of the attacker."""
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    assert security.origin_allowed("GET", "/trips", _hdr(origin=EVIL), BASE) is True


def test_the_liveness_probe_is_exempt(monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    assert security.origin_allowed("POST", "/healthz", _hdr(origin=EVIL), BASE) is True


def test_add_on_mode_is_exempt(monkeypatch):
    """Under HA the browser talks to HA and HA proxies to us, so every legitimate request
    carries HA's origin. Enforcing here would break the panel for every add-on user."""
    monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
    assert security.origin_allowed("POST", "/api/command/unlock",
                                   _hdr(origin="http://homeassistant.local:8123"), BASE) is True


def test_a_reverse_proxy_can_be_declared_trusted(monkeypatch):
    """The escape hatch: a proxy that rewrites Host makes the browser's correct origin look
    foreign to us."""
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    monkeypatch.setenv("MATE_TRUSTED_ORIGINS", "https://mate.example.com, https://other.test")
    assert security.origin_allowed("POST", "/api/x",
                                   _hdr(origin="https://mate.example.com"), BASE) is True
    assert security.origin_allowed("POST", "/api/x", _hdr(origin=EVIL), BASE) is False


def test_a_lookalike_host_is_not_our_origin(monkeypatch):
    """Prefix/substring matching would accept 192.168.1.50.evil.example; a different port is a
    different origin to the browser too."""
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    monkeypatch.delenv("MATE_TRUSTED_ORIGINS", raising=False)
    for bad in ("http://192.168.1.50:4000.evil.example", "http://192.168.1.500:4000",
                "http://192.168.1.50:4001", "http://192.168.1.50"):
        assert security.origin_allowed("POST", "/api/x", _hdr(origin=bad), BASE) is False


def test_https_behind_a_proxy_is_not_locked_out(monkeypatch):
    """The scheme is deliberately not compared. Behind an HTTPS proxy the browser says https://
    while we see http://, and uvicorn only trusts X-Forwarded-Proto from 127.0.0.1 — comparing
    schemes would 403 those users on their own UI. The host still has to match."""
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    monkeypatch.delenv("MATE_TRUSTED_ORIGINS", raising=False)
    assert security.origin_allowed("POST", "/api/x", _hdr(origin="https://192.168.1.50:4000"),
                                   BASE) is True
    proxied = "http://mate.example.com/"
    assert security.origin_allowed("POST", "/api/x", _hdr(origin="https://mate.example.com"),
                                   proxied) is True
    assert security.origin_allowed("POST", "/api/x", _hdr(origin="https://evil.example"),
                                   proxied) is False


def test_framing_is_refused(monkeypatch):
    """Clickjacking is the half no origin check can see: the click really is inside Mate."""
    assert security.SECURITY_HEADERS["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in security.SECURITY_HEADERS["Content-Security-Policy"]


# ── the login throttle ───────────────────────────────────────────────────────

def test_the_door_closes_after_repeated_misses(monkeypatch):
    monkeypatch.setattr(auth, "_fails", {})
    ip = "192.168.1.9"
    for _ in range(auth.FAIL_LIMIT):
        assert auth.attempt_allowed(ip) is True
        auth.note_failure(ip)
    assert auth.attempt_allowed(ip) is False


def test_a_correct_password_clears_the_count(monkeypatch):
    monkeypatch.setattr(auth, "_fails", {})
    ip = "192.168.1.9"
    for _ in range(auth.FAIL_LIMIT - 1):
        auth.note_failure(ip)
    auth.note_success(ip)
    for _ in range(auth.FAIL_LIMIT):
        assert auth.attempt_allowed(ip) is True
        auth.note_failure(ip)
    assert auth.attempt_allowed(ip) is False


def test_the_lockout_expires(monkeypatch):
    """The owner who fat-fingered it five times must get back in, not be shut out forever."""
    monkeypatch.setattr(auth, "_fails", {"1.2.3.4": (auth.FAIL_LIMIT,
                                                     time.time() - auth.LOCKOUT - 1)})
    assert auth.attempt_allowed("1.2.3.4") is True


def test_one_client_cannot_lock_out_another(monkeypatch):
    monkeypatch.setattr(auth, "_fails", {})
    for _ in range(auth.FAIL_LIMIT):
        auth.note_failure("10.0.0.1")
    assert auth.attempt_allowed("10.0.0.1") is False
    assert auth.attempt_allowed("10.0.0.2") is True
