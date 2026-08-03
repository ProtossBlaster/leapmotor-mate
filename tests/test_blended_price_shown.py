"""The blended €/kWh has to be VISIBLE — GitHub #200.

The rate behind a trip's cost was computed from the day per-trip cost shipped (a80da79, #18):
`get_trip_detail` sets `cost_per_kwh` on the line beside `cost`, and no template ever printed it.
Nothing failed and nothing looked wrong — the € was right — so the only way to check where the
figure came from was to redo the arithmetic by hand, which is exactly what @riri19 asked about.

Hence tests that assert the *rendering*, not the arithmetic: the maths already has
test_trip_cost_wac.py, and the maths was never the part that went missing.

The battery card is rendered from TWO routes — the Overview page and `/api/status-card`, which
swaps it back in on the live refresh — so a value passed by only one of them would show up and
then disappear a few seconds later. That is pinned here too.
"""
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
LANGS = ("en", "it", "fr", "de", "pl", "pt-PT", "nl")


def _read(rel):
    return (WEB / rel).read_text(encoding="utf-8")


def test_trip_detail_prints_the_rate_next_to_the_cost():
    """The regression this file exists for: the value is in the context and on screen."""
    tpl = _read("templates/trip_detail.html")
    assert "trip.cost_per_kwh" in tpl, "trip detail no longer prints the €/kWh behind the cost"
    assert "price3" in tpl, "the rate must use the 3-decimal price filter, like every other €/kWh"


def test_status_card_prints_the_battery_price():
    tpl = _read("templates/partials/status_card.html")
    assert "battery_price" in tpl, "the Overview battery card no longer shows the blended price"


# ── A rate of zero is a rate (#218, the free-charging end of it) ─────────────────
# An owner who charges only from his own roof has a blend of exactly 0.0. Jinja treats that as
# false, so both surfaces went blank — the trip cost printed "—" ("we don't know") to the one
# person who knows for certain, and the battery card said nothing at all. Truthiness is the bug.

def test_the_trip_rate_is_printed_when_it_is_zero():
    tpl = _read("templates/trip_detail.html")
    assert "{% if trip.cost_per_kwh is not none %}" in tpl, (
        "0.000 €/kWh is a real rate — a plain `if` hides it"
    )
    assert "{% if trip.cost_per_kwh %}" not in tpl


def test_the_battery_price_is_printed_when_it_is_zero():
    tpl = _read("templates/partials/status_card.html")
    assert tpl.count("battery_price is not none") == 2, (
        "both the block AND the spacing class must test for None, or the card mis-spaces"
    )
    assert "{% if battery_price %}" not in tpl


def test_both_routes_that_render_the_battery_card_pass_the_price():
    """A card fed by two routes: miss one and the number vanishes on the first auto-refresh.

    Counts the occurrences rather than merely finding one, so removing it from a single route
    fails here instead of silently degrading to a value that flickers away while you watch.
    """
    main = _read("main.py")
    assert main.count("battery_price=db_reader.current_blended_price()") == 2, (
        "battery_price must be passed by BOTH the overview route and /api/status-card"
    )


@pytest.mark.parametrize("lang", LANGS)
def test_every_language_has_the_label_and_its_explanation(lang):
    import json
    t = json.loads(_read(f"locales/{lang}.json"))["translations"]
    for key in ("blended_price", "blended_price_help"):
        assert key in t and t[key].strip(), f"{lang}.json is missing {key}"


def test_the_help_text_says_what_moves_the_number():
    """The explanation earns its place only if it carries the non-obvious half.

    That charging moves the rate is guessable; that DRIVING does not is the part nobody derives
    on their own, and it is the whole reason a trip taken after 300 km costs the same per kWh as
    one taken right after the charge.
    """
    import json
    en = json.loads(_read("locales/en.json"))["translations"]["blended_price_help"]
    assert "driving" in en.lower(), "the help must state that driving does not move the rate"
    assert "weighted average" in en.lower(), "the help must name the model it uses"


def test_the_rate_is_hidden_rather_than_dashed_before_the_first_priced_charge():
    """`blended_price_at` returns None until a charge has a cost, and None must draw nothing.

    A '—' would read as 'this trip was free' sitting under a cost; absent reads as 'not known yet',
    which is what it is.

    ⚠️ This used to anchor on the literal `{% if battery_price %}` — a TRUTHINESS guard, which is
    the defect it now guards against: it also hid a blend of exactly 0.0, the true rate for an
    owner who charges only from his own roof. The rule was always "None draws nothing", never
    "falsy draws nothing", so the anchor is the None test.
    """
    tpl = _read("templates/partials/status_card.html")
    block = re.search(r"\{%\s*if battery_price is not none\s*%\}(.*?)\{%\s*endif\s*%\}", tpl, re.S)
    assert block, "the battery price must be guarded on None, not on truthiness"
    assert "—" not in block.group(1), "no em-dash placeholder inside the guarded block"
