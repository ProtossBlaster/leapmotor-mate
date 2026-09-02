"""A range-extender's electric average must not be diluted by the kilometres an engine drove — on the
STATISTICS card (@michapr, beta #41).

getEC measures the energy that LEAVES THE BATTERY. On a series hybrid the generator can send power
past the pack straight to the wheels, and getEC never sees that path. A mixed trip therefore HAS a
getEC figure — so it lands inside `ec_km`, the denominator beta #40 introduced — while an engine
moved most of it. Measured on three owners' bundles:

    trips with the generator running   4.8 (michapr)   6.5 (pdifeo)   kWh/100 km
    trips without it                  14.3            16.1

No car does 4.8. So the Statistics card divides Mate's OWN battery-only pair, `energy_kwh` over
`eff_km` — the trips carrying an efficiency, which Mate blanks for every generator trip.

⚠️ ONLY the Statistics card. v3.15.4 put that pair on every copy of the Energy card, and on the
Trips page it sat under a month strip that divides by the getEC kilometres (beta #24 → #40,
@michapr's own design: "the Trips page should focus solely on the recorded trips"): 14.1 over 252 km
above 10.1 over 395, two numbers under one word on one page (beta #41, 02/09). The Trips and Report
copies are back on the beta #40 basis; the caller says which pair it wants (`battery_only`).
A full-electric car carries an efficiency on every trip, so both bases agree there.
"""
import sqlite3
from datetime import datetime, timezone

import db as poller_db
import db_reader
import pytest


def _main():
    """`web.main` imports fastapi, which the minimal CI env does not install."""
    pytest.importorskip("fastapi", reason="the card lives in web.main")
    import main
    return main


def _build(tmp_path, monkeypatch, reev):
    """600 km on the battery at 14.0, then 400 km the generator drove.

    The generator trip carries a getEC figure (20.0 kWh left the pack) but NO efficiency — exactly
    what Mate writes for it. Cloud total for the window is 104.0 kWh, the sum of both."""
    path = str(tmp_path / ("reev.db" if reev else "bev.db"))
    poller_db.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO vehicles (id, vin) VALUES (1, 'LFZTESTVIN000001')")
    con.execute("INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km, duration_min,"
                " ec_kwh, efficiency_kwh_100km) VALUES (1, ?, ?, 600.0, 600, 84.0, 14.0)",
                ("2026-07-05T08:00:00+00:00", "2026-07-05T18:00:00+00:00"))
    con.execute("INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km, duration_min,"
                " ec_kwh, efficiency_kwh_100km) VALUES (1, ?, ?, 400.0, 400, 20.0, NULL)",
                ("2026-07-10T08:00:00+00:00", "2026-07-10T15:00:00+00:00"))
    con.commit(); con.close()
    db_reader.set_setting("is_reev", "1" if reev else "0")
    return path


def _window():
    b = int(datetime(2026, 7, 5, tzinfo=timezone.utc).timestamp())
    e = int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp())
    return b, e


def test_the_totals_report_the_battery_only_distance(tmp_path, monkeypatch):
    """`eff_km` is the distance behind `energy_kwh`; `ec_km` still counts the mixed trip."""
    _build(tmp_path, monkeypatch, reev=True)
    tot = db_reader.get_trip_totals_between(*_window())
    assert tot["eff_km"] == pytest.approx(600.0), "the kilometres the battery actually drove"
    assert tot["ec_km"] == pytest.approx(1000.0), "getEC covers the generator trip too"
    assert tot["energy_kwh"] == pytest.approx(84.0)


def test_the_statistics_card_averages_over_the_battery_kilometres(tmp_path, monkeypatch):
    """14.0, the figure the car really achieved — not 10.4, which no owner recognises."""
    _build(tmp_path, monkeypatch, reev=True)
    main = _main()
    eb = main._enrich_eb_with_trip_totals({"total_kwh": 104.0}, *_window(), battery_only=True)
    assert eb["avg_kwh100"] == pytest.approx(14.0), \
        f"got {eb.get('avg_kwh100')} — 10.4 means the generator's 400 km diluted it"
    assert eb["avg_kwh100_km"] == pytest.approx(600.0)
    assert eb["avg_kwh100_basis"] == "battery", "the template picks the wording from this"


def test_the_trips_and_report_cards_keep_the_getec_basis_on_a_range_extender(tmp_path, monkeypatch):
    """The Trips page strip divides by the getEC kilometres (beta #40); the card below it must not
    print a second figure under the same word. Same rows, no `battery_only` → 10.4 over 1000."""
    _build(tmp_path, monkeypatch, reev=True)
    main = _main()
    eb = main._enrich_eb_with_trip_totals({"total_kwh": 104.0}, *_window())
    assert eb["avg_kwh100"] == pytest.approx(10.4)
    assert eb["avg_kwh100_km"] == pytest.approx(1000.0)
    assert eb["avg_kwh100_basis"] == "getec"


def test_only_the_statistics_endpoint_asks_for_the_battery_pair():
    """Anchored to the routes: energy-since-charge (Statistics) passes the flag, energy-period
    (Trips + Report) and report-driving do not."""
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1].joinpath("web", "main.py").read_text()
    since = src.split('@app.get("/api/energy-since-charge"', 1)[1].split("\n@app.", 1)[0]
    period = src.split('@app.get("/api/energy-period"', 1)[1].split("\n@app.", 1)[0]
    driving = src.split('@app.get("/api/report-driving"', 1)[1].split("\n@app.", 1)[0]
    assert "_enrich_eb_with_trip_totals(eb, begin_ts, end_ts, battery_only=True)" in since
    assert "_enrich_eb_with_trip_totals(eb, begin_ts, end_ts)" in period
    assert "_enrich_eb_with_trip_totals(eb, begin_ts, end_ts)" in driving


def test_the_distance_driven_is_untouched(tmp_path, monkeypatch):
    """Only the average's denominator changes. The card's Distance is still every kilometre."""
    _build(tmp_path, monkeypatch, reev=True)
    main = _main()
    eb = main._enrich_eb_with_trip_totals({"total_kwh": 104.0}, *_window(), battery_only=True)
    assert eb["distance_km"] == pytest.approx(1000.0)


def test_a_full_electric_car_keeps_the_beta_40_basis(tmp_path, monkeypatch):
    """The regression that would hurt everyone else: a BEV must still divide the cloud total by
    `ec_km`. Same rows, REEV flag off → 104.0 over 1000 km."""
    _build(tmp_path, monkeypatch, reev=False)
    main = _main()
    eb = main._enrich_eb_with_trip_totals({"total_kwh": 104.0}, *_window(), battery_only=True)
    assert eb["avg_kwh100"] == pytest.approx(10.4)
    assert eb["avg_kwh100_km"] == pytest.approx(1000.0)
