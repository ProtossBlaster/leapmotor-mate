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


def _render(lang="en", capped=False):
    tr = json.loads(LOCALES[0].read_text())["translations"]
    for p in LOCALES:
        if p.stem == lang:
            tr = json.loads(p.read_text())["translations"]
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)), autoescape=True)
    env.filters["nice"] = lambda v: f"{v:.1f}"
    return env.get_template("partials/fuel_detected.html").render(
        detected=[{"id": 1, "liters": 9.0, "ts_from_local": "10:00", "ts_local": "10:20",
                   "fuel_before_pct": 12.0, "fuel_after_pct": 100.0 if capped else 92.0,
                   "capped": capped}],
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


# ── A fill that topped the gauge out (beta #21, @pdifeo) ─────────────────────────
# 9.204 L on the card against 10.51 L on the pump, because the car stops counting at 100 %. The
# number is a FLOOR, so the card has to stop saying "about" and say so in the owner's language.

def test_a_capped_fill_reads_as_at_least_not_about():
    out = _render(capped=True)
    assert "≥ 9.0 L" in out
    assert "≈ 9.0 L" not in out


def test_an_ordinary_fill_still_reads_as_an_estimate():
    """Without this the change is untestable in the direction that matters: "≥" everywhere says
    nothing at all."""
    out = _render(capped=False)
    assert "≈ 9.0 L" in out
    assert "≥ 9.0 L" not in out


def _as_rendered(s):
    """The sentence as it comes OUT of the template, not as it goes in.

    The card renders with autoescape on, so an apostrophe becomes `&#39;` — the Italian string
    ("L'auto smette di contare…") is the one that catches this, and a naive substring check fails
    on Italian alone while passing in six other languages."""
    from markupsafe import escape
    return str(escape(s))


def test_the_capped_warning_is_visible_text_only_when_capped():
    tr = json.loads((ROOT / "web" / "locales" / "en.json").read_text())["translations"]
    warn = _as_rendered(tr["fuel_detected_capped"])
    assert warn in _render(capped=True)
    assert warn not in _render(capped=False)


def test_the_capped_sentence_exists_in_every_language():
    """A German owner reading an English warning is how this defect gets ignored twice."""
    for p in LOCALES:
        tr = json.loads(p.read_text())["translations"]
        assert tr.get("fuel_detected_capped"), p.name
        assert tr["fuel_detected_capped"] != tr.get("fuel_liters_hint"), p.name


@pytest.mark.parametrize("lang", [p.stem for p in LOCALES])
def test_every_language_renders_its_own_capped_sentence(lang):
    out = _render(lang, capped=True)
    tr = json.loads((ROOT / "web" / "locales" / f"{lang}.json").read_text())["translations"]
    assert _as_rendered(tr["fuel_detected_capped"]) in out


def test_the_money_fields_are_still_there_and_confirm_is_still_guarded():
    """The hint sits inside the same grid cell as the litres box — this catches a stray closing
    tag pushing the price fields out of the row, or the guard being lost in the edit."""
    out = _render()
    assert 'name="price_per_l"' in out and 'name="total_cost"' in out
    assert out.count("disabled") >= 1                     # Confirm starts disabled
    assert "this.form.price_per_l.value" in out           # the inline money guard survived
