"""The car's own two consumptions, from getPlugInLastNweeks100kmEC (beta #11/#22).

Measured by the car rather than worked out here: no nominal pack, no nominal tank, no percentages.
The figures match the official app's "last 6 weeks" screen to the decimal — @pdifeo's C10 REEV
returned 11.1 kWh + 1.6 L where his app showed 11,1 and 1,6, and our own B10 returns 20.0 + 0.0.

⚠️ The endpoint takes NO date range: the request carries the VIN and nothing else, so unlike getEC
it cannot be asked for a month. It answers its own six weeks — which is why the window is printed on
the card, beside monthly tiles that mean something different.
"""
import json
import pathlib

import pytest

jinja2 = pytest.importorskip("jinja2", reason="template render needs jinja2")

ROOT = pathlib.Path(__file__).resolve().parent.parent
TPL_DIR = ROOT / "web" / "templates"
MAIN = (ROOT / "web" / "main.py").read_text()
STATS = (TPL_DIR / "statistics.html").read_text()


def _render(pc):
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TPL_DIR)), autoescape=True)
    env.filters["nice"] = lambda v: ("%g" % v) if v is not None else "—"
    return env.get_template("partials/plugin_consumption.html").render(
        pc=pc, t=lambda k: {"plugin_consumption_label": "LABEL",
                            "plugin_consumption_note": "NOTE"}.get(k, k))


def test_a_range_extender_shows_both_consumptions():
    out = _render({"elec_kwh_100km": 11.1, "fuel_l_100km": 1.6, "fuel_mpg": 176.5})
    assert "11.1" in out and "kWh/100km" in out
    assert "1.6" in out and "L/100km" in out


def test_an_electric_car_shows_only_the_electric_one():
    """On a BEV the cloud answers 0.0 L. A permanent "0,0 L/100km" on an electric car is noise
    dressed up as information — the field is there, it just has nothing to say."""
    out = _render({"elec_kwh_100km": 20.0, "fuel_l_100km": 0.0, "fuel_mpg": 0.0})
    assert "20" in out
    assert "L/100km" not in out


def test_nothing_at_all_when_the_cloud_has_not_answered():
    """An empty card would claim zero, which is a different statement from "we were not told"."""
    assert _render(None).strip() == ""


def test_the_window_is_printed_on_the_card():
    """It cannot be asked for another period, so a card that stays silent about its own would sit
    beside the monthly tiles looking like it disagrees with them."""
    out = _render({"elec_kwh_100km": 11.1, "fuel_l_100km": 1.6})
    assert "LABEL" in out and "NOTE" in out


# ── wiring ───────────────────────────────────────────────────────────────────

def test_the_card_loads_on_its_own_so_a_slow_cloud_never_blocks_the_page():
    assert 'hx-get="api/plugin-consumption"' in STATS
    assert 'hx-trigger="load"' in STATS.split('hx-get="api/plugin-consumption"', 1)[1][:120]


def test_it_sits_above_the_derived_pair():
    """Measured first, derived second: when the two disagree the order says which to believe."""
    i_card = STATS.index('hx-get="api/plugin-consumption"')
    i_derived = STATS.index("{% if totals.reev_total or totals.reev_spend %}")
    assert i_card < i_derived


def test_a_stale_answer_is_kept_when_the_cloud_goes_quiet():
    """Losing the card the moment one call fails would make a working figure flicker in and out."""
    body = MAIN.split("async def plugin_consumption(", 1)[1].split("\n@app.", 1)[0]
    assert 'pc = data if data is not None else (c["data"] if c else None)' in body


def test_the_answer_is_cached_like_the_other_period_cards():
    body = MAIN.split("async def plugin_consumption(", 1)[1].split("\n@app.", 1)[0]
    assert "1800" in body, "30 minutes, same as the other cloud-backed cards"
    assert "run_in_executor" in body, "a blocking cloud call must not sit on the event loop"


@pytest.mark.parametrize("lang", ["en", "it", "fr", "de", "nl", "pl", "pt-PT"])
def test_the_labels_exist_in_every_language(lang):
    d = json.loads((ROOT / "web" / "locales" / f"{lang}.json").read_text())["translations"]
    for key in ("plugin_consumption_label", "plugin_consumption_note"):
        assert d.get(key), f"{lang} is missing {key}"
    assert "6" in d["plugin_consumption_label"], f"{lang} lost the window from the label"
