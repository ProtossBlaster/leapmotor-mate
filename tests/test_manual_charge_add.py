"""Manual historical charge (#87): add_manual_charge inserts a charge that counts in the lifetime
totals but carries no telemetry — no SoC, so it's excluded from the SoH estimate, and its cost is
protected (cost_manual=1, not the old location_type='MANUAL' placeholder — its location_type is
now the real AC/FAST type from the form's own AC/DC choice). Pure poller.db + db_reader on a tmp
DB → CI-safe."""
import db as D
import db_reader


def _setup(tmp_path, monkeypatch):
    pdb = D.Database(str(tmp_path / "t.db"))
    pdb.set_battery_capacity(67.0)
    pdb.ensure_vehicle("LVIN0000000000001", "B10", 2025)
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    return pdb


def test_manual_charge_counts_in_totals(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    cid = db_reader.add_manual_charge("2026-05-01T12:00:00", 24.5, cost=6.0, charge_type="AC")
    assert cid > 0
    s = db_reader.get_stats_summary()
    assert s["charge_count"] == 1
    assert abs((s["total_kwh_charged"] or 0) - 24.5) < 0.01
    assert abs((s["total_cost"] or 0) - 6.0) < 0.01


def test_manual_charge_excluded_from_soh(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    db_reader.add_manual_charge("2026-05-01T12:00:00", 50.0, cost=10.0, charge_type="DC")
    health = db_reader.get_battery_health()
    # No start/end SoC on a manual charge → it can't produce a SoH capacity point.
    assert health.get("latest_soh_pct") is None
    assert not health.get("points")


def test_manual_charge_fields(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    db_reader.add_manual_charge("2026-05-01T12:00:00", 10.0, charge_type="dc")
    row = pdb._conn.execute(
        "SELECT charge_type, location_type, cost_manual, start_soc, end_soc, ended_at "
        "FROM charges").fetchone()
    assert row["charge_type"] == "DC"           # normalised from "dc"
    assert row["location_type"] == "FAST"       # the REAL type now — searchable, not a placeholder
    assert row["cost_manual"] == 1              # cost protected from the auto-costers, on its own flag
    assert row["start_soc"] is None and row["end_soc"] is None   # no telemetry → out of SoH
    assert row["ended_at"] is not None          # set, so it counts in the (ended) totals


def test_manual_charge_ac_choice_maps_to_ac_type(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    db_reader.add_manual_charge("2026-05-01T12:00:00", 10.0, charge_type="AC")
    row = pdb._conn.execute("SELECT location_type FROM charges").fetchone()
    assert row["location_type"] == "AC"
