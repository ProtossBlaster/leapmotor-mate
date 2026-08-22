import sqlite3
import pytest
import db as poller_db
import db_reader

def _setup(tmp_path, monkeypatch):
    path = str(tmp_path / "c.db")
    poller_db.Database(path)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO vehicles (id, vin) VALUES (1, 'V1')")
    con.commit()
    con.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    return path

def _seed_charges(path):
    con = sqlite3.connect(path)
    # Piece A: 10 kWh energy, started at 12:00
    # Piece B: 5 kWh energy, started at 12:30 (30 min pause)
    for cid, s, e, ss, es, kwh in (
        (1, "2026-08-12T12:00:00+00:00", "2026-08-12T12:20:00+00:00", 40.0, 50.0, 10.0),
        (2, "2026-08-12T12:22:00+00:00", "2026-08-12T12:32:00+00:00", 50.0, 55.0, 5.0),
    ):
        con.execute(
            "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc, "
            "energy_added_kwh, duration_min, charge_type, location_type, cost) "
            "VALUES (?, 1, ?, ?, ?, ?, ?, 10.0, 'AC', NULL, NULL)",
            (cid, s, e, ss, es, kwh))
    con.commit()
    con.close()

def test_merged_charges_cost_recalculation(tmp_path, monkeypatch):
    path = _setup(tmp_path, monkeypatch)
    _seed_charges(path)

    # Set up some pricing config: HOME flat rate = 0.20 EUR/kWh
    monkeypatch.setattr(db_reader, "get_charge_prices", lambda: {"price_home_kwh": 0.20})
    monkeypatch.setattr(db_reader, "get_cost_config", lambda: {"mode": "flat"})

    # Merge them: 2 into 1
    res = db_reader.merge_charges(1, 2)
    assert res["ok"] is True

    # Check that both are still unconfirmed with NULL cost
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    r1 = con.execute("SELECT * FROM charges WHERE id=1").fetchone()
    r2 = con.execute("SELECT * FROM charges WHERE id=2").fetchone()
    con.close()
    assert r1["location_type"] is None
    assert r1["cost"] is None
    assert r2["location_type"] is None
    assert r2["cost"] is None

    # Now update type of the parent to HOME
    db_reader.update_charge_type(1, "HOME")

    # Check that BOTH charges are updated to HOME and costs are calculated
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    r1 = con.execute("SELECT * FROM charges WHERE id=1").fetchone()
    r2 = con.execute("SELECT * FROM charges WHERE id=2").fetchone()
    con.close()

    assert r1["location_type"] == "HOME"
    # Cost for 1: 10 kWh * 0.20 = 2.0 EUR
    assert r1["cost"] == pytest.approx(2.0)

    assert r2["location_type"] == "HOME"
    # Cost for 2: 5 kWh * 0.20 = 1.0 EUR
    assert r2["cost"] == pytest.approx(1.0)

    # Check that displaying the merged group yields the correct total cost (3.0 EUR)
    charges = db_reader.get_charges()
    assert len(charges) == 1
    assert charges[0]["cost"] == pytest.approx(3.0)

def test_merged_charges_manual_cost(tmp_path, monkeypatch):
    path = _setup(tmp_path, monkeypatch)
    _seed_charges(path)

    db_reader.merge_charges(1, 2)

    # Update to MANUAL with a custom total cost of 4.50 EUR
    db_reader.update_charge_type(1, "MANUAL", manual_cost=4.50)

    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    r1 = con.execute("SELECT * FROM charges WHERE id=1").fetchone()
    r2 = con.execute("SELECT * FROM charges WHERE id=2").fetchone()
    con.close()

    assert r1["location_type"] == "MANUAL"
    assert r1["cost"] == pytest.approx(4.50)

    assert r2["location_type"] == "MANUAL"
    assert r2["cost"] is None

    # Total displayed cost should be 4.50 EUR
    charges = db_reader.get_charges()
    assert len(charges) == 1
    assert charges[0]["cost"] == pytest.approx(4.50)

def test_merged_charges_gross_kwh_distribution(tmp_path, monkeypatch):
    path = _setup(tmp_path, monkeypatch)
    _seed_charges(path)

    db_reader.merge_charges(1, 2)

    # Set up flat price = 0.30 EUR/kWh
    monkeypatch.setattr(db_reader, "get_charge_prices", lambda: {"price_home_kwh": 0.30})
    monkeypatch.setattr(db_reader, "get_cost_config", lambda: {"mode": "flat"})

    # We must confirm it first so set_charge_gross_kwh doesn't bail early
    db_reader.update_charge_type(1, "HOME")

    # Set gross_kwh = 30.0 kWh on the merged charge (parent is 1)
    db_reader.set_charge_gross_kwh(1, 30.0)

    # Check database:
    # Total battery kwh = 10.0 + 5.0 = 15.0 kWh
    # Proportion for 1: 10 / 15 = 2/3. So gross_kwh = 30.0 * 2/3 = 20.0 kWh.
    # Proportion for 2: 5 / 15 = 1/3. So gross_kwh = 30.0 * 1/3 = 10.0 kWh.
    # Cost for 1: 20.0 * 0.30 = 6.0 EUR
    # Cost for 2: 10.0 * 0.30 = 3.0 EUR
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    r1 = con.execute("SELECT * FROM charges WHERE id=1").fetchone()
    r2 = con.execute("SELECT * FROM charges WHERE id=2").fetchone()
    con.close()

    assert r1["gross_kwh"] == pytest.approx(20.0)
    assert r1["cost"] == pytest.approx(6.0)

    assert r2["gross_kwh"] == pytest.approx(10.0)
    assert r2["cost"] == pytest.approx(3.0)

    # Total group gross and cost
    charges = db_reader.get_charges()
    assert len(charges) == 1
    assert charges[0]["gross_kwh"] == pytest.approx(30.0)
    assert charges[0]["cost"] == pytest.approx(9.0)
