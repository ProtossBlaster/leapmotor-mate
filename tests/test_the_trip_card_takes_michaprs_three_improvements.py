"""Three things @michapr built into his own copy of the trip card, taken into ours — beta #31.

He attached a working `trip_detail.html` on 13/09/26 and asked what we thought of it. Most of what
it does is the electric-vs-generator split, which is measured and cannot be shown honestly. Three of
its ideas introduce NO new number and are simply better, so they come across:

  1. the trip's running cost per distance, next to the total it is derived from;
  2. the secondary readings folded into a native disclosure instead of always-open rows.

His third — the electricity and the fuel as two COLUMNS instead of two stacked blocks — was built,
measured on the rendered page and reverted on 13/09: this page is `lg:grid-cols-3` and the card sits
in the first column, and with our 13–24px figures and a second split inside area 3 it did not fit
(**87px per column at a 1024px viewport, 148px at 1600px**, ~70px for "5.4 L/100km").
📍 It was TAKEN on 18/09/2026 in his own compact form (beta D #31, approved by Silvio), after
measuring again on the rendered page: 73px per box at a 1024px window, enough for his 10–16px type
with one figure per line — see test_the_trip_summary_is_in_boxes_for_every_car. The last test below
still holds the part that stands: no viewport breakpoint around the pair.

Rendered, not grepped: a source-level check would pass on a template that prints
``{'name': 'Euro'…}`` or resolves ``cost100_val`` to nothing.
→ [[feedback-a-green-test-can-assert-the-bug]] · [[mate-web-ui-gotchas]] ·
  [[feedback-a-layout-question-is-not-answered-by-measuring-harder]]
"""
import pathlib
import re

import jinja2

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "web" / "templates"


class _Quiet(jinja2.Undefined):
    """The page drags in the whole chrome — map, charts, a dozen globals. They resolve to nothing
    rather than being stubbed one by one.

    ⚠️ It cannot hide what is under test: every assertion below is on a VALUE that has to be drawn
    (a formatted cost, a `<details open>`, a class list), so anything that failed to resolve comes
    back empty and fails as loudly as a wrong number would."""
    def __call__(self, *a, **k):
        return self

    def __getattr__(self, name):
        return self

    def __getitem__(self, key):
        return self

    def __str__(self):
        return ""


class _Request:
    headers = {"x-ingress-path": ""}


def _trip(**over):
    """A range-extender trip that burned petrol: the only shape in which all three changes are
    visible at once (the fuel section exists, so the column rule has two occupants)."""
    t = {"id": 7, "started_at": "2026-09-01T07:10:00+02:00", "ended_at": "2026-09-01T08:20:00+02:00",
         "distance_km": 97.0, "duration_min": 70.0, "start_soc": 50.0, "end_soc": 23.0,
         "ec_kwh": 4.8, "ec_stable": 1, "ec_driving": 4.0, "ec_ac": 0.5, "ec_other": 0.3,
         "efficiency_kwh_100km": None, "cost": 1.34, "fuel_cost": 11.14, "cost_total": 12.48,
         "cost_per_kwh": 0.279, "fuel_used_l": 5.28, "fuel_l_100km": 5.4, "engine_ran": True,
         "engine_km": 62.0, "fuel_start_pct": 30.8, "fuel_end_pct": 20.3, "reconstructed": 0,
         "positions": [], "start_odometer_km": 12000.0, "end_odometer_km": 12097.0,
         "avg_speed_kmh": 83.0, "max_speed_kmh": 130.0, "elevation_gain_m": None,
         "elevation_loss_m": None, "outside_temp_start_c": None, "outside_temp_end_c": None,
         "elevation_profile_available": False, "ec_pending": False, "paid_kwh": None,
         "free_kwh": None, "fuel_price_per_l": 1.829,
         # the plain-electric branch of the energy tile, taken when is_reev is off
         "battery_net_kwh": 4.8, "energy_kwh": 4.8, "regen_kwh": 0.9}
    t.update(over)
    return t


def _env():
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)), autoescape=True,
                             undefined=_Quiet)
    env.filters["money"] = lambda v: "—" if v is None else f"{float(v):.2f} €"
    env.filters["price3"] = lambda v: "—" if v is None else f"{float(v):.3f}"
    env.filters["dec"] = lambda v, n=1: "—" if v is None else f"{float(v):.{n}f}"
    env.filters["nice"] = lambda v: "—" if v is None else f"{float(v):g}"
    env.filters["dist"] = lambda v, n=1: "—" if v is None else f"{float(v):.{n}f} km"
    env.filters["speed"] = lambda v, n=0: "—" if v is None else f"{float(v):.{n}f} km/h"
    env.filters["elev"] = lambda v, n=0: "—" if v is None else f"{float(v):.{n}f} m"
    env.filters["temp"] = lambda v, n=1: "—" if v is None else f"{float(v):.{n}f} °C"
    env.filters["localdate"] = lambda v: "1 Sep 2026"
    env.globals["dist_unit"] = lambda: "km"
    env.globals["dist_val"] = lambda v, n=1: None if v is None else round(float(v), n)
    env.globals["dist100_unit"] = lambda: "100 km"
    env.globals["cost100_val"] = lambda v: v          # metric: the figure passes through
    env.globals["eff_unit"] = lambda: "kWh/100km"
    env.globals["eff_val"] = lambda v, n=1: v
    env.globals["fmt_dur"] = lambda v: "1h 10m"
    return env


def _render(**ctx):
    base = dict(trip=_trip(), t=lambda k, **kw: k, request=_Request(), page="trips",
                version="test", demo=False, is_reev=True, research=False,
                currency={"name": "Euro", "symbol": "€", "pos": "after", "dec": 2},
                prev_trip_id=None, next_trip_id=None)
    base.update(ctx)
    return _env().get_template("trip_detail.html").render(**base)


# ── 1 · the running cost ──────────────────────────────────────────────────────────────────────
def test_the_card_prints_what_the_trip_cost_per_distance():
    """12,48 € over 97 km is 12,87 € per 100 km. Without it the card carries a total that cannot be
    compared with any other trip's, which is the figure @michapr put beside it."""
    html = _render()
    assert "12.87 €/100 km" in html, "the running cost per distance is not on the card"


def test_the_running_cost_uses_the_same_denominator_as_statistics():
    """Statistics says "/100 km" through dist100_unit(), so an imperial reader gets "/100 mi".
    Spelling it "€/km" here would give the same page two ways of saying one thing, and would stay
    metric for a reader whose every other distance is in miles."""
    env = _env()
    env.globals["dist100_unit"] = lambda: "100 mi"
    env.globals["cost100_val"] = lambda v: v * 1.609344
    html = env.get_template("trip_detail.html").render(
        trip=_trip(), t=lambda k, **kw: k, request=_Request(), page="trips", version="test",
        demo=False, is_reev=True, research=False,
        currency={"name": "Euro", "symbol": "€", "pos": "after", "dec": 2},
        prev_trip_id=None, next_trip_id=None)
    assert "/100 mi" in html, "the running cost ignores the reader's distance unit"
    assert "20.71 €/100 mi" in html


def test_a_trip_with_no_distance_prints_no_running_cost():
    """A reconstructed trip can close with 0 km. The division has to be gated, not merely tidy:
    ungated it raises inside the template and takes the whole page down, which is the failure a
    reader sees as a blank screen rather than a missing line."""
    html = _render(trip=_trip(distance_km=0))
    assert "/100 km" not in html


# ── 2 · the secondary readings fold ───────────────────────────────────────────────────────────
def test_the_secondary_readings_are_a_native_disclosure():
    html = _render()
    m = re.search(r"<details[^>]*>", html)
    assert m, "the secondary readings are not foldable"
    assert "card" in m.group(0), "the disclosure lost the card styling around it"


def test_the_fold_ships_open_so_nothing_is_taken_away():
    """🔴 The point of the fold is that it is OFFERED. Every reading inside has been visible on this
    page since it existed; shipping it closed would read as data removed — the complaint this card
    is answering, not one to create."""
    m = re.search(r"<details[^>]*>", _render())
    assert m, "the secondary readings are not foldable"
    assert "open" in m.group(0), "the fold hides readings that used to be visible"


def test_the_folded_rows_keep_their_spacing_and_their_content():
    """The rows were direct children of a `space-y-3` card; nesting them under <details> makes them
    one child, so the spacing has to move inward with them or the list renders flush."""
    html = _render()
    body = html[html.index("<details"):]
    assert "space-y-3" in body[:body.index("start_soc")], "the rows lost the spacing of the card"
    for key in ("start_soc", "end_soc", "start_odometer", "avg_speed", "gps_points"):
        assert key in body, f"{key} fell out of the card when it was folded"


# ── 3 · the one that was measured and NOT taken ───────────────────────────────────────────────
def test_the_two_energy_areas_are_not_put_side_by_side_at_a_viewport_breakpoint():
    """The card is the first column of an `lg:grid-cols-3` page, so a VIEWPORT rule says nothing
    about the room a box inside it has: the page is at its widest exactly where the column is at its
    narrowest (73px per box at a 1024px window, measured 18/09/2026). The pair is side by side at
    every width since then, sized for that column — which is why no breakpoint belongs around it.
    On 13/09 the same pair behind a breakpoint, with our larger type, measured 87px per column at
    1024px and ~70px for "5.4 L/100km", and was reverted.

    The assertion is deliberately narrow — a VIEWPORT breakpoint (`md:`/`lg:`/`xl:` + grid-cols-2)
    around these two areas. A future version driven by the CARD's own width (a container query),
    which is the only thing that could make this fit, is not blocked by it."""
    html = _render()
    start = html.index("trip_area_electric")
    end = html.index("trip_area_fuel")
    between = html[start - 400:end]
    assert not re.search(r"(md|lg|xl|2xl):grid-cols-2", between), \
        "the two energy areas are behind a viewport breakpoint again — measure the column first"


def test_a_car_with_no_tank_still_renders():
    """On a BEV area 3 does not exist. The card must not leave an unbalanced div behind it — the
    failure mode that makes the rest of the page disappear."""
    html = _render(is_reev=False, trip=_trip(fuel_used_l=None, engine_ran=False, engine_km=None))
    # `<details class=`, not `<details`: base.html carries the word in a script comment, and a bare
    # count would have been measuring that instead of the card.
    assert html.count('<details class=') == 1
    assert "gps_points" in html, "the page stops rendering partway on a car with no tank"
