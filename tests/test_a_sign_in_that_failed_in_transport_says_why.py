"""A sign-in that failed before any answer says why the transport failed.

The wizard and the log read "New API sign-in unavailable; stage=transport; no automatic retry", and
the poller's "Poll error: Cloud transport failed", for a DNS that does not resolve as much as for a
closed port or a refused certificate (#381, #384). mate-api 0.1.0a15 names the failure in one word
from a fixed vocabulary; the bridge carries that word into the sign-in detail, the setting the
bundle shows and the poll error, and nothing else changes.
"""
import threading
from types import SimpleNamespace

import pytest

import client as poller_client   # noqa: F401 — puts the pinned API runtime on sys.path
from leapmotor_cloud.authentication import LoginUnavailable
from leapmotor_cloud.transport import TransportError
from mate_api_runtime import api_v2_bridge as bridge


@pytest.fixture
def api(monkeypatch, tmp_path):
    rows = {}

    class FakeDB:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def commit(self): pass
    monkeypatch.setattr(bridge, 'connect_db', lambda: FakeDB())
    monkeypatch.setattr(bridge, 'set_setting', lambda db, key, value: rows.__setitem__(key, value))
    api = object.__new__(bridge.NewAPIClient)
    api.username, api.password, api.language = 'synthetic-user', 'synthetic-password', 'en-US'
    api._installation_device_id = 'synthetic-device'
    api.app_cert_path, api.app_key_path = 'app.crt', 'app.key'
    api._transport, api._mutex = object(), threading.RLock()
    api._audit = lambda *a, **k: None
    api.rows = rows
    return api


def _login_raises(monkeypatch, error):
    class FakeLoginClient:
        def __init__(self, *a, **kw): pass
        def login(self, *a, **kw): raise error
    monkeypatch.setattr(bridge, 'LoginClient', FakeLoginClient)


def test_the_sign_in_detail_carries_the_transports_word(api, monkeypatch):
    _login_raises(monkeypatch, LoginUnavailable('transport', reason='dns_failure'))
    with pytest.raises(bridge.LeapmotorApiError) as caught:
        api._authenticate_session()
    assert str(caught.value) == "New API sign-in unavailable; stage=transport (dns_failure); no automatic retry"
    assert api.rows['api_v2_login_failure'] == "stage=transport (dns_failure)"


def test_a_failure_past_the_transport_reads_as_before(api, monkeypatch):
    _login_raises(monkeypatch, LoginUnavailable('cloud_rejection', 200, 302010219))
    with pytest.raises(bridge.LeapmotorApiError) as caught:
        api._authenticate_session()
    assert str(caught.value) == "New API sign-in unavailable; stage=cloud_rejection; HTTP=200; API=302010219; no automatic retry"


def test_a_transport_error_names_its_cause():
    assert str(TransportError('dns_failure')) == "Cloud transport failed: dns_failure"
    assert str(TransportError()) == "Cloud transport failed: transport_failure"
