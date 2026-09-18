"""The values of a row in the trip summary line up — GitHub #199 follow-up, carried into the boxes.

The label is what varies: whether it wraps depends on the language and on the width, and a value
that simply follows its label ends up a line below its neighbour when only one of the two labels
wraps — measured at 18px on a running instance for #199, in the stat grid this card used to be.

Until 18/09/2026 that grid reserved two lines for every label (`.stat-pairs .stat-label { min-height:
3em }`). The grid became boxes (beta D #31), and the boxes of a row are the same height, so the
values now sit at the FOOT of their boxes: level whatever the labels do, and no empty line reserved
when nothing wraps. The old rule had no user left and went with it.

Both halves are pinned — the box being a flex column, and the value pushed to its foot — because
either one alone is inert.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
TPLS = ROOT / "web" / "templates"


def test_the_values_sit_at_the_foot_of_their_boxes():
    html = (TPLS / "trip_detail.html").read_text(encoding="utf-8")
    for key in ("distance", "duration"):
        m = re.search(r'<div class="([^"]*)">\s*<div class="stat-label">\{\{ t\(\'' + key
                      + r'\'\) \}\}</div>\s*<div class="([^"]*)">', html)
        assert m, f"the {key} box is not a label followed by its value"
        box, value = m.group(1).split(), m.group(2).split()
        assert "flex" in box and "flex-col" in box, f"the {key} box is not a column"
        assert "mt-auto" in value, f"the {key} value follows its label instead of sitting at the foot"


def test_the_retired_rule_left_with_its_last_user():
    """A rule nothing uses is a trap: the next grid to borrow the class inherits a 3em label."""
    for p in TPLS.rglob("*.html"):
        assert "stat-pairs" not in p.read_text(encoding="utf-8"), f"{p.name} still refers to stat-pairs"
