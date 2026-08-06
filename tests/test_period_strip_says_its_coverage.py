"""The period strip says how many of its kilometres the electric figure speaks for (beta #11).

Since v3.8.3 the kWh/100 km on a range-extender divides by the distance getEC was actually measured
over — not by every kilometre driven, which produced a figure at a fraction of the truth wherever a
reading was missing. But the strip did not SAY so, and the litres beside it are divided by all the
kilometres: two "per 100 km" figures, one line apart, on different distances and nothing marking it.

@michapr, 06/08/26, choosing between three shapes I offered and improving the one he picked:

    "10.4 kWh/100 km · over 452 km of 479 km"
    with a mouse-over (i) where is written "value for the kilometers with energy data recorded
    from the cloud"

🔑 His version, not mine. I had proposed the covered distance alone — which does not tell you
whether the slice is large or small. Both numbers do.

⚠️ Shown only when the two differ: "over 479 km of 479 km" is noise on a period whose readings are
all there, and noise is how a caveat stops being read.
"""
import pathlib
import re

import db_reader
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
MONTH = ROOT / "web" / "templates" / "partials" / "trips_calendar_month.html"
DAY = ROOT / "web" / "templates" / "partials" / "trips_calendar_day_content.html"


def _trip(km, ec=None):
    return {"distance_km": km, "ec_kwh": ec, "efficiency_kwh_100km": None}


# ── the numbers the strip needs ──────────────────────────────────────────────
def test_the_totals_carry_both_distances():
    """His example needs two: the metered one and the whole one."""
    tot = db_reader.trips_totals([_trip(100.0, ec=20.0), _trip(379.0)])
    assert tot["kwh_100km"] == 20.0
    assert tot["kwh_100km_km"] == 100.0      # what the figure speaks for
    assert tot["km"] == 479.0                # what was driven


def test_full_coverage_leaves_nothing_to_say():
    tot = db_reader.trips_totals([_trip(100.0, ec=20.0), _trip(100.0, ec=10.0)])
    assert tot["kwh_100km_km"] == tot["km"] == 200.0


def test_no_electric_figure_means_no_coverage_either():
    tot = db_reader.trips_totals([_trip(100.0), _trip(50.0)])
    assert tot["kwh_100km"] is None and tot["kwh_100km_km"] is None


# ── what the strip renders ───────────────────────────────────────────────────
def _render(path, totals, *, is_reev=True, research=True):
    jinja2 = pytest.importorskip("jinja2", reason="needs jinja2 to render the partial")
    src = path.read_text()
    start = src.index("{% set eff_all")
    end = src.index("{% endif %}", src.index("eff_cls(", start)) + len("{% endif %}")
    env = jinja2.Environment()
    env.filters["eff"] = lambda v: f"{v} kWh/100km"
    env.filters["nice"] = lambda v: f"{v:g}" if isinstance(v, (int, float)) else v
    env.filters["dist"] = lambda v, n=0: f"{v:.0f} km"
    name = "total" if path is MONTH else "day_totals"
    out = env.from_string(src[start:end]).render(
        **{name: totals}, is_reev=is_reev, research=research,
        eff_cls=lambda v: "eff-good",
        t=lambda k: {"trips_eff_over_km": "over {km} of {total}",
                     "trips_eff_over_km_hint": "value for the kilometres with energy data "
                                               "recorded from the cloud"}[k])
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", out)).strip(), out


@pytest.mark.parametrize("path", [MONTH, DAY])
def test_a_partly_covered_period_prints_both_distances(path):
    """His exact line."""
    totals = {"kwh_100km": 10.4, "kwh_100km_km": 452.0, "km": 479.0, "avg_eff": None}
    visible, _ = _render(path, totals)
    assert "10.4 kWh/100km" in visible
    assert "452" in visible and "479" in visible, visible


@pytest.mark.parametrize("path", [MONTH, DAY])
def test_the_hover_explains_which_kilometres(path):
    totals = {"kwh_100km": 10.4, "kwh_100km_km": 452.0, "km": 479.0, "avg_eff": None}
    _, html = _render(path, totals)
    assert "energy data" in html, "the (i) carries no explanation"
    assert "title=" in html


@pytest.mark.parametrize("path", [MONTH, DAY])
def test_a_fully_covered_period_says_nothing(path):
    """"over 479 km of 479 km" is noise, and noise is how a caveat stops being read."""
    totals = {"kwh_100km": 10.4, "kwh_100km_km": 479.0, "km": 479.0, "avg_eff": None}
    visible, _ = _render(path, totals)
    assert "10.4 kWh/100km" in visible
    assert "479" not in visible, visible


@pytest.mark.parametrize("path", [MONTH, DAY])
def test_a_rounding_difference_is_not_a_gap(path):
    """452.0 vs 452.3 is the sum of a few decimals, not a hole worth warning about."""
    totals = {"kwh_100km": 10.4, "kwh_100km_km": 452.0, "km": 452.3, "avg_eff": None}
    visible, _ = _render(path, totals)
    assert "452" not in visible, visible


@pytest.mark.parametrize("path", [MONTH, DAY])
def test_a_plain_electric_car_is_untouched(path):
    """The whole block is REEV-only: a BEV keeps its measured pill and gains no note."""
    totals = {"kwh_100km": 10.4, "kwh_100km_km": 452.0, "km": 479.0, "avg_eff": 17.2}
    visible, _ = _render(path, totals, is_reev=False)
    assert "17.2 kWh/100km" in visible
    assert "452" not in visible and "479" not in visible, visible


# ── the label reaches every language ─────────────────────────────────────────
def test_both_keys_exist_in_all_seven_languages():
    import json
    en = json.load(open(ROOT / "web" / "locales" / "en.json"))["translations"]
    for f in sorted((ROOT / "web" / "locales").glob("*.json")):
        d = json.load(open(f))["translations"]
        for k in ("trips_eff_over_km", "trips_eff_over_km_hint"):
            assert k in d, f"{k} missing from {f.name}"
            if f.name != "en.json":
                assert d[k] != en[k], f"{f.name} still carries the English {k}"


def test_the_label_keeps_both_placeholders():
    """`.format(km=…, total=…)` — a translation that drops one prints the other alone, which is the
    version he told us was not enough."""
    import json
    for f in sorted((ROOT / "web" / "locales").glob("*.json")):
        v = json.load(open(f))["translations"]["trips_eff_over_km"]
        assert "{km}" in v and "{total}" in v, f"{f.name}: {v}"
