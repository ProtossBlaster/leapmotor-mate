"""A month calendar for refuels, the twin of the charges one (beta #14 @gm27271, seconded by @michapr).

Refuels already had a page and a list; what they didn't have was the view everyone actually uses to
find something — "which days did I fill up, and how much did it cost". This is deliberately the same
view over a different table rather than a second way of laying out a month.

One real difference from charges: a refuel has no duration and no end, so there is nothing to group —
one row is one stop at the pump. And, like the charges twin, a month where nobody priced anything must
not look like a month that cost zero, which is what `has_cost` is for.
"""
import datetime as dt

import db_reader


def _p(ts, liters, cost=None, ppl=None):
    return {"id": 1, "ts": ts, "liters": liters, "price_per_l": ppl,
            "total_cost": cost, "fuel_before_pct": None, "note": None}


def _rows(monkeypatch, rows):
    monkeypatch.setattr(db_reader, "list_fuel_purchases", lambda limit=200: rows)
    # Localisation is the renderer's job and has its own tests; here the stored stamps are UTC and
    # the display zone is UTC too, so a date in means the same date out.
    monkeypatch.setattr(db_reader, "_local_tz", lambda: dt.timezone.utc)


def test_days_carry_their_own_litres_and_cost(monkeypatch):
    _rows(monkeypatch, [
        _p("2026-07-03T10:00:00+00:00", 30.0, cost=54.0),
        _p("2026-07-03T18:00:00+00:00", 10.0, cost=18.0),
        _p("2026-07-19T09:00:00+00:00", 25.0, cost=45.0),
    ])
    cal = db_reader.get_fuel_calendar_month(2026, 7)
    assert cal["days"][3] == {"count": 2, "liters": 40.0, "cost": 72.0, "has_cost": True}
    assert cal["days"][19]["count"] == 1
    assert cal["total"] == {"count": 3, "liters": 65.0, "cost": 117.0, "has_cost": True}


def test_other_months_are_left_out(monkeypatch):
    _rows(monkeypatch, [
        _p("2026-06-30T23:00:00+00:00", 40.0, cost=70.0),
        _p("2026-07-01T01:00:00+00:00", 20.0, cost=35.0),
        _p("2026-08-01T01:00:00+00:00", 15.0, cost=26.0),
    ])
    cal = db_reader.get_fuel_calendar_month(2026, 7)
    assert list(cal["days"]) == [1]
    assert cal["total"]["count"] == 1


def test_an_unpriced_month_is_not_a_free_one(monkeypatch):
    """has_cost exists so the template can stay silent instead of printing a confident 0."""
    _rows(monkeypatch, [_p("2026-07-05T10:00:00+00:00", 30.0)])
    cal = db_reader.get_fuel_calendar_month(2026, 7)
    assert cal["total"]["liters"] == 30.0
    assert cal["total"]["cost"] == 0.0 and cal["total"]["has_cost"] is False


def test_an_empty_month_has_no_days(monkeypatch):
    _rows(monkeypatch, [])
    cal = db_reader.get_fuel_calendar_month(2026, 7)
    assert cal["days"] == {} and cal["total"]["count"] == 0


def test_the_day_drawer_returns_only_that_day(monkeypatch):
    _rows(monkeypatch, [
        _p("2026-07-03T10:00:00+00:00", 30.0, cost=54.0),
        _p("2026-07-04T10:00:00+00:00", 12.0, cost=21.0),
    ])
    day = db_reader.get_fuel_calendar_day(2026, 7, 3)
    assert len(day) == 1 and day[0]["liters"] == 30.0


def test_a_row_with_no_timestamp_is_skipped_not_crashed(monkeypatch):
    _rows(monkeypatch, [_p(None, 30.0, cost=54.0), _p("2026-07-05T10:00:00+00:00", 10.0)])
    cal = db_reader.get_fuel_calendar_month(2026, 7)
    assert cal["total"]["count"] == 1
    assert db_reader.get_fuel_calendar_day(2026, 7, 5)[0]["liters"] == 10.0


def test_both_calendar_routes_sit_behind_the_same_gate_as_the_page():
    """Fuel data is withheld from a BEV and from any non-research build — the page redirects and the
    write endpoints refuse. A read route that skipped that check would hand out the whole refuel
    history anyway, which is how a gate gets defeated: not by removing it, but by adding a door
    beside it. I built exactly that door and caught it by curling a public build."""
    import pathlib
    import re
    main_py = (pathlib.Path(__file__).resolve().parent.parent / "web" / "main.py").read_text()
    for route in ("fuel_calendar", "fuel_calendar_day", "fuel_page", "fuel_add", "fuel_delete"):
        m = re.search(rf"async def {route}\(.*?(?=\n@app\.)", main_py, re.S)
        assert m, f"{route} is gone — renamed?"
        assert "_fuel_blocked()" in m.group(0), f"{route} serves fuel data without the gate"
