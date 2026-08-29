"""The Overview's range estimate aims at the car's charge target, not a fixed 100% (discussion #266,
@adoewa): if you only charge to 80/90%, the 100% figure isn't the one you plan around. The extrapolation
is the same the card already did (range ÷ SoC × target); this just swaps the fixed 100 for the target,
and falls back to 100 when the car hasn't reported a limit — so behaviour is unchanged until then.
Pure and CI-safe: no fastapi/app needed (the value is computed in db_reader)."""
import db_reader
import pytest


def test_estimate_scales_to_the_charge_target():
    est = db_reader.range_at_charge_target(416.0, 84.9, 90)
    assert est["pct"] == 90
    assert round(est["km"]) == 441          # 416 / 84.9 * 90 — @adoewa's own numbers


def test_no_reported_limit_falls_back_to_100_percent():
    """Unchanged behaviour when the car hasn't reported a charge limit yet."""
    est = db_reader.range_at_charge_target(416.0, 84.9, None)
    assert est["pct"] == 100
    assert round(est["km"]) == 490          # exactly the old "Estimated at 100%"


def test_target_below_current_soc_is_still_honoured():
    """Limit 80 while at 84.9% → the estimate is BELOW the current range, which is correct: at 80%
    you'd have less than you do now. The card reports the target, it doesn't clamp it."""
    est = db_reader.range_at_charge_target(416.0, 84.9, 80)
    assert est["pct"] == 80
    assert est["km"] < 416


@pytest.mark.parametrize("range_km,soc", [(None, 84.9), (416.0, 0), (416.0, None)])
def test_missing_range_or_soc_gives_no_estimate(range_km, soc):
    assert db_reader.range_at_charge_target(range_km, soc, 90) is None


def test_status_card_shows_the_target_not_a_fixed_100():
    """The Overview card must read the target through the global and label it with the real %, never a
    hard-coded percentage or a hard-coded language. It now shows the target AND 100% side by side
    (range_estimates), which is the same promise: the number you plan around is the one you charge to."""
    import pathlib
    src = (pathlib.Path(db_reader.__file__).resolve().parent
           / "templates" / "partials" / "status_card.html").read_text(encoding="utf-8")
    assert "range_estimates(status.range_km" in src
    assert "ests.limit_est.pct" in src            # the target %, read from the estimate — not a literal
    assert "Stima al" not in src                  # labels come from the locale files, not the template
