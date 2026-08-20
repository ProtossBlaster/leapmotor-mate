"""Signal 1255 (vehicleSecurityActive) → a persisted `security_active` flag shown in the
overview's first card, above READY — green "Active" when the car is locked + alarm armed
(the kerniger HA integration exposes the same thing as binary_sensor.leapmotor_vehicle_security_active).

Same plumbing as charge_completed (3736): the poller parses 1255 into VehicleData and writes it to
positions; the web reads it back via get_latest_status (SELECT *). The live semantics (does 1255
read 1 when locked+armed?) are validated on-car — here we exercise the PLUMBING with synthetic
values. CI-safe (tmp DB, no network)."""
import sqlite3

import client          # poller/client.py
import db as D         # poller schema + migrations
import db_reader


def _sig(**kw):
    base = {"1010": 0, "1319": 0}   # parked, stationary
    base.update(kw)
    return base


def test_parse_signal_maps_1255():
    # On the B10 the ARMED value is 2 (observed live with the car locked) — the flag is truthy,
    # NOT == 1, and matches the kerniger integration's bool(value).
    assert client._parse_signal("VIN", _sig(**{"1255": 2})).security_active is True   # armed (real B10 value)
    assert client._parse_signal("VIN", _sig(**{"1255": 1})).security_active is True
    assert client._parse_signal("VIN", _sig(**{"1255": 0})).security_active is False
    # 🔴 CAMBIATO il 20/08 (#256): assente → None, non False. Due C10 non mandano mai il 1255, e
    # leggerlo come False stampava «Inattivo» su una riga di SICUREZZA.
    # → tests/test_absent_security_signal_is_not_inactive.py
    assert client._parse_signal("VIN", _sig()).security_active is None                # absent → unknown


def test_persists_and_reads_back(tmp_path, monkeypatch):
    D.Database(str(tmp_path / "t.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    db_reader.upsert_vehicle("VIN", "B10")

    db_reader.save_fresh_signals({"1255": 2})              # the B10 armed value
    assert db_reader.get_latest_status()["security_active"] == 1

    db_reader.save_fresh_signals({"1255": 0})
    assert db_reader.get_latest_status()["security_active"] == 0

    db_reader.save_fresh_signals({})                       # signal absent → NULL (#256), never 0
    assert db_reader.get_latest_status()["security_active"] is None


def test_schema_has_column(tmp_path):
    p = str(tmp_path / "m.db")
    D.Database(p)
    cols = {r[1] for r in sqlite3.connect(p).execute("PRAGMA table_info(positions)")}
    assert "security_active" in cols
