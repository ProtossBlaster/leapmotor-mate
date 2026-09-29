"""The daily prune is attempted once a day even when it fails.

@dommi1966's bundle (#338) carried 1013 `database is locked` lines in four hours, and **266** of
them were the prune: a job meant to run once a day, attempted on every poll. The "done" mark
(`last_prune_ts`) was written as the LAST statement of the block, so anything that raised — a
locked database above all — left the gate open and the next poll tried again. On the filesystem
that produced those locks the retry cannot succeed either, so it only adds a write to a database
already contended, thirty seconds later, for as long as the condition lasts.

The mark belongs to the ATTEMPT, not to its success.
"""
import importlib.util
import pathlib
import sys

import db as D
import pytest


def _poller_main(name="poller_main_prune"):
    path = pathlib.Path(__file__).parents[1] / "poller" / "main.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


PM = _poller_main()


@pytest.fixture
def db(tmp_path, monkeypatch):
    d = D.Database(str(tmp_path / "p.db"))
    d.ensure_vehicle("VINPRUNE000000001", "B10")
    monkeypatch.setattr(PM, "_research_enabled", lambda: False)
    # the in-process gate is module state: a test that inherited it from the previous one would
    # pass or fail for the wrong reason (see [[a-test-that-reads-the-environment-is-not-isolated]])
    monkeypatch.setattr(PM, "_last_prune_attempt", 0.0)
    return d


def _attempts(db, monkeypatch, times, fail=False):
    """Call the prune `times` times on a clock that does not move, counting the attempts."""
    calls = []
    def prune_poll_log(days):
        calls.append(days)
        if fail:
            raise __import__("sqlite3").OperationalError("database is locked")
        return 0
    monkeypatch.setattr(db, "prune_poll_log", prune_poll_log)
    for _ in range(times):
        PM._prune_daily(db)
    return calls


def test_a_prune_that_worked_is_not_repeated_the_same_day(db, monkeypatch):
    assert len(_attempts(db, monkeypatch, 5)) == 1, "the gate does not hold after a good prune"


def test_a_prune_that_failed_is_not_repeated_on_the_next_poll(db, monkeypatch):
    """The defect: 266 attempts in four hours on a database that was locked for all of them."""
    calls = _attempts(db, monkeypatch, 20, fail=True)
    assert len(calls) == 1, (
        f"{len(calls)} attempts where the day allows one — a locked database turns the daily "
        f"prune into a retry on every poll (#338)")


def test_the_mark_is_written_even_when_the_prune_failed(db, monkeypatch):
    _attempts(db, monkeypatch, 1, fail=True)
    assert db.get_setting("last_prune_ts", "0") not in (None, "", "0"), (
        "nothing recorded that today's prune was attempted")


def test_the_next_day_tries_again(db, monkeypatch):
    now = {"t": 1_760_000_000.0}
    monkeypatch.setattr(PM.time, "time", lambda: now["t"])
    first = _attempts(db, monkeypatch, 3, fail=True)
    now["t"] += 86400 + 1
    second = _attempts(db, monkeypatch, 3, fail=True)
    assert len(first) == 1 and len(second) == 1, (first, second)


def test_a_database_that_takes_no_write_at_all_is_still_asked_once(db, monkeypatch, caplog):
    """The gate cannot live in the database alone.

    In @dommi1966's install the heartbeat was three hours stale, so the database was refusing
    writes — the mark among them. A gate that only remembers in the database can never be closed
    under exactly the condition it exists for, and the prune goes back to one attempt per poll,
    each leaving its line: 266 of them in four hours. So the attempt is also remembered here.

    What is counted is the ATTEMPT, not the pruning: with the mark written first, a database that
    refuses it never reaches the work at all — and the storm is the attempts.
    """
    import logging
    import sqlite3
    monkeypatch.setattr(db, "set_setting",
                        lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")))
    with caplog.at_level(logging.WARNING):
        for _ in range(20):
            PM._prune_daily(db)
    tries = [r for r in caplog.records if "DB prune skipped" in r.getMessage()]
    assert len(tries) <= 1, (
        f"{len(tries)} attempts on a database that accepted no write: the gate is only in the "
        f"database, so nothing could close it (#338)")
