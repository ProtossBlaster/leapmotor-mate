"""Search results carry a total, so a custom period can be read off them (discussion #263, @joeyoong).

His electricity is billed 22nd→21st, not by calendar month, and asked for the calendar to follow that
cycle. The date filters that select such a period already exist on both search forms — what was
missing is the SUM: the calendar's month strip totals its sessions, the search results listed cards and
totalled nothing, so any period other than a calendar month had to be added up by hand.

Same figures and same source as the month strips: `_billed_kwh` for the delivered side on charges,
`_totals_*` on trips — a total that disagreed with the calendar's would be worse than none.
"""
import db_reader
import pytest


def test_charges_search_total_sums_the_matches():
    charges = [
        {"id": 1, "energy_added_kwh": 10.0, "wallbox_kwh": 12.0, "cost": 3.0},
        {"id": 2, "energy_added_kwh": 20.0, "wallbox_kwh": 22.0, "cost": 5.0},
    ]
    t = db_reader.search_results_total_charges(charges)
    assert t["count"] == 2
    assert t["battery_kwh"] == 30.0                      # what reached the battery
    assert t["kwh"] == sum(db_reader._billed_kwh(c) for c in charges)   # the delivered side
    assert t["cost"] == 8.0 and t["has_cost"] is True


def test_charges_total_without_any_cost_says_so():
    """A period where nothing carries a price must not print a confident 0.00 — same rule the
    month strip follows (`has_cost`)."""
    t = db_reader.search_results_total_charges([{"id": 1, "energy_added_kwh": 5.0, "cost": None}])
    assert t["count"] == 1 and t["has_cost"] is False


def test_trips_search_total_sums_km_and_cost():
    trips = [
        {"id": 1, "distance_km": 100.0, "efficiency_kwh_100km": 16.0, "ec_kwh": 16.0,
         "regen_kwh": 1.0, "cost": 4.0, "fuel_cost": 0.0, "fuel_used_l": 0.0},
        {"id": 2, "distance_km": 50.0, "efficiency_kwh_100km": None, "ec_kwh": None,
         "regen_kwh": 0.0, "cost": 0.0, "fuel_cost": 7.0, "fuel_used_l": 4.0},
    ]
    t = db_reader.search_results_total_trips(trips)
    assert t["count"] == 2
    assert t["km"] == 150.0
    assert t["cost"] == 11.0        # electricity + fuel, like the calendar's day and month lines
    assert t["fuel_l"] == 4.0


def test_empty_search_totals_are_empty_not_zeroed():
    assert db_reader.search_results_total_charges([])["count"] == 0
    assert db_reader.search_results_total_trips([])["count"] == 0


@pytest.mark.parametrize("tpl,needle", [
    ("charges_search_results.html", "search_total"),
    ("trips_search_results.html", "search_total"),
])
def test_both_result_templates_render_the_total(tpl, needle):
    import pathlib
    src = (pathlib.Path(db_reader.__file__).resolve().parent / "templates" / "partials" / tpl).read_text()
    assert needle in src, f"{tpl} does not render a total"
