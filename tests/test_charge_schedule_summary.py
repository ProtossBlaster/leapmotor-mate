"""#190 (@riri19): the Scheduling page never said what the car is actually DOING.

It is an editor. The day chips are an input, and an empty row means "every day" — which is the
state every car leaves the factory in, and the state you also land in by selecting all seven.
@riri19 selected all seven, saw them cleared after a reload, and reported his schedule as lost.
Nothing was lost. Nothing said so either.

The chips keep their behaviour (empty = every day is deliberate: you SELECT days rather than
deselect six of seven). What was missing is a sentence above the form, built from the same reply
the form is filled with. These tests hold that sentence, its placeholders and its languages.
"""
import json
import pathlib
import re

import pytest

jinja2 = pytest.importorskip("jinja2", reason="needs jinja2 to render the partial")

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "web" / "templates"
LOCALES = sorted((ROOT / "web" / "locales").glob("*.json"))
KEYS = ("sched_summary_every_day", "sched_summary_on", "sched_summary_on_start_only",
        "sched_summary_off", "sched_summary_recharge")


def _tr(lang="en"):
    return json.loads((ROOT / "web" / "locales" / f"{lang}.json").read_text())["translations"]


def _render(lang="en", advanced=True):
    tr = _tr(lang)
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)), autoescape=True)
    return env.get_template("partials/charge_schedule.html").render(
        t=lambda k: tr.get(k, k), charge_schedule_advanced=advanced)


# ── the read-out is there and is fed by the existing call ────────────────────
def test_summary_element_exists_above_the_form():
    out = _render()
    assert 'id="cs-summary"' in out
    assert out.index('id="cs-summary"') < out.index('hx-post="api/charge-schedule"')


def test_summary_uses_the_same_payload_as_the_form():
    """No second call to the car: the sentence is drawn from the reply that fills the fields."""
    out = _render()
    assert out.count("fetch('api/charge-schedule')") == 1
    assert "summary(d, on, sel, n)" in out


def test_it_redraws_after_a_save():
    """The listener that re-reads on a confirmed save must reach the sentence too, or it would
    keep describing the schedule the car had a minute ago."""
    out = _render()
    assert "chargeScheduleSaved" in out
    body = out[out.index("function refresh()"):out.index("function summary(")]
    assert "summary(" in body                      # refresh() paints it, and refresh() is the hook


# ── what it says ─────────────────────────────────────────────────────────────
def test_every_day_is_named_rather_than_left_blank():
    """The heart of #190: 0 and 7 both mean every day, and the chips can show neither."""
    out = _render()
    assert "n === 0 || n === 7" in out
    assert "S.every" in out


def test_off_says_what_happens_instead():
    """"No schedule" is not the absence of information — the car charges on plug-in, and the
    sentence says so rather than leaving the user to guess."""
    for p in LOCALES:
        tr = json.loads(p.read_text())["translations"]
        assert len(tr["sched_summary_off"]) > 20, p.name


def test_recharge_flag_is_reported_when_the_car_has_it():
    """Mate can't change `recharge` (the official app can) — but it can stop hiding it."""
    out = _render()
    assert "String(d.recharge) === '1'" in out
    assert "S.recharge" in out


def test_a_car_without_a_charge_window_gets_the_shorter_sentence():
    """A T03 has a start and no end (#146): the sentence must stop, not trail into an empty 'to'.

    Compared as JSON, which is how the template emits it — `|tojson` escapes the ⚡ to \\u26a1, so
    a plain substring check fails on a template that is perfectly correct. (It did.)
    """
    assert json.dumps(_tr("en")["sched_summary_on_start_only"]) in _render(advanced=False)
    assert json.dumps(_tr("en")["sched_summary_on"]) in _render(advanced=True)
    assert json.dumps(_tr("en")["sched_summary_on"]) not in _render(advanced=False)


# ── the strings themselves ───────────────────────────────────────────────────
@pytest.mark.parametrize("lang", [p.stem for p in LOCALES])
def test_every_language_has_all_five(lang):
    tr = _tr(lang)
    for k in KEYS:
        assert tr.get(k), f"{lang}: {k}"


@pytest.mark.parametrize("lang", [p.stem for p in LOCALES])
def test_placeholders_survive_translation(lang):
    """A translator dropping {start} would silently print a schedule with no time in it."""
    tr = _tr(lang)
    assert set(re.findall(r"\{(\w+)\}", tr["sched_summary_on"])) == {"days", "start", "end", "soc"}
    assert set(re.findall(r"\{(\w+)\}", tr["sched_summary_on_start_only"])) == {"days", "start", "soc"}


def test_italian_does_not_say_fino_al_80():
    """"fino al {soc}%" reads correctly at 100 and 90 and wrong at 80 — the one value in the
    50-100 range that needs the elision. Caught on screen, not by a test, which is why it's here."""
    assert "fino al {soc}" not in _tr("it")["sched_summary_on"]
