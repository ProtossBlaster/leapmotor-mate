"""The detected-refuel card: the litres box has to read as editable, not as a reading.

@gm27271 (beta #10) worked the consequence out before he hit it. The litres come from a float
gauge; the receipt comes from a pump. Confirm 9 L against a 10 L receipt and the price per litre
lands at 20/9 = 2.22 where 2.00 was paid — and that price is not cosmetic, it weights the blend
and so the fuel cost of every trip that burns from that tankful.

The box was always editable. Nothing said so, and "≈ 9.0 L" in amber above it reads like the car
talking. These tests hold the sentence that fixes that, in every language the card is drawn in.
"""
import json
import pathlib

import pytest

jinja2 = pytest.importorskip("jinja2", reason="needs jinja2 to render the partial")

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "web" / "templates"
LOCALES = sorted((ROOT / "web" / "locales").glob("*.json"))


def _render(lang="en"):
    tr = json.loads(LOCALES[0].read_text())["translations"]
    for p in LOCALES:
        if p.stem == lang:
            tr = json.loads(p.read_text())["translations"]
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)), autoescape=True)
    env.filters["nice"] = lambda v: f"{v:.1f}"
    return env.get_template("partials/fuel_detected.html").render(
        detected=[{"id": 1, "liters": 9.0, "ts_from_local": "10:00", "ts_local": "10:20",
                   "fuel_before_pct": 12.0, "fuel_after_pct": 92.0}],
        t=lambda k: tr.get(k, k),
        currency={"symbol": "€"})


def test_there_are_locales_to_check():
    """Guards the glob: an empty list would make every check below pass vacuously."""
    assert len(LOCALES) >= 6, [p.name for p in LOCALES]


def test_litres_is_an_input_prefilled_with_the_estimate():
    out = _render()
    assert 'name="liters"' in out
    assert 'value="9.0"' in out


def test_the_card_says_the_litres_are_an_estimate_to_replace():
    """The whole point: the number is a starting suggestion, and the card has to say so.

    Checked as ELEMENT TEXT (`>sentence<`), not merely "somewhere in the HTML" — the same string
    is also the input's `title`, so a plain substring check stays green with the visible line
    deleted. It did, when I removed the line to see this test fail.
    """
    out = _render()
    tr = json.loads((ROOT / "web" / "locales" / "en.json").read_text())["translations"]
    hint = tr["fuel_liters_hint"]
    assert f">{hint}<" in out, "the hint must be visible text, not only a tooltip"


def test_the_hint_exists_in_every_language():
    """A card drawn in Italian with an English sentence under it is worse than none."""
    for p in LOCALES:
        tr = json.loads(p.read_text())["translations"]
        assert tr.get("fuel_liters_hint"), p.name
        assert tr["fuel_liters_hint"] != tr.get("fuel_either_hint"), p.name


@pytest.mark.parametrize("lang", [p.stem for p in LOCALES])
def test_every_language_renders_its_own_hint(lang):
    out = _render(lang)
    tr = json.loads((ROOT / "web" / "locales" / f"{lang}.json").read_text())["translations"]
    assert f">{tr['fuel_liters_hint']}<" in out


def test_the_money_fields_are_still_there_and_confirm_is_still_guarded():
    """The hint sits inside the same grid cell as the litres box — this catches a stray closing
    tag pushing the price fields out of the row, or the guard being lost in the edit."""
    out = _render()
    assert 'name="price_per_l"' in out and 'name="total_cost"' in out
    assert out.count("disabled") >= 1                     # Confirm starts disabled
    assert "this.form.price_per_l.value" in out           # the inline money guard survived
