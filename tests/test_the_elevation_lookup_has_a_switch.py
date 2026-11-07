"""The elevation lookup has a switch in Settings.

A few minutes after a trip ends, elevation_enrich sends its GPS track to Open-Meteo. The module read
an `elevation_enabled` setting and the manual said the lookup "can be switched off in Settings", but
no page wrote that setting: the only way to stop the track leaving the device was to edit the
database. Settings → Trips now has the box, beside the outside temperature's. It starts ticked, as
the lookup has always been on, and the 'Calculate elevation' button on a trip's page is not governed
by it: that is one trip, asked for.
"""
import json
import pathlib

import pytest

pytest.importorskip("fastapi", reason="web/main.py needs fastapi (absent in the minimal CI env)")
pytest.importorskip("httpx", reason="Starlette TestClient needs httpx")

import db as D
import db_reader
import elevation_enrich
import main
from starlette.testclient import TestClient

LOCALES = pathlib.Path(__file__).resolve().parent.parent / "web" / "locales"


@pytest.fixture
def client(tmp_path, monkeypatch):
    D.Database(str(tmp_path / "p.db")).close()
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "p.db"))
    db_reader.set_setting("setup_complete", "1")
    return TestClient(main.app)


def _box(html: str) -> str:
    at = html.index('name="elevation_enabled"')
    return html[at:html.index(">", at)]


def test_the_box_starts_ticked_and_the_lookup_on(client):
    assert elevation_enrich._enabled()
    assert "checked" in _box(client.get("/settings").text)


def test_unticking_the_box_stops_the_lookup(client, monkeypatch):
    monkeypatch.setattr(elevation_enrich, "_sweep_now", lambda: None)   # no thread reaches the network
    monkeypatch.setattr(elevation_enrich, "_running", False)

    r = client.post("/api/settings/elevation", data={})      # an unticked box submits nothing
    assert r.status_code == 200
    assert db_reader.get_setting("elevation_enabled", "1") == "0"
    assert not elevation_enrich._enabled()
    # a sweep stamps `elev_sweep_at` before it starts: unstamped, none was started
    elevation_enrich.maybe_sweep()
    assert db_reader.get_setting("elev_sweep_at", "") == ""
    assert "checked" not in _box(client.get("/settings").text)
    assert db_reader.get_setting("elev_sweep_at", "") == ""    # nor by the page being opened

    client.post("/api/settings/elevation", data={"elevation_enabled": "1"})
    elevation_enrich.maybe_sweep()
    assert db_reader.get_setting("elev_sweep_at", "") != ""


def test_ticking_it_again_brings_the_lookup_back(client):
    client.post("/api/settings/elevation", data={})
    client.post("/api/settings/elevation", data={"elevation_enabled": "1"})
    assert elevation_enrich._enabled()
    assert "checked" in _box(client.get("/settings").text)


@pytest.mark.parametrize("path", sorted(LOCALES.glob("*.json")), ids=lambda p: p.stem)
def test_every_language_names_the_box(path):
    strings = json.loads(path.read_text(encoding="utf-8"))["translations"]
    assert strings.get("elevation_enable_title")
    # the description names the button that still works, in that language's own words
    assert strings["elevation_calc_btn"] in strings["elevation_enable_desc"]
