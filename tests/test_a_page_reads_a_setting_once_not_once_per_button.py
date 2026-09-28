"""Deciding which buttons a car may show must not read the database once per button.

27/09/2026. "tutto è lentissimo da caricare … come add-on il problema è ancora più grave", and:
"nella 3.19 era decisamente più veloce". Both true. Measured on one copy of a real database, the
web process alone, median of five:

    Overview   3.17.3 33 ms · 3.18.0 31 ms · 3.19.2 32 ms · 4.0.0 59 ms
    Trips      3.19.2 271 ms · 4.0.0 352 ms

The jump is at 4.0.0, and `_ctx` — which runs for every page — calls `hidden_controls_css`. That
walks every command and asks `command_allowed` for each, and each of those reads three settings.
Profiled: **1040 command_allowed and 3120 get_setting calls for 20 renders — 52 and 156 per page**,
and `db_reader.get_setting` opens a NEW SQLite connection every time. 17 ms per page on an SSD; on
an add-on running off an SD card, where a connection is not nearly free, it is the whole complaint.

The account name and the car's capability snapshot do not change while one page is being built, so
they are read once per render. This asks for exactly that, by counting reads.
"""
import sys
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
for _p in (ROOT / "poller" / "vendor", ROOT / "poller" / "mate_api_runtime"):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))   # APPEND: web/ must stay ahead, both have a main.py


def _counting_settings():
    """A get_setting that answers like a real install and counts what it is asked."""
    calls = []

    def get_setting(key, default=""):
        calls.append(key)
        return default

    return get_setting, calls


def test_one_render_does_not_read_a_setting_per_button():
    ui = pytest.importorskip("ui_command_access",
                             reason="the 4.x command runtime needs the vendored cloud library")
    get_setting, calls = _counting_settings()
    ui.hidden_controls_css("LFZTEST0000000001", get_setting)
    assert len(calls) <= 8, (
        f"{len(calls)} settings reads to draw one page — each one opens its own SQLite "
        f"connection. Distinct keys: {sorted(set(calls))}"
    )


def test_the_same_key_is_not_read_twice():
    ui = pytest.importorskip("ui_command_access",
                             reason="the 4.x command runtime needs the vendored cloud library")
    get_setting, calls = _counting_settings()
    ui.hidden_controls_css("LFZTEST0000000001", get_setting)
    repeats = {k: calls.count(k) for k in set(calls) if calls.count(k) > 1}
    assert not repeats, f"the same setting is read again and again within one render: {repeats}"


def test_the_caller_can_still_impose_its_own_rule():
    """`shown` is how the page and Home Assistant stay one decision; caching must not break it."""
    ui = pytest.importorskip("ui_command_access",
                             reason="the 4.x command runtime needs the vendored cloud library")
    get_setting, calls = _counting_settings()
    css = ui.hidden_controls_css("LFZTEST0000000001", get_setting, shown=lambda name: False)
    assert css.endswith("{display:none!important}") and "api/command/" in css
    assert calls == [], f"with the caller's own rule nothing needs reading: {calls}"
