"""A page that draws a chart must load Chart.js BEFORE the code that calls it (beta #38, @michapr).

His "Consumption vs outside temperature" card came out blank in v3.14.19: the caption underneath it
printed the trend line — so the server had the points and the fit — while the canvas stayed empty and
the console said nothing. The card had been added at the TOP of statistics.html, and the page loads
`chart.umd.min.js` further down, next to the monthly charts. When the card's inline script ran, the
library did not exist yet, and its own guard (`typeof Chart === 'undefined'`) returned in silence.
The four charts below the include drew normally, which is exactly what he reported: *this* one is empty.

The suite could not see it: every test here renders the template or reads the context, and both were
correct. What was wrong was the ORDER in which the browser executes what we render — so this test reads
the templates as text and checks that order, the one thing an assertion on the output cannot catch."""
import pathlib
import re

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "web" / "templates"
LIB = "chart.umd.min.js"


def _pages_that_draw():
    """Every template that constructs a Chart.js chart, with its text."""
    return [(p, p.read_text(encoding="utf-8"))
            for p in sorted(TEMPLATES.rglob("*.html"))
            if "new Chart(" in p.read_text(encoding="utf-8")]


def test_at_least_one_page_draws_charts():
    # A guard on the guard: if the pages are ever renamed or the library swapped, the check below
    # would pass by finding nothing to check.
    assert _pages_that_draw(), "no template calls new Chart() — has the library changed?"


def test_every_chart_page_loads_the_library_before_using_it():
    for path, text in _pages_that_draw():
        include = text.find(LIB)
        assert include != -1, f"{path.name} calls new Chart() but never loads {LIB}"
        first_use = min(m.start() for m in re.finditer(r"new Chart\(", text))
        assert include < first_use, (
            f"{path.name}: {LIB} is loaded at offset {include}, after the first new Chart() at "
            f"{first_use} — that chart draws nothing and reports no error (beta #38)")
