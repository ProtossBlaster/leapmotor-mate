"""The BetaTester banner can be closed, and the build stays identifiable afterwards (beta #13).

@ebagnoli reads Mate on a phone: the purple banner stood between him and the menu. Lowering its
z-index was one half (see test_notice_bars_below_the_menu); letting him close it is the other. But a
tester runs two installs side by side, so once the banner is gone something must still say WHICH
build this is — hence a small red BETA badge beside the version, in both sidebars, permanently.

⚠️ The subtle one is `test_a_dismiss_route_never_answers_204`. htmx performs NO swap on a 204 by
design, so `hx-swap="delete"` never ran and the strip stayed on screen until the next page load.
Measured in a browser: the POST went out, was answered 204, and the bar did not move. The desktop
notice had carried that defect since it shipped, with a comment claiming the opposite.
"""
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASE = (ROOT / "web" / "templates" / "base.html").read_text()
MAIN = (ROOT / "web" / "main.py").read_text()


# ── the badge ────────────────────────────────────────────────────────────────

def test_the_beta_badge_is_a_macro_not_two_copies():
    """base.html opens with a note about a badge that used to be written twice and drifted. The
    DEMO badge beside this one is still duplicated; this one must not repeat that."""
    assert BASE.count("{%- macro beta_badge()") == 1
    assert BASE.count("{{ beta_badge() }}") == 2, "the badge must reach BOTH sidebars"


def test_the_badge_shows_only_on_a_betatester_build():
    gated = re.findall(r"\{% if research %\}\{\{ beta_badge\(\) \}\}\{% endif %\}", BASE)
    assert len(gated) == 2, "both call sites must sit behind the research gate"


def test_the_badge_is_not_the_same_colour_as_the_demo_one():
    """Red vs amber. Two badges of the same colour in the same corner are one badge with two
    meanings — and a tester comparing installs reads that corner at a glance."""
    macro = BASE.split("{%- macro beta_badge() -%}", 1)[1].split("{%- endmacro -%}", 1)[0]
    assert "bg-red-600" in macro
    assert "bg-amber" not in macro


def test_the_badge_survives_the_banner_being_closed():
    """The identification must NOT sit inside the dismissible block, or closing the warning would
    also remove the only sign of which build this is."""
    banner_block = BASE.split("{% if research and not beta_notice_dismissed %}", 1)[1] \
                       .split("{% endif %}", 1)[0]
    assert "beta_badge" not in banner_block


# ── closing it ───────────────────────────────────────────────────────────────

def test_the_banner_is_gated_on_the_dismissed_flag():
    assert "{% if research and not beta_notice_dismissed %}" in BASE


def test_the_flag_reaches_every_page():
    """The banner is in base.html, so the context that hides it has to be in the shared context —
    not added per-route, where the first forgotten route brings the banner back."""
    assert 'beta_notice_dismissed": db_reader.get_setting("beta_notice_dismissed"' in MAIN


def test_the_close_button_posts_and_deletes_the_strip():
    block = BASE.split('<div id="beta-bar"', 1)[1].split("</div>", 1)[0]
    assert 'hx-post="api/settings/dismiss-beta-notice"' in block
    assert 'hx-target="#beta-bar"' in block and 'hx-swap="delete"' in block


def test_the_button_uses_a_generically_named_string():
    """It first reused `desktop_open_notice_ok` — right words, wrong name: make that string specific
    to the desktop notice one day and this button inherits a sentence about something else."""
    block = BASE.split('<div id="beta-bar"', 1)[1].split("</div>", 1)[0]
    assert "t('btn_close')" in block
    assert "desktop_open_notice_ok" not in block


@pytest.mark.parametrize("route", ["dismiss_desktop_notice", "dismiss_beta_notice"])
def test_a_dismiss_route_never_answers_204(route):
    """The one that was actually broken. htmx does not swap on 204, so hx-swap="delete" is dead and
    the strip stays until the page is loaded again. Both of these answered 204."""
    body = MAIN.split(f"async def {route}(", 1)[1].split("\n@app.", 1)[0]
    assert "status_code=204" not in body, f"{route} answers 204 — htmx will not run the swap"
    assert "status_code=200" in body
