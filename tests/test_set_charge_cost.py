"""set_charge_cost(): the pencil-cost field's backend — the REAL total paid, independent of the
charge's type. Opposite convention from set_charge_gross_kwh/set_charge_solar_kwh ON PURPOSE: those
two always open EMPTY (an accidental open-and-Enter must be a no-op), this one opens PRE-FILLED
with the current effective cost, so a genuinely empty submission is a deliberate CLEAR back to the
computed default — not "leave it alone". See tests/test_charge_manual.py for the equivalent
coverage through update_charge_type directly; this file is the thin wrapper's own contract.
"""
import db as D
import db_reader


def _setup(tmp_path, monkeypatch):
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    return pdb


def _charge(pdb, cid, *, ctype="AC", cost=None, cost_manual=0, energy=10.0):
    pdb._conn.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc,"
        " energy_added_kwh, location_type, cost, cost_manual, reconstructed)"
        " VALUES (?,1,'2026-06-02T16:00:00+00:00','2026-06-02T17:00:00+00:00',40,60,"
        " ?,?,?,?,0)",
        (cid, energy, ctype, cost, cost_manual))
    pdb._conn.commit()


def _row(pdb, cid):
    return dict(pdb._conn.execute("SELECT * FROM charges WHERE id=?", (cid,)).fetchone())


def test_setting_a_value_marks_it_manual(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, ctype="HPC", cost=6.0)
    out = db_reader.set_charge_cost(1, 18.45)
    assert out["cost"] == 18.45
    assert out["cost_manual"] == 1
    assert out["location_type"] == "HPC"   # untouched — this field never touches the type


def test_clearing_it_recomputes_the_default(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    db_reader.set_setting("price_hpc_kwh", "0.79")
    _charge(pdb, 1, ctype="HPC", cost=18.45, cost_manual=1, energy=10.0)
    out = db_reader.set_charge_cost(1, None)
    assert out["cost_manual"] == 0
    assert out["cost"] == 7.9   # 10 kWh × 0.79, the normal computed price


def test_an_untyped_charge_is_left_alone(tmp_path, monkeypatch):
    """The pencil field is only offered on a confirmed charge — same rule as the gross/solar
    fields. An untyped one must not become typed by a side effect of pricing it."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, ctype=None)
    out = db_reader.set_charge_cost(1, 18.45)
    assert out["location_type"] is None
    assert out["cost"] is None


def test_a_legacy_manual_charge_is_left_alone_not_crashed(tmp_path, monkeypatch):
    """A charge stuck on the pre-cost_manual 'MANUAL' placeholder is truthy but not a real type —
    routing it into update_charge_type (which rejects anything outside CHARGE_TYPES) used to
    return {} and crash the template on charge.cost, a 500 found in review. Same "left alone"
    contract as a NULL charge, not a crash: there's no real type to compute anything against."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, ctype="MANUAL", cost=18.45, cost_manual=1)
    out = db_reader.set_charge_cost(1, 25.0)
    assert out != {}
    assert out["location_type"] == "MANUAL"
    assert out["cost"] == 18.45   # unchanged — no route to price a charge with no real type


def test_a_later_retag_preserves_the_manual_cost(tmp_path, monkeypatch):
    """End-to-end: set the pencil, then correct the badge — the price set through the pencil must
    survive a plain type change, the entire reason the two are separate fields now."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, ctype="HPC")
    db_reader.set_charge_cost(1, 18.45)
    out = db_reader.update_charge_type(1, "FAST")
    assert out["cost"] == 18.45
    assert out["cost_manual"] == 1
