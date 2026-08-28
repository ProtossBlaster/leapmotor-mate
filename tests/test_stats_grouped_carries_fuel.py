"""Statistics month/day nodes carry the FUEL side too (beta #35 follow-up, @michapr): a range-extender
month shows litres and L/100 km next to the kWh figures, and the per-day L/100 km feeds the trend chart
that sits where a BEV shows regen. The totals machinery already computes both (`_totals_seal` →
fuel_l / fuel_l_100km); get_stats_grouped simply dropped them on the way out.

Same basis as everywhere else in Mate: L/100 km over ALL the kilometres (the car's own basis), while
kWh/100 km is over the km getEC covers — two different denominators under two different words, which is
exactly why beta #35 was opened.
"""
import db_reader
import pytest


@pytest.fixture
def reev_trips(monkeypatch):
    """Two July days: one driven on the generator (fuel), one purely electric."""
    trips = [
        {"id": 1, "started_at": "2026-07-10T08:00:00+00:00", "distance_km": 100.0,
         "efficiency_kwh_100km": None, "regen_kwh": 0.5, "fuel_used_l": 8.0, "fuel_cost": 14.0,
         "cost": 0.0, "ec_kwh": None, "vehicle_id": 1},
        {"id": 2, "started_at": "2026-07-11T08:00:00+00:00", "distance_km": 50.0,
         "efficiency_kwh_100km": 16.0, "regen_kwh": 1.0, "fuel_used_l": 0.0, "fuel_cost": 0.0,
         "cost": 2.0, "ec_kwh": 8.0, "vehicle_id": 1},
    ]
    monkeypatch.setattr(db_reader, "get_trips", lambda **kw: trips)
    monkeypatch.setattr(db_reader, "_localized_trips",
                        lambda rows: [{**t, "_dt": db_reader._local_dt(t["started_at"])} for t in rows])
    monkeypatch.setattr(db_reader, "get_language", lambda: "en")
    return trips


def _july(grouped):
    return grouped[0]["months"]["2026-07"] if "2026-07" in grouped[0]["months"] \
        else list(grouped[0]["months"].values())[0]


def test_month_carries_litres_and_l_100km(reev_trips):
    july = _july(db_reader.get_stats_grouped())
    assert july["total_fuel_l"] == 8.0
    # 8 L over ALL 150 km — the car's own basis, not the fuelled km alone
    assert july["fuel_l_100km"] == pytest.approx(5.3, abs=0.05)


def test_each_day_carries_its_own_fuel_for_the_trend(reev_trips):
    days = {d["day_key"]: d for d in _july(db_reader.get_stats_grouped())["days"]}
    assert days["2026-07-10"]["total_fuel_l"] == 8.0
    assert days["2026-07-10"]["fuel_l_100km"] == pytest.approx(8.0, abs=0.05)   # 8 L / 100 km
    # the electric-only day burned nothing: no L/100 km to plot, and the chart leaves a gap
    assert days["2026-07-11"]["total_fuel_l"] == 0.0
    assert days["2026-07-11"]["fuel_l_100km"] is None


def test_the_electric_figures_are_untouched(reev_trips):
    """The fuel columns are additive: nothing about the kWh side moves (beta #35 was a
    disagreement between pages — it must not come back)."""
    july = _july(db_reader.get_stats_grouped())
    assert july["trip_count"] == 2
    assert july["total_km"] == 150.0
    assert july["avg_efficiency"] == 16.0          # getEC ÷ the km getEC covers (50), not 150
    assert july["avg_efficiency_km"] == 50.0
