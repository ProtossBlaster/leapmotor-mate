"""Command endpoint content-negotiation: a caller sending `Accept: application/json` gets a
structured JSON result, while the web UI (HTMX) keeps getting its HTML fragment. This lets a
script, an iOS Shortcut, a smartwatch HTTP button or a Home Assistant `rest_command` fire a
command and parse the outcome. Feature adapted from irek's fork branch `fix/json-api-response`.

Needs web.main (fastapi); the minimal CI env skips this module cleanly."""
import asyncio
import json as _json
import types

import pytest

pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI test env)")
import main
from fastapi import BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse


@pytest.fixture(autouse=True)
def permitted_test_command(monkeypatch):
    monkeypatch.setattr(main.capability_profile, 'command_shown', lambda *args, **kwargs: True)
    monkeypatch.setattr(main.db_reader, 'get_vehicle', lambda: ({'vin':'SYNTHETIC'}, {}))


def _req(accept):
    """Minimal stand-in for a Request — run_command only reads request.headers.get('accept')."""
    return types.SimpleNamespace(headers={"accept": accept})


def _body(resp):
    return _json.loads(resp.body)


# ── the two helpers (pure) ───────────────────────────────────────────────────

def test_wants_json_detects_accept_header():
    assert main._wants_json(_req("application/json")) is True
    assert main._wants_json(_req("application/json, text/html")) is True   # substring is enough
    assert main._wants_json(_req("text/html")) is False
    assert main._wants_json(_req("")) is False


def test_cmd_response_negotiates_type_and_keeps_status():
    j = main._cmd_response(_req("application/json"), html="<b>hi</b>", payload={"ok": True}, status=400)
    assert isinstance(j, JSONResponse) and j.status_code == 400
    assert _body(j) == {"ok": True}
    h = main._cmd_response(_req("text/html"), html="<b>hi</b>", payload={"ok": True}, status=400)
    assert isinstance(h, HTMLResponse) and h.status_code == 400 and b"<b>hi</b>" in h.body


# ── the endpoint, both content types ─────────────────────────────────────────

def test_unknown_command_json_and_html():
    jr = asyncio.run(main.run_command("does_not_exist", _req("application/json"), BackgroundTasks()))
    assert isinstance(jr, JSONResponse) and jr.status_code == 400
    assert _body(jr) == {"ok": False, "error": "unknown_command"}
    # backward-compatible: no JSON asked → the browser still gets HTML, same 400
    hr = asyncio.run(main.run_command("does_not_exist", _req("text/html"), BackgroundTasks()))
    assert isinstance(hr, HTMLResponse) and hr.status_code == 400


def test_cooldown_returns_structured_json(monkeypatch):
    import time
    monkeypatch.setattr(main.db_reader, "get_latest_status", lambda: {})   # parked → not drive-blocked
    monkeypatch.setattr(main.db_reader, "get_language", lambda: "en")
    main._last_command_at = time.time()                                    # a command just fired → cooldown on
    r = asyncio.run(main.run_command("lock", _req("application/json"), BackgroundTasks()))
    b = _body(r)
    assert b["ok"] is False and b["cooldown"] is True and b["retry_in"] >= 1


def test_success_returns_ok_done_json(monkeypatch):
    monkeypatch.setattr(main, "_IS_DEMO", False)
    monkeypatch.setattr(main.db_reader, "get_latest_status", lambda: {})
    monkeypatch.setattr(main.db_reader, "get_language", lambda: "en")
    monkeypatch.setattr(main.db_reader, "set_setting", lambda *a, **k: None)
    monkeypatch.setitem(main._COMMANDS, "test_ok_cmd", lambda: (True, "ok"))
    main._last_command_at = 0                                              # clear any cooldown from a prior test
    r = asyncio.run(main.run_command("test_ok_cmd", _req("application/json"), BackgroundTasks()))
    assert _body(r) == {"ok": True, "status": "done"}


def test_failure_returns_error_json(monkeypatch):
    monkeypatch.setattr(main, "_IS_DEMO", False)
    monkeypatch.setattr(main.db_reader, "get_latest_status", lambda: {})
    monkeypatch.setattr(main.db_reader, "get_language", lambda: "en")
    monkeypatch.setattr(main.db_reader, "set_setting", lambda *a, **k: None)
    monkeypatch.setitem(main._COMMANDS, "test_fail_cmd", lambda: (False, "car timeout"))
    main._last_command_at = 0
    r = asyncio.run(main.run_command("test_fail_cmd", _req("application/json"), BackgroundTasks()))
    b = _body(r)
    assert b["ok"] is False and b["error"] == "car timeout"
