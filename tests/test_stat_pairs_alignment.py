"""Values in the trip-detail stat grid line up across a row — GitHub #199 follow-up.

The cells are 130px wide, so whether a label wraps depends on the language: "Energia consumata",
"Verbrauchte Energie" and "Verbruikte energie" take two lines while "Consumo medio" beside them
takes one. The value simply follows its label, so one value sat 18px below its neighbour while the
rows above and below lined up perfectly — measured on a running instance before the fix.

`.stat-pairs .stat-label { min-height: 3em }` reserves the two-line height for every label in that
grid, which is what puts the values back on a shared baseline. 3em is two lines at this font-size
and line-height, so it tracks the type instead of a hard-coded pixel count.

Both halves are pinned because either one alone is inert: the class without the rule, or the rule
without the class, and the values drift apart again with nothing failing.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
TPLS = ROOT / "web" / "templates"


def test_the_grid_carries_the_class():
    html = (TPLS / "trip_detail.html").read_text(encoding="utf-8")
    assert re.search(r'class="grid grid-cols-2 gap-4 stat-pairs"', html), (
        "the trip-detail stat grid must keep the stat-pairs class, or its labels stop reserving "
        "two lines and the values drift out of line by however much a label wraps"
    )


def test_the_rule_exists_and_is_scoped():
    css = (TPLS / "base.html").read_text(encoding="utf-8")
    m = re.search(r"\.stat-pairs\s+\.stat-label\s*\{[^}]*min-height:\s*([^;}]+)", css)
    assert m, ".stat-pairs .stat-label { min-height } is missing from base.html"
    assert "em" in m.group(1), (
        "the reserved height must be in em so it follows the label's font-size and line-height; "
        "a pixel value silently stops covering two lines if the type ever changes"
    )
    assert not re.search(r"^\s*\.stat-label\s*\{[^}]*min-height", css, re.M), (
        ".stat-label is used all over the app — reserving two lines globally would add slack "
        "everywhere nothing wraps. The rule must stay scoped to .stat-pairs."
    )
