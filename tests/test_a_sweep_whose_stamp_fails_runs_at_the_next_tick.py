"""A sweep whose start could not be stamped runs at the next tick.

The getEC and the elevation sweeps stamp their start in a setting, so that one runs at most every five
minutes. When that write fails (the database locked by another writer), the sweep is skipped and the
next tick tries again; the flag that keeps two sweeps from overlapping must not stay up for a sweep
that never started, or no trip is enriched again until the add-on restarts."""
import sqlite3
import threading

import pytest

import db as D
import db_reader
import ec_enrich
import elevation_enrich


@pytest.mark.parametrize("module,stamp", [(ec_enrich, "ec_sweep_at"), (elevation_enrich, "elev_sweep_at")],
                         ids=["getEC", "elevation"])
def test_a_sweep_whose_stamp_fails_runs_at_the_next_tick(tmp_path, monkeypatch, module, stamp):
    D.Database(str(tmp_path / "t.db")).close()
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(module, "_running", False)
    swept = threading.Event()
    monkeypatch.setattr(module, "_sweep_now", swept.set)
    write = db_reader.set_setting

    def locked_once(*_):
        monkeypatch.setattr(db_reader, "set_setting", write)
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(db_reader, "set_setting", locked_once)
    module.maybe_sweep()
    assert db_reader.get_setting(stamp, "") == "" and not swept.is_set()

    module.maybe_sweep()
    assert db_reader.get_setting(stamp, "") != ""
    assert swept.wait(5)
