"""The Commands sunshade tile says how far the sunshade is open, and part-way it offers both ends.

The command (240) knows two values the car acts on: 10 opens fully and 0 closes; anything between is
accepted by the cloud and ignored by the car (tried on a B10 from 100 % and from 0 %). The sunshade
still stops part-way when it is stopped from the car's screen, and from there the tile offered only
"Close". Now 1–99 % shows "Open" and "Close" side by side, the percent sits under them as it does
under the windows' slider, and a command is confirmed only once 1724 reaches the end it asked for:
from 50 %, "Open" no longer counts as done on the first frame. The page's own wait is held by
test_the_commands_page_in_a_browser.py.
"""
import re

import pytest

pytest.importorskip("fastapi", reason="web.main needs fastapi")
from events_fixture import Car, web


def _tile(tmp_path, monkeypatch, pct):
    car = Car(tmp_path)
    client = web(car, monkeypatch)
    car.frame(**{"1724": pct})
    page = client.get("/api/cmd-grid").text
    tile = page[page.index("<!-- Sunshade -->"):page.index("<!-- Climate")]
    badge = re.sub(r"\s+", " ", re.search(r'rounded-full[^>]*>([^<]*)<', tile).group(1)).strip()
    sends = re.findall(r'hx-post="api/command/(open_sunshade|close_sunshade)"', tile)
    under = re.search(r'text-\[10px\] text-slate-400">([^<]*)<', tile).group(1)
    return badge, sends, under


@pytest.mark.parametrize("pct, badge, sends, under", [
    (0, "○ Closed", ["open_sunshade"], "0%"),
    (50, "● Open", ["open_sunshade", "close_sunshade"], "50%"),
    (100, "● Open", ["close_sunshade"], "100%"),
])
def test_the_tile_says_how_far_and_offers_what_the_car_can_do(tmp_path, monkeypatch, pct, badge, sends, under):
    assert _tile(tmp_path, monkeypatch, pct) == (badge, sends, under)


def test_open_from_part_way_waits_for_the_top(monkeypatch):
    import main
    from test_command_verify import _patch
    readings = iter([50, 50, 85, 100])
    _, calls = _patch(monkeypatch, lambda: {"1724": next(readings, 100)})
    main._command_epoch = 9
    main._post_command_refresh(main._OPTIMISTIC["open_sunshade"], epoch=9, delay=3, deadline_s=30)
    assert [s["1724"] for s in calls["save"]] == [100] and calls["clear"] == 0
