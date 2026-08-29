"""The kilometres the generator drove are a figure, not a footnote — @gm27271, beta #31.

He asked for a layout. He got three answers about measurement, all of them correct and none of them
about what he asked, and after two weeks he closed the thread with: *«OK, whatever. Then let's
display this distance in crappy comment after loads of text like today.»*

He was describing the page accurately. Measured in the browser on a generator trip:

    the note before the number   217 characters, 41 words
    the number's type            10px, rgb(100,116,139)
    the same treatment as        "v3.14.23", the build number in the sidebar corner

One `·` at the end of a paragraph, in the smallest type on the card, for the one figure a
range-extender owner opens this page to read. Nothing about the number changes here; only where it
sits.

🔑 **And promoting it means carrying its limit with it.** There is no "generator on" signal in the
cloud — all 89 signals were checked, and the best of them separates at 61% once the car is already
moving. The kilometres are counted from the samples where the odometer rises *and* the fuel falls,
and that count is SHORT: 54.0 against the 60.2 km @pdifeo's own dashboard showed. A number in 10px
grey claims nothing; the same number on its own line claims confidence, so the line has to say it
is a floor. Otherwise this trades a layout defect for the two-numbers-one-word defect.

What the figure LOOKS like once it is out of the note — its size and its colour — is decided
by inheritance and can only be read in a browser: that half is in
test_the_generator_distance_is_legible.py. This file holds the half that runs in CI.
"""
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = (ROOT / "web" / "templates" / "trip_detail.html").read_text()
GUARD = "{% if trip.engine_km %}"


def _engine_block() -> str:
    """Everything the page prints for the generator's distance, from its own `{% if %}` to the
    matching `{% endif %}` — nesting counted, so an inner `{% if %}` cannot end the slice early."""
    i = HTML.find(GUARD)
    assert i > 0, "the generator distance is not behind a guard of its own"
    depth, j = 1, i + len(GUARD)
    for m in re.finditer(r"\{%-?\s*(if|endif)\b", HTML[j:]):
        depth += 1 if m.group(1) == "if" else -1
        if not depth:
            return HTML[i:j + m.end()]
    raise AssertionError("the generator-distance guard is never closed")


def _note_line() -> str:
    """The `<div>…</div>` that prints the getEC note — the paragraph the figure used to hang off."""
    i = HTML.find("reev_elec_source_note")
    start = HTML.rfind("<div", 0, i)
    return HTML[start:HTML.find("</div>", i) + 6]


# ── where the figure sits ─────────────────────────────────────────────────────

def test_the_distance_is_not_the_tail_of_the_note():
    """The defect itself: one element carried a 41-word explanation AND the figure, in that order,
    joined by a `·`."""
    assert "engine_km" not in _note_line(), (
        "the generator's distance is still printed inside the note — the thing beta #31 is about")


def test_the_figure_is_printed_once():
    """The mirror check. Moving it out has to empty the tail, not copy it: printed in both places
    the fix reads as done and the page is worse than before."""
    assert HTML.count("trip.engine_km |") + HTML.count("trip.engine_km|") == 1, \
        "the distance is now on the page twice"


# ── and what it has to carry now that it is visible ───────────────────────────

def test_the_figure_says_it_is_a_floor():
    """🔑 The condition on promoting it. The count runs ~10% short (54.0 measured against a
    dashboard's 60.2), so a bare "25 km" on its own line asserts a precision we measured we do not
    have. Two quantities under one label is the defect we keep re-introducing."""
    assert "reev_engine_km_floor" in _engine_block(), (
        "the distance was promoted without the qualifier that it is inferred and short")


def test_every_language_can_say_it():
    """A key that exists in en.json alone leaves seven interfaces printing an empty line.

    `reev_engine_km` is on the list because it was REPURPOSED, not added: it used to be a fragment
    after a colon ("generator on: 25 km") and now has to read after the number ("25 km with the
    generator running"). A language left on the old wording still renders — wrongly."""
    missing = [p.name for p in sorted((ROOT / "web" / "locales").glob("*.json"))
               for k in ("reev_engine_km", "reev_engine_km_floor")
               if k not in json.loads(p.read_text())["translations"]]
    assert not missing, f"the new keys are missing from {sorted(set(missing))}"


def test_a_drive_that_never_burned_anything_prints_nothing():
    """`engine_km` is None on a pure-electric drive and on every BEV. Both the figure and its
    qualifier live behind the same guard, or those trips grow an empty "⛽  km" and a caveat about
    a generator that never ran."""
    block = _engine_block()
    assert "trip.engine_km" in block and "reev_engine_km_floor" in block, \
        "the figure and its qualifier are not behind the same guard"
