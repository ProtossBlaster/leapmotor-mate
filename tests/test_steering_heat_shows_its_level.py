"""The steering-wheel heat tile says which level the wheel is on, not just that it is on.

Since B10 software 3.41.30 (rolled out over the air from 30.08.2026) the wheel heats at two levels,
and signal 1816 reports four states, read twice against the car's own screen:

    0  off
    1  level I
    2  level II, switched on in the car (the button in the car starts at II)
    3  level II, switched on remotely (official app or API); a press in the car then drops it to I

An enumeration, not a bit mask — 3 is not "I and II". Before 3.41.30 a remote "on" read 2.

The remote command (320) knows three payloads the car acts on: level "0" or "1" turns the heat off
and level "2" turns it on at II; every other level is accepted by the cloud and ignored by the car.
So the tile is a slider like the heated seats', Off | 1 | 2, sending the same two commands the
on/off button sent — and 1 is a level it can show but never ask for. That last rule lives in the
page's JavaScript and is held by test_the_commands_page_in_a_browser.py.
"""
import json
import re

import pytest

pytest.importorskip("fastapi", reason="web/main.py needs fastapi (absent in the minimal CI env)")
pytest.importorskip("httpx", reason="Starlette TestClient needs httpx")

import capability_profile
import db as D
import db_reader
import main
from starlette.testclient import TestClient

VIN = "LVIN0000000000001"


@pytest.fixture
def grid(tmp_path, monkeypatch):
    """Render the Commands grid of a B10 whose wheel reports `raw` on 1816."""
    D.Database(str(tmp_path / "t.db")).ensure_vehicle(VIN, "B10", 2025)
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    db_reader.set_setting("setup_complete", "1")
    client = TestClient(main.app)

    def render(raw):
        db_reader.set_setting(f"comfort_state_{VIN.lower()}", json.dumps({"steering_heat": raw}))
        r = client.get("/api/cmd-grid")
        assert r.status_code == 200, r.text[:500]
        return r.text
    return render


def _slider(html):
    m = re.search(r'<input[^>]*hx-post="api/command/steering_heat_on"[^>]*>', html)
    assert m, "no steering slider in the grid"
    return m.group(0)


def _readout(html):
    return re.search(r'id="sm-steering_heat">([^<]*)<', html).group(1)


@pytest.mark.parametrize("raw, level, readout, remote", [
    (0, "0", "Off", False),
    (1, "1", "Lv 1", False),
    (2, "2", "Lv 2", False),
    (3, "2", "Lv 2", True),
])
def test_the_tile_shows_the_level_the_car_reports(grid, raw, level, readout, remote):
    html = grid(raw)
    assert f'value="{level}"' in _slider(html)
    assert _readout(html) == readout
    assert ("(turned on remotely)" in html) is remote


def test_level_two_switched_on_remotely_is_not_called_level_three(grid):
    # The badge prints the level beside "On" the way the seats' does. Fed the raw 3 it would say
    # "On · 3", a level the wheel does not have.
    assert "· 3" not in grid(3)


def test_the_slider_sends_the_two_commands_the_button_sent(grid):
    slider = _slider(grid(0))
    assert 'hx-post="api/command/steering_heat_on"' in slider
    assert "api/command/steering_heat_off" in slider
    assert 'max="2"' in slider


def test_without_the_command_the_level_is_still_shown(grid):
    # A car whose remote steering command is confirmed broken keeps a read-only tile: no slider,
    # but the level and the remote note are the car's state and stay.
    capability_profile.save(VIN, {"steering_heat_cmd": "broken"})
    html = grid(3)
    assert not re.search(r'<input[^>]*hx-post="api/command/steering_heat_on"', html)
    assert "● On · 2" in html
    assert "(turned on remotely)" in html
