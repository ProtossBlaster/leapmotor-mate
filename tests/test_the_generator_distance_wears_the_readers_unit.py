"""The generator distance printed "km" on an install reading in miles (30/08 audit).

Three places show that figure. Statistics already ran it through the `dist` filter, which converts
the number AND writes the unit; the trip detail and the REEV page printed `| nice` followed by a
hard-coded "km" — so on an imperial install the whole page said miles and those two lines said km,
over a number that had not been converted either. Wrong unit AND wrong number, on the same line.

The audit named one of them. There were two: the copy is what makes this class of defect survive a
fix, so both are asserted here.
"""
import pathlib
import re

import pytest

PAGES = ("web/templates/trip_detail.html", "web/templates/reev.html",
         "web/templates/statistics.html")


@pytest.mark.parametrize("page", PAGES)
def test_no_page_hard_codes_km_after_the_generator_distance(page):
    html = pathlib.Path(page).read_text()
    for line in html.splitlines():
        if "engine_km" not in line:
            continue
        assert not re.search(r"engine_km[^}]*\}\}\s*km", line), \
            f"{page}: the reader's unit is not always km — use the dist filter:\n    {line.strip()}"


@pytest.mark.parametrize("page", PAGES)
def test_every_generator_distance_goes_through_the_unit_filter(page):
    """Not merely "no literal km": the number itself has to be converted. `dist` does both."""
    html = pathlib.Path(page).read_text()
    # Only lines that PRINT the value. `t('reev_engine_km_floor')` carries the same substring and is
    # a translation key, not a distance — matching it would fail the test on correct markup.
    printer = re.compile(r"\{\{[^}]*(?<!')\.engine_km[^}]*\}\}")
    for line in html.splitlines():
        if not printer.search(line):
            continue
        assert "| dist(" in line or "dist_val(" in line, \
            f"{page}: this distance is printed unconverted:\n    {line.strip()}"
