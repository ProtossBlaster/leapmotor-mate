"""The pieces of a group confirmed BEFORE the fix have to be repaired, or the fix reaches nobody.

`update_charge_type` now prices every piece — but only when someone confirms a charge, and a charge
already confirmed is never touched again. Measured on a container: the corrected code, opened on a
database written by the old one, still shows 2,00 € for a 15 kWh home charge. The fix would ship and
change nothing for the people who already have the defect, which is the shape that bit us in August
with the responsiveness badge.

The repair takes the rate the group was PRICED AT — the parent's own cost ÷ its own billed energy —
rather than today's tariff. A confirmed cost is frozen ('new charges only'), so re-pricing the
parent with the prices in effect now would rewrite history to fix an omission. The parent is never
written at all: only the children get the type and the missing share, at the rate their group
already carries.

Groups whose price belongs to the GROUP and already sits on the parent — a MANUAL total, a typed
gross_kwh (#222) — get the type on their children and no cost, exactly as a fresh confirm would.
"""
import sqlite3

import pytest

import db as poller_db
import db_reader


def _old_style_group(tmp_path, monkeypatch, *, parent_type="HOME", parent_cost=2.00,
                     gross=None, manual_entry=0):
    """A group as the OLD code left it: parent typed and priced, child untouched."""
    path = str(tmp_path / "c.db")
    poller_db.Database(path)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO vehicles (id, vin) VALUES (1, 'V1')")
    con.execute("INSERT INTO charges (id,vehicle_id,started_at,ended_at,start_soc,end_soc,"
                "energy_added_kwh,duration_min,charge_type,location_type,cost,gross_kwh,"
                "merged_into_id) VALUES (1,1,'2026-08-12T12:00:00+00:00','2026-08-12T12:20:00+00:00',"
                "40,50,10.0,20,'AC',?,?,?,NULL)", (parent_type, parent_cost, gross))
    con.execute("INSERT INTO charges (id,vehicle_id,started_at,ended_at,start_soc,end_soc,"
                "energy_added_kwh,duration_min,charge_type,location_type,cost,gross_kwh,"
                "merged_into_id) VALUES (2,1,'2026-08-12T12:22:00+00:00','2026-08-12T12:32:00+00:00',"
                "50,55,5.0,10,'AC',NULL,NULL,NULL,1)")
    con.commit()
    con.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    # Today's tariff is DELIBERATELY different from the one the group was priced at (0.20).
    monkeypatch.setattr(db_reader, "get_charge_prices", lambda: {"price_home_kwh": 0.50})
    monkeypatch.setattr(db_reader, "get_cost_config", lambda: {"mode": "flat"})
    return path


def _rows(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    out = {r["id"]: dict(r) for r in con.execute("SELECT * FROM charges")}
    con.close()
    return out


def test_the_missing_piece_gets_the_share_it_never_had(tmp_path, monkeypatch):
    path = _old_style_group(tmp_path, monkeypatch)
    assert db_reader.repair_merged_charge_pieces() == 1
    after = _rows(path)
    assert after[2]["location_type"] == "HOME"
    assert after[2]["cost"] == pytest.approx(1.00), "5 kWh at the group's own 0.20 €/kWh"


def test_it_uses_the_rate_the_group_was_priced_at_not_todays(tmp_path, monkeypatch):
    """Today's home price is 0.50. The group was confirmed at 0.20 and must stay there."""
    path = _old_style_group(tmp_path, monkeypatch)
    db_reader.repair_merged_charge_pieces()
    after = _rows(path)
    assert after[1]["cost"] == pytest.approx(2.00), "the parent's frozen cost was rewritten"
    assert after[2]["cost"] == pytest.approx(1.00), "the child was priced at today's tariff"


def test_a_manual_total_stays_the_whole_groups_price(tmp_path, monkeypatch):
    path = _old_style_group(tmp_path, monkeypatch, parent_type="MANUAL", parent_cost=9.90)
    db_reader.repair_merged_charge_pieces()
    after = _rows(path)
    assert after[2]["location_type"] == "MANUAL"
    assert after[2]["cost"] is None, "a hand-typed total must not be multiplied by the pieces"


def test_a_typed_meter_reading_stays_the_whole_groups_price(tmp_path, monkeypatch):
    path = _old_style_group(tmp_path, monkeypatch, parent_type="AC", parent_cost=13.50, gross=30.0)
    db_reader.repair_merged_charge_pieces()
    after = _rows(path)
    assert after[2]["location_type"] == "AC"
    assert after[2]["cost"] is None


def test_running_it_twice_changes_nothing(tmp_path, monkeypatch):
    path = _old_style_group(tmp_path, monkeypatch)
    assert db_reader.repair_merged_charge_pieces() == 1
    before = _rows(path)
    assert db_reader.repair_merged_charge_pieces() == 0, "the second pass must find nothing"
    assert _rows(path) == before


def test_an_unconfirmed_group_is_left_alone(tmp_path, monkeypatch):
    """Nothing to inherit: the parent carries no type and no rate."""
    path = _old_style_group(tmp_path, monkeypatch, parent_type=None, parent_cost=None)
    assert db_reader.repair_merged_charge_pieces() == 0
    assert _rows(path)[2]["location_type"] is None


def test_a_charge_that_was_never_merged_is_left_alone(tmp_path, monkeypatch):
    path = str(tmp_path / "s.db")
    poller_db.Database(path)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO vehicles (id, vin) VALUES (1, 'V1')")
    con.execute("INSERT INTO charges (id,vehicle_id,started_at,ended_at,energy_added_kwh,"
                "start_soc,end_soc,location_type,cost) VALUES "
                "(1,1,'2026-08-12T12:00:00+00:00','2026-08-12T12:20:00+00:00',10.0,40,50,'HOME',2.0)")
    con.commit()
    con.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    assert db_reader.repair_merged_charge_pieces() == 0


def test_a_table_without_the_merge_column_is_not_an_error(tmp_path, monkeypatch):
    """Same rule as everywhere here: the poller owns the migration."""
    path = str(tmp_path / "min.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE charges (id INTEGER PRIMARY KEY, vehicle_id INTEGER, cost REAL)")
    con.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    con.commit()
    con.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    assert db_reader.repair_merged_charge_pieces() == 0
