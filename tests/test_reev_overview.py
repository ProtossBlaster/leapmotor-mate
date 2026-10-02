"""REEV dual-energy on the Overview — the fuel tank is persisted alongside the battery so the status
card can show fuel %, fuel range and combined range live (range-extender models only), refreshing on
the same 30s cadence as the rest of the card. The fuel level MUST stay None on a BEV so the capability
guard hides the block entirely. Signals: 3235 fuel %, 3259 fuel range, 3260 EV range, 3261 combined.
CI-safe: pure parse + a tmp DB, no network and no ambient DB (the container's real DB would mask a
missing-DB dependency — see tests/test_charge_free.py)."""
import client


def test_parse_maps_fuel_ranges():
    d = client._parse_signal("V", {"3235": "91.4", "3259": "740", "3260": "92", "3261": "832"})
    assert d.fuel_level_pct == 91.4
    assert d.fuel_range_km == 740.0
    assert d.range_km == 92.0            # 3260 stays the EV-only range, unchanged
    assert d.combined_range_km == 832.0
    assert d.is_reev is True


def test_parse_bev_has_no_fuel():
    d = client._parse_signal("V", {"3260": "150"})   # BEV: no 3235/3259/3261
    assert d.fuel_level_pct is None
    assert d.fuel_range_km is None
    assert d.combined_range_km is None
    assert d.is_reev is False


def _setup(tmp_path, monkeypatch):
    import db as D            # poller schema + migrations
    import db_reader
    D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    db_reader.upsert_vehicle("VIN", "C10")
    return db_reader


def test_fresh_signals_persist_fuel_for_reev(tmp_path, monkeypatch):
    """The after-command write path (db_reader.save_fresh_signals) must carry fuel too, or the block
    would blink out for the ~30s between a command and the next poll."""
    db_reader = _setup(tmp_path, monkeypatch)
    db_reader.save_fresh_signals({"1204": "60", "3235": "91.4", "3259": "740", "3260": "92", "3261": "832"})
    st = db_reader.get_latest_status()
    assert st["fuel_level_pct"] == 91.4
    assert st["fuel_range_km"] == 740.0
    assert st["combined_range_km"] == 832.0
    assert st["range_km"] == 92.0        # EV range still separate from the combined figure


def test_fresh_signals_bev_fuel_is_none(tmp_path, monkeypatch):
    """A BEV must persist fuel as NULL, never 0.0 — otherwise the Overview guard
    (`status.get('fuel_level_pct') is not none`) would render an empty '0%' fuel block."""
    db_reader = _setup(tmp_path, monkeypatch)
    db_reader.save_fresh_signals({"1204": "60", "3260": "150"})   # no fuel signals at all
    st = db_reader.get_latest_status()
    assert st["fuel_level_pct"] is None
    assert st["combined_range_km"] is None


def test_poller_save_position_persists_fuel(tmp_path, monkeypatch):
    """The primary path: the poll loop parses a signal then save_position()s it."""
    import db as D
    import db_reader
    dbp = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    vid = dbp.ensure_vehicle("VIN", "C10")
    vd = client._parse_signal("VIN", {"3235": "55.0", "3259": "400", "3260": "80", "3261": "480"})
    dbp.save_position(vid, vd)
    st = db_reader.get_latest_status()
    assert st["fuel_level_pct"] == 55.0
    assert st["fuel_range_km"] == 400.0
    assert st["combined_range_km"] == 480.0
