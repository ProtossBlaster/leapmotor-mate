"""A comfort tile drops the "✓ Done" of a command the cloud took, and only that: a warning or an
error must stay in the tile. So run_command marks a taken command in its answer, `data-ok`, the
way it marks a warning `data-warn` — the page reads the marker, not the wording or the glyph of
the "✓ Done" it happens to show."""
import pytest

pytest.importorskip("fastapi", reason="web/main.py needs fastapi (absent in the minimal CI env)")
pytest.importorskip("httpx", reason="Starlette TestClient needs httpx")

import db as D
import db_reader
import main
from starlette.testclient import TestClient

VIN = "LVIN0000000000001"


@pytest.fixture
def web(tmp_path, monkeypatch):
    D.Database(str(tmp_path / "t.db")).ensure_vehicle(VIN, "B10", 2025)
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    db_reader.set_setting("setup_complete", "1")
    monkeypatch.setattr(main, "_post_command_refresh", lambda *a, **k: None)
    monkeypatch.setattr(main, "_last_command_at", 0.0)                     # no cooldown from an earlier test
    return TestClient(main.app)


@pytest.mark.parametrize("outcome, taken", [((True, ""), True), ((False, "boom"), False)],
                         ids=["taken", "failed"])
def test_the_page_can_tell_a_command_the_cloud_took(web, monkeypatch, outcome, taken):
    monkeypatch.setitem(main._COMMANDS, "mirror_heat_on", lambda: outcome)   # no cloud
    r = web.post("/api/command/mirror_heat_on")
    assert r.status_code == 200
    assert ('data-ok="1"' in r.text) is taken, r.text
