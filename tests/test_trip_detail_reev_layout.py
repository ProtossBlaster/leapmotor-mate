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


def _fuel_section():
    """The ⛽ section of the card — area 3 of the three @gm27271 asked for (beta #31).

    Sliced from its own gate down to the range-extender block that follows it, and asserted
    non-empty: a slice that silently came back blank would make every count below read zero and
    pass for the wrong reason."""
    start = HTML.find("{% if is_reev and trip.fuel_used_l %}")
    assert start > 0, "the fuel section is gone entirely"
    end = HTML.find("{% if is_reev and research and trip.engine_ran %}", start)
    assert end > start, "the fuel section has swallowed the block below it"
    return HTML[start:end]


# ── the litres move up ────────────────────────────────────────────────────────

def test_the_header_carries_the_litres():
    """His actual request: the fuel next to the kWh, in the tile that already exists."""
    head = _header()
    assert "fuel_used_l" in head, "the header still says nothing about the petrol"
    assert "fuel_l_100km" in head, "…and nothing about the L/100km beside it"


def test_the_header_still_shows_the_electric_figure():
    """Moving the fuel up must not push the kWh out — a REEV trip has both."""
    assert "trip.ec_kwh" in _header()


def test_neither_fuel_figure_can_be_torn_from_its_unit():
    """🔴 Shipped in v3.8.5 and only spotted on 07/08/26 by opening the page.

    The tile was 130px and "⛽ 4.6 L · 6.8 L/100km" needs 140 at 13px, so it wrapped — and it wrapped
    INSIDE the second figure, leaving `L/100km` alone on the next line under a bare `6.8`. The same
    orphaned-unit look as #199, on the line beta #27 had just asked for.

    Measured in the browser: shrinking the type is not a fix — a realistic big trip
    ("⛽ 12.4 L · 15.7 L/100km") still needs 132px at 11px. The halves are 51 and 86px on their own,
    so each is made unbreakable and the wrap falls BETWEEN them: two whole figures, always.

    📍 The two figures now sit in the FUEL section (beta #31, three areas) instead of stacked under
    the kWh, which is why this reads the section rather than the old tile. The section is full width
    so neither wraps at 375px today — but the guard stays: a longer language will want the wrap to
    fall between the figures, not inside one."""
    section = _fuel_section()
    # The two FIGURES only — the €/L line further down is its own single figure and may set its own
    # leading. Slicing wider made this fail on that line, which is not what it is asking about.
    figures = section[section.index("t('fuel_used')"):section.index("trip.engine_km")]
    assert figures.count("whitespace-nowrap") == 2, \
        "the litres and the L/100km are not both unbreakable — one can still lose its unit"
    assert "leading-none" not in figures, \
        "leading-none makes the two lines touch once it wraps"


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
    """🔑 The reason this was not done blind. Each of these has exactly one home on the page;
    reorganising the card without a home for them loses them.

    📍 The generator's distance moved OUT of this block in the three-area rearrangement: it is a
    labelled figure in the ⛽ section now, beside the litres that produced it, which is one step
    further along the road @gm27271 asked for in beta #31. So it is checked there, not here — and
    checked it is, because dropping it is exactly what this test exists to catch."""
    body = _block()
    for needle, what in (
            ("trip.start_soc", "the SoC start→end"),
            ("trip.fuel_start_pct", "the tank start→end"),
            ("trip.paid_kwh", "the kWh actually paid for at the plug"),
            ("reev_elec_source_note", "the note on what getEC measures")):
        assert needle in body, f"{what} was dropped"
    assert "trip.engine_km" in _fuel_section(), \
        "the kilometres the generator drove were dropped on the way to the fuel section"
    assert "reev_engine_km_floor" in _fuel_section(), \
        "the figure moved without the line that says it is a floor"


def test_the_fuel_money_is_still_shown_somewhere():
    """€ and €/L for the petrol: they were in the block and must not vanish in the move."""
    assert "fuel_price_per_l" in HTML and "trip.fuel_cost" in HTML


def test_a_plain_electric_car_is_untouched():
    """A car with no tank must not gain a fuel line.

    📍 The litres used to live inside the energy tile's `{% if is_reev %}` branch; they are their own
    section now, so what has to hold is that the SECTION is gated — and gated on the tank having
    been used, so a pure-electric drive on a range extender shows no empty section either.

    ⚠️ Checked by slicing the gate out, not by counting braces — the first version compared
    `{% if is_reev` against `{% endif %}` totals across the whole header and failed on arithmetic
    that meant nothing."""
    head = _header()
    assert "{% if is_reev and trip.fuel_used_l %}" in head, \
        "the fuel section is not gated on a range extender that actually burned something"
    # …and nothing prints litres outside it: the energy tile must be clean of them.
    tile = head[head.index("{% if is_reev %}"):head.index("{% elif", head.index("{% if is_reev %}"))]
    assert "fuel_used_l" not in tile, \
        "the litres are back inside the energy tile, which is the stacking beta #31 was about"
