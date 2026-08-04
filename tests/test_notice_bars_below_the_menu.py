"""The notice bars must never outrank the navigation (@ebagnoli, beta #13).

On a phone the sidebar is an off-canvas drawer. Four sticky strips live at the top of every page —
the BetaTester banner, the demo bar, the auth warning and the MateDesktop notice — and each carried
a z-index ABOVE the drawer, so opening the menu painted the strip over it and swallowed the top
entries: *"ci sono voci del menù alle quali non puoi accedere"*.

A tie is a bug too: at equal z-index the later element in the document wins, and every one of these
strips comes after the sidebar. So the test asks for strictly less, not less-or-equal.
"""
import pathlib
import re

import pytest

BASE = pathlib.Path(__file__).resolve().parent.parent / "web" / "templates" / "base.html"
SRC = BASE.read_text()

DRAWER = 1003          # <aside id="sidebar">      — the mobile menu itself
OVERLAY = 1002         # <div id="sidebar-overlay"> — the dimmer behind it


def _z_of_id(html: str, el_id: str) -> int:
    """The z-index of the element carrying this id, from its inline style."""
    m = re.search(rf'id="{el_id}"[^>]*?z-index:(\d+)', html)
    assert m, f"{el_id} not found, or it no longer carries an inline z-index"
    return int(m.group(1))


def _z_of_beta_banner(html: str) -> int:
    """The BetaTester banner has no id — it is the sticky strip in the `{% if research %}` block."""
    block = html.split("{% if research %}", 1)[1]
    m = re.search(r"position:sticky;top:0;z-index:(\d+)", block)
    assert m, "the BetaTester banner is no longer a sticky strip with a z-index"
    return int(m.group(1))


def test_the_drawer_and_its_overlay_still_sit_where_this_test_thinks():
    """Anchors the two numbers the rest of the file compares against — if the sidebar is restacked,
    this fails first and says so, instead of the others passing for the wrong reason."""
    assert re.search(rf'id="sidebar"[^>]*z-\[{DRAWER}\]', SRC), "the drawer's z-index moved"
    assert re.search(rf'id="sidebar-overlay"[^>]*z-\[{OVERLAY}\]', SRC), "the overlay's z-index moved"


@pytest.mark.parametrize("bar", ["auth-bar", "desktop-bar", "demo-bar"])
def test_a_notice_bar_stays_under_the_open_menu(bar):
    assert _z_of_id(SRC, bar) < OVERLAY, f"{bar} would cover the open mobile menu"


def test_the_betatester_banner_stays_under_the_open_menu():
    """The one that was reported: purple, on every page, and on a phone it hid the menu."""
    assert _z_of_beta_banner(SRC) < OVERLAY


def test_no_notice_bar_ties_with_the_drawer():
    """Equal z-index is not safe: the strips come later in the document, so a tie still paints them
    on top. Kept as its own test because a future edit is far more likely to land on 1003 than above
    it — the drawer's own value is the number someone would copy."""
    zs = [_z_of_id(SRC, b) for b in ("auth-bar", "desktop-bar", "demo-bar")] + [_z_of_beta_banner(SRC)]
    assert all(z != DRAWER for z in zs), "a notice bar ties with the drawer and wins on DOM order"
