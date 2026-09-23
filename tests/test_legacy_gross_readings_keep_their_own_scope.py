"""Infer legacy reading scope from automatic group pricing, without repricing history.

Unpriced groups and manual totals leave entry order ambiguous; keep their readings
independent until the owner enters a new figure on the merged card.
"""
import db as D
import db_reader as R
import pytest


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    path = str(tmp_path / "legacy.db")
    pdb = D.Database(path)
    monkeypatch.setattr(R, "DB_PATH", path)
    con = pdb._conn
    con.execute("ALTER TABLE charges DROP COLUMN gross_kwh_from")
    con.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'V','C10')")
    con.executemany(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc,"
        " energy_added_kwh, location_type) VALUES (?,1,?,?,?,?,?,'AC')",
        [(1, "2026-07-03T09:00:00+00:00", "2026-07-03T11:00:00+00:00", 20, 70, 16.0),
         (2, "2026-07-03T11:10:00+00:00", "2026-07-03T12:00:00+00:00", 70, 80, 7.0)],
    )
    con.commit()
    yield pdb
    pdb.close()


def _rows(pdb):
    return [dict(row) for row in pdb._conn.execute("SELECT * FROM charges ORDER BY id")]


def _migrate(pdb):
    before = _rows(pdb)
    D.Database(pdb._path).close()
    after = _rows(pdb)
    assert [{key: row[key] for key in before[0]} for row in after] == before
    return [row["gross_kwh_from"] for row in after]


def _assert_energy(expected):
    group, = R.get_charges()
    assert R._billed_kwh(group) == expected
    assert R.get_charge_stats()["total_kwh"] == expected
    assert R.get_charges_calendar_month(2026, 7)["total"]["kwh"] == expected
    split = R.get_ac_dc_stats()
    assert split["ac"]["kwh"] + split["dc"]["kwh"] == expected


@pytest.mark.parametrize("piece_gross,piece_cost,expected,scope", [
    pytest.param(8.0, 6.32, 28.0, 2, id="author-two-priced-readings"),
    pytest.param(8.0, None, 20.0, 1, id="superseded-reading"),
    pytest.param(8.0, 0.0, 28.0, 2, id="free-reading"),
    pytest.param(None, 5.53, 27.0, None, id="priced-battery-piece"),
    pytest.param(None, 0.0, 27.0, None, id="free-battery-piece"),
    pytest.param(None, None, 20.0, 1, id="group-reading-only"),
    pytest.param(0.0, None, 20.0, 1, id="cleared-piece-reading"),
])
def test_migration_recovers_group_scope_without_changing_costs(
    legacy, piece_gross, piece_cost, expected, scope,
):
    con = legacy._conn
    con.execute("UPDATE charges SET gross_kwh=20, cost=15.80 WHERE id=1")
    con.execute("UPDATE charges SET gross_kwh=?, cost=?, merged_into_id=1 WHERE id=2",
                (piece_gross, piece_cost))
    con.commit()
    R.set_setting("price_ac_kwh", "9.99")  # Today's tariff must not reprice history.

    assert _migrate(legacy) == [1, scope]
    _assert_energy(expected)
    stats = R.get_charge_stats()
    assert stats["total_cost"] == pytest.approx(15.80 + (piece_cost or 0))
    assert stats["avg_price"] == pytest.approx((15.80 + (piece_cost or 0)) / expected, abs=0.0005)
    assert _migrate(legacy) == [1, scope]


@pytest.mark.parametrize("group_entry,expected,cost", [(True, 20.0, 15.80), (False, 28.0, 22.12)])
def test_both_legacy_entry_orders_keep_their_energy_and_unit_price(legacy, group_entry, expected, cost):
    R.set_setting("price_ac_kwh", "0.79")
    R.set_charge_gross_kwh(2, 8.0)
    if not group_entry:
        R.set_charge_gross_kwh(1, 20.0)
    assert R.merge_charges(1, 2)["ok"]
    if group_entry:
        R.set_charge_gross_kwh(1, 20.0)

    R.set_setting("price_ac_kwh", "9.99")
    assert _migrate(legacy) == [1, 1 if group_entry else 2]
    _assert_energy(expected)
    stats = R.get_charge_stats()
    assert stats["total_cost"] == pytest.approx(cost)
    assert stats["avg_price"] == pytest.approx(0.79)


@pytest.mark.parametrize("has_manual_column", [True, False])
@pytest.mark.parametrize("parent_type,expected,scope", [("AC", 20.0, 1), ("MANUAL", 28.0, 2)])
def test_the_legacy_manual_type_is_recognized_before_cost_manual_is_added(
    legacy, has_manual_column, parent_type, expected, scope,
):
    con = legacy._conn
    if not has_manual_column:
        con.execute("ALTER TABLE charges DROP COLUMN cost_manual")
    con.execute("UPDATE charges SET gross_kwh=20, cost=15.80, location_type=? WHERE id=1",
                (parent_type,))
    con.execute("UPDATE charges SET gross_kwh=8, merged_into_id=1 WHERE id=2")
    con.commit()

    assert _migrate(legacy) == [1, scope]
    _assert_energy(expected)


@pytest.mark.parametrize("history", ["no-tariff", "manual-total", "group-entry-no-tariff"])
def test_a_null_cost_does_not_prove_that_a_reading_was_superseded(legacy, history):
    if history == "manual-total":
        R.set_setting("price_ac_kwh", "0.79")
    R.set_charge_gross_kwh(2, 8.0)
    if history != "group-entry-no-tariff":
        R.set_charge_gross_kwh(1, 20.0)
    assert R.merge_charges(1, 2)["ok"]
    if history == "group-entry-no-tariff":
        R.set_charge_gross_kwh(1, 20.0)
    if history == "manual-total":
        R.set_charge_cost(1, 22.12)

    rows = _rows(legacy)
    assert [row["gross_kwh"] for row in rows] == [20.0, 8.0]
    assert [row["cost"] for row in rows] == [22.12 if history == "manual-total" else None, None]
    assert _migrate(legacy) == [1, 2]
    # Both no-tariff histories leave identical legacy fields: preserve both readings,
    # even when a group entry actually superseded the piece's reading before the upgrade.
    _assert_energy(28.0)


def test_a_new_group_entry_establishes_scope_and_survives_restart(legacy):
    con = legacy._conn
    con.execute("UPDATE charges SET gross_kwh=20, cost=15.80 WHERE id=1")
    con.execute("UPDATE charges SET gross_kwh=8, cost=6.32, merged_into_id=1 WHERE id=2")
    con.commit()
    assert _migrate(legacy) == [1, 2]
    _assert_energy(28.0)

    R.set_setting("price_ac_kwh", "0.79")
    R.set_charge_gross_kwh(1, 20.0)
    _assert_energy(20.0)
    assert [row["cost"] for row in _rows(legacy)] == [15.80, None]
    assert _migrate(legacy) == [1, 1]
    _assert_energy(20.0)
