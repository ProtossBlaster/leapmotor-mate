"""The cost card bills the petrol BURNED, not the petrol bought.

@michapr, beta #25, 06/08/26 — he had said the card "looks wrong" and, asked which number, pointed
at it: **27.72 €/100km** total with **24.18 €** of fuel. Measured on the trips.csv in the bundle he
attached to beta #27:

    9.60 L burned over 6 generator trips, 479 km
    18.54 €  ← what MATE ITSELF costs those trips at (`fuel_cost` per trip) = 3.87 €/100km = 1.93 €/L
   116.00 €  ← what the card charged                                       = 24.18 €/100km = 12.06 €/L

1.93 €/L is a pump price. 12.06 is not. The card summed `fuel_purchases.total_cost` — every euro of
every refuel — while ~50 of the ~60 litres he bought were still in the tank, waiting to move the car
on kilometres this window has never seen.

🔑 **Everywhere else Mate multiplies litres burned by a blended €/L** (`_fuel_wac_blend` /
`_trip_fuel_rate_fn`), written for this same tester on beta #11. So the Trips page said that petrol
cost 18.54 € while the Statistics card said 116 € — two numbers under one word, same program, same
period, same car. This does not invent a rule; it removes the one place that ignored it.

⚠️ `reev_actual_spend` keeps summing purchases and is RIGHT to: that card answers "what did you
buy", and a tank you paid for is bought whether or not you have burned it yet. Two cards, two
questions. Do not "unify" them.
"""
import pathlib

import db as PollerDB
import db_reader
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def reev(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    pdb = PollerDB.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    pdb.ensure_vehicle("LVIN0000000000001", "C10", 2025)
    db_reader.set_setting("is_reev", "1")
    db_reader.set_setting("reev_tank_l", "50")

    def trip(day, km, litres=0.0, hour=8):
        l0 = 40.0
        pdb._conn.execute(
            "INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc,"
            " fuel_start_pct, fuel_end_pct, fuel_start_l, fuel_end_l) VALUES (1,?,?,?,80,60,?,?,?,?)",
            (f"2026-07-{day:02d}T{hour:02d}:00:00+00:00", f"2026-07-{day:02d}T{hour + 2:02d}:00:00+00:00",
             km, l0 * 2, (l0 - litres) * 2, l0, l0 - litres))
        pdb._conn.commit()

    def refuel(day, litres, total_cost, before_pct=20.0):
        # ⚠️ Through the POLLER's connection, like every other helper here. The first version used
        # `db_reader._conn_rw()` while `pdb` held its own write connection to the same file: two
        # writers, "database is locked", and 26 seconds of lock timeout per test before it failed.
        pdb._conn.execute(
            "CREATE TABLE IF NOT EXISTS fuel_purchases (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " vehicle_id INTEGER, ts TEXT, liters REAL, price_per_l REAL, total_cost REAL,"
            " fuel_before_pct REAL, note TEXT)")
        pdb._conn.execute(
            "INSERT INTO fuel_purchases (vehicle_id, ts, liters, price_per_l, total_cost,"
            " fuel_before_pct) VALUES (1,?,?,?,?,?)",
            (f"2026-07-{day:02d}T07:00:00+00:00", litres, total_cost / litres, total_cost,
             before_pct))
        pdb._conn.commit()

    def charge(day, kwh, cost):
        pdb._conn.execute(
            "INSERT INTO charges (vehicle_id, started_at, ended_at, start_soc, end_soc,"
            " energy_added_kwh, cost) VALUES (1,?,?,40,80,?,?)",
            (f"2026-07-{day:02d}T20:00:00+00:00", f"2026-07-{day:02d}T22:00:00+00:00", kwh, cost))
        pdb._conn.commit()

    return trip, refuel, charge


def _card(litres):
    return db_reader.cost_per_100km(litres)


# ── his own case, to the decimal ──────────────────────────────────────────────

def test_a_full_tank_is_not_charged_to_the_kilometres_it_has_not_driven(reev):
    """60 litres bought at 1.93, 9.6 burned, 479 km. The fuel side must read what the six generator
    trips cost — not the whole purchase."""
    trip, refuel, charge = reev
    refuel(1, litres=60.0, total_cost=115.80)
    trip(10, km=164.0, litres=9.6)
    trip(11, km=315.0)
    charge(12, kwh=48.0, cost=12.11)

    card = _card(9.6)
    assert card["fuel_100km"] == pytest.approx(3.87, abs=0.15), \
        f"still billing the purchase: {card['fuel_100km']} €/100km"
    assert card["fuel_100km"] < 5, "24.18 was the defect"


def test_it_agrees_with_what_the_trips_page_says(reev):
    """The invariant, not the arithmetic: whatever the Trips page charges for that petrol, the card
    charges the same. Two numbers under one word is the defect regardless of which is right."""
    trip, refuel, charge = reev
    refuel(1, litres=60.0, total_cost=115.80)
    trip(10, km=164.0, litres=9.6)
    trip(11, km=315.0)
    charge(12, kwh=48.0, cost=12.11)

    from_trips = sum(t.get("fuel_cost") or 0 for t in db_reader.get_trips(limit=1000))
    card_total = _card(9.6)["fuel_100km"] * 479.0 / 100.0
    assert card_total == pytest.approx(from_trips, abs=0.5), \
        f"card {card_total:.2f} € vs trips {from_trips:.2f} €"


# ── and what must not move ────────────────────────────────────────────────────

def test_the_electric_side_is_untouched(reev):
    trip, refuel, charge = reev
    refuel(1, litres=60.0, total_cost=115.80)
    trip(10, km=200.0, litres=9.6)
    charge(12, kwh=48.0, cost=12.00)
    assert _card(9.6)["elec_100km"] == pytest.approx(6.0, abs=0.01)   # 12 € over 200 km


def test_a_plain_electric_car_never_reads_the_tank(reev):
    """`fuel_l_burned=None` means a car with no tank: the fuel table is not touched at all."""
    trip, refuel, _ = reev
    refuel(1, litres=60.0, total_cost=115.80)
    trip(10, km=200.0)
    card = db_reader.cost_per_100km()
    assert card is None or card["fuel_100km"] is None


def test_no_refuel_entered_still_says_so(reev):
    """The honesty flag stays: petrol was burned and nothing was typed in, so the total is a floor.
    Without a single refuel there is no €/L to multiply by either."""
    trip, _, charge = reev
    trip(10, km=200.0, litres=9.6)
    charge(12, kwh=48.0, cost=12.00)
    card = _card(9.6)
    assert card["fuel_missing"] is True
    assert not card["fuel_100km"]


def test_what_was_BOUGHT_still_sums_the_purchases(reev):
    """🔴 `reev_actual_spend` answers a different question — what you paid out — and a tank is paid
    for whether or not it is burned. It must NOT follow the card. Here so nobody unifies them."""
    trip, refuel, charge = reev
    refuel(1, litres=60.0, total_cost=115.80)
    trip(10, km=164.0, litres=9.6)
    charge(12, kwh=48.0, cost=12.11)
    spend = db_reader.reev_actual_spend()
    assert spend["cost"] == pytest.approx(115.80 + 12.11, abs=0.01)
    assert spend["litres"] == pytest.approx(60.0, abs=0.01)


def test_the_card_no_longer_sums_total_cost():
    """Read on the source: the loop that added every refuel's `total_cost` into the figure is gone.
    ⚠️ Anchored to the assignment, not to the word — `total_cost` still appears in the same function
    to COUNT the refuels entered, which is what keeps the 'nothing entered' warning working."""
    src = (ROOT / "web" / "db_reader.py").read_text()
    body = src.split("def cost_per_100km(", 1)[1].split("\ndef ", 1)[0]
    assert "fuel_cost + p[" not in body.replace('"', "'"), \
        "the card still adds the purchases into the fuel cost"
