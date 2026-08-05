"""REEV — the trips LIST shows both energies, not one (@michapr, beta #11: "only one will be shown").

A trip where the range-extender ran has its electric efficiency blanked on purpose (finalize_trip:
once the generator refills the pack underneath, a SoC drop stops measuring anything). So the ⚡ pill
in the row has nothing to print and only the ⛽ line survives — while the car plainly also used
electricity, and the detail page has been showing exactly that figure all along.

get_trips now makes the same _reev_trip_elec call the detail page makes. It reads `ec_kwh`, a stored
column filled by the EC enrichment, so this costs a dict lookup rather than a cloud call.

⚠️ It read `ec_kwh` — the MOTOR's share — until 05/08/26, when Silvio settled it: *«la quota
guida non dovremmo mai prenderla in considerazione, sempre l'energia totale, quello che facciamo
anche per le EV»*. The share was also not the figure the money is billed on — `reev_trip_electric_cost`
draws the paid stock down by `ec_kwh` — so a card showed "1.7 kWh" over a cost worked out on 2.0
(@michapr, beta #11). These tests kept passing throughout: they name the column, and the column was
the thing that was wrong.
"""
import db as D
import db_reader
import pytest


@pytest.fixture
def reev(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    database = D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    database._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'V','C10')")
    database.set_setting("is_reev", "1")
    database._conn.commit()
    return database


def _trip(db, km, fuel_from, fuel_to, ec_kwh, eff=None, day=1):
    """One finished trip. `eff` stays NULL for a generator trip — that is what the app writes."""
    db._conn.execute(
        "INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc,"
        " fuel_start_pct, fuel_end_pct, efficiency_kwh_100km, ec_kwh)"
        " VALUES (1,?,?,?,80,70,?,?,?,?)",
        (f"2026-07-{day:02d}T08:00:00+00:00", f"2026-07-{day:02d}T09:00:00+00:00",
         km, fuel_from, fuel_to, eff, ec_kwh))
    db._conn.commit()


def _row(db, **kw):
    _trip(db, **kw)
    rows = db_reader.get_trips()
    assert len(rows) == 1
    return rows[0]


def test_a_generator_trip_carries_the_electric_figure_too(reev):
    """The whole point. 100 km, the tank dropped, and the car still pulled 12 kWh from the pack."""
    r = _row(reev, km=100.0, fuel_from=80.0, fuel_to=76.0, ec_kwh=12.0)
    assert r["engine_ran"] is True
    assert r["fuel_l_100km"] is not None          # the side that was already there
    assert r["reev_elec_kwh"] == 12.0             # …and the side that was missing
    assert r["reev_elec_kwh_100km"] == 12.0       # 12 kWh / 100 km × 100


def test_the_two_figures_are_both_present_on_the_same_row(reev):
    """The defect was never a wrong number — it was one number where there are two."""
    r = _row(reev, km=200.0, fuel_from=90.0, fuel_to=80.0, ec_kwh=20.0)
    assert r["reev_elec_kwh_100km"] is not None and r["fuel_l_100km"] is not None


def test_the_blanked_efficiency_is_what_made_the_row_look_electric_free(reev):
    """Holds the reason: the app deliberately leaves efficiency_kwh_100km NULL on these trips, so
    the row's normal ⚡ pill cannot render and the new figure is the only electric one available."""
    r = _row(reev, km=100.0, fuel_from=80.0, fuel_to=76.0, ec_kwh=12.0)
    assert r["efficiency_kwh_100km"] is None
    assert r["reev_elec_kwh_100km"] == 12.0


def test_a_trip_the_generator_never_touched_gets_no_extra_figure(reev):
    """No generator → nothing to pair, and the ordinary efficiency pill already says it all."""
    r = _row(reev, km=100.0, fuel_from=80.0, fuel_to=80.0, ec_kwh=12.0, eff=12.0)
    assert r["engine_ran"] is False
    assert r["reev_elec_kwh_100km"] is None
    assert r["efficiency_kwh_100km"] == 12.0


def test_a_generator_trip_with_no_metered_energy_yet_shows_only_the_fuel(reev):
    """ec_kwh is filled by a background enrichment, so a fresh trip has none. It must degrade to
    what we had before — never to a zero, which would read as "it used no electricity"."""
    r = _row(reev, km=100.0, fuel_from=80.0, fuel_to=76.0, ec_kwh=None)
    assert r["engine_ran"] is True
    assert r["fuel_l_100km"] is not None
    assert r["reev_elec_kwh_100km"] is None


def test_a_missing_electric_figure_leaves_a_dash_not_a_blank():
    """A range-extender always draws from the pack to move, so a row carrying petrol alone reads as
    a car that ran on fuel only — which never happens. The slot stays, with the reason in its tooltip:
    the ⏳ marker beside it expires after six hours, and after that a blank row cannot tell "not yet"
    from "never". Checked on the SERVED template, since this is a rendering decision."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "web" / "templates" / "partials" / "trip_row.html").read_text()
    block = src.split("{% if is_reev and research and trip.engine_ran %}", 1)[1].split("{% endif %}\n    <span", 1)[0]
    assert "{% else %}" in block, "no fallback: an absent electric figure would render nothing"
    assert "⚡ —" in block
    assert "reev_elec_pending" in block, "the dash must carry the reason, not just a dash"


def test_the_row_template_prints_both_lines():
    """Checked against the SERVED template: a value can be right and still never reach the screen."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "web" / "templates" / "partials" / "trip_row.html").read_text()
    block = src.split("{% if is_reev and research and trip.engine_ran %}", 1)[1].split("{% endif %}", 1)[0]
    assert "reev_elec_kwh_100km" in block
    assert "fuel_l_100km" in src
