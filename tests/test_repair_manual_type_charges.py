"""repair_manual_type_charges(): the one-time backfill for every charge still stuck on the old
location_type='MANUAL' placeholder. The poller's own schema migration already flags every such row
cost_manual=1 (see poller/schema.py) — this function's only job is location_type itself, reclassified
from whatever telemetry survives (max_power_kw for a real charge, the charge_type AC/DC column for a
hand-typed one), and it must never touch cost.
"""
import db as D
import db_reader


def _setup(tmp_path, monkeypatch):
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    return pdb


def _row(pdb, cid, *, manual_entry=0, max_power_kw=None, charge_type="AC", cost=9.90,
         location_type="MANUAL"):
    pdb._conn.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc,"
        " energy_added_kwh, location_type, cost, cost_manual, manual_entry, charge_type,"
        " max_power_kw, reconstructed)"
        " VALUES (?,1,'2026-06-02T16:00:00+00:00','2026-06-02T17:00:00+00:00',40,60,10.0,"
        " ?,?,1,?,?,?,0)",
        (cid, location_type, cost, manual_entry, charge_type, max_power_kw))
    pdb._conn.commit()


def _get(pdb, cid):
    return dict(pdb._conn.execute("SELECT * FROM charges WHERE id=?", (cid,)).fetchone())


def test_a_measured_charge_is_reclassified_from_its_peak_power(tmp_path, monkeypatch):
    """Power can only ever tell AC from DC — never DC from HPC, since max_power_kw is the car's own
    charging curve, not the charger's rating. A charge measured well above the DC floor still comes
    back FAST, never HPC — that split has always been, and stays, a human judgement call on the badge."""
    pdb = _setup(tmp_path, monkeypatch)
    _row(pdb, 1, max_power_kw=7.4)    # ≤ 11 kW default charge_dc_min_kw
    _row(pdb, 2, max_power_kw=22.0)   # above the threshold
    _row(pdb, 3, max_power_kw=120.0)  # well above the threshold — still just FAST, never HPC
    assert db_reader.repair_manual_type_charges() == 3
    assert _get(pdb, 1)["location_type"] == "AC"
    assert _get(pdb, 2)["location_type"] == "FAST"
    assert _get(pdb, 3)["location_type"] == "FAST"


def test_cost_is_never_touched(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _row(pdb, 1, max_power_kw=120.0, cost=42.42)
    db_reader.repair_manual_type_charges()
    assert _get(pdb, 1)["cost"] == 42.42
    assert _get(pdb, 1)["cost_manual"] == 1


def test_a_hand_typed_charge_is_reclassified_from_its_own_ac_dc_column(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _row(pdb, 1, manual_entry=1, charge_type="DC", max_power_kw=None)
    _row(pdb, 2, manual_entry=1, charge_type="AC", max_power_kw=None)
    db_reader.repair_manual_type_charges()
    assert _get(pdb, 1)["location_type"] == "FAST"
    assert _get(pdb, 2)["location_type"] == "AC"


def test_a_row_with_neither_signal_is_left_alone(tmp_path, monkeypatch):
    """No power measured and not hand-typed either — nothing to reclassify from. Left as 'MANUAL'
    rather than guessed at; the count only reflects rows actually fixed."""
    pdb = _setup(tmp_path, monkeypatch)
    _row(pdb, 1, manual_entry=0, max_power_kw=None)
    assert db_reader.repair_manual_type_charges() == 0
    assert _get(pdb, 1)["location_type"] == "MANUAL"


def test_a_real_type_is_never_touched(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _row(pdb, 1, location_type="HPC", max_power_kw=120.0)
    assert db_reader.repair_manual_type_charges() == 0
    assert _get(pdb, 1)["location_type"] == "HPC"


def test_a_home_charge_is_categorically_untouched(tmp_path, monkeypatch):
    """The specific guarantee asked for before pushing: a genuine HOME charge — identified by the
    car actually being plugged into the wallbox, never by power — must never be reclassified or
    repriced by this repair, whatever its measured power looks like. The WHERE clause already
    excludes anything that isn't literally 'MANUAL', but this pins it down for HOME by name rather
    than relying on that being true for every OTHER real type too."""
    pdb = _setup(tmp_path, monkeypatch)
    _row(pdb, 1, location_type="HOME", max_power_kw=9.5, cost=2.10)
    assert db_reader.repair_manual_type_charges() == 0
    after = _get(pdb, 1)
    assert after["location_type"] == "HOME"
    assert after["cost"] == 2.10


def test_it_is_safe_to_run_twice(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _row(pdb, 1, max_power_kw=120.0)
    assert db_reader.repair_manual_type_charges() == 1
    before = _get(pdb, 1)
    assert db_reader.repair_manual_type_charges() == 0, "the second pass must find nothing"
    assert _get(pdb, 1) == before


def test_documented_limitation_a_home_charge_cannot_be_recovered_as_home(tmp_path, monkeypatch):
    """🔴 A charge that was ORIGINALLY a home session, manually priced, and stuck on 'MANUAL' has
    no way back to HOME here — home/away was never power-determined and no geofencing exists
    anywhere in Mate. This test exists to make that trade-off a visible, asserted contract rather
    than a silent surprise on somebody's real history: a plausible home-charger power (7.4 kW, no
    HA wallbox meter reading recorded) comes out AC, not HOME."""
    pdb = _setup(tmp_path, monkeypatch)
    _row(pdb, 1, max_power_kw=7.4)
    db_reader.repair_manual_type_charges()
    assert _get(pdb, 1)["location_type"] == "AC"
    assert _get(pdb, 1)["location_type"] != "HOME"
