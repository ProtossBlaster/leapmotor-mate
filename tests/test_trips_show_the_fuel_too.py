"""The petrol half, where a range-extender's driving is summed up (@michapr, BetaTester #11).

He had the litres on each individual trip and nothing at all where those trips are added together:
*"missing the data still here: at the top of calendar and at the top of the trips"* — twice, a day
apart. The Charges page had grown its delivered/battery pair; Trips still answered in kilowatt-hours
alone, on a car that also burns petrol.

Both surfaces are fed from a place that already exists, deliberately:

  · the month strip, the day drawer header and `trips_totals` all fold trips through the same three
    helpers, so the fuel is added once and appears in all three;
  · the page hero reads `reev_fuel_summary()`, the one function that totals litres — a second sum
    here would be the third copy of a rule that has already cost one release.
"""
import json
import pathlib

import db as D
import db_reader
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
MONTH = (ROOT / "web" / "templates" / "partials" / "trips_calendar_month.html").read_text()
TRIPS = (ROOT / "web" / "templates" / "trips.html").read_text()
MAIN = (ROOT / "web" / "main.py").read_text()
LOCALES = sorted((ROOT / "web" / "locales").glob("*.json"))


@pytest.fixture
def reev(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    pdb._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'V','B10')")
    pdb.set_setting("is_reev", "1")
    pdb._conn.commit()
    return pdb


def _trip(pdb, tid, day, km, *, p0=None, p1=None, l0=None, l1=None, eff=None):
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc,"
        " efficiency_kwh_100km, fuel_start_pct, fuel_end_pct, fuel_start_l, fuel_end_l)"
        " VALUES (?,1,?,?,?,80,70,?,?,?,?,?)",
        (tid, f"2026-07-{day:02d}T08:00:00+00:00", f"2026-07-{day:02d}T09:00:00+00:00",
         km, eff, p0, p1, l0, l1))
    pdb._conn.commit()


# ── the totals themselves ─────────────────────────────────────────────────────

def test_the_month_adds_up_the_litres(reev):
    _trip(reev, 1, 3, 120.0, p0=50.0, p1=48.0, l0=25.000, l1=24.000)
    _trip(reev, 2, 9, 80.0, p0=48.0, p1=47.4, l0=24.000, l1=23.700)
    t = db_reader.get_trips_calendar_month(2026, 7)["total"]
    assert t["fuel_l"] == pytest.approx(1.3, abs=0.01)


def test_the_litres_per_100km_use_all_the_kilometres(reev):
    """Same basis as the car's own display, and as every other L/100 km in Mate since beta #23 —
    not the kilometres the generator happened to run."""
    _trip(reev, 1, 3, 200.0, p0=50.0, p1=48.0, l0=25.000, l1=24.000)
    t = db_reader.get_trips_calendar_month(2026, 7)["total"]
    assert t["km"] == 200.0 and t["fuel_l_100km"] == 0.5


def test_an_all_electric_month_reports_no_fuel(reev):
    """A REEV fortnight driven on the battery must not produce a zero-litre badge."""
    _trip(reev, 1, 3, 120.0, p0=50.0, p1=50.0, l0=25.0, l1=25.0, eff=16.0)
    t = db_reader.get_trips_calendar_month(2026, 7)["total"]
    assert not t["fuel_l"] and t["fuel_l_100km"] is None


def test_the_day_header_and_the_month_line_cannot_disagree(reev):
    """They sit centimetres apart and are folded by the same three helpers on purpose."""
    _trip(reev, 1, 3, 120.0, p0=50.0, p1=48.0, l0=25.000, l1=24.000)
    _trip(reev, 2, 3, 40.0, p0=48.0, p1=47.6, l0=24.000, l1=23.800)
    month = db_reader.get_trips_calendar_month(2026, 7)
    day = db_reader.trips_totals(db_reader.get_trips_calendar_day(2026, 7, 3))
    assert day["fuel_l"] == month["days"][3]["fuel_l"] == month["total"]["fuel_l"]


def test_a_bev_is_untouched(tmp_path, monkeypatch):
    path = str(tmp_path / "b.db")
    pdb = D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    pdb._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'W','B10')")
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc,"
        " efficiency_kwh_100km) VALUES (1,1,'2026-07-03T08:00:00+00:00','2026-07-03T09:00:00+00:00',"
        "100,80,70,16.0)")
    pdb._conn.commit()
    t = db_reader.get_trips_calendar_month(2026, 7)["total"]
    assert t["fuel_l"] == 0.0 and t["fuel_l_100km"] is None and t["avg_eff"] == 16.0


def test_an_empty_set_divides_by_nothing(reev):
    t = db_reader.trips_totals([])
    assert t["fuel_l"] == 0.0 and t["fuel_l_100km"] is None


# ── and where it shows ────────────────────────────────────────────────────────

def test_the_month_strip_shows_it_beside_the_electric_figure():
    assert "total.fuel_l" in MONTH and "total.fuel_l_100km" in MONTH
    assert "{% if is_reev and research and total.fuel_l %}" in MONTH, \
        "gated like every other REEV surface, and silent with no fuel"


def test_the_hero_shows_it_where_the_regen_tile_is_hidden():
    """On a range-extender the regen tile is already suppressed, which left the slot free."""
    assert "reev_summary.total_l" in TRIPS
    assert "{% if is_reev and research and reev_summary and reev_summary.total_l %}" in TRIPS
    i, j = TRIPS.index("reev_summary.total_l"), TRIPS.index("{% if not is_reev %}")
    assert i < j, "the fuel tile must come before the regen tile it stands in for"


def test_the_hero_is_given_the_one_function_that_totals_litres():
    """Not a second SUM in the route: that would be the third copy of a rule that already cost a
    release (beta #23)."""
    body = MAIN.split('@app.get("/trips", response_class=HTMLResponse)', 1)[1].split("\n@app.", 1)[0]
    assert "reev_summary=db_reader.reev_fuel_summary()" in body
    assert "fuel_start_pct" not in body, "the route must not work the litres out for itself"


@pytest.mark.parametrize("path", LOCALES, ids=lambda p: p.stem)
def test_the_words_exist_in_every_language(path):
    d = json.loads(path.read_text())["translations"]
    for key in ("trips_fuel_hint", "report_fuel_burned"):
        assert d.get(key), f"{path.stem} is missing {key}"


# ── and the THIRD place a day's trips are added up ────────────────────────────

def test_the_day_header_shows_it_too():
    """The month strip and the page header got the fuel; this one was missed, and @michapr reported
    beta #11 a third time to say so. The totals already carried the litres — they simply were not
    printed here. All three read the same three helpers, so the only thing that can be missing is
    the printing."""
    day = (ROOT / "web" / "templates" / "partials" / "trips_calendar_day_content.html").read_text()
    assert "day_totals.fuel_l" in day
    assert "{% if is_reev and research and day_totals.fuel_l %}" in day


def test_all_three_places_print_the_same_two_numbers():
    """One rule, three surfaces, and they sit within a screen of each other."""
    day = (ROOT / "web" / "templates" / "partials" / "trips_calendar_day_content.html").read_text()
    for tpl, var in ((MONTH, "total"), (day, "day_totals")):
        assert f"{var}.fuel_l | nice" in tpl and f"{var}.fuel_l_100km | nice" in tpl


# ── and the tile beside it, on the same kilometres (beta #24) ─────────────────

def test_a_range_extender_hero_divides_both_tiles_by_the_same_distance(reev):
    """@michapr, beta #24: *«For REEV users would be more interesting the kWh usage / 100km because
    they see also the fuel/100km in next column.»*

    He is describing the defect of the week wearing a fourth face. `summary.avg_eff` is the car's
    MEASURED efficiency, and Mate stores none for a generator trip — so that tile silently covers
    only the battery-driven part of the window while the litres beside it cover all of it. Two
    figures ending in "per 100 km", one screen-inch apart, dividing by different distances.

    Here 300 km are driven, 100 of them on the generator: the measured average covers 200 km, the
    all-kilometres pair covers 300. Whatever the tile shows, it has to be the second one."""
    _trip(reev, 1, 3, 200.0, p0=50.0, p1=50.0, eff=15.0)          # battery only: has an efficiency
    _trip(reev, 2, 9, 100.0, p0=50.0, p1=40.0, l0=25.0, l1=20.0)  # generator: no efficiency at all
    summary = db_reader.get_trips_summary()
    total = db_reader.reev_total_consumption()
    assert summary["avg_eff"] == 15.0, "the measured average still covers 200 km only"
    assert total["total_km"] == 300.0
    assert total["kwh_100km"] != summary["avg_eff"], \
        "if these agreed the fixture could not tell the two denominators apart"


def test_the_hero_prefers_the_all_kilometres_figure_on_a_range_extender():
    """⚠️ Anchored to the Jinja tags, not to the variable names: the first version of this searched
    for `summary.avg_eff` and found it in the comment that explains the branch, four hundred
    characters above the code. Third time in one day — a string in a source file is also in its
    prose (see the `data-holds-selection` test, 04/08)."""
    hero = TRIPS.split('<div class="hero-ico">⚡</div>', 1)[1].split('<div class="hero-label"', 1)[0]
    assert ("{% set eff_all = reev_total.kwh_100km "
            "if (is_reev and research and reev_total) else None %}") in hero
    assert "{% if eff_all %}" in hero
    assert "{% elif summary.avg_eff %}" in hero, "a BEV must still get the measured efficiency"
    assert hero.index("{% if eff_all %}") < hero.index("{% elif summary.avg_eff %}")


def test_a_bev_never_reaches_that_branch():
    """`reev_total` is None for a BEV in the route, and the template gate names is_reev anyway —
    belt and braces, because this tile is on the page every owner opens first."""
    body = MAIN.split('@app.get("/trips", response_class=HTMLResponse)', 1)[1].split("\n@app.", 1)[0]
    assert 'reev_total=(db_reader.reev_total_consumption()' in body
    assert 'if db_reader.get_setting("is_reev", "0") == "1" else None)' in body


# ── the same denominator, in the OTHER two places (beta #11, 05/08) ───────────

def test_the_month_strip_divides_both_figures_by_the_same_kilometres(reev):
    """@michapr again, two hours after the Trips header was fixed: his July strip read
    **416 km · 14.2 kWh/100km · 9.6 L · 2.3 L/100km**, and he asked whether the 14.2 was right for a
    range-extender. It was not. `avg_eff` is the mean over the kilometres that HAVE an efficiency —
    Mate stores none for a generator trip — while the litres beside it are over all of them.

    I fixed that in the Trips header (beta #24) and left this one and the day drawer. Same defect,
    two more copies, found by the tester in two hours.

    Here 300 km, 100 of them on the generator: the measured mean covers 200 km, the pair covers 300."""
    _trip(reev, 1, 3, 200.0, p0=50.0, p1=50.0, eff=15.0)
    _trip(reev, 2, 9, 100.0, p0=50.0, p1=40.0, l0=25.0, l1=20.0)
    t = db_reader.get_trips_calendar_month(2026, 7)["total"]
    assert t["km"] == 300.0
    assert t["avg_eff"] == 15.0, "the measured mean still covers its 200 km"
    # The VALUE, not just "not None": dividing the same energy by the 200 km that carry an
    # efficiency instead of all 300 is exactly the defect, and it survives a `is not None` check.
    assert t["kwh_100km"] == pytest.approx(
        0.20 * db_reader.get_battery_capacity_kwh() / 300 * 100, abs=0.1)
    assert t["kwh_100km"] != t["avg_eff"], \
        "if these agreed the fixture could not tell the two denominators apart"


def test_a_trip_the_generator_refilled_is_not_dropped(reev):
    """On a range-extender the generator can hand the pack more than the motor took, so a trip can
    end FULLER than it started. That energy arrived and was paid for — in the litres. Summed signed
    and floored once at the end, like reev_total_consumption; skipping those trips would count the
    refill nowhere and the drain in full, and the strip would disagree with the Statistics card."""
    reev._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc,"
        " fuel_start_pct, fuel_end_pct) VALUES (1,1,'2026-07-03T08:00:00+00:00',"
        "'2026-07-03T09:00:00+00:00',100,40,60,50.0,40.0)")          # +20 %: the generator won
    reev._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc)"
        " VALUES (2,1,'2026-07-09T08:00:00+00:00','2026-07-09T09:00:00+00:00',100,60,40)")
    reev._conn.commit()
    t = db_reader.get_trips_calendar_month(2026, 7)["total"]
    assert t["kwh_100km"] is None, \
        "−20 % and +20 % cancel: nothing net came out of the pack, so there is nothing to report"


def test_a_pack_the_generator_left_fuller_reports_no_electric_figure(reev):
    """The same thing past zero: over the month the generator handed the pack MORE than the motor
    drew. A negative consumption is not a number to print — the fortnight was driven on petrol, and
    the litres beside it say so. Same floor as reev_total_consumption on the Statistics page."""
    reev._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc,"
        " fuel_start_pct, fuel_end_pct) VALUES (1,1,'2026-07-03T08:00:00+00:00',"
        "'2026-07-03T09:00:00+00:00',200,30,70,60.0,40.0)")          # +40 %, on petrol
    reev._conn.commit()
    t = db_reader.get_trips_calendar_month(2026, 7)["total"]
    assert t["kwh_100km"] is None
    assert t["fuel_l"] > 0, "the petrol that did the work is still counted"


def test_the_all_kilometres_figure_uses_the_same_distance_as_the_litres(reev):
    """The invariant that matters: whatever the two numbers are, they are per the SAME 300 km."""
    _trip(reev, 1, 3, 200.0, p0=50.0, p1=50.0, eff=15.0)
    _trip(reev, 2, 9, 100.0, p0=50.0, p1=40.0, l0=25.0, l1=20.0)
    t = db_reader.get_trips_calendar_month(2026, 7)["total"]
    assert t["fuel_l"] == pytest.approx(5.0, abs=0.05)
    assert t["fuel_l_100km"] == pytest.approx(5.0 / 300 * 100, abs=0.05)
    # Derived from the fixture rather than written by hand: `_trip` hard-codes 80 → 70 % on EVERY
    # trip (the p0/p1 arguments are the TANK), so two trips drop 20 % of whatever pack this model
    # defaults to. The first version of this line asserted a number I had worked out for a 50 kWh
    # pack the fixture never sets — and the code was right, my expectation was not.
    expected = 0.20 * db_reader.get_battery_capacity_kwh() / 300 * 100
    assert t["kwh_100km"] == pytest.approx(expected, abs=0.1)
    assert t["kwh_100km"] != t["fuel_l_100km"], "two different quantities, one shared distance"


def test_a_bev_month_keeps_the_measured_mean(reev):
    """No fuel anywhere: the strip goes on showing the efficiency the car measured, unchanged."""
    _trip(reev, 1, 3, 200.0, p0=50.0, p1=50.0, eff=15.0)
    t = db_reader.get_trips_calendar_month(2026, 7)["total"]
    assert t["avg_eff"] == 15.0


def test_both_strips_prefer_the_all_kilometres_figure_on_a_range_extender():
    """Anchored to the Jinja tags: the words are also in the comments that explain them."""
    day = (ROOT / "web" / "templates" / "partials" / "trips_calendar_day_content.html").read_text()
    for tpl, var in ((MONTH, "total"), (day, "day_totals")):
        assert ("{%% set eff_all = %s.kwh_100km if (is_reev and research) else None %%}" % var) in tpl
        assert "{% if eff_all %}" in tpl
        assert ("{%% elif %s.avg_eff %%}" % var) in tpl
