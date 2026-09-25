"""A poll that times out must not cost a full login — the one thing the cloud is rationing.

Measured on three real installs (beta #49 + #295 @gm27271, #296 @adoewa, a bundle from
@ebagnoli), whose logs from 17–18/09/2026 all break the same way, line for line:

    04:34:41 [ERROR]   Poll error: ... Read timed out
    04:34:41 [INFO]    Attempting session recovery (re-login)…
    04:34:42 [WARNING] Re-login failed, will retry next cycle: Leapmotor login failed: Error occurred

A *Read timed out* is a network blip, not a dead session — but `relogin()` answers it by deleting
the shared-session blob (token AND refresh token) and asking the login endpoint for a new one.
From 17/09 that endpoint accepts 5–12 requests a day where it used to take ~310, so every blip
spends one of the few the account still has, and throws away a refresh token that was very
probably still good.

The cloud is not ours to fix. Spending a login where a token refresh would do is.
So `relogin()` asks for a REFRESH first and only falls back to a full login when that fails —
which is also what heals the case the method was written for, a vanished account cert: the
refresh is a signed request, so it fails too and the full login still runs.
"""
import sqlite3

import client as poller_client
import crypto
import session_share


class _API:
    """Records what was asked of the cloud, in order."""

    def __init__(self, refresh_works=True):
        self.calls = []
        self._refresh_works = refresh_works
        for attr in session_share._ATTRS:
            setattr(self, attr, None)
        self.token = "TOK"
        self.refresh_token = "REF"

    def token_refresh(self):
        self.calls.append("token_refresh")
        if not self._refresh_works:
            raise RuntimeError("Could not find the TLS certificate file")
        self.token = "TOK2"

    def login(self):
        self.calls.append("login")
        self.token = "TOK3"

    def get_vehicle_list(self):
        class _V:
            vin, car_type, is_shared = "VIN1", "c10", False
        return [_V()]


def _db_with_session(tmp_path, monkeypatch):
    db = tmp_path / "s.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    con.execute("INSERT INTO settings (key, value) VALUES ('shared_session', ?)",
                (crypto.encrypt('{"token": "TOK", "ts": 0}'),))
    con.commit()
    con.close()
    monkeypatch.setenv("DB_PATH", str(db))
    return db


def _session_row(db):
    con = sqlite3.connect(str(db))
    row = con.execute("SELECT value FROM settings WHERE key='shared_session'").fetchone()
    con.close()
    return row


def _client(api):
    c = object.__new__(poller_client.LeapmotorMateClient)
    c._api = api
    c._vehicles = []
    c._vehicle = None
    return c


def test_a_refreshable_session_is_refreshed_and_not_logged_in_again(tmp_path, monkeypatch):
    db = _db_with_session(tmp_path, monkeypatch)
    api = _API(refresh_works=True)

    _client(api).relogin()

    assert api.calls == ["token_refresh"], \
        "a blip must spend a refresh, not one of the few logins the cloud still accepts"
    assert _session_row(db) is not None, "the shared session must survive a refresh"


def test_a_session_the_refresh_cannot_save_still_gets_its_full_login(tmp_path, monkeypatch):
    """The case relogin() was written for: the account cert vanished, so the signed refresh
    fails too. Nothing about that install changes — it still gets its full login."""
    db = _db_with_session(tmp_path, monkeypatch)
    api = _API(refresh_works=False)

    _client(api).relogin()

    assert api.calls == ["token_refresh", "login"]
    assert _session_row(db) is None, "a full login starts from a cleared blob, as it does today"


def test_a_session_with_no_refresh_token_goes_straight_to_the_login(tmp_path, monkeypatch):
    """An install that has never stored one (an old blob, a session restored from a version
    before the refresh token was kept) must not pay an extra failing round trip."""
    db = _db_with_session(tmp_path, monkeypatch)
    api = _API(refresh_works=True)
    api.refresh_token = None

    _client(api).relogin()

    assert api.calls == ["login"]
