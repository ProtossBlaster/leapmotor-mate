"""The Vehicle page's roof tile says how far the sunshade is open, as the window tiles do.

Signal 1724 is the percent the car's own screen shows. The tile said only "Open"; it now says
"40%" over "Open", and a closed sunshade still reads "Closed".

Its "open" and "closed" are the sunshade's own words, not the generic ones of the door tiles: under
the official app's names the sunshade is masculine in Spanish (el parasol) and feminine in Portuguese
(a cortina) and Polish (osłona przeciwsłoneczna), and the generic "Abierta", "Aberto" and "Otwarty"
do not agree with it.
"""
import re

import pytest

pytest.importorskip("fastapi", reason="web.main needs fastapi")
from events_fixture import Car, web


def _roof(tmp_path, monkeypatch, pct, lang="en"):
    import i18n
    import main
    car = Car(tmp_path)
    client = web(car, monkeypatch, lang=lang)
    monkeypatch.setattr(main.command_client, "get_fresh_signals", lambda: {"1724": pct})
    page = client.get("/api/vehicle-status").text
    tile = page[page.index(i18n.get_t(lang)("win_sunshade")):]
    return re.sub(r"<[^>]+>|\s+", " ", tile[:tile.index("</div>\n</div>")]).replace("\u00ad", "").split()


def test_an_open_sunshade_says_how_far(tmp_path, monkeypatch):
    assert _roof(tmp_path, monkeypatch, 40) == ["Sunshade", "40%", "Open"]


def test_a_closed_sunshade_says_closed(tmp_path, monkeypatch):
    words = _roof(tmp_path, monkeypatch, 0)
    assert "Closed" in words and not any(w.endswith("%") for w in words)


@pytest.mark.parametrize("lang, pct, words", [
    ("es", 40, ["Parasol", "40%", "Abierto"]),
    ("es", 0, ["Parasol", "Cerrado"]),
    ("pt-PT", 40, ["Cortina", "40%", "Aberta"]),
    ("pt-PT", 0, ["Cortina", "Fechada"]),
    ("pl", 40, ["Osłona", "przeciwsłoneczna", "40%", "Otwarta"]),
    ("pl", 0, ["Osłona", "przeciwsłoneczna", "Zamknięta"]),
])
def test_open_and_closed_agree_with_the_sunshades_name(tmp_path, monkeypatch, lang, pct, words):
    assert _roof(tmp_path, monkeypatch, pct, lang) == words
