"""«Sotto quanti €/kWh conviene caricare invece di andare a benzina?» (@ebagnoli, beta #13).

His words: *«dovrebbe apparire da qualche parte il costo al KWh in Euro dell'elettricità alla
colonnina affinché la ricarica elettrica risulti conveniente rispetto alla benzina del REEV»*. He
charges at home from solar surplus, so for him the answer today is "always" — the number he needs is
for when he is out and standing in front of a column.

It is one division, but the two rates must each be measured on **their own kilometres**:

    break-even €/kWh = (€/L × L/100km with the generator running) ÷ (kWh/100km driving electric)

⚠️ `reev_total_consumption` already reports a kWh/100km and an L/100km and they CANNOT be reused:
both are divided by the WHOLE distance, which is right for "what did I spend" and wrong for "which
is cheaper". Mixing the two questions under the same two numbers is exactly the defect Silvio named
— two correct figures meaning different things. → [[feedback-two-numbers-one-word]]

And the answer has to say how much it stands on. On @ebagnoli's own history the electric side rests
on 614 km and the petrol side on **46 km — one trip**. A bare "0.64 €/kWh" would be a confident
number with half of it resting on a sample of one.
"""
from datetime import datetime, timedelta, timezone

import db as D
import db_reader
import pytest

T0 = datetime(2026, 7, 1, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
def reev(tmp_path, monkeypatch):
    path = str(tmp_path / "p.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'LFZTEST','C10')")
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('is_reev','1')")
    c.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return pdb


TANK_L = 47.5                      # a C10's nominal tank — the % and the litres must agree


def _trip(pdb, n, km, *, ec_kwh=None, fuel_l=None, engine_km=None):
    """One finished trip. `fuel_l` makes it a generator trip; `ec_kwh` alone is electric.

    ⚠️ Both the percentage and the litre columns are filled, because a real trip carries both and
    the readers pick whichever they trust. Seeding only the litres made every generator trip
    invisible to `reev_fuel_summary` — a fixture that was not a trip."""
    s = T0 + timedelta(hours=n * 3)
    e = s + timedelta(minutes=40)
    if fuel_l:
        f_pct = (100.0, round((TANK_L - fuel_l) / TANK_L * 100, 2))
        f_l = (TANK_L, TANK_L - fuel_l)
    else:
        f_pct = f_l = (100.0, 100.0)          # tank untouched → the generator sat this one out
    pdb._conn.execute(
        "INSERT INTO trips (id, vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc,"
        " ec_kwh, fuel_start_pct, fuel_end_pct, fuel_start_l, fuel_end_l)"
        " VALUES (?,1,?,?,?,?,?,?,?,?,?,?)",
        (n, s.isoformat(), e.isoformat(), km, 80, 70, ec_kwh, *f_pct, *f_l))
    pdb._conn.commit()


def _refuel(pdb, litres=30.0, price=1.75):
    db_reader.add_fuel_purchase((T0 - timedelta(days=1)).isoformat(), litres, price_per_l=price)


# ── it does not exist for a car that is not a range-extender ──────────────────

def test_a_plain_electric_car_gets_nothing(reev, monkeypatch):
    """The whole card is a REEV question: there is no petrol to compare against."""
    reev._conn.execute("UPDATE settings SET value='0' WHERE key='is_reev'")
    reev._conn.commit()
    assert db_reader.reev_breakeven_kwh_price() is None


# ── the number itself ─────────────────────────────────────────────────────────

def test_the_break_even_is_the_petrol_cost_of_the_same_hundred_kilometres(reev):
    """🔴 RED before: the function did not exist.

    15 kWh/100km electric · 5 L/100km on the generator · petrol at 1.80 €/L
    → 100 km cost 9.00 € on petrol → 9.00 ÷ 15 = **0.60 €/kWh**. Above that, burn petrol."""
    _refuel(reev, price=1.80)
    for i in range(1, 5):                                  # 4 electric trips: 400 km, 60 kWh
        _trip(reev, i, 100.0, ec_kwh=15.0)
    _trip(reev, 9, 200.0, fuel_l=10.0, engine_km=200.0)    # generator: 200 km, 10 L
    r = db_reader.reev_breakeven_kwh_price()
    assert r is not None
    assert r["elec_kwh_100km"] == pytest.approx(15.0, abs=0.1)
    assert r["fuel_l_100km"] == pytest.approx(5.0, abs=0.1)
    assert r["breakeven_kwh"] == pytest.approx(0.60, abs=0.01), r


def test_each_side_is_measured_on_its_OWN_kilometres(reev):
    """The trap this card exists to avoid. Add a long electric trip and the PETROL rate must not
    move: the generator still drinks what it drinks over the kilometres it drove. Dividing both by
    the total distance — which is what `reev_total_consumption` does, correctly, for its own
    question — would drag the fuel figure down and the break-even with it."""
    _refuel(reev, price=1.80)
    _trip(reev, 1, 100.0, ec_kwh=15.0)
    _trip(reev, 9, 200.0, fuel_l=10.0, engine_km=200.0)
    before = db_reader.reev_breakeven_kwh_price()
    for i in range(2, 8):                                  # six more electric trips
        _trip(reev, i, 100.0, ec_kwh=15.0)
    after = db_reader.reev_breakeven_kwh_price()
    assert after["fuel_l_100km"] == pytest.approx(before["fuel_l_100km"], abs=0.05), (
        f"the petrol rate moved because electric kilometres were added: "
        f"{before['fuel_l_100km']} → {after['fuel_l_100km']}")
    assert after["breakeven_kwh"] == pytest.approx(before["breakeven_kwh"], abs=0.01)


# ── it has to say what it stands on ───────────────────────────────────────────

def test_a_side_resting_on_too_few_kilometres_is_declared(reev):
    """@ebagnoli's real shape: 614 electric km against **46 petrol km — one trip**. The number is
    still computable and still shown; what must not happen is showing it as if both halves were
    equally solid."""
    _refuel(reev, price=1.80)
    for i in range(1, 7):
        _trip(reev, i, 100.0, ec_kwh=15.0)
    _trip(reev, 9, 46.0, fuel_l=2.5, engine_km=46.0)
    r = db_reader.reev_breakeven_kwh_price()
    assert r["thin"] == "fuel", r
    assert r["fuel_km"] == pytest.approx(46.0, abs=1.0)
    assert r["elec_km"] == pytest.approx(600.0, abs=1.0)


def test_both_sides_solid_is_not_flagged(reev):
    """The control: without it the test above passes on a build that flags everything."""
    _refuel(reev, price=1.80)
    for i in range(1, 7):
        _trip(reev, i, 100.0, ec_kwh=15.0)
    _trip(reev, 9, 300.0, fuel_l=15.0, engine_km=300.0)
    assert db_reader.reev_breakeven_kwh_price()["thin"] is None


# ── and it refuses rather than guessing ───────────────────────────────────────

def test_no_generator_kilometres_means_no_answer(reev):
    """A REEV that has never used its generator has nothing to compare against. Better nothing than
    a break-even built on an assumed consumption. → [[feedback-verified-vs-inferred]]"""
    _refuel(reev, price=1.80)
    for i in range(1, 7):
        _trip(reev, i, 100.0, ec_kwh=15.0)
    assert db_reader.reev_breakeven_kwh_price() is None


def test_fuel_burned_but_the_generator_drove_nowhere_means_no_answer(reev):
    """🔴 Found by a mutation that escaped, and it was a gap here rather than an inert mutation.

    A trip can burn petrol over **zero** kilometres — the generator charging the pack while the car
    stands still. `reev_fuel_summary` then answers with a dict whose `engine_l_100km` is None and
    `engine_km` is 0: not None, so a guard that only checks "did the summary come back" sails past
    it and divides by a rate that does not exist. Nothing to compare against → no answer.
    → [[mutation-testing-poisons-pycache]] (the three outcomes: weak test · mutation never arrived
    · mutation without effect — this one was the first)"""
    _refuel(reev, price=1.80)
    for i in range(1, 7):
        _trip(reev, i, 100.0, ec_kwh=15.0)
    _trip(reev, 9, 0.0, fuel_l=4.8)                 # petrol burned, no distance covered
    assert db_reader.reev_breakeven_kwh_price() is None


def test_no_petrol_price_means_no_answer(reev):
    """Never invent a pump price: without a refuel of his own there is no euro to divide."""
    for i in range(1, 7):
        _trip(reev, i, 100.0, ec_kwh=15.0)
    _trip(reev, 9, 200.0, fuel_l=10.0, engine_km=200.0)
    assert db_reader.reev_breakeven_kwh_price() is None


def test_a_generator_trip_is_not_counted_as_electric_driving(reev):
    """The electric rate must come from trips the generator sat out. On a generator trip the pack is
    being refilled underneath, so its kWh figure does not describe electric driving at all.
    → [[reev-getec-is-battery-not-traction]]"""
    _refuel(reev, price=1.80)
    for i in range(1, 7):
        _trip(reev, i, 100.0, ec_kwh=15.0)
    _trip(reev, 9, 200.0, ec_kwh=90.0, fuel_l=10.0, engine_km=200.0)   # absurd on purpose
    r = db_reader.reev_breakeven_kwh_price()
    assert r["elec_kwh_100km"] == pytest.approx(15.0, abs=0.2), (
        f"the generator trip leaked into the electric rate: {r['elec_kwh_100km']}")
