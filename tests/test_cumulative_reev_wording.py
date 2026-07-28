"""The cumulative average says which quantity it is on a range-extender (beta #10 @gm27271, #11 @michapr).

`lifetime_eff_kwh_100km` is totalEnergy ÷ totalmileage. On a BEV those are the same kilometres and
the figure is what everyone means by "average consumption". On a range-extender part of the distance
was driven on petrol, so the electricity gets spread over kilometres it never moved the car through —
@michapr's B10 reports **12.6 kWh/100 km** on its own screen where Mate reports **8.9** over the same
1032 km, 42 % of which were fuel. Both are arithmetically right; they are not the same quantity, and
they were sitting under the same words.

The value is deliberately kept — it is a true figure, and the one that says how much electricity a
kilometre of *travelling* costs, which is what decides how often you charge. What changes is the
wording. Computing the car's number is not possible from here: the electric/fuel split of the
distance is in no cloud endpoint we have captured, which is what the v2.14.1 research probe is for.

These tests render the real template, because the fault this guards against is a wording that reads
wrong — not a value that computes wrong.
"""
import pytest

pytest.importorskip("fastapi", reason="web.main needs fastapi (absent in the minimal CI test env)")

import i18n                                                   # noqa: E402
import main                                                   # noqa: E402

CS = {"total_energy_kwh": 92.0, "total_mileage_km": 1032.0, "lifetime_eff_kwh_100km": 8.9,
      "ec_driving_kwh": None, "ec_ac_kwh": None, "ec_other_kwh": None,
      "driving_kwh": None, "parked_kwh": None}


def _render(*, is_reev, lang="en"):
    t = i18n.get_t(lang)
    tpl = main.templates.get_template("partials/cumulative_summary.html")
    return tpl.render(cs=CS, is_reev=is_reev, t=t,
                      eff_val=lambda v: v, eff_unit=lambda: "kWh/100km",
                      dist_val=lambda v, d=1: v, dist_unit=lambda: "km",
                      nice=lambda v: v)


def test_a_range_extender_does_not_call_it_average_consumption(monkeypatch):
    """The exact collision: the car's screen uses that phrase for a different quantity."""
    out = _render(is_reev=True)
    t = i18n.get_t("en")
    assert t("cum_elec_only") in out
    assert t("avg_efficiency") not in out


def test_a_fully_electric_car_is_untouched():
    """On a BEV the two quantities are the same, so nothing about this changes for anyone else."""
    out = _render(is_reev=False)
    t = i18n.get_t("en")
    assert t("avg_efficiency") in out
    assert t("cum_elec_only") not in out
    assert t("cum_elec_all_distance") not in out
    assert t("cum_reev_note") not in out


def test_the_value_is_still_shown_on_a_range_extender():
    """Withholding it would lose a true figure. The number stays; the words around it change."""
    assert "8.9" in _render(is_reev=True)


def test_the_qualifier_and_the_explanation_appear_only_on_a_range_extender():
    """The qualifying line under the value, and the note that says why the car reads higher."""
    out = _render(is_reev=True)
    t = i18n.get_t("en")
    assert t("cum_elec_all_distance") in out
    assert t("cum_reev_note") in out


@pytest.mark.parametrize("lang", ["it", "en", "de", "fr", "pl", "pt-PT"])
def test_every_language_has_all_three_strings(lang):
    """A missing key renders as the key itself — the wording fix would silently not happen."""
    t = i18n.get_t(lang)
    for key in ("cum_elec_only", "cum_elec_all_distance", "cum_reev_note"):
        assert t(key) != key, f"{key} missing in {lang}"
    out = _render(is_reev=True, lang=lang)
    assert t("cum_elec_only") in out
