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
