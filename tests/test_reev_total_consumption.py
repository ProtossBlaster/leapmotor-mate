"""REEV — what the driving cost, over every trip (db_reader.reev_total_consumption).

Mate blanks the efficiency figure on a trip where the range-extender ran, which is right for
"how efficient was this" and leaves "what did it cost me" unanswered on exactly the long trips
that cost the most. This fills that in: net battery drop (grid energy you bought) beside the
litres burned (fuel you bought). Proposed by @michapr; the shape of these tests follows the
things that actually went wrong when it was first run over a real range-extender history.
"""
import pathlib

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
    database.set_setting("battery_capacity_kwh", "28.4")
    database._conn.commit()
    return database


def _trip(db, km, soc_from, soc_to, fuel_from=None, fuel_to=None, day=1):
    db._conn.execute(
        "INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc,"
        " fuel_start_pct, fuel_end_pct) VALUES (1,?,?,?,?,?,?,?)",
        (f"2026-07-{day:02d}T08:00:00+00:00", f"2026-07-{day:02d}T09:00:00+00:00",
         km, soc_from, soc_to, fuel_from, fuel_to))
    db._conn.commit()


def test_nothing_to_report_before_there_is_anything_to_divide_by(reev):
    assert db_reader.reev_total_consumption() is None


def test_a_plain_electric_trip_is_the_battery_drop(reev):
    _trip(reev, 100.0, 80.0, 40.0)          # 40% of 28.4 kWh = 11.36
    out = db_reader.reev_total_consumption()
    assert out["total_kwh"] == 11.4
    assert out["kwh_100km"] == 11.4
    assert out["total_fuel_l"] == 0.0


def test_a_generator_trip_counts_too_instead_of_being_skipped(reev):
    """The whole point. Mate stores no efficiency for this trip; the cost still happened."""
    _trip(reev, 200.0, 50.0, 45.0, fuel_from=60.0, fuel_to=40.0)   # 20% of 50 L = 10 L
    out = db_reader.reev_total_consumption()
    assert out["total_fuel_l"] == 10.0
    assert out["total_kwh"] == 1.4
    assert out["total_km"] == 200.0


def test_a_refuel_is_a_purchase_not_a_negative_consumption(reev):
    """Found by running this over a real range-extender history: one 6% → 76% fill-up turned
    22 litres genuinely burned into MINUS 23. A tank only goes up at a pump, so a trip that
    ends fuller than it started must not be subtracted — the same guard reev_fuel_summary
    already applies. Without it this test reports a negative total."""
    _trip(reev, 300.0, 60.0, 20.0, fuel_from=80.0, fuel_to=40.0, day=1)   # burned 20 L
    _trip(reev, 10.0, 30.0, 28.0, fuel_from=6.0, fuel_to=76.0, day=2)     # refuelled
    out = db_reader.reev_total_consumption()
    assert out["total_fuel_l"] == 20.0
    assert out["fuel_l_100km"] > 0


def test_a_generator_that_overfills_the_pack_never_reports_negative_electricity(reev):
    """Over a period the range-extender can hand the pack more than the driving took out. That
    stored energy was paid for in petrol and is already billed in the litres beside it, so the
    electric side floors at zero rather than showing a negative kWh/100km, which reads as a bug."""
    _trip(reev, 150.0, 20.0, 70.0, fuel_from=90.0, fuel_to=60.0)
    out = db_reader.reev_total_consumption()
    assert out["total_kwh"] == 0.0
    assert out["kwh_100km"] == 0.0
    assert out["total_fuel_l"] == 15.0


def test_trips_with_no_fuel_reading_at_all_still_contribute_their_kilometres(reev):
    """A BEV-shaped trip row (fuel columns NULL) must not wipe the fuel total to NULL."""
    _trip(reev, 50.0, 90.0, 80.0, day=1)
    _trip(reev, 50.0, 80.0, 78.0, fuel_from=50.0, fuel_to=40.0, day=2)
    out = db_reader.reev_total_consumption()
    assert out["total_km"] == 100.0
    assert out["total_fuel_l"] == 5.0


def test_it_is_scoped_to_the_current_vehicle(reev, monkeypatch):
    reev._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (2,'W','C10')")
    reev._conn.execute(
        "INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc)"
        " VALUES (2,'2026-07-05T08:00:00+00:00','2026-07-05T09:00:00+00:00',999.0,90.0,10.0)")
    _trip(reev, 100.0, 80.0, 40.0)
    reev._conn.commit()
    monkeypatch.setattr(db_reader, "_current_vehicle_id", lambda: 1)
    assert db_reader.reev_total_consumption()["total_km"] == 100.0




# ── beside it: what was actually BOUGHT (db_reader.reev_actual_spend) ─────────────
#
# The card above derives both sides from percentages against a nominal pack and a nominal tank.
# On a lifetime total Mate can do better than derive: every charge it recorded carries its own
# energy and cost, every refuel the owner entered carries litres off a receipt.


def _charge(db, kwh, cost=None, ac=None, home=False, day=1):
    db._conn.execute(
        "INSERT INTO charges (vehicle_id, started_at, ended_at, energy_added_kwh, ac_energy_kwh,"
        " location_type, cost) VALUES (1,?,?,?,?,?,?)",
        (f"2026-07-{day:02d}T20:00:00+00:00", f"2026-07-{day:02d}T23:00:00+00:00",
         kwh, ac, "HOME" if home else "PUBLIC", cost))
    db._conn.commit()


def _refuel(db, litres, price=1.8, day=1):
    db_reader._ensure_fuel_purchases(db._conn)
    db._conn.execute(
        "INSERT INTO fuel_purchases (vehicle_id, ts, liters, price_per_l, total_cost)"
        " VALUES (1,?,?,?,?)",
        (f"2026-07-{day:02d}T12:00:00+00:00", litres, price, round(litres * price, 2)))
    db._conn.commit()


def test_nothing_bought_yet_reports_nothing(reev):
    assert db_reader.reev_actual_spend() is None


def test_it_bills_the_wall_not_the_battery(reev):
    """The whole reason this card exists beside the derived one. A home charge with a wallbox
    reading is billed on the AC kWh the meter counted, not the DC kWh that reached the pack —
    the 10-15% lost in conversion is energy you paid for (the answer to #134). Taking the DC
    figure here would understate every electricity bill, always in the same direction."""
    _charge(reev, kwh=10.0, ac=11.5, home=True, cost=2.3)
    assert db_reader.reev_actual_spend()["kwh"] == 11.5


def test_a_public_charge_has_no_wallbox_reading_so_it_uses_what_reached_the_pack(reev):
    _charge(reev, kwh=30.0, ac=None, home=False, cost=18.0)
    assert db_reader.reev_actual_spend()["kwh"] == 30.0


def test_refuels_come_from_the_receipt_not_from_the_tank_gauge(reev):
    _charge(reev, kwh=10.0, cost=2.0)
    _refuel(reev, litres=41.3, price=1.75)
    out = db_reader.reev_actual_spend()
    assert out["litres"] == 41.3
    assert out["cost"] == round(2.0 + 41.3 * 1.75, 2)


def test_a_total_missing_every_refuel_says_so(reev):
    """Charges arrive by themselves; refuels only exist if somebody typed them in. A total that
    silently omits all the petrol is not "what you spent", it is "what Mate was told about", and
    the card has to be able to say which one it is showing."""
    _charge(reev, kwh=10.0, cost=2.0)
    assert db_reader.reev_actual_spend()["has_fuel_entries"] is False
    _refuel(reev, litres=20.0)
    assert db_reader.reev_actual_spend()["has_fuel_entries"] is True


def test_it_is_scoped_to_the_current_vehicle(reev, monkeypatch):
    reev._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (2,'W','C10')")
    reev._conn.execute(
        "INSERT INTO charges (vehicle_id, started_at, ended_at, energy_added_kwh, cost)"
        " VALUES (2,'2026-07-05T20:00:00+00:00','2026-07-05T23:00:00+00:00',999.0,500.0)")
    _charge(reev, kwh=10.0, cost=2.0)
    monkeypatch.setattr(db_reader, "_current_vehicle_id", lambda: 1)
    assert db_reader.reev_actual_spend()["kwh"] == 10.0


def test_both_cards_are_gated_on_a_range_extender():
    """BEV owners must never see a range-extender card — the standing rule for every REEV
    feature. The gate is in the route, so a BEV never even runs either query."""
    main = (pathlib.Path(__file__).resolve().parent.parent / "web" / "main.py").read_text()
    block = main.split('_reev = db_reader.get_setting("is_reev"', 1)[1].split("\n\n", 1)[0]
    assert block.count("if _reev else None") == 2


def test_the_page_never_calls_either_of_them_a_consumption():
    """Two efficiency cards sit directly above these, and a smaller number under the same word
    is how a correct figure gets reported as a bug. Checked against the SERVED strings, in every
    language, not against the template."""
    import json

    root = pathlib.Path(__file__).resolve().parent.parent
    for loc in (root / "web" / "locales").glob("*.json"):
        d = json.load(open(loc))["translations"]
        for key in ("stats_reev_gauges_label", "stats_reev_spend_label"):
            v = d[key].lower()
            assert not any(w in v for w in ("consum", "verbrauch", "conso", "zużyci")), \
                f"{loc.name}/{key}: {d[key]}"
