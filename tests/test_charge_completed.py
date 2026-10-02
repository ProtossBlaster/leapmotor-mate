"""Signal 3736 (chargeCompleted) → a persisted `charge_completed` flag + the overview's
"Fully charged" badge.

The poller parses 3736 into VehicleData.charge_completed and writes it to the positions row;
the web reads it back via get_latest_status (SELECT *), and charging_live.html shows the badge
when plugged + completed + not actively charging.

NOTE: the exact on-car semantics of 3736 (does it read 1 at full charge?) must be confirmed on a
real charge — the car was asleep when this was built, so here we exercise the PLUMBING with
synthetic values. CI-safe (tmp DB, no network)."""
import sqlite3

import client          # poller/client.py
import db as D         # poller schema + migrations
import db_reader


def _sig(**kw):
    base = {"1010": 0, "1319": 0}   # parked, stationary (so unrelated gates don't fire)
    base.update(kw)
    return base


def test_parse_signal_maps_3736():
    # Truthy mapping: the B10 status flags read 2 (not 1) — see vehicleSecurityActive. The exact
    # "completed" value is still to be confirmed on a real full charge; any non-zero counts.
    assert client._parse_signal("VIN", _sig(**{"3736": 2})).charge_completed is True
    assert client._parse_signal("VIN", _sig(**{"3736": 1})).charge_completed is True
    assert client._parse_signal("VIN", _sig(**{"3736": 0})).charge_completed is False
    assert client._parse_signal("VIN", _sig()).charge_completed is False        # absent → False


def test_persists_and_reads_back(tmp_path, monkeypatch):
    D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    db_reader.upsert_vehicle("VIN", "B10")

    db_reader.save_fresh_signals({"1204": 50, "3736": 1})
    assert db_reader.get_latest_status()["charge_completed"] == 1

    db_reader.save_fresh_signals({"1204": 50, "3736": 0})
    assert db_reader.get_latest_status()["charge_completed"] == 0

    db_reader.save_fresh_signals({"1204": 50})             # signal absent → 0, never NULL/crash
    assert db_reader.get_latest_status()["charge_completed"] == 0


def test_schema_has_column_and_migration_is_idempotent(tmp_path):
    p = str(tmp_path / "m.db")
    D.Database(p)
    cols = {r[1] for r in sqlite3.connect(p).execute("PRAGMA table_info(positions)")}
    assert "charge_completed" in cols
    D.Database(p)        # re-init must not raise (the `if not in cols` migration guard)
