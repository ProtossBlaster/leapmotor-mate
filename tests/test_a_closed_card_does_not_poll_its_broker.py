"""A status dot on a card nobody opened must not keep dialling out.

Measured on the production add-on (aarch64) on 28/09/2026: `/api/settings/mqtt/status` costs
**1.011 s**, because it opens a real TCP connection to the MQTT broker to decide the colour of one
dot. The Settings card asks for it `on load, every 30s` — so while that page is open, whether or
not anyone has expanded the MQTT card, the box spends a second dialling a broker every half minute.
The same macro drives the ABRP dot, which reaches the ABRP service.

HTMX can gate a repeating trigger on a condition, so the repeat only runs while the card is open.
The first load still happens — the collapsed summary is meant to show the colour — and expanding a
card gives a fresh reading, as before.
"""
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SETTINGS = ROOT / "web" / "templates" / "settings.html"


def _macro_trigger():
    html = SETTINGS.read_text(encoding="utf-8")
    block = html.split("{% macro scard(", 1)[1].split("{% endmacro %}", 1)[0]
    match = re.search(r'hx-get="\{\{ status_url \}\}"[^>]*hx-trigger="([^"]+)"', block)
    assert match, "the status dot's trigger is not where this test expects it"
    return match.group(1)


def test_the_repeat_only_runs_while_the_card_is_open():
    trigger = _macro_trigger()
    assert "every" in trigger, f"the dot no longer refreshes at all: {trigger!r}"
    repeat = [part for part in trigger.split(",") if "every" in part][0]
    assert "[" in repeat and "open" in repeat, (
        f"the repeat is unconditional, so a closed card keeps dialling the broker: {repeat!r}"
    )


def test_the_first_reading_still_happens():
    """The collapsed summary shows the colour; that is the whole point of the dot."""
    trigger = _macro_trigger()
    assert trigger.strip().startswith("load"), f"the dot never loads: {trigger!r}"


def test_every_card_with_a_dot_goes_through_the_macro():
    """If a card ever grew its own hx-get for a status, this would quietly stop protecting it."""
    html = SETTINGS.read_text(encoding="utf-8")
    outside = [line for line in html.splitlines()
               if "/status" in line and "hx-get" in line and "status_url" not in line]
    assert not outside, f"a status dot outside the macro: {outside}"
