"""Renewing the session instead of spending a login for it.

Measured on the lab on 27/09/2026: the login response carries a `refreshToken` and states
`tokenExpireTime` 7200 and `refreshTokenExpireTime` 604799, and
`POST /base/base-user/token/v1/refresh` answers `code 0` with a whole new session. mate-api
0.1.0a11 turns that into `LoginClient.refresh`.

Until now `token_refresh()` on this adapter did the opposite of its name: it zeroed the saved
expiry and called a `login()` that could only log in. With a token capped at half an hour that
was a login every thirty minutes — 48 a day on an account the cloud has been rationing since
17 September.

What this file pins, through the real `login()` — every read goes through it, and it is where a
session runs out every two hours; `token_refresh()` reaches the renewal through it too:
  · a renewal is asked for, and a login is NOT spent, when the saved session can be renewed;
  · the renewed session is written back whole, refresh material included, so the next process
    to read it can renew in turn;
  · a session with no refresh material — every session saved before this version — still takes
    the old path, because there is nothing to renew from;
  · a refusal falls back to the login instead of leaving the adapter with a dead session, and
    the refresh token it refused is not offered again;
  · a session saved for another account is never renewed.
"""
import base64
import hashlib
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import mate_api  # puts poller/mate_api_runtime on sys.path, as the poller process does
import api_v2_bridge as bridge
from leapmotor_cloud.authentication import LoginUnavailable

# The real clock: _renew_session() checks the refresh token's expiry against it.
NOW = datetime.now(timezone.utc).replace(microsecond=0)
KEY = base64.b64encode(bytes(range(32))).decode()
SQLITE = bridge.connect_db, bridge.setting, bridge.set_setting   # before the fixture stands in for them


@pytest.fixture
def adapter(monkeypatch, tmp_path):
    """The real adapter and its real login(), with its database, the cloud's login and the
    login client that renews replaced. `calls` gets one 'login' for every login the cloud is
    asked for; `login_fails` makes those logins fail."""
    saved_rows = {}

    class FakeDB:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def commit(self): pass
    monkeypatch.setattr(bridge, 'connect_db', lambda: FakeDB())
    monkeypatch.setattr(bridge, 'setting', lambda db, key, default=None: saved_rows.get(key, default))
    monkeypatch.setattr(bridge, 'set_setting', lambda db, key, value: saved_rows.__setitem__(key, value))
    monkeypatch.setattr(bridge.crypto, 'encrypt', lambda value: value)
    monkeypatch.setattr(bridge.crypto, 'decrypt', lambda value: value)
    monkeypatch.setattr(bridge, 'certificate_usable', lambda *a, **k: True)
    monkeypatch.setattr(bridge, 'session_device_id', lambda token, fallback: fallback)

    api = object.__new__(bridge.NewAPIClient)
    api.username, api.password, api.language = 'synthetic-user', 'synthetic-password', 'en-US'
    api._installation_device_id = 'synthetic-device'
    api.app_cert_path, api.app_key_path = 'app.crt', 'app.key'
    api._transport, api._routes, api._access_refresh_attempt = object(), {}, None
    api._audit = lambda *a, **k: None
    api._mutex, api.on_login = threading.RLock(), None
    monkeypatch.setattr(bridge, 'DB', str(tmp_path / 'leapmotor_mate.db'))   # the session lock sits beside it
    fixture = SimpleNamespace(api=api, rows=saved_rows, calls=[], login_fails=False, monkeypatch=monkeypatch)

    def authenticate():
        fixture.calls.append('login')
        if fixture.login_fails:
            raise bridge.LeapmotorApiError('New API sign-in unavailable')
        return _logged_in_session()
    api._authenticate_session = authenticate
    return fixture


def _save(adapter, *, refresh=True):
    row = dict(token='synthetic-token', user_id='synthetic-user-id', device_id='synthetic-device',
               key=KEY, cert='cert.pem', private_key='key.pem',
               expires_at=(NOW + timedelta(seconds=7200)).timestamp(),
               username_hash=hashlib.sha256(b'synthetic-user').hexdigest())
    if refresh:
        row['refresh_token'] = 'synthetic-refresh'
        row['refresh_expires_at'] = (NOW + timedelta(seconds=604799)).timestamp()
    adapter.rows[bridge.SESSION_KEY] = json.dumps(row)
    adapter.api._apply_session(row)
    return row


def _renewal(adapter, result):
    """Stand in for mate-api's LoginClient: record the session it is handed, answer `result`."""
    seen = {}

    class FakeLoginClient:
        def __init__(self, *a, **kw): pass
        def refresh(self, session, *, device_id):
            seen['asked'] = seen.get('asked', 0) + 1
            seen['session'] = session
            seen['device_id'] = device_id
            if isinstance(result, Exception):
                raise result
            return result
    adapter.monkeypatch.setattr(bridge, 'LoginClient', FakeLoginClient)
    return seen


def _renewed_session():
    return SimpleNamespace(token='renewed-token', user_id='synthetic-user-id',
                           device_id='synthetic-device', key=bytes(range(32)),
                           client_cert=('cert.pem', 'key.pem'),
                           expires_at=NOW + timedelta(seconds=7200),
                           refresh_token='next-refresh',
                           refresh_expires_at=NOW + timedelta(seconds=604799))


def _logged_in_session():
    return SimpleNamespace(**dict(vars(_renewed_session()), token='logged-in-token'))


def test_a_renewable_session_is_renewed_and_no_login_is_spent(adapter):
    _save(adapter)
    seen = _renewal(adapter, _renewed_session())
    adapter.api.token_refresh()

    assert adapter.calls == [], "a renewal must not spend a login"
    assert seen['session'].refresh_token == 'synthetic-refresh'
    assert seen['device_id'] == 'synthetic-device'
    assert adapter.api.token == 'renewed-token'


def test_the_renewed_session_is_written_back_whole(adapter):
    """Including the new refresh material: the next process must be able to renew in turn."""
    _save(adapter)
    _renewal(adapter, _renewed_session())
    adapter.api.token_refresh()

    stored = json.loads(adapter.rows[bridge.SESSION_KEY])
    assert stored['token'] == 'renewed-token'
    assert stored['refresh_token'] == 'next-refresh'
    assert stored['refresh_expires_at'] == (NOW + timedelta(seconds=604799)).timestamp()
    assert stored['expires_at'] == (NOW + timedelta(seconds=7200)).timestamp()


def test_a_session_saved_before_this_version_still_takes_the_old_path(adapter):
    """No refresh material, nothing to renew from: reauthenticate as before."""
    _save(adapter, refresh=False)
    _renewal(adapter, AssertionError('a renewal must not be attempted without material'))
    adapter.api.token_refresh()

    assert adapter.calls == ['login']
    assert adapter.api.token == 'logged-in-token'


def test_a_refused_renewal_falls_back_to_the_login(adapter):
    """`302010219 Token refresh error` must not leave the adapter holding a dead session."""
    _save(adapter)
    seen = _renewal(adapter, LoginUnavailable('cloud_rejection', 200, 302010219))
    adapter.api.token_refresh()

    assert seen['asked'] == 1, "the refused token must not be asked again on the way to the login"
    assert adapter.calls == ['login']
    assert adapter.api.token == 'logged-in-token'


def _run_out(adapter):
    """The saved session run out (inside login()'s 90-second margin), its refresh token good for
    days: what every read finds two hours after the last login."""
    row = _save(adapter)
    row.update(expires_at=time.time() + 30, refresh_expires_at=time.time() + 6 * 86400)
    adapter.rows[bridge.SESSION_KEY] = json.dumps(row)


def test_a_session_that_runs_out_is_renewed_on_the_way_to_a_read(adapter):
    """Measured on a live installation on 29/09/2026: a login every 119 minutes, twelve a day,
    and not one renewal, with the saved refresh token good for another week."""
    _run_out(adapter)
    seen = _renewal(adapter, _renewed_session())
    adapter.api.login()

    assert adapter.calls == [], "a session that can be renewed must not spend a login"
    assert seen['session'].refresh_token == 'synthetic-refresh'
    assert adapter.api.token == 'renewed-token'
    assert json.loads(adapter.rows[bridge.SESSION_KEY])['token'] == 'renewed-token'


def test_a_refused_renewal_on_the_way_to_a_read_logs_in(adapter):
    _run_out(adapter)
    _renewal(adapter, LoginUnavailable('cloud_rejection', 200, 302010219))
    adapter.api.login()

    assert adapter.calls == ['login']
    assert adapter.api.token == 'logged-in-token'


def test_a_refused_renewal_is_not_asked_for_again(adapter):
    """While the login fails too (a changed password, a rationed day), every read goes through
    login(): the refresh token the cloud turned down must not be offered on each of them."""
    _run_out(adapter)
    adapter.login_fails = True
    seen = _renewal(adapter, LoginUnavailable('cloud_rejection', 200, 302010219))
    with pytest.raises(bridge.LeapmotorApiError, match='sign-in unavailable'):
        adapter.api.login()
    with pytest.raises(bridge.LeapmotorApiError, match='deferred'):
        adapter.api.login()

    assert seen['asked'] == 1
    assert 'refresh_token' not in json.loads(adapter.rows[bridge.SESSION_KEY])
    assert adapter.calls == ['login']


def test_a_session_saved_for_another_account_is_not_renewed(adapter):
    """New credentials in Settings: the previous account's refresh token must not keep it alive."""
    _run_out(adapter)
    row = json.loads(adapter.rows[bridge.SESSION_KEY])
    row['username_hash'] = hashlib.sha256(b'previous-user').hexdigest()
    adapter.rows[bridge.SESSION_KEY] = json.dumps(row)
    seen = _renewal(adapter, _renewed_session())
    adapter.api.login()

    assert 'asked' not in seen, "another account's session must not be renewed"
    assert adapter.calls == ['login']
    assert adapter.api.token == 'logged-in-token'


def test_token_refresh_resumes_a_session_another_process_has_renewed(adapter):
    """The web's command hit an expired token while the poller had already renewed: the saved
    session is not expired again, nor its rotated refresh token asked for, and no login is spent."""
    _save(adapter)
    row = json.loads(adapter.rows[bridge.SESSION_KEY])
    row.update(token='renewed-by-the-poller', refresh_token='rotated-by-the-poller')
    adapter.rows[bridge.SESSION_KEY] = json.dumps(row)
    seen = _renewal(adapter, AssertionError('nothing to renew'))
    adapter.api.token_refresh()

    assert 'asked' not in seen
    assert adapter.calls == []
    assert adapter.api.token == 'renewed-by-the-poller'


def test_a_refused_token_stays_dropped_when_the_login_is_deferred(adapter):
    """After a 401 the login waits a minute, and that wait is an exception inside the transaction
    the drop was written in. FakeDB cannot roll back, so this one runs on SQLite."""
    _save(adapter)
    for name, real in zip(('connect_db', 'setting', 'set_setting'), SQLITE):
        adapter.monkeypatch.setattr(bridge, name, real)
    adapter.monkeypatch.delenv('MATE_LAB_LOGIN_ONCE', raising=False)
    with bridge.connect_db() as db:
        db.execute('CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)')
        for key, value in adapter.rows.items():
            bridge.set_setting(db, key, value)
    assert adapter.api._invalidate_session(adapter.api.token)
    seen = _renewal(adapter, LoginUnavailable('cloud_rejection', 200, 302010219))
    for _ in range(2):
        with pytest.raises(bridge.LeapmotorApiError, match='deferred'):
            adapter.api.login()

    with bridge.connect_db() as db:
        saved = json.loads(bridge.setting(db, bridge.SESSION_KEY))
    assert seen['asked'] == 1
    assert 'refresh_token' not in saved
    assert adapter.calls == []
