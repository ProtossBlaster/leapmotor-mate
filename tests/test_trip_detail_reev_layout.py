"""On a range-extender the trip's fuel sits in the header, and the block below stops repeating it.

@michapr, beta #27, 06/08/26 — he opens by saying the beta's defects are behind us and it is time
for the presentation: *«Since REEV users expect to consume gasoline, this specific notice (or
warning) isn't strictly necessary; we could integrate the data into the existing upper field.»*

Looking at the rendered page made his point sharper than his words. The header already showed
**12.4** as AVG CONSUMPTION and **12.4 kWh** as ENERGY USED — and the amber "Dual energy" box
directly underneath opened with **12.4 kWh** again. The same number three times in one column, the
third framed as a warning; what the box actually added was the fuel.

So: the litres move up beside the kWh (which is what a plain electric car already does), the
electric figure stops being repeated below, and the box loses the amber border and the title — it
was never a warning, it is the detail.

🔑 **Nothing is dropped.** Four things live only in that block and have nowhere else to go: the SoC
and tank start→end, the split between kWh paid at the plug and kWh the generator supplied, the
kilometres the generator drove, and the note explaining why the electric figure is right for the
cost and wrong for the consumption. The test below holds each of them.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = (ROOT / "web" / "templates" / "trip_detail.html").read_text()


def _block():
    """The range-extender detail block: from its `is_reev and research and engine_ran` gate down to
    the line that closes it.

    ⚠️ The first version cut at the first `{% endif %}` plus a few hundred characters, which used to
    reach the end and — once the block got shorter — stopped before `paid_kwh`, reporting a figure
    as DROPPED that was three lines further down. A test that fails on where it decided to stop
    reading is worse than no test."""
    start = HTML.find("{% if is_reev and research and trip.engine_ran %}")
    assert start > 0, "the range-extender block is gone entirely"
    end = HTML.find("reev_elec_source_note", start)
    assert end > start, "the block lost its closing note"
    return HTML[start:HTML.find("\n", end)]


def _header():
    return HTML.split("{% if is_reev and research and trip.engine_ran %}", 1)[0]


# ── the litres move up ────────────────────────────────────────────────────────

def test_the_header_carries_the_litres():
    """His actual request: the fuel next to the kWh, in the tile that already exists."""
    head = _header()
    assert "fuel_used_l" in head, "the header still says nothing about the petrol"
    assert "fuel_l_100km" in head, "…and nothing about the L/100km beside it"


def test_the_header_still_shows_the_electric_figure():
    """Moving the fuel up must not push the kWh out — a REEV trip has both."""
    assert "trip.ec_kwh" in _header()


# ── and the block stops repeating them ────────────────────────────────────────

def test_the_block_no_longer_repeats_the_electric_kwh():
    """🔴 The duplication nobody had noticed: 12.4 in AVG CONSUMPTION, 12.4 kWh in ENERGY USED, and
    12.4 kWh again at the top of the block."""
    body = _block()
    assert "reev_elec_kwh_100km" not in body, "the block still repeats the kWh/100km"
    assert not re.search(r"trip\.reev_elec_kwh\s*\|", body), "the block still repeats the kWh"


def test_it_is_no_longer_dressed_as_a_warning():
    """Amber border and a title that reads like an alert, for something a range-extender owner
    expects to happen every day. It is the detail, so it looks like the detail."""
    body = _block()
    assert "f59e0b40" not in body, "the amber warning border is still there"
    assert "reev_dual_energy" not in body, "the 'Dual energy' title is still there"


# ── nothing was lost ──────────────────────────────────────────────────────────

def test_the_four_things_that_live_nowhere_else_survive():
    """🔑 The reason this was not done blind. Each of these appears in that block and in no other
    place on the page; folding the block into the header without a home for them loses them."""
    body = _block()
    for needle, what in (
            ("trip.start_soc", "the SoC start→end"),
            ("trip.fuel_start_pct", "the tank start→end"),
            ("trip.paid_kwh", "the kWh actually paid for at the plug"),
            ("trip.engine_km", "the kilometres the generator drove"),
            ("reev_elec_source_note", "the note on what getEC measures")):
        assert needle in body, f"{what} was dropped"


def test_the_fuel_money_is_still_shown_somewhere():
    """€ and €/L for the petrol: they were in the block and must not vanish in the move."""
    assert "fuel_price_per_l" in HTML and "trip.fuel_cost" in HTML


def test_a_plain_electric_car_is_untouched():
    """The whole change sits inside the energy tile's `{% if is_reev %}` branch; a car with no tank
    must not gain a fuel line.

    ⚠️ Checked by slicing that branch out, not by counting braces — the first version compared
    `{% if is_reev` against `{% endif %}` totals across the whole header and failed on arithmetic
    that meant nothing."""
    head = _header()
    i = head.find("{% if is_reev %}")
    assert i > 0, "the energy tile lost its range-extender branch"
    branch = head[i:head.find("{% elif", i)]
    assert "fuel_used_l" in branch, "the litres are printed outside the range-extender branch"
