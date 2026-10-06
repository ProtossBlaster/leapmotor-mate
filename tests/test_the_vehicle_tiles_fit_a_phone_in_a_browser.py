"""No label on the Vehicle page runs past its tile on a phone, in any language.

The official app's Polish name for the sunshade, «Osłona przeciwsłoneczna», has a word wider than a
tile at 390 px: in capitals it ran past the tile's border. A soft hyphen in the name lets it break
as «PRZECIW-SŁONECZNA», and only where it has to. Letting every label break anywhere was tried and is
worse: «SONNENBLENDE» and «RECIRCULACIÓN», which end inside the border, lost their last letter to a
line of its own. Measured in a real browser, because only layout says where a word ends.
"""
import pytest

pytest.importorskip("fastapi", reason="web.main needs fastapi")
from events_fixture import Car, serve, web

LANGS = ("en", "it", "fr", "de", "pl", "pt-PT", "nl", "es")
# Each tile label whose text ends right of its tile's border, and by how many pixels.
_PAST = """() => [...document.querySelectorAll('#vehicle-status .rounded-xl')].map(tile => {
  const label = tile.querySelector('.uppercase');
  if (!label) return null;
  const text = document.createRange(); text.selectNodeContents(label);
  const right = Math.max(...[...text.getClientRects()].map(r => r.right));
  return [label.textContent.trim(), Math.round(right - tile.getBoundingClientRect().right)];
}).filter(past => past && past[1] > 0)"""


@pytest.mark.parametrize("lang", LANGS)
def test_every_tile_label_ends_inside_its_tile(tmp_path, monkeypatch, lang):
    pw = pytest.importorskip("playwright.sync_api")
    import main
    car = Car(tmp_path)
    client = web(car, monkeypatch, lang=lang)
    monkeypatch.setattr(main.command_client, "get_fresh_signals", lambda: {"1724": 40})
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 900})
        page.route("**/*", serve(client))
        page.goto("http://mate.test/vehicle")
        page.locator("#vehicle-status .uppercase").first.wait_for()
        assert page.evaluate(_PAST) == []
        browser.close()
