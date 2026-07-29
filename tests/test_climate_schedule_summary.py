"""The climate card gets the same read-out as the charge one — and needs it more.

The two cards sit one under the other on the Scheduling page with an identical row of day chips,
and an empty row means the OPPOSITE thing in each:

    charge  — no days = EVERY day   (cycles_from_day_flags: empty coerces to all-1)
    climate — no days = ONE TIME    (save_climate_schedule: `[] = one-time`)

Nothing on screen distinguished them. A user who reads the top card correctly reads the bottom
one exactly wrong, and a one-shot pre-conditioning looks identical to a daily one.
"""
import json
import pathlib
import re

import pytest

jinja2 = pytest.importorskip("jinja2", reason="needs jinja2 to render the partial")

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "web" / "templates"
LOCALES = sorted((ROOT / "web" / "locales").glob("*.json"))
KEYS = ("sched_summary_clima_once", "sched_summary_clima_on", "sched_summary_clima_off")


def _tr(lang="en"):
    return json.loads((ROOT / "web" / "locales" / f"{lang}.json").read_text())["translations"]


def _render(lang="en"):
    tr = _tr(lang)
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)), autoescape=True)
    return env.get_template("partials/climate_schedule.html").render(t=lambda k: tr.get(k, k))


def test_the_read_out_sits_above_the_form():
    out = _render()
    assert 'id="cls-summary"' in out
    assert out.index('id="cls-summary"') < out.index('hx-post="api/climate-schedule"')


def test_one_time_is_named():
    """The whole point. An empty day list is a schedule that fires once — the chips show nothing,
    and nothing showing is also what a daily schedule would look like on the card above."""
    out = _render()
    assert "days.length === 0 ? SC.once" in out
    assert "days.length === 7 ? SC.every" in out


@pytest.mark.parametrize("lang", [p.stem for p in LOCALES])
def test_it_reads_the_climate_day_order_not_the_charge_one(lang):
    """Climate days are 0=Sun..6=Sat; the charge mask is Mon-first. Getting this backwards would
    name Saturday's pre-conditioning "Sunday" — and the two orders live twenty lines apart.

    Compared against the locale's own abbreviations, in Sunday-first order: hard-coding "Sun"/"Mon"
    passes in no language at all (English says "Su"/"Mo"), which is how this test first failed.
    """
    tr = _tr(lang)
    out = _render(lang)
    dow = out[out.index("dow: ["):]
    dow = dow[:dow.index("]")]
    got = [json.loads(s) for s in re.findall(r'"(?:[^"\\]|\\.)*"', dow)]
    assert got == [tr["dow_sun"], tr["dow_mon"], tr["dow_tue"], tr["dow_wed"],
                   tr["dow_thu"], tr["dow_fri"], tr["dow_sat"]], lang


def test_the_mode_is_spelled_out():
    """"Quick cool" rather than mode=cold + circle=in + wshld=0, which is what the payload says."""
    out = _render()
    for preset in ("cool", "heat", "vent", "defrost", "none"):
        assert f"{preset}:" in out.split("mode: {")[1][:400]


def test_it_redraws_after_a_save():
    out = _render()
    assert "climateScheduleSaved" in out
    body = out[out.index("function refresh()"):out.index("function summary(")]
    assert "summary(d, on, has)" in body


def test_no_schedule_at_all_says_so():
    out = _render()
    assert "if (!on || !has)" in out
    assert "SC.off" in out


@pytest.mark.parametrize("lang", [p.stem for p in LOCALES])
def test_every_language_has_the_three_strings(lang):
    tr = _tr(lang)
    for k in KEYS:
        assert tr.get(k), f"{lang}: {k}"


@pytest.mark.parametrize("lang", [p.stem for p in LOCALES])
def test_placeholders_survive_translation(lang):
    assert set(re.findall(r"\{(\w+)\}", _tr(lang)["sched_summary_clima_on"])) == {
        "mode", "when", "time", "temp"}


def test_the_two_cards_do_not_share_a_sentence():
    """They describe different things and must not be collapsed into one string later: the charge
    one talks about a window and a target SoC, this one about a mode and a temperature."""
    tr = _tr("en")
    assert tr["sched_summary_clima_on"] != tr["sched_summary_on"]
    assert "{soc}" not in tr["sched_summary_clima_on"]
    assert "{temp}" not in tr["sched_summary_on"]
