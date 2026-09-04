"""Cross-site write guard + login throttle.

The install being protected is the one with NO password — the default for standalone Docker —
because Mate's API opens the doors and a page in the owner's own browser is already inside the
network. See web/security.py for the two attack shapes and why add-on mode is exempt.
"""
import time

import pytest

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
    monkeypatch.delenv("MATE_FRAME_ANCESTORS", raising=False)
    headers = security.security_headers()
    assert headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]


def test_framing_is_allowed_for_a_declared_frame_ancestor(monkeypatch):
    """A dashboard you named (e.g. your own Home Assistant) may embed Mate as a panel; nothing
    else can, since X-Frame-Options has no way to say "this one origin" and would just re-block
    it, so it's dropped in favor of CSP frame-ancestors alone. 'self' is not added for free —
    only the origin actually named."""
    monkeypatch.setenv("MATE_FRAME_ANCESTORS", "https://hass.example.com")
    headers = security.security_headers()
    assert "X-Frame-Options" not in headers
    assert headers["Content-Security-Policy"] == "frame-ancestors https://hass.example.com"


def test_trusting_an_origin_for_writes_does_not_also_allow_it_to_frame_mate(monkeypatch):
    """MATE_TRUSTED_ORIGINS and MATE_FRAME_ANCESTORS are different permissions. Before they were
    split, setting the one for a reverse proxy silently dropped X-Frame-Options for everyone who
    had — a proxy host is not automatically a page you want framing the app."""
    monkeypatch.delenv("MATE_FRAME_ANCESTORS", raising=False)
    monkeypatch.setenv("MATE_TRUSTED_ORIGINS", "https://mate.example.com")
    headers = security.security_headers()
    assert headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]


def test_multiple_frame_ancestors_are_all_named(monkeypatch):
    """The join() between origins is only exercised with 2+ entries."""
    monkeypatch.setenv("MATE_FRAME_ANCESTORS", "https://hass.example.com, https://other.example")
    headers = security.security_headers()
    assert headers["Content-Security-Policy"] == \
        "frame-ancestors https://hass.example.com https://other.example"


@pytest.mark.parametrize("bad", [
    "*",
    "https://*.example.com",
    "https://a.test; script-src *",
    "https://a.test/some/path",
    "https://user@a.test",
    "https://a.test?x=1",
    "ftp://a.test",
    "a.test",
    "https://пример.рф",
    "https://a b.test",
])
def test_a_malformed_frame_ancestor_is_dropped_not_forwarded(monkeypatch, bad):
    """Each of these is either an inert-looking permission that is actually real (a wildcard, an
    injected second directive) or a value that crashes the response outright: current Starlette
    encodes header values as latin-1, so a non-ASCII entry like the Cyrillic one here would turn
    EVERY response into a 500 — /healthz included — while the app itself starts clean. Dropping
    anything that isn't exactly scheme://host[:port] means neither can happen."""
    monkeypatch.setenv("MATE_FRAME_ANCESTORS", bad)
    headers = security.security_headers()
    assert headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]


def test_a_good_entry_survives_alongside_bad_ones(monkeypatch):
    monkeypatch.setenv("MATE_FRAME_ANCESTORS", "not a url, https://hass.example.com, *")
    headers = security.security_headers()
    assert headers["Content-Security-Policy"] == "frame-ancestors https://hass.example.com"


def test_a_trailing_slash_from_the_address_bar_is_forgiven(monkeypatch):
    """What a browser shows for a bare origin — copy it as-is and it still works, since that's
    the single most likely way anyone pastes one in."""
    monkeypatch.setenv("MATE_FRAME_ANCESTORS", "https://hass.example.com/")
    headers = security.security_headers()
    assert headers["Content-Security-Policy"] == "frame-ancestors https://hass.example.com"


def test_a_slash_followed_by_an_actual_path_is_still_rejected(monkeypatch):
    """The forgiveness above is for the bare trailing "/" only — a real path must still fail."""
    monkeypatch.setenv("MATE_FRAME_ANCESTORS", "https://hass.example.com/lovelace/")
    headers = security.security_headers()
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]


def test_an_ipv6_literal_is_accepted(monkeypatch):
    """CSP host-source syntax wants the brackets kept, same as a URL does."""
    monkeypatch.setenv("MATE_FRAME_ANCESTORS", "http://[::1]:8123")
    headers = security.security_headers()
    assert headers["Content-Security-Policy"] == "frame-ancestors http://[::1]:8123"


def test_a_rejected_entry_is_logged(monkeypatch, caplog):
    """Silent used to mean untraceable: MATE_FRAME_ANCESTORS is set to something, the panel still
    won't embed, and nothing says why. The web log (readable from Settings) now does."""
    monkeypatch.setenv("MATE_FRAME_ANCESTORS", "https://hass.example.com/some/path")
    with caplog.at_level("WARNING", logger="security"):
        security.security_headers()
    assert "MATE_FRAME_ANCESTORS" in caplog.text
    assert "https://hass.example.com/some/path" in caplog.text


def test_a_rejected_entry_is_logged_once_and_not_once_per_response(monkeypatch, caplog):
    """security_headers() runs on EVERY response, so a warning built into it repeats with the
    traffic: Mate's own polling alone is thousands of requests a day, and a single typo would
    bury the rest of the web log — the log the message tells you to go and read. Warn per bad
    value, not per request; a different bad value is still worth its own line."""
    monkeypatch.setattr(security, "_warned", set())
    monkeypatch.setenv("MATE_FRAME_ANCESTORS", "https://hass.example.com/some/path")
    with caplog.at_level("WARNING", logger="security"):
        for _ in range(50):
            security.security_headers()
        assert caplog.text.count("MATE_FRAME_ANCESTORS") == 1

        monkeypatch.setenv("MATE_FRAME_ANCESTORS", "https://other.example/*")
        for _ in range(50):
            security.security_headers()
        assert caplog.text.count("MATE_FRAME_ANCESTORS") == 2


def test_no_header_value_can_ever_break_latin1_encoding(monkeypatch):
    """What Starlette actually does when writing a response header out — this reproduces the
    crash a validation gap would cause, independent of whether fastapi/starlette are installed
    in this test environment."""
    for bad in ("https://пример.рф", "*", "https://a.test; evil", "https://a.test\r\nX: y"):
        monkeypatch.setenv("MATE_FRAME_ANCESTORS", bad)
        for value in security.security_headers().values():
            value.encode("latin-1")  # must not raise


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


# ── the password you can actually reach ──────────────────────────────────────
# The login already existed, as MATE_AUTH_PASSWORD. Protecting the car therefore meant editing
# a compose file and restarting, so essentially nobody did — which is why it can now be set
# from the Settings page and stored as a salted hash.

def _clean(monkeypatch, tmp_path):
    import db as D
    import db_reader
    path = str(tmp_path / "auth.db")
    D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.delenv("MATE_AUTH_PASSWORD", raising=False)
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    monkeypatch.delenv("HASSIO_TOKEN", raising=False)


def test_a_fresh_standalone_install_is_flagged_as_open(monkeypatch, tmp_path):
    _clean(monkeypatch, tmp_path)
    assert auth.unprotected() is True
    assert auth.enabled() is False


def test_setting_a_password_from_the_page_turns_the_login_on(monkeypatch, tmp_path):
    _clean(monkeypatch, tmp_path)
    assert auth.set_password("correct horse") is True
    assert auth.enabled() is True and auth.unprotected() is False
    assert auth.check_password("correct horse") is True
    assert auth.check_password("wrong horse") is False


def test_the_password_is_never_stored_in_clear(monkeypatch, tmp_path):
    """A DB copy — backup, diagnostics bundle, a support request — must not carry it."""
    import db_reader
    _clean(monkeypatch, tmp_path)
    auth.set_password("hunter2hunter2")
    stored = db_reader.get_setting(auth.PASSWORD_SETTING, "")
    assert "hunter2hunter2" not in stored and stored.startswith("pbkdf2$")


def test_the_same_password_stores_differently_each_time(monkeypatch, tmp_path):
    """Per-password salt: two installs with the same password don't share a hash."""
    import db_reader
    _clean(monkeypatch, tmp_path)
    auth.set_password("same password")
    first = db_reader.get_setting(auth.PASSWORD_SETTING, "")
    auth.set_password("same password")
    assert db_reader.get_setting(auth.PASSWORD_SETTING, "") != first
    assert auth.check_password("same password") is True


def test_a_too_short_password_is_refused(monkeypatch, tmp_path):
    """Refused rather than accepted, so nobody walks away believing they're protected."""
    _clean(monkeypatch, tmp_path)
    assert auth.set_password("short") is False
    assert auth.set_password("") is False
    assert auth.enabled() is False


def test_the_environment_still_wins(monkeypatch, tmp_path):
    """Whoever put it in their compose file expects that file to be the source of truth."""
    _clean(monkeypatch, tmp_path)
    auth.set_password("from the page")
    monkeypatch.setenv("MATE_AUTH_PASSWORD", "from the compose file")
    assert auth.env_password_wins() is True
    assert auth.check_password("from the compose file") is True
    assert auth.check_password("from the page") is False


def test_turning_it_off_reopens(monkeypatch, tmp_path):
    _clean(monkeypatch, tmp_path)
    auth.set_password("temporary one")
    auth.clear_password()
    assert auth.enabled() is False and auth.unprotected() is True


def test_the_add_on_is_never_nagged(monkeypatch, tmp_path):
    """Behind ingress there is nothing to warn about — HA authenticates every request."""
    _clean(monkeypatch, tmp_path)
    monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
    assert auth.unprotected() is False and auth.enabled() is False


def test_a_corrupted_stored_password_fails_closed(monkeypatch, tmp_path):
    import db_reader
    _clean(monkeypatch, tmp_path)
    for junk in ("garbage", "pbkdf2$notanumber$aa$bb", "scrypt$1$aa$bb", "pbkdf2$1$zz$bb"):
        db_reader.set_setting(auth.PASSWORD_SETTING, junk)
        assert auth.check_password("anything") is False
