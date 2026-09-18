"""A wrong Home Assistant URL must not take over the Settings page (#294, @synvoll).

Settings calls `api/wallbox/test` on load whenever a token is saved. Mate appends `/api/` to the
URL typed in the wallbox card; with a URL that carries a path — a dashboard link, say —
`<url>/api/` lands on HA's FRONTEND, whose catch-all answers 200 with its own HTML page. Mate read
that 200 as "API running", put the whole page in the message, and `_ha_test_html` wrapped it in a
span without escaping it: htmx swapped HA's shell into Settings, ran its scripts, and the page
became "Could not load Home Assistant". The form to fix the URL lives only there, so the user was
locked out of the one place that could undo it.

Two faults, two guards. HA's `/api/` always answers JSON, so a 200 that is not JSON is "this is not
the API", never a success. And nothing that comes back from the configured URL reaches the page
unescaped — whatever answers there, a page of it is not ours to run.
"""
import pytest

import ha_client

HA_SHELL = ('<!DOCTYPE html><html><head><script src="/frontend_latest/core.js"></script></head>'
            '<body>Could not load Home Assistant.</body></html>')


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(ha_client, "is_configured", lambda: True)
    monkeypatch.setattr(ha_client, "_creds", lambda: ("http://ha.local:8123/lovelace/0", "tok"))

    def answer(status, body):
        monkeypatch.setattr(ha_client, "_request", lambda *a, **k: (status, body))
    return answer


def test_a_200_that_is_not_json_is_not_the_api(configured):
    configured(200, HA_SHELL)
    res = ha_client.test_connection()
    assert res["ok"] is False
    assert "<" not in res["error"], "the page itself must not become the error"


def test_the_real_api_answer_still_connects(configured):
    configured(200, {"message": "API running."})
    assert ha_client.test_connection() == {"ok": True, "message": "API running."}


def _route_html(monkeypatch, res):
    pytest.importorskip("fastapi", reason="web.main needs the production web dependencies")
    pytest.importorskip("httpx", reason="Starlette TestClient needs httpx")
    import main
    from fastapi.testclient import TestClient
    monkeypatch.setattr(main.ha_client, "test_connection", lambda: res)
    return TestClient(main.app).get("/api/wallbox/test").text


def test_the_settings_snippet_escapes_a_message_from_the_url(monkeypatch):
    html = _route_html(monkeypatch, {"ok": True, "message": HA_SHELL})
    assert "<script" not in html
    assert "&lt;script" in html


def test_the_settings_snippet_escapes_an_error_from_the_url(monkeypatch):
    html = _route_html(monkeypatch, {"ok": False, "error": HA_SHELL})
    assert "<script" not in html
    assert "&lt;script" in html


def test_the_page_says_the_url_is_wrong_instead_of_connected(monkeypatch, configured):
    configured(200, HA_SHELL)
    pytest.importorskip("fastapi", reason="web.main needs the production web dependencies")
    pytest.importorskip("httpx", reason="Starlette TestClient needs httpx")
    import main
    from fastapi.testclient import TestClient
    html = TestClient(main.app).get("/api/wallbox/test").text
    assert "Connected" not in html
    assert "<script" not in html
