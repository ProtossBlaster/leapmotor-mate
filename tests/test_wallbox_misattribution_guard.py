"""A public charge must never be mislabelled as a home-wallbox session (PR #147 follow-up,
GitHub issue from @arch.gamberoni: an Enel X charge in Fistra (MC) never got its 📍 station
name because the poller had seeded `wallbox_energy_start_kwh` from the home wallbox's idle
energy-counter reading, even though the car was charging hundreds of km away).

Two independent guards:
  1. poller/recorder.py._read_wallbox_energy — only trust the energy reading when a mapped
     `power` sensor confirms the wallbox is ACTIVELY delivering power right now; a reachable-
     but-idle wallbox no longer poisons a charge that isn't actually happening on it.
  2. web/db_reader.update_charge_type — retyping a charge away from HOME clears the wallbox
     fields, so a charge the user corrects (or that was already stuck from before guard #1
     existed) becomes eligible again for the 📍 charging-station lookup.

CI-safe: pure recorder/db_reader logic, no fastapi, no network.
"""
import db as D             # poller schema (creates charges/settings tables + migrations)
import db_reader
import recorder as R


# ── guard 1: the poller no longer trusts an idle wallbox's stale reading ─────────

def _recorder():
    db = D.Database(":memory:")
    db._conn.execute("INSERT INTO vehicles (id, vin) VALUES (1, 'V')")
    db._conn.commit()
    return R.Recorder(db, vehicle_id=1)


def test_idle_wallbox_with_power_sensor_is_not_trusted(monkeypatch):
    """Power sensor mapped and reads ~0 (car charging elsewhere) → energy reading discarded."""
    import ha_client
    monkeypatch.setattr(ha_client, "get_mapping", lambda: {"power": "sensor.wb_power"})
    monkeypatch.setattr(ha_client, "get_live",
                         lambda: {"energy_kwh": 1234.5, "power_kw": 0.0, "charging": False})
    rec = _recorder()
    assert rec._read_wallbox_energy() is None


def test_active_wallbox_with_power_sensor_is_trusted(monkeypatch):
    """Power sensor mapped and reads real power (genuine home charge) → energy reading kept."""
    import ha_client
    monkeypatch.setattr(ha_client, "get_mapping", lambda: {"power": "sensor.wb_power"})
    monkeypatch.setattr(ha_client, "get_live",
                         lambda: {"energy_kwh": 1234.5, "power_kw": 7.4, "charging": True})
    rec = _recorder()
    assert rec._read_wallbox_energy() == 1234.5


def test_no_power_sensor_mapped_keeps_old_best_effort_behaviour(monkeypatch):
    """No power role mapped (only energy) → nothing to verify against → unchanged behaviour."""
    import ha_client
    monkeypatch.setattr(ha_client, "get_mapping", lambda: {"energy": "sensor.wb_energy"})
    monkeypatch.setattr(ha_client, "get_live",
                         lambda: {"energy_kwh": 42.0, "power_kw": None, "charging": False})
    rec = _recorder()
    assert rec._read_wallbox_energy() == 42.0


def test_recorder_does_not_seed_baseline_from_an_idle_wallbox(monkeypatch):
    """End-to-end: a charge opened while the home wallbox is reachable-but-idle must not get a
    wallbox baseline — the exact scenario that stuck the Fistra charge with no 📍 name forever."""
    import ha_client
    monkeypatch.setattr(ha_client, "get_mapping", lambda: {"power": "sensor.wb_power"})
    monkeypatch.setattr(ha_client, "get_live",
                         lambda: {"energy_kwh": 500.0, "power_kw": 0.0, "charging": False})
    from client import _parse_signal

    rec = _recorder()
    rec.process(_parse_signal("V", {"1010": 0, "1319": 0, "100003": 55}))            # parked
    rec.process(_parse_signal("V", {"1010": 0, "1319": 0, "100003": 55,
                                     "1149": 2, "1178": 16, "1177": 230}))            # charging
    assert rec._active_charge_id is not None
    row = rec._db._conn.execute(
        "SELECT wallbox_energy_start_kwh, ac_energy_kwh FROM charges WHERE id=?",
        (rec._active_charge_id,)).fetchone()
    assert row["wallbox_energy_start_kwh"] is None
    assert row["ac_energy_kwh"] in (None, 0)


# ── guard 2: retyping away from HOME clears the wallbox fields ──────────────────

def _setup(tmp_path, monkeypatch):
    pdb = D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    return pdb


def _charge(pdb, cid, *, lat=43.039036, lon=13.166765, ctype="AC", ac=0.0, wb_start=0.0):
    pdb._conn.execute(
        "INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc, end_soc,"
        " energy_added_kwh, latitude, longitude, location_type, ac_energy_kwh,"
        " wallbox_energy_start_kwh)"
        " VALUES (?,1,'2026-07-18T15:27:18+00:00','2026-07-18T16:31:51+00:00',49.7,64.5,"
        "9.9,?,?,?,?,?)",
        (cid, lat, lon, ctype, ac, wb_start))
    pdb._conn.commit()


def test_retag_away_from_home_clears_wallbox_fields(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, ctype="AC", wb_start=0.0)
    out = db_reader.update_charge_type(1, "AC")
    assert out["wallbox_energy_start_kwh"] is None
    assert out["ac_energy_kwh"] is None


def test_retag_to_home_keeps_wallbox_fields(tmp_path, monkeypatch):
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, ctype="AC", wb_start=12.3, ac=4.2)
    out = db_reader.update_charge_type(1, "HOME")
    assert out["wallbox_energy_start_kwh"] == 12.3
    assert out["ac_energy_kwh"] == 4.2


def test_retagged_charge_becomes_a_location_lookup_candidate(tmp_path, monkeypatch):
    """The whole point of guard 2: after the retag, the charge is no longer excluded by
    _LOCATION_CANDIDATES_WHERE and can get its 📍 station name resolved."""
    pdb = _setup(tmp_path, monkeypatch)
    _charge(pdb, 1, ctype="AC", wb_start=0.0)
    assert db_reader.has_location_lookup_candidates() is False   # stuck, as reported
    db_reader.update_charge_type(1, "AC")
    assert db_reader.has_location_lookup_candidates() is True
    cands = db_reader.get_location_lookup_candidates()
    assert [c["id"] for c in cands] == [1]
