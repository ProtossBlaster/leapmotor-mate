"""A charge that cost nothing must read 0,00 € — not the dash that means "no price known".

#218 settled it for the pricing engine: a zero is a price. The cost cell never got the message. It
tests the cost for truth — `{% if charge.cost %}` — and 0.0 is falsy in Jinja exactly as in Python,
so a genuinely free charge renders identical to one nobody has priced: grey, and a dash.

Three real ways to get here, and the third arrived today:
  · the #120 "free" tick on a home charge (solar, or any charge that cost nothing),
  · a time-of-use band priced at 0 the owner typed on purpose,
  · #272's solar figure equal to the measured energy — a charge entirely off the roof.

The distinction is `is none` versus falsy, and it has to be made in all three places on that line:
the colour, the figure, and the €/kWh under it.
"""
import pathlib
import re

import jinja2
import pytest

CELL = pathlib.Path("web/templates/partials/charge_cost_cell.html").read_text()


def _render(cost):
    env = jinja2.Environment(loader=jinja2.FileSystemLoader("web/templates"))
    env.filters["money"] = lambda v: f"{float(v):.2f} €"
    env.filters["dec"] = lambda v, n=1: f"{float(v):.{n}f}"
    charge = {"id": 1, "cost": cost, "ac_energy_kwh": 20.0, "energy_added_kwh": 18.0,
              "gross_kwh": None, "location_type": "HOME"}
    return env.get_template("partials/charge_cost_cell.html").render(charge=charge)


def test_a_free_charge_shows_a_zero():
    out = _render(0.0)
    assert "0.00" in out, f"a free charge must show its zero, got: {out.strip()[:200]}"
    assert "—" not in out, "the dash means 'no price known' — that is a different thing"


def test_an_unpriced_charge_still_shows_the_dash():
    """The dash must keep meaning what it means: nobody has priced this one."""
    out = _render(None)
    assert "—" in out and "0.00" not in out


def test_a_priced_charge_is_unchanged():
    out = _render(4.8)
    assert "4.80" in out and "—" not in out


@pytest.mark.parametrize("frag", ["class=", "money", "/kWh"])
def test_every_test_on_that_line_distinguishes_none_from_zero(frag):
    """Colour, figure and €/kWh each ask the question separately — one of them left falsy is a line
    that half-believes the charge was free."""
    for line in CELL.splitlines():
        if frag not in line or "charge.cost" not in line:
            continue
        assert not re.search(r"\{%\s*if charge\.cost\s*%\}", line) and \
               not re.search(r"if charge\.cost and\b", line), \
            f"still truth-testing the cost:\n    {line.strip()}"
