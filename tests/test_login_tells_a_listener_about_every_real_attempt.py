"""The API client tells a listener about every login the cloud is really asked for.

Both backends can come back from `login()` without a login: the legacy one restores the blob
the two processes share, the 4.x one resumes its saved session while the certificate holds —
and the 4.x bridge logs in on its own from inside a read or a command whose token had lapsed,
where no caller sees a `login()` at all. Whoever counts logins — the one thing the cloud
rations — needs the one place the login is made to say so: `api.on_login(None)` when the cloud
let us in, `api.on_login(exc)` when it did not, nothing for a session that was resumed or an
attempt the bridge put off locally.
"""
import hashlib
import importlib.util
import json
import pathlib
import sqlite3
import threading
import time
import types
from datetime import datetime, timedelta, timezone

import client as poller_client   # puts the pinned API runtime on sys.path
import crypto
import pytest
from mate_api_runtime import api_v2_bridge as bridge

_ROOT = pathlib.Path(__file__).resolve().parents[1]


class _Listener:
    def __init__(self):
        self.heard = []

    def __call__(self, exc):
        self.heard.append(exc)


def _copy(process):
    """The poller and the web each carry their own copy of session_share; both must agree."""
    spec = importlib.util.spec_from_file_location(f"session_share_{process}", _ROOT / process / "session_share.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── the legacy shared session ─────────────────────────────────────────────────

class _LegacyApi:
    def __init__(self, raises=None):
        self.logins = 0
        self.raises = raises

    def login(self):
        self.logins += 1
        if self.raises:
            raise self.raises


def _legacy(monkeypatch, process, restored, raises=None):
    share = _copy(process)
    monkeypatch.setattr(share, "_restore", lambda a: restored)
    monkeypatch.setattr(share, "_save", lambda a: None)
    api = _LegacyApi(raises)
    api.login = types.MethodType(share._shared_login, api)
    api.on_login = _Listener()
    return api


@pytest.mark.parametrize("process", ["poller", "web"])
def test_a_restored_shared_session_is_not_a_login(monkeypatch, process):
    api = _legacy(monkeypatch, process, restored=True)
    api.login()
    assert api.logins == 0 and api.on_login.heard == []


@pytest.mark.parametrize("process", ["poller", "web"])
def test_a_real_legacy_login_is_told(monkeypatch, process):
    api = _legacy(monkeypatch, process, restored=False)
    api.login()
    assert api.logins == 1 and api.on_login.heard == [None]


@pytest.mark.parametrize("process", ["poller", "web"])
def test_a_legacy_login_the_cloud_refused_is_told_and_still_raises(monkeypatch, process):
    refused = RuntimeError("Leapmotor login failed: Error occurred")
    api = _legacy(monkeypatch, process, restored=False, raises=refused)
    with pytest.raises(RuntimeError):
        api.login()
    assert api.on_login.heard == [refused]


@pytest.mark.parametrize("process", ["poller", "web"])
def test_the_legacy_path_needs_no_listener(monkeypatch, process):
    api = _legacy(monkeypatch, process, restored=False)
    api.on_login = None
    api.login()
    assert api.logins == 1


# ── the 4.x bridge ────────────────────────────────────────────────────────────

def _bridge(tmp_path, monkeypatch, saved, raises=None, login_once=True):
    db = tmp_path / "v2.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    if saved:
        con.execute("INSERT INTO settings VALUES (?, ?)", (bridge.SESSION_KEY, crypto.encrypt(json.dumps(saved))))
    con.commit(); con.close()
    monkeypatch.setattr(bridge, "DB", str(db))
    monkeypatch.setattr(bridge, "certificate_usable", lambda cert, key: True)
    if login_once:
        monkeypatch.setenv("MATE_LAB_LOGIN_ONCE", "1")
    else:
        monkeypatch.delenv("MATE_LAB_LOGIN_ONCE", raising=False)
    api = object.__new__(bridge.NewAPIClient)
    api._mutex = threading.RLock()
    api.username, api.password, api._installation_device_id, api.token = "u", "p", "dev", None
    api.applied, api.authenticated = [], 0
    api._apply_session = lambda s: api.applied.append(s)
    api.on_login = _Listener()
    session = types.SimpleNamespace(token="T", user_id="U", device_id="D", key=b"k", client_cert=("c", "k"),
                                    expires_at=datetime.now(timezone.utc) + timedelta(hours=1))

    def _auth():
        api.authenticated += 1
        if raises:
            raise raises
        return session
    api._authenticate_session = _auth
    return api


def _saved():
    return {"username_hash": hashlib.sha256(b"u").hexdigest(), "expires_at": time.time() + 3600,
            "cert": "c", "private_key": "k"}


def test_a_resumed_saved_session_is_not_a_login(tmp_path, monkeypatch):
    api = _bridge(tmp_path, monkeypatch, _saved())
    api.login()
    assert api.authenticated == 0 and len(api.applied) == 1
    assert api.on_login.heard == []


def test_a_real_bridge_login_is_told(tmp_path, monkeypatch):
    api = _bridge(tmp_path, monkeypatch, saved=None)
    api.login()
    assert api.authenticated == 1 and len(api.applied) == 1
    assert api.on_login.heard == [None]


def test_a_bridge_login_the_cloud_refused_is_told_and_still_raises(tmp_path, monkeypatch):
    refused = bridge.LeapmotorApiError("New API sign-in unavailable; stage=cloud_rejection; HTTP=200; API=39; no automatic retry")
    api = _bridge(tmp_path, monkeypatch, saved=None, raises=refused)
    with pytest.raises(bridge.LeapmotorApiError):
        api.login()
    assert api.on_login.heard == [refused]


@pytest.mark.parametrize("on_the_way_to", ["_ensure_token", "token_refresh"])
def test_a_login_made_on_the_way_to_a_read_is_told_too(tmp_path, monkeypatch, on_the_way_to):
    """The bridge logs in by itself when a read finds no token, and a token refresh on it is a
    login — the listener hears those, which no caller of `login()` ever could."""
    api = _bridge(tmp_path, monkeypatch, saved=None)
    getattr(api, on_the_way_to)()
    assert api.authenticated == 1 and api.on_login.heard == [None]


def test_an_attempt_the_bridge_put_off_locally_is_not_told(tmp_path, monkeypatch):
    """Inside a minute of a refused attempt the bridge does not ask the cloud at all."""
    refused = bridge.LeapmotorApiError("New API sign-in unavailable; stage=cloud_rejection; no automatic retry")
    api = _bridge(tmp_path, monkeypatch, saved=None, raises=refused, login_once=False)
    with pytest.raises(bridge.LeapmotorApiError, match="cloud_rejection"):
        api.login()
    with pytest.raises(bridge.LeapmotorApiError, match="temporarily deferred"):
        api.login()
    assert api.authenticated == 1 and api.on_login.heard == [refused]


def test_the_bridge_needs_no_listener(tmp_path, monkeypatch):
    api = _bridge(tmp_path, monkeypatch, saved=None)
    api.on_login = None
    api.login()
    assert api.authenticated == 1


def test_the_poller_client_hands_the_listener_to_its_backend(monkeypatch):
    heard = _Listener()
    made = {}

    class _Api:
        def __init__(self, **kw):
            made.update(kw)
    monkeypatch.setattr(poller_client, "LeapmotorApiClient", _Api)
    c = poller_client.LeapmotorMateClient(username="u", password="p", pin="1", cert_path="c", key_path="k",
                                          on_login=heard)
    assert c._api.on_login is heard
