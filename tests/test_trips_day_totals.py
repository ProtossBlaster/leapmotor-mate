"""The day drawer carries that day's own totals (#175 @ghuaywen-ai).

Not a new idea: the year/month/day accordion the calendar replaced (68ff5e7) showed distance, ♻️ regen,
cost and a weighted efficiency on every one of its three levels. The calendar kept the month line and
dropped the day one, which is what @ghuaywen-ai noticed — he reported a number that had gone missing,
not a feature he wanted invented.

Two things are worth pinning down here. Efficiency is a DISTANCE-WEIGHTED mean, so a 2 km hop can't
drag the day's figure around the way a plain average would. And the day line must come out of the same
arithmetic as the month line above it: they sit centimetres apart, and two implementations of the same
mean eventually disagree.
"""
import db_reader


def _t(km, eff=None, regen=None, cost=None, fuel_cost=None):
    """Shaped like what `get_trips` really hands over, `cost_total` included. That field is what the
    totals fold since 05/08 — `cost` alone is the ELECTRIC line, and on a range-extender folding it
    gave @michapr a day of "129 km · 8.3 L · 0.08 €" (beta #11). A helper that only sets `cost` is
    describing a trip Mate never produces."""
    parts = [c for c in (cost, fuel_cost) if c is not None]
    return {"distance_km": km, "efficiency_kwh_100km": eff, "regen_kwh": regen,
            "cost": cost, "fuel_cost": fuel_cost,
            "cost_total": round(sum(parts), 2) if parts else None}


def test_sums_distance_regen_and_cost():
    tot = db_reader.trips_totals([_t(14.0, 16.5, 0.42, 2.10), _t(6.0, 20.0, 0.18, 0.90)])
    assert tot["count"] == 2
    assert tot["km"] == 20.0
    assert tot["regen"] == 0.6
    assert tot["cost"] == 3.0


def test_efficiency_is_weighted_by_distance():
    # 100 km at 15 and 1 km at 40: the weighted mean stays near 15, a plain average would say 27.5.
    tot = db_reader.trips_totals([_t(100.0, 15.0), _t(1.0, 40.0)])
    assert tot["avg_eff"] == round((100 * 15 + 1 * 40) / 101, 1)
    assert tot["avg_eff"] < 15.3


def test_trips_without_an_efficiency_do_not_break_the_mean():
    tot = db_reader.trips_totals([_t(10.0, 18.0), _t(5.0, None), _t(0.0, 99.0)])
    assert tot["avg_eff"] == 18.0          # only the one trip that has both distance and a figure
    assert tot["km"] == 15.0               # …but its distance still counts


def test_empty_day_has_no_efficiency_and_no_error():
    tot = db_reader.trips_totals([])
    # Shape assertion on purpose: it caught the fuel keys arriving (beta #11) and would catch the
    # next addition too. Zero litres, and no L/100 km to divide into nothing.
    assert tot == {"count": 0, "km": 0.0, "regen": 0.0, "cost": 0.0, "avg_eff": None,
                   "fuel_l": 0.0, "fuel_l_100km": None,
                   "kwh_100km": None, "kwh_100km_km": None}


def test_missing_fields_are_treated_as_zero():
    tot = db_reader.trips_totals([{"distance_km": 5.0}])
    assert tot["regen"] == 0.0 and tot["cost"] == 0.0 and tot["avg_eff"] is None


def test_day_totals_match_the_month_line_they_sit_under(monkeypatch):
    """The guard against the two-implementations drift: build a month from known trips, then run the
    same trips through trips_totals and require the day node and the standalone totals to agree."""
    import datetime as dt
    trips = [
        {"_dt": dt.datetime(2026, 7, 24, 8, 10), "distance_km": 14.0,
         "efficiency_kwh_100km": 16.5, "regen_kwh": 0.42, "cost": 2.10},
        {"_dt": dt.datetime(2026, 7, 24, 16, 34), "distance_km": 6.0,
         "efficiency_kwh_100km": 20.0, "regen_kwh": 0.18, "cost": 0.90},
        {"_dt": dt.datetime(2026, 7, 23, 9, 0), "distance_km": 30.0,
         "efficiency_kwh_100km": 14.0, "regen_kwh": 1.0, "cost": 4.0},
    ]
    monkeypatch.setattr(db_reader, "get_trips", lambda **kw: trips)
    monkeypatch.setattr(db_reader, "_localized_trips", lambda ts: ts)
    cal = db_reader.get_trips_calendar_month(2026, 7)

    day24 = [t for t in trips if t["_dt"].day == 24]
    assert cal["days"][24] == db_reader.trips_totals(day24)
    # and the month line is the whole set, by the same route
    assert cal["total"] == db_reader.trips_totals(trips)
