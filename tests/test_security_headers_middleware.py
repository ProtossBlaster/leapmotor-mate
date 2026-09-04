"""The security-headers middleware, exercised through a real request — not just security.py.

Every other check on `security_headers()` calls the function directly, so none of them can see a
failure that only exists in how Starlette actually serializes what it returns (header values are
encoded as latin-1 when the response is written). `security.py`'s own validation is what prevents
that failure now, but this file is the one that would notice if some future change reintroduced
an unvalidated value on the path from MATE_FRAME_ANCESTORS to a live response header.

Needs web.main (fastapi); the minimal CI env skips this module cleanly, same as
test_command_json_api.py and friends.
"""
import pytest

pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI test env)")

import main
from starlette.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    monkeypatch.delenv("HASSIO_TOKEN", raising=False)
    return TestClient(main.app)


def test_default_headers_reach_a_real_response(client, monkeypatch):
    monkeypatch.delenv("MATE_FRAME_ANCESTORS", raising=False)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["content-security-policy"] == "frame-ancestors 'none'"


def test_a_declared_frame_ancestor_reaches_a_real_response(client, monkeypatch):
    monkeypatch.setenv("MATE_FRAME_ANCESTORS", "https://hass.example.com")
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert "x-frame-options" not in resp.headers
    assert resp.headers["content-security-policy"] == "frame-ancestors https://hass.example.com"


def test_a_hostile_env_value_never_500s_a_real_response(client, monkeypatch):
    """This is the exact failure the validation in security.py exists to prevent: an unvalidated
    non-ASCII value here used to raise UnicodeEncodeError while Starlette wrote the response
    headers, turning every request — this liveness probe included — into a 500 with the process
    otherwise running normally."""
    monkeypatch.setenv("MATE_FRAME_ANCESTORS", "https://пример.рф")
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["content-security-policy"] == "frame-ancestors 'none'"
