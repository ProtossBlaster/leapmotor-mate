"""The ⚡ figure on a REEV row must say what it is, on the row itself (beta #31, @gm27271).

On an engine-on trip the list prints `⚡ 4.5 kWh/100km` and nothing else. That number is the energy
that LEFT THE BATTERY over ALL the kilometres — what the generator sends straight to the motor never
passes through the pack, so it is not in there. On a long generator trip it therefore reads
impossibly good: 645 km against 20 kWh, 3.1 kWh/100 km, on a car that cannot do it.

The trip DETAIL has carried the explanation since v3.2.0 (`reev_elec_source_note`). The list never
did, and the list is where the number is met first: @gm27271 read it, took it for the car's electric
consumption, and built a whole redesign proposal on the assumption that part of the distance had
been driven by the engine. → [[feedback-two-numbers-one-word]]

⚠️ The row's right-hand column is **84px wide**. So the visible text is a SHORT label and the full
sentence stays in the tooltip — a note that wraps to four lines would push every row apart. The
length is held by a test here, because a translation is where that guarantee gets lost.
"""
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOCALES = sorted(p for p in (ROOT / "web" / "locales").glob("*.json"))
KEY = "reev_elec_battery_only"


def _block():
    """The ⚡ pill of the row, with its else-branch — rendered exactly as the page renders it."""
    src = (ROOT / "web" / "templates" / "partials" / "trip_row.html").read_text()
    start = src.index("{% if trip.reev_elec_kwh_100km is not none %}")
    end = src.index("{% endif %}", start) + len("{% endif %}")
    return src[start:end]


def _render(trip, lang="en"):
    jinja2 = pytest.importorskip("jinja2", reason="needs jinja2 to render the row")
    tr = json.loads((ROOT / "web" / "locales" / f"{lang}.json").read_text())["translations"]
    env = jinja2.Environment()
    env.filters["nice"] = lambda v: v
    return env.from_string(_block()).render(trip=trip, t=lambda k: tr[k])


def test_the_row_says_the_number_is_the_battery_only():
    out = _render({"reev_elec_kwh_100km": 4.5})
    tr = json.loads((ROOT / "web" / "locales" / "en.json").read_text())["translations"]
    assert "4.5 kWh/100km" in out
    assert tr[KEY] in out, f"the row still prints the number bare:\n{out}"


def test_the_full_sentence_is_still_one_hover_away():
    """The short label says WHICH energy; only the long note says why the generator is missing.
    Dropping it to make room would trade one silence for another."""
    out = _render({"reev_elec_kwh_100km": 4.5})
    assert "reev_elec_source_note" not in out          # rendered, not left as a key
    tr = json.loads((ROOT / "web" / "locales" / "en.json").read_text())["translations"]
    assert tr["reev_elec_source_note"] in out


def test_a_pending_row_is_left_alone():
    """`⚡ —` already carries its own reason (the cloud has not answered yet). Stacking the
    battery-only label under a dash would explain a number that is not there."""
    out = _render({"reev_elec_kwh_100km": None})
    tr = json.loads((ROOT / "web" / "locales" / "en.json").read_text())["translations"]
    assert "⚡ —" in out
    assert tr[KEY] not in out


@pytest.mark.parametrize("path", LOCALES, ids=lambda p: p.stem)
def test_every_language_has_it_and_keeps_it_short(path):
    """84px of column. A label that wraps to three lines pushes every row in the list apart, and
    the translation is exactly where that guarantee is lost — so the bound lives here, not in a
    comment. Twenty characters is 'solo batteria' with room, and half of 'not counting the
    generator's share'."""
    tr = json.loads(path.read_text())["translations"]
    assert KEY in tr, f"{path.stem} is missing {KEY}"
    assert 0 < len(tr[KEY]) <= 20, f"{path.stem}: {tr[KEY]!r} is {len(tr[KEY])} chars"
