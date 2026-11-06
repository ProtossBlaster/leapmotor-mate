"""The text of an error is shown as written, and never run.

Three answers put somebody else's words into the page as HTML: a failed command gives the cloud's
message or an exception's text, the MQTT test names the broker it was asked to try, and a refused
restore gives the reason. htmx swaps each answer into the page, so markup in those words was
markup in the page: a broker typed as `<img src=x onerror=…>` came back as an element, and a
message from the cloud would have too. Escaped, the words are read and nothing else happens.
"""
import pytest

pytest.importorskip("fastapi", reason="web/main.py needs fastapi (absent in the minimal CI env)")
pytest.importorskip("httpx", reason="Starlette TestClient needs httpx")

import db as D
import db_reader
import main
import mqtt_check
from starlette.testclient import TestClient

MARKUP = '<img src=x onerror="alert(1)">'
AS_TEXT = "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;"


@pytest.fixture
def client(tmp_path, monkeypatch):
    D.Database(str(tmp_path / "p.db")).close()
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "p.db"))
    return TestClient(main.app)


def test_a_failed_command_shows_the_message_as_text(client):
    html = main._cmd_error_html("refused: " + MARKUP)
    assert "<img" not in html
    assert AS_TEXT in html


def test_a_failed_command_still_reads_as_before(client):
    assert main._cmd_error_html("PIN refused") == '<span style="color:#ef4444">✗ PIN refused</span>'


def test_the_mqtt_test_shows_the_reason_as_text(client, monkeypatch):
    monkeypatch.setattr(mqtt_check, "check_connection",
                        lambda *a, **k: (False, "No response from " + MARKUP + ":1883"))
    r = client.post("/api/settings/mqtt/test", data={"mqtt_broker": MARKUP})
    assert "<img" not in r.text
    assert AS_TEXT in r.text


@pytest.mark.parametrize("error, status", [(ValueError, 400), (RuntimeError, 500)])
def test_a_refused_restore_shows_the_reason_as_text(client, monkeypatch, error, status):
    def refuse(blob):
        raise error("cannot read " + MARKUP)
    monkeypatch.setattr(db_reader, "restore_database", refuse)
    r = client.post("/api/import/database", files={"file": ("backup.db", b"x")})
    assert r.status_code == status
    assert "<img" not in r.text
    assert AS_TEXT in r.text
