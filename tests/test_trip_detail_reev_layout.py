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
    """The ⛽ box of the trip summary — area 3 of the three @gm27271 asked for (beta #31), a box
    beside the ⚡ one since 18/09/2026 (beta D #31).

    Sliced from its heading down to the generator's line that follows it, and asserted non-empty:
    a slice that silently came back blank would make every count below read zero and pass for the
    wrong reason."""
    start = HTML.find("⛽ {{ t('trip_area_fuel') }}")
    assert start > 0, "the fuel box is gone entirely"
    end = HTML.find("{# The generator's distance", start)
    assert end > start, "the fuel box has swallowed the line below it"
    return HTML[start:end]


def _engine_line():
    """The generator's distance: its own guard down to the `{% endif %}` that closes it."""
    start = HTML.find("{% if is_reev and trip.engine_km %}")
    assert start > 0, "the generator's distance lost its guard"
    return HTML[start:HTML.index("{% endif %}", start)]


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
    fall between the figures, not inside one.

    📍 18/09/2026: in the ⛽ box (beta D #31) each figure is a line of its own that cannot break
    (`truncate` carries the nowrap), measured to fit the box at a 1024px window."""
    section = _fuel_section()
    # The two FIGURES only — the €/L line further down is its own single figure and may set its own
    # leading. Slicing wider made this fail on that line, which is not what it is asking about.
    lines = [ln for ln in section.splitlines()
             if "trip.fuel_used_l|nice" in ln or "trip.fuel_l_100km|nice" in ln]
    assert len(lines) == 2, "the litres and the L/100km are no longer two figures of the fuel box"
    for ln in lines:
        assert "truncate" in ln or "whitespace-nowrap" in ln, \
            "a fuel figure can break — it can lose its unit: " + ln.strip()[:80]
    assert "leading-none" not in "\n".join(lines), \
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

    📍 The generator's distance moved OUT of this block in the three-area rearrangement, and since
    18/09/2026 (beta D #31) it is a full-width line of the boxed summary, right under the ⛽ box —
    no box has room for it at 1024px. So it is checked there, not here — and checked it is,
    because dropping it is exactly what this test exists to catch."""
    body = _block()
    for needle, what in (
            ("trip.start_soc", "the SoC start→end"),
            ("trip.fuel_start_pct", "the tank start→end"),
            ("trip.paid_kwh", "the kWh actually paid for at the plug"),
            ("reev_elec_source_note", "the note on what getEC measures")):
        assert needle in body, f"{what} was dropped"
    assert "trip.engine_km" in _engine_line(), \
        "the kilometres the generator drove were dropped on the way to the summary"
    assert "reev_engine_km_floor" in _engine_line(), \
        "the figure moved without the line that says it is a floor"


def test_the_fuel_money_is_still_shown_somewhere():
    """€ and €/L for the petrol: they were in the block and must not vanish in the move."""
    assert "fuel_price_per_l" in HTML and "trip.fuel_cost" in HTML


def test_a_car_with_no_tank_gets_no_fuel():
    """A car with no tank must not gain a fuel line.

    📍 18/09/2026 (beta D #31, Silvio): the plain electric car gets the SAME boxed summary as a range
    extender — that change to its card is deliberate — minus the fuel. So what has to hold is that
    the ⛽ box is gated on the car having a tank, and that the ⚡ box carries no litres. On a range
    extender the ⛽ box is always there, with a dash when nothing burned: @michapr's layout, approved
    with it — so the gate no longer asks whether the tank was used.

    ⚠️ Checked by slicing the gate out, not by counting braces — the first version compared
    `{% if is_reev` against `{% endif %}` totals across the whole header and failed on arithmetic
    that meant nothing."""
    head = _header()
    gate = head.index("{% if is_reev %}\n        {# ⛽")
    assert head.index("⛽ {{ t('trip_area_fuel') }}") > gate, \
        "the fuel box is not behind the tank's gate"
    # …and nothing prints litres outside it: the ⚡ box must be clean of them.
    elec = head[head.index("⚡ {{ t('trip_area_electric') }}"):gate]
    assert "fuel_used_l" not in elec, \
        "the litres are back inside the electricity, which is the stacking beta #31 was about"
