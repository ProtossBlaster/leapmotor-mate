"""The Vehicle page's roof tile says how far the sunshade is open, as the window tiles do.

Signal 1724 is the percent the car's own screen shows. The tile said only "Open"; it now says
"40%" over "Open", and a closed sunshade still reads "Closed".
"""
import re

import pytest

pytest.importorskip("fastapi", reason="web.main needs fastapi")
from events_fixture import Car, web


def _roof(tmp_path, monkeypatch, pct):
    import main
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    monkeypatch.setattr(main.command_client, "get_fresh_signals", lambda: {"1724": pct})
    page = client.get("/api/vehicle-status").text
    tile = page[page.index("Panoramic roof"):]
    return re.sub(r"<[^>]+>|\s+", " ", tile[:tile.index("</div>\n</div>")]).split()


def test_an_open_sunshade_says_how_far(tmp_path, monkeypatch):
    assert _roof(tmp_path, monkeypatch, 40) == ["Panoramic", "roof", "40%", "Open"]


def test_a_closed_sunshade_says_closed(tmp_path, monkeypatch):
    words = _roof(tmp_path, monkeypatch, 0)
    assert "Closed" in words and not any(w.endswith("%") for w in words)
