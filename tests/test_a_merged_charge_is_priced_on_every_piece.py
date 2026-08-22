"""Confirming a merged charge must price ALL of it, not just the row you clicked.

Found by **@damde** (PR #258): `update_charge_type` writes the parent only, so the child pieces stay
untyped and unpriced — and the Charges page sums the pieces, counting nothing for them. A 15 kWh
home charge split 10 + 5 shows two thirds of its cost. Measured across every type: HOME with and
without a wallbox, AC, DC and HPC all lose exactly the share of energy sitting in the children; only
a hand-typed MANUAL total survives, because that number lives on the parent and nobody divides it.

The fix takes his finding and keeps ONE pricing rule — `update_charge_type` applied to each piece —
rather than a second implementation of it beside the first. Three shapes, in this order:

  * a MANUAL total is the GROUP's price → it stays on the parent, children carry no cost;
  * a typed `gross_kwh` (#222) is the GROUP's meter reading → same: it prices the whole group once,
    on the parent, children carry no cost. The typed number is NEVER rewritten or split;
  * otherwise every piece is priced on its own energy, exactly as an unmerged charge would be.

The last three tests are guards, not new behaviour: they hold what `unmerge_charges` promises —
*"Nothing was ever overwritten, so they come back exactly as they were"* — and the migration guard
`_charges_have_gross` exists for, an OperationalError that was a 500 on Silvio's own instance hours
after v3.6.6 shipped.
"""
import sqlite3

import pytest

import db as poller_db
import db_reader


def _two_pieces(tmp_path, monkeypatch, *, meter=(None, None), gross=None, child_type=None,
                child_cost=None):
    """A charge the car split in two: 10 kWh, a 2-minute pause, then 5 kWh."""
    path = str(tmp_path / "c.db")
    poller_db.Database(path)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO vehicles (id, vin) VALUES (1, 'V1')")
    con.execute("INSERT INTO charges (id,vehicle_id,started_at,ended_at,start_soc,end_soc,"
                "energy_added_kwh,ac_energy_kwh,duration_min,charge_type,location_type,cost,gross_kwh)"
                " VALUES (1,1,'2026-08-12T12:00:00+00:00','2026-08-12T12:20:00+00:00',40,50,10.0,?,"
                "10.0,'AC',NULL,NULL,?)", (meter[0], gross))
    con.execute("INSERT INTO charges (id,vehicle_id,started_at,ended_at,start_soc,end_soc,"
                "energy_added_kwh,ac_energy_kwh,duration_min,charge_type,location_type,cost,gross_kwh)"
                " VALUES (2,1,'2026-08-12T12:22:00+00:00','2026-08-12T12:32:00+00:00',50,55,5.0,?,"
                "10.0,'AC',?,?,NULL)", (meter[1], child_type, child_cost))
    con.commit()
    con.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setattr(db_reader, "get_charge_prices", lambda: {
        "price_home_kwh": 0.20, "price_ac_kwh": 0.45, "price_fast_kwh": 0.60, "price_hpc_kwh": 0.79})
    monkeypatch.setattr(db_reader, "get_cost_config", lambda: {"mode": "flat"})
    return path


def _rows(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    out = {r["id"]: dict(r) for r in con.execute("SELECT * FROM charges ORDER BY id")}
    con.close()
    return out


def _group_cost():
    return [c for c in db_reader.get_charges(limit=50) if c.get("is_merged")][0]["cost"]


@pytest.mark.parametrize("kind,price", [("HOME", 0.20), ("AC", 0.45), ("FAST", 0.60), ("HPC", 0.79)])
def test_every_piece_of_the_group_is_priced(tmp_path, monkeypatch, kind, price):
    """15 kWh at the type's rate — not the parent's 10."""
    _two_pieces(tmp_path, monkeypatch)
    assert db_reader.merge_charges(1, 2)["ok"]
    db_reader.update_charge_type(1, kind)
    assert _group_cost() == pytest.approx(15.0 * price)


def test_a_wallbox_reading_on_each_piece_is_billed_in_full(tmp_path, monkeypatch):
    """HOME bills the meter, and each piece carries its own delta: 12 + 6 kWh, not 12."""
    _two_pieces(tmp_path, monkeypatch, meter=(12.0, 6.0))
    assert db_reader.merge_charges(1, 2)["ok"]
    db_reader.update_charge_type(1, "HOME")
    assert _group_cost() == pytest.approx(18.0 * 0.20)


def test_a_typed_meter_reading_prices_the_group_once(tmp_path, monkeypatch):
    """#222: the number you type on a group is the GROUP's reading. It prices the group once —
    a child priced on its own energy on top of it would bill that energy twice."""
    path = _two_pieces(tmp_path, monkeypatch, gross=30.0)
    assert db_reader.merge_charges(1, 2)["ok"]
    db_reader.update_charge_type(1, "AC")
    assert _group_cost() == pytest.approx(30.0 * 0.45)
    assert _rows(path)[2]["cost"] is None, "the child must not add a second price"


def test_the_typed_number_is_never_split_or_rewritten(tmp_path, monkeypatch):
    """You typed 30. It stays 30, on the row you typed it on — so unmerging gives it back whole."""
    path = _two_pieces(tmp_path, monkeypatch, gross=30.0)
    assert db_reader.merge_charges(1, 2)["ok"]
    db_reader.update_charge_type(1, "AC")
    after = _rows(path)
    assert after[1]["gross_kwh"] == pytest.approx(30.0)
    assert after[2]["gross_kwh"] is None


def test_a_manual_total_is_not_multiplied_by_the_pieces(tmp_path, monkeypatch):
    """A hand-typed price is what the whole session cost."""
    path = _two_pieces(tmp_path, monkeypatch)
    assert db_reader.merge_charges(1, 2)["ok"]
    db_reader.update_charge_type(1, "MANUAL", manual_cost=9.90)
    assert _group_cost() == pytest.approx(9.90)
    assert _rows(path)[2]["cost"] is None


def test_merging_into_an_unconfirmed_parent_leaves_a_confirmed_child_alone(tmp_path, monkeypatch):
    """GUARD. You price a charge, then notice it was split and join it to the piece before it —
    which you never confirmed. Merging is a marker, not a re-write: what you priced stays priced."""
    path = _two_pieces(tmp_path, monkeypatch, child_type="HOME", child_cost=1.00)
    assert db_reader.merge_charges(1, 2)["ok"]
    child = _rows(path)[2]
    assert child["location_type"] == "HOME" and child["cost"] == pytest.approx(1.00)


def test_a_charge_that_was_never_merged_keeps_its_typed_kwh(tmp_path, monkeypatch):
    """GUARD. Re-tagging must not wipe a column it is not changing (#222 on a lone charge)."""
    path = _two_pieces(tmp_path, monkeypatch, gross=30.0)
    db_reader.update_charge_type(1, "MANUAL", manual_cost=9.90)
    assert _rows(path)[1]["gross_kwh"] == pytest.approx(30.0)


def test_a_table_without_the_gross_column_still_confirms(tmp_path, monkeypatch):
    """GUARD. The poller owns the migration; the web serves the same file and never alters it.
    Naming gross_kwh unguarded is an OperationalError — a 500 on the Charges page."""
    path = str(tmp_path / "min.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE charges (id INTEGER PRIMARY KEY, vehicle_id INTEGER, started_at TEXT,"
                " ended_at TEXT, start_soc REAL, end_soc REAL, energy_added_kwh REAL,"
                " ac_energy_kwh REAL, duration_min REAL, charge_type TEXT, location_type TEXT,"
                " cost REAL, is_free INTEGER DEFAULT 0, merged_into_id INTEGER)")
    con.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("CREATE TABLE vehicles (id INTEGER PRIMARY KEY, vin TEXT, car_type TEXT)")
    con.execute("INSERT INTO vehicles VALUES (1,'V1','C10')")
    con.execute("INSERT INTO charges (id,vehicle_id,started_at,ended_at,energy_added_kwh,start_soc,"
                "end_soc) VALUES (1,1,'2026-08-12T12:00:00+00:00','2026-08-12T12:20:00+00:00',10.0,40,50)")
    con.commit()
    con.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setattr(db_reader, "get_charge_prices", lambda: {"price_home_kwh": 0.20})
    monkeypatch.setattr(db_reader, "get_cost_config", lambda: {"mode": "flat"})
    db_reader.update_charge_type(1, "MANUAL", manual_cost=9.90)   # must not raise


def test_marking_a_merged_home_charge_free_zeroes_the_whole_group(tmp_path, monkeypatch):
    """#120 on a group. The 🆓 mark writes the row you clicked — which is the parent. Once the
    children carry a cost of their own (which is the whole point of this file), a mark that stops at
    the parent leaves the group costing the children's share instead of nothing."""
    _two_pieces(tmp_path, monkeypatch)
    assert db_reader.merge_charges(1, 2)["ok"]
    db_reader.update_charge_type(1, "HOME")
    db_reader.set_charge_free(1, True)
    assert _group_cost() == pytest.approx(0.0), "a free charge cannot cost anything"


def test_unmarking_free_gives_the_whole_group_its_price_back(tmp_path, monkeypatch):
    _two_pieces(tmp_path, monkeypatch)
    assert db_reader.merge_charges(1, 2)["ok"]
    db_reader.update_charge_type(1, "HOME")
    db_reader.set_charge_free(1, True)
    db_reader.set_charge_free(1, False)
    assert _group_cost() == pytest.approx(15.0 * 0.20)
