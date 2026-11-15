"""Every tyre pressure the car sends is kept with the reading it came with (D#483).

Until now the pressures were shown live on the Vehicle page, published to MQTT and ABRP, and then
thrown away: there was nothing to draw a history from. A pressure the car did not send stays NULL:
a sensor that is silent is not a tyre at 0 bar.
"""
import sqlite3

import client
import db as D
from test_missed_charge_scan import _seed

_NAMES = ("tire_fl_bar", "tire_fr_bar", "tire_rl_bar", "tire_rr_bar")
_TYRES = "SELECT tire_fl_bar, tire_fr_bar, tire_rl_bar, tire_rr_bar FROM positions"


def test_the_four_pressures_are_stored_in_bar(tmp_path, monkeypatch):
    db = _seed(tmp_path, monkeypatch)
    sig = {"100003": 60.0, "1318": 1000.0, "2646": 250, "2653": 252, "2660": 248, "2667": 251}
    db.save_position(1, client._parse_signal("VIN1", sig))
    assert tuple(db._conn.execute(_TYRES).fetchone()) == (2.50, 2.52, 2.48, 2.51)


def test_a_tyre_the_car_did_not_send_is_null_not_zero(tmp_path, monkeypatch):
    db = _seed(tmp_path, monkeypatch)
    sig = {"100003": 60.0, "1318": 1000.0, "2646": 250}
    db.save_position(1, client._parse_signal("VIN1", sig))
    assert tuple(db._conn.execute(_TYRES).fetchone()) == (2.50, None, None, None)


def test_a_database_from_before_gets_the_columns(tmp_path):
    """A database written by an older Mate has no such columns: opening it adds them."""
    path = str(tmp_path / "old.db")
    D.Database(path)._conn.close()
    con = sqlite3.connect(path)
    for c in {r[1] for r in con.execute("PRAGMA table_info(positions)")} & set(_NAMES):
        con.execute(f"ALTER TABLE positions DROP COLUMN {c}")
    con.commit(); con.close()
    D.Database(path)._conn.close()
    cols = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(positions)")}
    assert set(_NAMES) <= cols


def test_the_row_the_web_writes_after_a_command_keeps_them_too(tmp_path, monkeypatch):
    """The second writer of `positions` (a command, a Refresh) reads a frame as the poller does."""
    import db_reader
    db = _seed(tmp_path, monkeypatch)
    sig = {"100003": 60.0, "1318": 1000.0, "2646": 250, "2653": 252, "2660": 248}
    db.save_position(1, client._parse_signal("VIN1", sig))
    db_reader.save_fresh_signals(sig)
    assert [tuple(r) for r in db._conn.execute(_TYRES + " ORDER BY id")] == [(2.50, 2.52, 2.48, None)] * 2
