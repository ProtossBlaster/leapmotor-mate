"""Units stay small and grey, never the size of the number — GitHub #199.

@adoewa reported "the large green kWh/100km" on the Statistics page. `units.efficiency` returns the
value and the unit as ONE string ("18.5 kWh/100km"), so `{{ x | eff }}` inside a
`stat-value text-green-400` styled the unit like the number: 24px, green. The Avg-consumption tile
right beside it already split the two, which is what made the mismatch obvious.

The trap is the filter, not one template: anything piping a value through `eff` gets the unit back
inside its own span and inherits whatever the number is wearing. Hence the sweep below across every
template, rather than a hand-checked list of tiles.

Matched per LINE, deliberately. The first draft of this file searched from `{% if totals.X %}` to
the next `{% else %}`, which works for the best-efficiency tile and silently truncates the average
one — its colour banding carries an `{% else %}` of its own, so the fragment ended before the unit
span and the test failed against correct markup.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
TPLS = ROOT / "web" / "templates"
UNIT_STYLE = "text-base font-normal text-slate-400"


def _line_rendering(name, tpl="statistics.html"):
    """The single line that prints this figure's number."""
    for line in (TPLS / tpl).read_text(encoding="utf-8").splitlines():
        if f"eff_val(totals.{name})" in line:
            return line
    raise AssertionError(f"no line renders eff_val(totals.{name}) in {tpl} — has the tile moved?")


def test_the_best_efficiency_tile_splits_number_from_unit():
    """The regression: value large and coloured, unit one step down and grey, on the same line."""
    line = _line_rendering("best_efficiency")
    assert "eff_unit()" in line, "the unit must be rendered separately so it can be styled separately"
    assert UNIT_STYLE in line, "the unit must be smaller, unbolded and grey"


def test_the_two_efficiency_tiles_are_styled_the_same_way():
    """They sit side by side; fixing only one leaves exactly the mismatch that was reported."""
    for name in ("avg_efficiency", "best_efficiency"):
        assert UNIT_STYLE in _line_rendering(name), f"the {name} tile styles its unit differently"


def test_no_stat_value_prints_a_bare_eff_filter():
    """`| eff` inside a stat-value is the shape that caused this — caught anywhere, not just here."""
    offenders = []
    for tpl in sorted(TPLS.rglob("*.html")):
        for n, line in enumerate(tpl.read_text(encoding="utf-8").splitlines(), 1):
            if "stat-value" in line and re.search(r"\|\s*eff\s*\}\}", line):
                offenders.append(f"{tpl.relative_to(TPLS)}:{n}")
    assert not offenders, (
        "a stat-value pipes a value through `eff`, which returns number+unit as one string, so the "
        "unit inherits the number's size and colour (#199): " + ", ".join(offenders)
    )
