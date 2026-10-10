"""A sign-in the cloud refuses with 302010108 reads as a refused password.

The 4.x bridge words a refusal by its number alone ("API=302010108"), and the poller tells a
credentials problem from a passing one by the words in it, so this one read as passing: the
startup login asked again every 5 minutes instead of every hour, and the page never said to check
the password (#411: 257 refused sign-ins in a day). 302010108 is "incorrect account or password"
(kerniger/leapmotor-ha#74), and in #338 it went the moment the right password was in.

The cloud-history worker asked on its own every 5 minutes too: in #411 the night's refusals came
from it. It waits the same hour now.
"""
import json
import sys
import threading
from types import SimpleNamespace

import pytest

import client as poller_client   # noqa: F401 — puts the pinned API runtime on sys.path
from leapmotor_cloud.authentication import LoginUnavailable
from mate_api_runtime import api_v2_bridge as bridge
from mate_api_runtime import history_service
from poll_cycle_fixture import NOW, make_startup, poller_main

PM = poller_main("poller_main_refused_password")


@pytest.fixture
def api(monkeypatch):
    class FakeDB:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(bridge, 'connect_db', lambda: FakeDB())
    monkeypatch.setattr(bridge, 'set_setting', lambda db, key, value: None)
    api = object.__new__(bridge.NewAPIClient)
    api.username, api.password, api.language = 'synthetic-user', 'synthetic-password', 'en-US'
    api._installation_device_id = 'synthetic-device'
    api.app_cert_path, api.app_key_path = 'app.crt', 'app.key'
    api._transport, api._mutex = object(), threading.RLock()
    api._audit = lambda *a, **k: None
    return api


@pytest.fixture
def startup(tmp_path, monkeypatch):
    return make_startup(PM, tmp_path, monkeypatch)


def _refused(api, monkeypatch, code):
    """What the bridge raises when the cloud answers a sign-in with `code`."""
    class FakeLoginClient:
        def __init__(self, *a, **kw): pass
        def login(self, *a, **kw): raise LoginUnavailable('cloud_rejection', 200, code)
    monkeypatch.setattr(bridge, 'LoginClient', FakeLoginClient)
    with pytest.raises(bridge.LeapmotorApiError) as caught:
        api._authenticate_session()
    return caught.value


def test_the_code_reads_as_a_refused_password(api, monkeypatch):
    error = _refused(api, monkeypatch, 302010108)
    assert PM._is_bad_credentials(str(error))
    assert "API=302010108" in str(error), "the number stays, for the bundle and for us"


def test_a_refusal_whose_meaning_is_unknown_still_reads_as_passing(api, monkeypatch):
    assert not PM._is_bad_credentials(str(_refused(api, monkeypatch, 302010219)))


def test_the_startup_login_waits_an_hour_and_the_page_says_why(api, monkeypatch, startup):
    client, db, now = startup(refusals=1, error=_refused(api, monkeypatch, 302010108))
    assert client.attempts == 2
    assert now - NOW >= 3600, "a refused password was asked again within the hour"
    assert json.loads(db.get_setting("poll_link", "{}"))["bad_creds"] is True


class _Stop(BaseException):
    pass


def _history_waits(monkeypatch, error):
    """How long the history worker's own loop waits after a sync that raised `error`."""
    waits = []

    class Event:
        def wait(self, seconds):
            waits.append(seconds)
            raise _Stop

    class Thread:
        def __init__(self, target, **kw): self.target = target
        def start(self): self.target()
        def is_alive(self): return False

    def sync_once(on_login=None):
        raise error
    monkeypatch.delenv("MATE_DEMO", raising=False)
    monkeypatch.setitem(sys.modules, "history_worker", SimpleNamespace(sync_once=sync_once))
    monkeypatch.setattr(history_service, "threading", SimpleNamespace(Event=Event, Thread=Thread))
    monkeypatch.setattr(history_service, "_thread", None)
    with pytest.raises(_Stop):
        history_service.start_history_worker()
    return waits


def test_the_history_worker_waits_an_hour_after_a_refused_password(api, monkeypatch):
    assert _history_waits(monkeypatch, _refused(api, monkeypatch, 302010108)) == [3600]


def test_after_any_other_failure_the_history_worker_asks_again_in_five_minutes(api, monkeypatch):
    assert _history_waits(monkeypatch, _refused(api, monkeypatch, 302010219)) == [300]
