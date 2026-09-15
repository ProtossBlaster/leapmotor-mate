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


def test_an_untyped_charge_can_still_be_priced_by_hand(tmp_path, monkeypatch):
    """The field's own docstring promise — independent of the charge's type — means it has to
    actually work before a type is picked, not just avoid crashing: the template offers the pencil
    UNCONDITIONALLY (unlike gross/solar, which are gated on `location_type`). A first pass at the
    500 fix (see git log) made this a silent no-op instead — 200 OK, the typed figure discarded,
    no message — which is what was found wrong in review (PR #284)."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, ctype=None)
    out = db_reader.set_charge_cost(1, 18.45)
    assert out["location_type"] is None      # still not typed — this field never types a charge
    assert out["cost"] == 18.45
    assert out["cost_manual"] == 1


def test_clearing_an_untyped_charges_price_just_wipes_it(tmp_path, monkeypatch):
    """No real type means no computed default to fall back to — "clear" here can only mean
    "nothing typed", not "recompute"."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, ctype=None, cost=18.45, cost_manual=1)
    out = db_reader.set_charge_cost(1, None)
    assert out["cost"] is None
    assert out["cost_manual"] == 0


def test_a_legacy_manual_charge_can_have_its_typed_price_corrected(tmp_path, monkeypatch):
    """A charge stuck on the pre-cost_manual 'MANUAL' placeholder is truthy but not a real type —
    routing it into update_charge_type (which rejects anything outside CHARGE_TYPES) used to
    return {} and crash the template on charge.cost, a 500 found in review. The fix isn't to leave
    it alone (that just swapped the crash for a silent no-op, still found wrong in review): the
    owner can still correct the one thing they know — what they actually paid — before the type
    itself is ever sorted out."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, ctype="MANUAL", cost=18.45, cost_manual=1)
    out = db_reader.set_charge_cost(1, 25.0)
    assert out != {}
    assert out["location_type"] == "MANUAL"   # still not a real type — this field never sets one
    assert out["cost"] == 25.0
    assert out["cost_manual"] == 1


def test_a_later_retag_preserves_the_manual_cost(tmp_path, monkeypatch):
    """End-to-end: set the pencil, then correct the badge — the price set through the pencil must
    survive a plain type change, the entire reason the two are separate fields now."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, ctype="HPC")
    db_reader.set_charge_cost(1, 18.45)
    out = db_reader.update_charge_type(1, "FAST")
    assert out["cost"] == 18.45
    assert out["cost_manual"] == 1
