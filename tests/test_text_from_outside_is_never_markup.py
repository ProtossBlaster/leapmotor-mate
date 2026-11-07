"""Text that comes from outside Mate does not become markup by another door.

The car's address is OpenStreetMap's text (anyone can edit it), and /api/nav/current-address returned
it as text/html: the Navigation page put it in as text, but a visit to the URL itself rendered it.
And the Costs page put its bands and modes into a <script> as json.dumps, which leaves "</script>"
as it is. Both found in the 4.24.0 security review.
"""
import json

import pytest

pytest.importorskip("fastapi", reason="web.main needs the production web dependencies")
pytest.importorskip("httpx", reason="Starlette TestClient needs httpx")

from starlette.testclient import TestClient  # noqa: E402

import main  # noqa: E402


def test_the_current_address_is_plain_text(monkeypatch):
    for var in ("MATE_AUTH_PASSWORD", "SUPERVISOR_TOKEN", "HASSIO_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(main.db_reader, "get_latest_status", lambda: {"latitude": 45.1, "longitude": 9.2})
    monkeypatch.setattr(main.geocode, "reverse_geocode", lambda *a, **k: '<img src=x onerror="alert(1)">')
    response = TestClient(main.app).get("/api/nav/current-address", headers={"Host": "192.168.1.5:4000"})
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == '<img src=x onerror="alert(1)">'


def test_json_in_a_script_cannot_close_the_script():
    value = [{"name": "</script><script>alert(1)</script>", "price": 0.2}]
    out = main._script_json(value)
    assert "</script>" not in out and "<" not in out
    assert json.loads(out) == value
