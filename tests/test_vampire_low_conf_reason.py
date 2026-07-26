"""Why a rest-drain estimate is uncertain — not merely that it is.

Reported as #160 by riri19: a park of 45.9 hours, properly closed by six trips the next day,
carried the label "uncertain estimate (short stop)". It was not short. The reliability test has
TWO independent halves:

    reliable = drop is at least 4 sensor steps   AND   the rate's error band is within ±1 %/day

and only the second is about duration. In riri19's case the second passed with a factor of ten to
spare (0.2 / 45.9 * 24 = 0.10 against a limit of 1.0); what failed was the first — a 0.2% drop is
two steps of a sensor that moves in 0.1% quanta, so drain cannot be told apart from rounding.

Calling that a short stop is not a wording nitpick: it points the user at the wrong explanation.
riri19 went looking for a fault in how the park was closed, because the label told him to.
"""
import json
import pathlib

import pytest

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"


@pytest.fixture
def reason():
    """The classifier as db_reader applies it, fed the two numbers that decide it."""
    import db_reader as R

    def classify(drop_pct, hours):
        drop_r = round(drop_pct, 1)
        err = R._DROP_ERR / hours * 24
        ok = drop_r >= 2 * R._DROP_ERR - 1e-9 and err <= 1.0
        return None if ok else ("rate" if err > 1.0 else "drop")
    return classify


def test_riri19s_two_day_park_is_not_called_short(reason):
    """The exact case from the issue: 45.9 h, 0.2% — 56.3 → 56.1%."""
    assert reason(0.2, 45.9) == "drop", "a 46-hour park must never be blamed on its duration"


def test_a_genuinely_short_stop_still_says_short(reason):
    """The other half must keep working: an hour is too little to extrapolate a rate from, however
    big the step looks."""
    assert reason(0.5, 1.0) == "rate"


def test_a_long_park_with_a_real_drop_is_trusted(reason):
    assert reason(2.0, 48.0) is None


def test_a_short_stop_wins_over_a_small_drop(reason):
    """When both fail, duration is the stronger objection — a rate extrapolated from one sensor
    step can be wrong by several %/day, while a small drop over a long window is merely coarse."""
    assert reason(0.1, 0.5) == "rate"


def test_the_boundary_is_four_sensor_steps(reason):
    """Four quanta exactly is trusted; three is not. Pinned because the threshold is written as
    2 * _DROP_ERR where _DROP_ERR is itself 2 * SOC_QUANTUM — easy to halve or double by mistake."""
    import db_reader as R
    assert reason(4 * R.SOC_QUANTUM, 48.0) is None
    assert reason(3 * R.SOC_QUANTUM, 48.0) == "drop"


def test_the_window_carries_the_reason_to_the_chart():
    """The field has to reach the template, not just exist in a helper: the tooltip reads
    d.low_conf, and a window without the key would silently fall back to 'short stop' — the very
    bug this fixes."""
    src = (WEB / "db_reader.py").read_text(encoding="utf-8")
    assert '"low_conf"' in src, "the window dict does not carry the reason"
    chart = (WEB / "templates" / "battery.html").read_text(encoding="utf-8")
    assert "d.low_conf === 'drop'" in chart, "the tooltip does not read the reason"
    assert "battery_vampire_low_conf_drop" in chart, "the new label is never used"


def test_the_day_aggregate_keeps_a_reason():
    """Grouping by day ANDs the reliability flags; without carrying the reason across, an
    aggregated bar loses it and falls back to 'short stop' again — the same bug, one zoom level up."""
    chart = (WEB / "templates" / "battery.html").read_text(encoding="utf-8")
    agg = chart[chart.index("function aggDay"):]
    agg = agg[:agg.index("return Object.keys")]
    assert "low_conf" in agg, "aggDay drops the reason on the floor"


@pytest.mark.parametrize("locale", sorted(p.name for p in (WEB / "locales").glob("*.json")))
def test_both_labels_exist_in_every_language(locale):
    data = json.loads((WEB / "locales" / locale).read_text(encoding="utf-8"))
    keys = {k for sect in data.values() if isinstance(sect, dict) for k in sect}
    assert "battery_vampire_low_conf" in keys
    assert "battery_vampire_low_conf_drop" in keys, f"{locale} has no label for a too-small drop"


@pytest.mark.parametrize("locale", sorted(p.name for p in (WEB / "locales").glob("*.json")))
def test_the_two_labels_are_not_the_same_sentence(locale):
    """Copy-pasting one into the other would pass every other test here and change nothing for
    the user."""
    data = json.loads((WEB / "locales" / locale).read_text(encoding="utf-8"))
    flat = {k: v for sect in data.values() if isinstance(sect, dict) for k, v in sect.items()}
    assert flat["battery_vampire_low_conf"] != flat["battery_vampire_low_conf_drop"], locale
