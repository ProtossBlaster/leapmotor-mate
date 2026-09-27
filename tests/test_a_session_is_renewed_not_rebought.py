"""Renewing the session instead of spending a login for it.

Measured on the lab on 27/09/2026: the login response carries a `refreshToken` and states
`tokenExpireTime` 7200 and `refreshTokenExpireTime` 604799, and
`POST /base/base-user/token/v1/refresh` answers `code 0` with a whole new session. mate-api
0.1.0a11 turns that into `LoginClient.refresh`.

Until now `token_refresh()` on this adapter did the opposite of its name: it zeroed the saved
expiry and called `login()`. With a token capped at half an hour that was a login every thirty
minutes — 48 a day on an account the cloud has been rationing since 17 September.

What this file pins:
  · a renewal is asked for, and a login is NOT spent, when the saved session can be renewed;
  · the renewed session is written back whole, refresh material included, so the next process
    to read it can renew in turn;
  · a session with no refresh material — every session saved before this version — still takes
    the old path, because there is nothing to renew from;
  · a refusal falls back to the login instead of leaving the adapter with a dead session.
"""
import base64
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import mate_api  # puts poller/mate_api_runtime on sys.path, as the poller process does
import api_v2_bridge as bridge
from leapmotor_cloud.authentication import LoginUnavailable

NOW = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
KEY = base64.b64encode(bytes(range(32))).decode()


@pytest.fixture
def adapter(monkeypatch, tmp_path):
    """The real adapter, with its database and its login client replaced."""
    saved_rows = {}

    class FakeDB:
        def __enter__(self): return self
        def __exit__(self, *a): return False
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
    calls = []
    api.login = lambda: calls.append('login')
    return SimpleNamespace(api=api, rows=saved_rows, calls=calls, monkeypatch=monkeypatch)


def _save(adapter, *, refresh=True):
    row = dict(token='synthetic-token', user_id='synthetic-user-id', device_id='synthetic-device',
               key=KEY, cert='cert.pem', private_key='key.pem',
               expires_at=(NOW + timedelta(seconds=7200)).timestamp(),
               username_hash='synthetic-hash')
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
    assert json.loads(adapter.rows[bridge.SESSION_KEY])['expires_at'] == 0


def test_a_refused_renewal_falls_back_to_the_login(adapter):
    """`302010219 Token refresh error` must not leave the adapter holding a dead session."""
    _save(adapter)
    _renewal(adapter, LoginUnavailable('cloud_rejection', 200, 302010219))
    adapter.api.token_refresh()

    assert adapter.calls == ['login']
    assert json.loads(adapter.rows[bridge.SESSION_KEY])['expires_at'] == 0
