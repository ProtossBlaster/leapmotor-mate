"""The cost card counts charges the way the Charges page shows them: a merged group is ONE (#366).

@marco783, B10, 01/10/2026: five charges on the Charges page, all priced, and the Statistics cost
card read "missing 1 of 6". He had merged two rows of one plug-in. A merge only writes
`merged_into_id` — the rows stay, which is what makes it reversible — and the page folds them
into one charge whose cost is the sum of the pieces that carry one. The card counted the stored
rows instead, so the merged piece without a price of its own was announced as a charge nobody
priced. The euros were always right (the pieces' costs are added either way); the count was not.
"""
import db as D
import db_reader
import pytest


@pytest.fixture
def bev(tmp_path, monkeypatch):
    path = str(tmp_path / "b.db")
    database = D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    database._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'W','B10')")
    database._conn.execute(
        "INSERT INTO trips (vehicle_id, started_at, ended_at, distance_km, start_soc, end_soc)"
        " VALUES (1,'2026-07-01T08:00:00+00:00','2026-07-06T18:00:00+00:00',200,80,40)")
    database._conn.commit()
    return database


def _charge(db, day, cost, kwh=10.0, merged_into=None, hour=10):
    cur = db._conn.execute(
        "INSERT INTO charges (vehicle_id, started_at, ended_at, start_soc, end_soc,"
        " energy_added_kwh, cost, merged_into_id) VALUES (1,?,?,20,60,?,?,?)",
        (f"2026-07-{day:02d}T{hour:02d}:00:00+00:00", f"2026-07-{day:02d}T{hour:02d}:30:00+00:00",
         kwh, cost, merged_into))
    db._conn.commit()
    return cur.lastrowid


def _five_charges_one_of_them_merged(db, child_cost=None, parent_cost=3.0):
    for day in (1, 2, 3, 4):
        _charge(db, day, 3.0)
    parent = _charge(db, 5, parent_cost, hour=10)
    _charge(db, 5, child_cost, hour=11, merged_into=parent)   # the piece the page folds away


def test_a_merged_piece_without_its_own_price_is_not_a_missing_charge(bev):
    """Marco's card: the page shows five priced charges, so nothing is missing."""
    _five_charges_one_of_them_merged(bev)
    card = db_reader.cost_per_100km()
    assert (card["priced_charges"], card["total_charges"]) == (5, 5)
    assert card["partial"] is False


def test_the_euros_are_the_same_either_way(bev):
    """Only the count changes: every piece's cost was always added, and still is."""
    _five_charges_one_of_them_merged(bev, child_cost=1.0)
    card = db_reader.cost_per_100km()
    assert card["total_charges"] == 5
    assert card["elec_100km"] == round((3.0 * 4 + 3.0 + 1.0) * 100 / 200, 2)


def test_a_merged_charge_nobody_priced_is_still_one_missing_charge(bev):
    """Absent is not zero: a group whose pieces all lack a price is one charge without a price."""
    _five_charges_one_of_them_merged(bev, child_cost=None, parent_cost=None)
    card = db_reader.cost_per_100km()
    assert (card["priced_charges"], card["total_charges"]) == (4, 5)
    assert card["partial"] is True


def test_the_energy_count_folds_a_merged_charge_too(tmp_path, monkeypatch):
    """The same card's "kWh missing for n of tot" counts charges as well: a merged piece the car
    measured nothing for must not be one more charge without energy than the page shows."""
    path = str(tmp_path / "e.db")
    database = D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    database._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,'W','B10')")
    database.set_setting("battery_capacity_kwh", "50.0")
    database._conn.commit()
    for day in (1, 2):
        _charge(database, day, 3.0)
    parent = _charge(database, 3, 3.0)
    _charge(database, 3, None, kwh=None, hour=11, merged_into=parent)
    basis = {"bal": ("2026-07-01T00:00:00+00:00", "2026-07-31T00:00:00+00:00", 40.0, 50.0)}
    _, counted, missing = db_reader._energy_balance_kwh(db_reader._get(), 200.0, basis)
    assert (counted, missing) == (3, 0)
