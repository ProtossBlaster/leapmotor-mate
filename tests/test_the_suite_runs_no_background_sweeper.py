"""Importing web/main.py in the suite starts no background sweeper.

main.py starts three timers at import (ec_enrich, elevation_enrich, place_lookup). In the suite
they outlived the test that imported main and went on reading `db_reader.DB_PATH` — which by then
a later test had pointed at its own temporary file. place_lookup's ticks every 60 s: one that
landed while a restore test built its backup held a second connection on it, so the test's
close() was not the last one, the WAL stayed in old.db-wal and the backup read was a one-page
file, "missing tables" (CI on main, 1b6d931). conftest marks the three as started instead."""
import threading


def test_importing_main_starts_no_background_sweeper():
    import main  # noqa: F401
    loops = [t.name for t in threading.enumerate()
             if getattr(getattr(t, "_target", None), "__qualname__", "") == "start_background.<locals>._loop"]
    assert loops == []
