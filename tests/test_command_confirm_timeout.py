"""A car-confirm timeout must NOT be treated as a network error — riri19 / #73.

When the cloud accepts a command (HTTP 200) but the car doesn't acknowledge within the
cloud's poll window, the library raises "Timed out waiting for remote control result".
That is not a connection fault: the old code matched the bare word "timed out" and so
reset the session + RE-SENT the command (firing it at the car twice) + forced a needless
re-login. The command path must now stop after a SINGLE send — no reset, no re-login —
and still classify the outcome as 'timeout_car' for the responsiveness log.

A GENUINE socket timeout ("Read timed out", no "remote control result") must STILL reset
+ retry exactly as before — that part must not regress.
"""
import types

import pytest


import command_client as cc


class _FakeAPI:
    def __init__(self):
        self.token = "T1"
        self.closed = 0

    def close(self):
        self.closed += 1


def _session_with(monkeypatch, api):
    sess = cc.LeapmotorSession()
    veh = types.SimpleNamespace(vin="VIN123")

    def fake_connect():            # inject the fake api+vehicle instead of a real login
        if sess._api is None:      # re-injected after a _reset() → counts a "re-login"
            sess._api = api
            sess._vehicle = veh

    monkeypatch.setattr(sess, "_connect", fake_connect)
    monkeypatch.setattr(cc.time, "sleep", lambda *_a, **_k: None)
    return sess


# The exact message the library raises (cloud answered HTTP 200, car never ACKed → data:0).
_CONFIRM_TIMEOUT = ("Timed out waiting for remote control result: "
                    "{'code': 0, 'result': 0, 'message': 'Request successful', 'data': 0}")
_SOCKET_TIMEOUT = "HTTPSConnectionPool(host='...', port=443): Read timed out. (read timeout=30)"


# ── the fix: a confirm timeout sends once, no reset, no re-login ─────────────────

def test_confirm_timeout_sends_once_no_relogin(monkeypatch):
    api = _FakeAPI()
    sess = _session_with(monkeypatch, api)
    n = {"c": 0}

    def action(_api, _vin):
        n["c"] += 1
        raise RuntimeError(_CONFIRM_TIMEOUT)

    ok, msg = sess.execute(action)
    assert ok is False
    assert n["c"] == 1            # sent ONCE — not re-fired at the car
    assert api.closed == 0        # session never reset → no needless re-login
    assert "remote control result" in msg


def test_confirm_timeout_classified_timeout_car():
    assert cc._classify_outcome(False, _CONFIRM_TIMEOUT) == "timeout_car"


# ── no regression: a genuine socket timeout still resets + retries ───────────────

def test_genuine_socket_timeout_is_not_retried(monkeypatch):
    api = _FakeAPI()
    sess = _session_with(monkeypatch, api)
    n = {"c": 0}

    def action(_api, _vin):
        n["c"] += 1
        raise RuntimeError(_SOCKET_TIMEOUT)

    ok, msg = sess.execute(action)
    assert ok is False
    assert n["c"] == 1            # ambiguous transport outcome must not replay a command
    assert api.closed == 0        # no automatic reset/replay


def test_genuine_socket_timeout_classified_unreachable():
    # a real socket read-timeout (no "remote control result") = cloud_unreachable, not timeout_car
    assert cc._classify_outcome(False, _SOCKET_TIMEOUT) == "cloud_unreachable"


# ── no regression: the success path is untouched ────────────────────────────────

def test_success_sends_once(monkeypatch):
    api = _FakeAPI()
    sess = _session_with(monkeypatch, api)
    n = {"c": 0}

    def action(_api, _vin):
        from types import SimpleNamespace
        n["c"] += 1
        _api.last_new_command_receipt = SimpleNamespace(outcome='accepted')

    ok, msg = sess.execute(action)
    assert ok is True and "physical execution not confirmed" in msg
    assert n["c"] == 1 and api.closed == 0
