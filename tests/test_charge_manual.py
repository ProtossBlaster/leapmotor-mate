"""cost_manual: the user-entered total actually paid OVERRIDES the automatic cost, and the
automatic costers (auto-confirm + the one-time repairs) leave a manually-priced charge's cost
alone — while still feeding the WAC like any priced charge. Independent of `location_type`: this
used to be the 'MANUAL' location_type itself, which meant picking it lost a charge's real type
(HOME/AC/FAST/HPC) for good. Runs on a tmp_path DB (poller schema + db_reader pointed at it) — no
settings DB, CI-safe."""
import db as D            # poller schema (creates charges/positions/settings + migrations)
import db_reader


def _setup(tmp_path, monkeypatch):
    pdb = D.Database(str(tmp_path / "t.db"))
    pdb.set_battery_capacity(60.0)
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    return pdb


def _charge(pdb, cid, *, start_soc=40, end_soc=80, energy=24.0, cost=None, ctype=None,
            ac=None, cost_manual=0, started="2026-06-02T16:48:39+00:00",
            ended="2026-06-02T21:18:36+00:00"):
    pdb._conn.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc,"
        " energy_added_kwh, ac_energy_kwh, location_type, cost, cost_manual, reconstructed)"
        " VALUES (?,1,?,?,?,?,?,?,?,?,?,0)",
        (cid, started, ended, start_soc, end_soc, energy, ac, ctype, cost, cost_manual))
    pdb._conn.commit()


def _row(pdb, cid):
    return pdb._conn.execute("SELECT * FROM charges WHERE id=?", (cid,)).fetchone()


def test_manual_cost_overrides_estimate(tmp_path, monkeypatch):
    """Typing a total on a REAL type (FAST here, not the old 'MANUAL' placeholder) overrides the
    computed estimate and marks the charge cost_manual — its type is untouched."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, ctype="FAST", cost=12.0)                       # Mate's table estimate
    out = db_reader.update_charge_type(1, "FAST", manual_cost=18.45, cost_manual=True)
    assert out["location_type"] == "FAST"
    assert out["cost"] == 18.45
    assert out["cost_manual"] == 1


def test_manual_accepts_comma_via_caller(tmp_path, monkeypatch):
    # update_charge_type takes a float; the endpoint normalises "18,45" → 18.45 before calling.
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, ctype="AC")
    out = db_reader.update_charge_type(1, "AC", manual_cost=float("18.45"), cost_manual=True)
    assert out["cost"] == 18.45


def test_an_ordinary_retag_preserves_a_manually_typed_cost(tmp_path, monkeypatch):
    """The whole point of splitting cost_manual out of location_type: correcting a charge's TYPE
    (a plain badge click, no manual_cost/cost_manual passed) must never touch a PRICE the owner
    already typed by hand."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, ctype="HPC", cost=18.45, cost_manual=1)
    out = db_reader.update_charge_type(1, "FAST")          # correcting HPC → FAST, no cost given
    assert out["location_type"] == "FAST"
    assert out["cost"] == 18.45                             # untouched
    assert out["cost_manual"] == 1                           # still flagged manual


def test_clearing_the_manual_cost_recomputes_the_default(tmp_path, monkeypatch):
    """The pencil field's "clear" case: `cost_manual=False` recomputes via the normal rule
    (FREE = explicit 0.0 here) and drops the flag."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, ctype="FREE", cost=18.45, cost_manual=1)
    out = db_reader.update_charge_type(1, "FREE", cost_manual=False)
    assert out["location_type"] == "FREE"
    assert out["cost"] == 0.0
    assert out["cost_manual"] == 0


def test_switching_type_alone_never_implicitly_clears_the_manual_flag(tmp_path, monkeypatch):
    """A plain re-tag (no cost_manual argument at all) is not a clear, whichever type it lands
    on — only the pencil field's explicit `cost_manual=False` is."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, ctype="HPC", cost=18.45, cost_manual=1)
    out = db_reader.update_charge_type(1, "FREE")           # no cost_manual= passed
    assert out["location_type"] == "FREE"
    assert out["cost"] == 18.45                              # NOT recomputed to 0.0
    assert out["cost_manual"] == 1


def test_an_unrecognised_type_is_refused(tmp_path, monkeypatch):
    """The old 'MANUAL' string is no longer a valid location_type at all — a stale client (cached
    pre-deploy HTML) submitting it must not silently reintroduce the bug for that one row."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, ctype="FAST", cost=12.0)
    out = db_reader.update_charge_type(1, "MANUAL", manual_cost=99.0)
    assert out == {}
    assert _row(pdb, 1)["location_type"] == "FAST"           # untouched
    assert _row(pdb, 1)["cost"] == 12.0


def test_snap_to_full_repair_keeps_manual_cost(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    # cost_manual charge ending at 100% with over-counted energy; a real charging sample at 90%.
    _charge(pdb, 1, start_soc=50, end_soc=100, energy=30.0, cost=25.0, ctype="HPC", cost_manual=1)
    pdb._conn.execute(
        "INSERT INTO positions (vehicle_id, recorded_at, soc, charging)"
        " VALUES (1, '2026-06-02T20:00:00+00:00', 90.0, 1)")
    pdb.set_setting("charges_soc_snap_repair_v1", "")     # let the one-time repair run again
    pdb._conn.commit()
    pdb._repair_snap_to_full_charges()
    r = _row(pdb, 1)
    assert r["cost"] == 25.0                               # manual € untouched
    assert r["energy_added_kwh"] < 30.0                    # energy still corrected (50→90, not →100)
