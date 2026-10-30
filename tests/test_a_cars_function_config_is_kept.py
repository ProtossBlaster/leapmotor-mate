"""The cloud's vehicle list gives each car its function config (`funcConfig`): the fan's and the
temperature's range for remote climate. It comes in each car's own list entry, so Mate keeps it, as
sent, in the record it already keeps of that entry for the account's rights, refreshed with every
list it reads; the diagnostic bundle shows it from there, after the rights.
"""
import json
import sqlite3

import api_v2_bridge as bridge
import db_reader
import diagnostics
from test_cloud_access_refresh import client
from ui_command_access import snapshot_key

VIN = "LFZB10TEST00000001"
FUNC = {"HVAC": {"fan": {"max": "9", "min": "1", "unit": "gear"},
                 "temperature": {"max": "32", "min": "18", "unit": "celsius"}},
        "valid": True}


def _list(monkeypatch, tmp_path, **entry):
    """The adapter reads the vehicle list once; the bundle is then built from what it kept."""
    db = tmp_path / "test.db"
    monkeypatch.setattr(bridge, "DB", str(db))
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT)")
    api = client()
    api.read = lambda path, body, **kw: {"data": {"bindcars": [{"vin": VIN, "carType": "B10", **entry}]}}
    api._get_vehicle_list()
    with sqlite3.connect(db) as conn:
        settings = {snapshot_key(VIN): bridge.setting(conn, snapshot_key(VIN))}
    monkeypatch.setattr(db_reader, "get_vehicle", lambda: ({"vin": VIN}, settings))
    return diagnostics._abilities_section()


def test_the_bundle_shows_it_as_the_list_gave_it(monkeypatch, tmp_path):
    assert f"funcConfig: {json.dumps(FUNC)}" in _list(monkeypatch, tmp_path, funcConfig=FUNC)


def test_a_later_list_without_it_is_shown_as_such(monkeypatch, tmp_path):
    _list(monkeypatch, tmp_path, funcConfig=FUNC)
    assert "funcConfig: null" in _list(monkeypatch, tmp_path)

