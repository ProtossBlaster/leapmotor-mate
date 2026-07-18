"""Timezone selector (#145, dsbloomer). The UI must let the user pick the zone every timestamp is
displayed in — a bare Docker container is UTC, and an HA whose zone Mate can't read is UTC too, so
'Auto' alone isn't enough (dsbloomer saw everything 12h off).

Verifies: resolution precedence (UI setting > env TZ > system-local), that a bad/stale value degrades
to Auto instead of wedging every render, that the same stored UTC instant renders in the chosen zone,
that ToU charge COSTS follow the selected zone (Silvio's question — a charge priced now uses the
active zone; history stays frozen by the existing 'new charges only' rule, unchanged here), and the
picker's shape (canonical continents + UTC, no misleading legacy/Etc±N aliases).
"""
import sqlite3
from datetime import timedelta

import db_reader


def _db():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    con.commit()
    return con


def _wire(monkeypatch, con):
    monkeypatch.setattr(db_reader, "_get", lambda: con)
    monkeypatch.setattr(db_reader, "_conn_rw", lambda: con)
    db_reader._TZ_CACHE["key"] = "\x00"        # drop any zone memoised by a prior test


# ── resolution & precedence ──────────────────────────────────────────────────

def test_selected_zone_applied_to_local_dt(monkeypatch):
    con = _db(); _wire(monkeypatch, con)
    db_reader.set_timezone("Pacific/Auckland")          # NZ, July = winter → NZST = UTC+12
    dt = db_reader._local_dt("2026-07-01T00:00:00")     # stored UTC midnight
    assert dt.hour == 12 and dt.utcoffset() == timedelta(hours=12)


def test_default_empty_is_auto_and_honours_env(monkeypatch):
    con = _db(); _wire(monkeypatch, con)
    monkeypatch.setenv("TZ", "Asia/Tokyo")              # UTC+9
    assert db_reader.get_timezone() == ""               # nothing stored = Auto
    dt = db_reader._local_dt("2026-07-01T00:00:00")
    assert dt.hour == 9 and dt.utcoffset() == timedelta(hours=9)


def test_ui_setting_overrides_env(monkeypatch):
    con = _db(); _wire(monkeypatch, con)
    monkeypatch.setenv("TZ", "Pacific/Auckland")        # env says +12…
    db_reader.set_timezone("Asia/Tokyo")                # …but the user picked +9
    dt = db_reader._local_dt("2026-07-01T00:00:00")
    assert dt.hour == 9                                 # the explicit choice wins


def test_bad_zone_degrades_to_auto(monkeypatch):
    con = _db(); _wire(monkeypatch, con)
    monkeypatch.delenv("TZ", raising=False)
    db_reader.set_timezone("Bogus/Nowhere")
    assert db_reader.get_timezone() == ""              # garbage is never stored
    # a stale/unknown value written straight to the DB must still not crash a render
    con.execute("INSERT OR REPLACE INTO settings VALUES ('timezone','Bogus/Nowhere')"); con.commit()
    db_reader._TZ_CACHE["key"] = "\x00"
    assert db_reader._local_dt("2026-07-01T00:00:00") is not None


def test_roundtrip(monkeypatch):
    con = _db(); _wire(monkeypatch, con)
    db_reader.set_timezone("Europe/Rome")
    assert db_reader.get_timezone() == "Europe/Rome"
    db_reader.set_timezone("")                          # back to Auto
    assert db_reader.get_timezone() == ""


def test_missing_db_never_raises(monkeypatch):
    # get_setting raising (no DB yet) must degrade to container tz, never crash a page render
    def _boom(*a, **k):
        raise sqlite3.OperationalError("unable to open database file")
    monkeypatch.setattr(db_reader, "get_setting", _boom)
    monkeypatch.delenv("TZ", raising=False)
    db_reader._TZ_CACHE["key"] = "\x00"
    assert db_reader._local_dt("2026-07-01T00:00:00") is not None


# ── ToU cost follows the selected zone (Silvio's question) ────────────────────

def test_tou_cost_follows_selected_zone(monkeypatch):
    con = _db(); _wire(monkeypatch, con)
    monkeypatch.setattr(db_reader, "get_charge_prices", lambda: {"price_home_kwh": 0.30})
    # daytime band 08:00–20:00 @ 0.10 €/kWh for HOME; base 0.30 applies outside it
    cfg = {"mode": "tou", "method": "start", "modes": {},
           "bands": [{"start": "08:00", "end": "20:00", "prices": {"HOME": 0.10}}]}
    charge = {"location_type": "HOME", "energy_added_kwh": 10.0, "ac_energy_kwh": None,
              "started_at": "2026-07-01T12:00:00",       # 12:00 UTC
              "ended_at": "2026-07-01T14:00:00"}
    # UTC: 12:00 is inside the daytime band → 0.10 × 10 = 1.00
    db_reader.set_timezone("UTC")
    assert db_reader.compute_cost(charge, cfg) == 1.00
    # Auckland (+12): 12:00 UTC = 00:00 local → outside the band → base 0.30 × 10 = 3.00
    db_reader.set_timezone("Pacific/Auckland")
    assert db_reader.compute_cost(charge, cfg) == 3.00


# ── picker shape ──────────────────────────────────────────────────────────────

def test_timezone_options_canonical_only():
    opts = db_reader.timezone_options()
    assert {"Europe", "America", "Asia", "Pacific", "UTC"} <= set(opts)
    assert not (set(opts) & {"US", "Brazil", "Canada", "Chile", "Mexico", "Etc", "Other"})
    allz = {v for zs in opts.values() for v, _ in zs}
    for z in ("Pacific/Auckland", "Europe/Rome", "America/New_York", "Asia/Tokyo", "UTC"):
        assert z in allz
    assert opts["UTC"] == [("UTC", "UTC")]
