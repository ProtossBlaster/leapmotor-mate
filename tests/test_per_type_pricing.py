"""Per-charge-type pricing modes (GitHub #106, twiktorowicz): each charge type (HOME/AC/FAST/HPC)
picks its own mode — Fixed / Time-of-use / Dynamic-HA-sensor — with its own dynamic sensor when
dynamic. The headline scenario is the requester's own: HOME on a dynamic home-tariff sensor while
public charging stays on the operators' fixed prices.

Simulazioni: the mixed-mode matrix through compute_cost, per-type sensor resolution with the
legacy single-entity fallback, upgrade equivalence (pre-#106 settings behave exactly as before),
and save/sanitise round-trips. Same hand-checkable fixture as test_dynamic_price_cost: a 2h
charge at constant 5 kW → 10 kWh, so expected costs are one multiplication."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import db_reader
import ha_client


T0 = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


def _db():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE charges (id INT, location_type TEXT, energy_added_kwh REAL, "
                "cost REAL, ac_energy_kwh REAL, started_at TEXT, ended_at TEXT, vehicle_id INTEGER DEFAULT 1)")
    con.execute("CREATE TABLE positions (recorded_at TEXT, charging INT, "
                "charge_voltage_v REAL, charge_current_a REAL)")
    for i in range(9):   # 0..120 min every 15 min → 8 intervals @ 1.25 kWh = 10 kWh
        t = (T0 + timedelta(minutes=15 * i)).isoformat()
        con.execute("INSERT INTO positions VALUES (?,1,250,20)", (t,))
    con.execute("ALTER TABLE positions ADD COLUMN vehicle_id INTEGER DEFAULT 1")
    con.commit()
    return con


def _charge(ctype="HOME"):
    return {
        "location_type": ctype, "energy_added_kwh": 10.0, "ac_energy_kwh": None,
        "started_at": T0.isoformat(),
        "ended_at": (T0 + timedelta(hours=2)).isoformat(),
    }


PRICES = {"price_home_kwh": 0.25, "price_ac_kwh": 0.50, "price_fast_kwh": 0.60, "price_hpc_kwh": 0.80}


def _setup(monkeypatch, con, modes, entities=None, legacy_entity="", legacy_mode="flat"):
    """Wire compute_cost to an in-memory DB with per-type modes and per-type sensors.
    The entity mock mirrors the REAL rule: only HOME falls back to the legacy single sensor."""
    monkeypatch.setattr(db_reader, "_get", lambda: con)
    monkeypatch.setattr(db_reader, "get_cost_config",
                        lambda: {"mode": legacy_mode, "modes": modes, "method": "split", "bands": []})
    monkeypatch.setattr(db_reader, "get_charge_prices", lambda: dict(PRICES))
    ents = entities or {}
    monkeypatch.setattr(db_reader, "get_dynamic_price_entity", lambda: legacy_entity)
    monkeypatch.setattr(db_reader, "get_dynamic_price_entity_for",
                        lambda ct: (ents.get(ct) or "").strip()
                        or (legacy_entity if ct == "HOME" else ""))


def _ts(minutes):
    return (T0 + timedelta(minutes=minutes)).timestamp()


def test_requesters_scenario_home_dynamic_public_fixed(monkeypatch):
    """The #106 headline: HOME=dynamic (sensor at 0.30), AC/FAST/HPC=flat operator prices."""
    con = _db()
    modes = {"HOME": "dynamic", "AC": "flat", "FAST": "flat", "HPC": "flat"}
    _setup(monkeypatch, con, modes, entities={"HOME": "sensor.home_price"})
    monkeypatch.setattr(ha_client, "get_history", lambda eid, lo, hi: [(_ts(0), 0.30)])
    assert db_reader.compute_cost(_charge("HOME")) == 3.00   # 10 kWh × sensor 0.30
    assert db_reader.compute_cost(_charge("AC")) == 5.00     # 10 × flat 0.50 — sensor NOT used
    assert db_reader.compute_cost(_charge("FAST")) == 6.00
    assert db_reader.compute_cost(_charge("HPC")) == 8.00


def test_compute_cost_defense_in_depth_rejects_away_dynamic(monkeypatch):
    """Dynamic is HOME-ONLY (Silvio 02/07: no HA integration prices public AC/DC/HPC charging).
    Even a config that CLAIMS an away type is dynamic — raw/stale data bypassing
    get_cost_config's own sanitisation — must not be honoured by compute_cost itself: the guard
    lives at the dispatch point too, not just in the read/write helpers."""
    con = _db()
    modes = {"HOME": "dynamic", "AC": "flat", "FAST": "dynamic", "HPC": "flat"}
    _setup(monkeypatch, con, modes,
           entities={"HOME": "sensor.home_price", "FAST": "sensor.dc_price"})
    hist = {"sensor.home_price": [(_ts(0), 0.30)], "sensor.dc_price": [(_ts(0), 0.05)]}
    monkeypatch.setattr(ha_client, "get_history", lambda eid, lo, hi: hist.get(eid, []))
    assert db_reader.compute_cost(_charge("HOME")) == 3.00   # HOME dynamic is legitimate
    assert db_reader.compute_cost(_charge("FAST")) == 6.00   # FAST forced to its base, sensor ignored


def test_dynamic_type_without_sensor_falls_back_to_its_base(monkeypatch):
    con = _db()
    modes = {"HOME": "flat", "AC": "dynamic", "FAST": "flat", "HPC": "flat"}
    _setup(monkeypatch, con, modes, entities={})   # AC dynamic but no sensor anywhere
    assert db_reader.compute_cost(_charge("AC")) == 5.00   # AC base price, not silently uncosted


def test_home_dynamic_falls_back_to_legacy_single_entity(monkeypatch):
    """Pre-fix dynamic setups keep their HOME pricing: no per-type sensor → the old global one
    (which IS the home tariff) prices home charges, zero reconfiguration."""
    con = _db()
    modes = {"HOME": "dynamic", "AC": "flat", "FAST": "flat", "HPC": "flat"}
    _setup(monkeypatch, con, modes, entities={}, legacy_entity="sensor.legacy")
    monkeypatch.setattr(ha_client, "get_history", lambda eid, lo, hi:
                        [(_ts(0), 0.40)] if eid == "sensor.legacy" else [])
    assert db_reader.compute_cost(_charge("HOME")) == 4.00


def test_away_dynamic_never_borrows_the_home_sensor(monkeypatch):
    """The heart of the fix: an away type explicitly set to dynamic WITHOUT its own sensor must
    NOT be priced by the legacy home sensor (that would re-introduce the bug) — it prices at
    its base."""
    con = _db()
    modes = {"HOME": "flat", "AC": "flat", "FAST": "dynamic", "HPC": "flat"}
    _setup(monkeypatch, con, modes, entities={}, legacy_entity="sensor.legacy")
    monkeypatch.setattr(ha_client, "get_history", lambda eid, lo, hi: [(_ts(0), 0.05)])
    assert db_reader.compute_cost(_charge("FAST")) == 6.00   # base 0.60 — NOT 0.05×10 from home


def test_tri_mode_mix_in_one_config(monkeypatch):
    """Silvio's scenario: the three modes COEXIST per type — HOME=dynamic + AC=time bands +
    DC/HPC=flat, all in the same config, each charge priced by its own regime."""
    con = _db()
    band = {"start": "00:00", "end": "24:00", "days": list(range(7)),
            "prices": {"HOME": None, "AC": 0.20, "FAST": None, "HPC": None}}
    modes = {"HOME": "dynamic", "AC": "tou", "FAST": "flat", "HPC": "flat"}
    monkeypatch.setattr(db_reader, "_get", lambda: con)
    monkeypatch.setattr(db_reader, "get_cost_config",
                        lambda: {"mode": "flat", "modes": modes, "method": "start", "bands": [band]})
    monkeypatch.setattr(db_reader, "get_charge_prices", lambda: dict(PRICES))
    monkeypatch.setattr(db_reader, "get_dynamic_price_entity", lambda: "")
    monkeypatch.setattr(db_reader, "get_dynamic_price_entity_for",
                        lambda ct: "sensor.home_price" if ct == "HOME" else "")
    monkeypatch.setattr(ha_client, "get_history", lambda eid, lo, hi: [(_ts(0), 0.30)])
    assert db_reader.compute_cost(_charge("HOME")) == 3.00   # dynamic sensor
    assert db_reader.compute_cost(_charge("AC")) == 2.00     # its band price
    assert db_reader.compute_cost(_charge("FAST")) == 6.00   # flat base
    assert db_reader.compute_cost(_charge("HPC")) == 8.00    # flat base


def test_bands_express_different_time_windows_per_type(monkeypatch):
    """'Fasce fuori casa' with their OWN hours: one shared band table, and a blank cell now
    means "this band is not for this type" — the TYPE-AWARE cascade lets OVERLAPPING windows
    serve different types (a HOME-only 10-14 band and an AC-only 11-15 band coexist; before
    the fix the first time-matching band won for every type and the AC band was dead in the
    overlap). A type blank in every covering band gets its base."""
    con = _db()
    b_home = {"start": "10:00", "end": "14:00", "days": list(range(7)),
              "prices": {"HOME": 0.10, "AC": None, "FAST": None, "HPC": None}}
    b_ac = {"start": "11:00", "end": "15:00", "days": list(range(7)),
            "prices": {"HOME": None, "AC": 0.33, "FAST": None, "HPC": None}}
    modes = {"HOME": "tou", "AC": "tou", "FAST": "tou", "HPC": "flat"}
    monkeypatch.setattr(db_reader, "_get", lambda: con)
    monkeypatch.setattr(db_reader, "get_cost_config",
                        lambda: {"mode": "flat", "modes": modes, "method": "start",
                                 "bands": [b_home, b_ac]})
    monkeypatch.setattr(db_reader, "get_charge_prices", lambda: dict(PRICES))
    monkeypatch.setattr(db_reader, "_local_tz", lambda: timezone.utc)
    # charge starts 12:00 → inside BOTH windows; each type reads ITS band, not the first one
    assert db_reader.compute_cost(_charge("HOME")) == 1.00   # its 10-14 band, 0.10
    assert db_reader.compute_cost(_charge("AC")) == 3.30     # its 11-15 band, 0.33 — cascade past b_home
    assert db_reader.compute_cost(_charge("FAST")) == 6.00   # blank in every band → base


def test_e2e_migration_corrects_legacy_dynamic_through_real_config(monkeypatch):
    """END-TO-END through the REAL get_cost_config/get_dynamic_price_entity_for (no mocks on
    them): a pre-fix install (cost_mode='dynamic', one legacy sensor, NO cost_modes key) must —
    after the update — price HOME with the sensor and public types at their flat bases. This is
    the corrective migration, proven on the real read path."""
    con = _db()
    con.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    for k, v in [("cost_mode", "dynamic"), ("dynamic_price_entity_id", "sensor.home_tariff"),
                 ("price_home_kwh", "0.25"), ("price_ac_kwh", "0.50"),
                 ("price_fast_kwh", "0.60"), ("price_hpc_kwh", "0.80")]:
        con.execute("INSERT INTO settings VALUES (?,?)", (k, v))
    con.commit()
    monkeypatch.setattr(db_reader, "_get", lambda: con)
    monkeypatch.setattr(ha_client, "get_history", lambda eid, lo, hi:
                        [(_ts(0), 0.30)] if eid == "sensor.home_tariff" else [])
    assert db_reader.compute_cost(_charge("HOME")) == 3.00   # sensor keeps pricing home
    assert db_reader.compute_cost(_charge("FAST")) == 6.00   # NOT 3.00 — the bug is corrected
    assert db_reader.compute_cost(_charge("AC")) == 5.00
    assert db_reader.compute_cost(_charge("HPC")) == 8.00


def test_config_without_modes_map_uses_global_mode(monkeypatch):
    """Older monkeypatched/stored configs (no 'modes' key) behave exactly as pre-fix."""
    con = _db()
    monkeypatch.setattr(db_reader, "_get", lambda: con)
    monkeypatch.setattr(db_reader, "get_cost_config",
                        lambda: {"mode": "dynamic", "method": "split", "bands": []})
    monkeypatch.setattr(db_reader, "get_charge_prices", lambda: dict(PRICES))
    monkeypatch.setattr(db_reader, "get_dynamic_price_entity", lambda: "sensor.legacy")
    monkeypatch.setattr(db_reader, "get_dynamic_price_entity_for",
                        lambda ct: "sensor.legacy" if ct == "HOME" else "")
    monkeypatch.setattr(ha_client, "get_history", lambda eid, lo, hi: [(_ts(0), 0.30)])
    assert db_reader.compute_cost(_charge("HOME")) == 3.00


def test_tou_for_one_type_only(monkeypatch):
    """AC on time bands while HOME stays flat: the band prices only AC charges."""
    con = _db()
    band = {"start": "00:00", "end": "24:00", "days": list(range(7)),
            "prices": {"HOME": 0.10, "AC": 0.20, "FAST": None, "HPC": None}}
    modes = {"HOME": "flat", "AC": "tou", "FAST": "flat", "HPC": "flat"}
    monkeypatch.setattr(db_reader, "_get", lambda: con)
    monkeypatch.setattr(db_reader, "get_cost_config",
                        lambda: {"mode": "flat", "modes": modes, "method": "start", "bands": [band]})
    monkeypatch.setattr(db_reader, "get_charge_prices", lambda: dict(PRICES))
    assert db_reader.compute_cost(_charge("AC")) == 2.00     # band 0.20 × 10
    assert db_reader.compute_cost(_charge("HOME")) == 2.50   # flat base 0.25 — band IGNORED


def _settings_db(monkeypatch):
    """A real settings table so the get/save round-trips run unmocked."""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    con.commit()
    monkeypatch.setattr(db_reader, "_get", lambda: con)
    monkeypatch.setattr(db_reader, "_conn_rw", lambda: con)   # set_setting writes via the RW conn
    return con


def test_get_cost_config_resolves_missing_types_to_legacy(monkeypatch):
    _settings_db(monkeypatch)
    db_reader.set_setting("cost_mode", "tou")
    db_reader.set_setting("cost_modes", json.dumps({"HOME": "dynamic"}))
    cfg = db_reader.get_cost_config()
    assert cfg["modes"] == {"HOME": "dynamic", "AC": "tou", "FAST": "tou", "HPC": "tou"}


def test_legacy_dynamic_defaults_to_home_only(monkeypatch):
    """The corrective migration in the real resolver: global 'dynamic' (the bug) becomes
    HOME=dynamic + public types on flat — NOT dynamic everywhere."""
    _settings_db(monkeypatch)
    db_reader.set_setting("cost_mode", "dynamic")
    cfg = db_reader.get_cost_config()
    assert cfg["modes"] == {"HOME": "dynamic", "AC": "flat", "FAST": "flat", "HPC": "flat"}
    # dynamic is HOME-ONLY: an explicit per-type 'dynamic' on an away type is REJECTED at read
    # time too (not just honoured because it's "explicit") — falls back to the default (flat)
    db_reader.set_setting("cost_modes", json.dumps({"FAST": "dynamic"}))
    assert db_reader.get_cost_config()["modes"]["FAST"] == "flat"
    # an explicit HOME/AC choice still applies normally
    db_reader.set_setting("cost_modes", json.dumps({"HOME": "dynamic", "AC": "tou"}))
    m = db_reader.get_cost_config()["modes"]
    assert m["HOME"] == "dynamic" and m["AC"] == "tou"


def test_save_cost_modes_sanitises_and_aligns_legacy(monkeypatch):
    _settings_db(monkeypatch)
    db_reader.save_cost_modes({"HOME": "dynamic", "AC": "bogus", "FAST": "flat", "HPC": "flat", "X": "flat"})
    saved = json.loads(db_reader.get_setting("cost_modes"))
    assert saved == {"HOME": "dynamic", "FAST": "flat", "HPC": "flat"}   # bogus + unknown dropped
    # uniform saves align the legacy key too
    db_reader.save_cost_modes({t: "tou" for t in ("HOME", "AC", "FAST", "HPC")})
    assert db_reader.get_setting("cost_mode") == "tou"


def test_save_cost_modes_rejects_dynamic_on_away_types(monkeypatch):
    """Write-time enforcement of the HOME-only rule: a raw save with AC/FAST claiming 'dynamic'
    must not even reach storage — a UI bypass (direct API call) can't resurrect the bug."""
    _settings_db(monkeypatch)
    db_reader.save_cost_modes({"HOME": "dynamic", "AC": "dynamic", "FAST": "dynamic", "HPC": "flat"})
    saved = json.loads(db_reader.get_setting("cost_modes"))
    assert saved == {"HOME": "dynamic", "HPC": "flat"}   # AC/FAST dynamic silently dropped


def test_per_type_entity_roundtrip_and_fallback(monkeypatch):
    _settings_db(monkeypatch)
    db_reader.save_dynamic_price_entity("sensor.legacy")
    assert db_reader.get_dynamic_price_entity_for("HOME") == "sensor.legacy"   # HOME-only fallback
    assert db_reader.get_dynamic_price_entity_for("AC") == ""                  # away types: NO borrow
    db_reader.save_dynamic_price_entity_for("HOME", "sensor.home")
    assert db_reader.get_dynamic_price_entity_for("HOME") == "sensor.home"
    db_reader.save_dynamic_price_entity_for("AC", "sensor.public_ac")
    assert db_reader.get_dynamic_price_entity_for("AC") == "sensor.public_ac"  # explicit wins
    db_reader.save_dynamic_price_entity_for("BOGUS", "sensor.x")               # ignored
    assert "BOGUS" not in json.loads(db_reader.get_setting("dynamic_price_entities"))
