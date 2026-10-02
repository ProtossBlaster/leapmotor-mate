"""One write that finds the database busy does not stop every write after it (#338).

@dommi1966, 02/10/2026, Mate 4.7.15, a B03X and a T03 on one account: from 01:06:49 every frame failed
with `database is locked`, 3,814 times in 1,061 minutes, until the container was restarted at 18:53.
Nothing was stored in those 17 hours: the Overview stayed at 74 % and 75 % while the cars charged to
about 90 % and 97 %, both charges were closed at those values when Mate came back, and a 10 km trip of
the B03X is missing. The same on 30/09 (118 minutes) and 01/10 (170 minutes).

Reproduced with the real code. Something holds the write lock longer than the poller's connection
waits for it (5 s), once, and the poller's write fails. Python leaves the transaction that write
opened in place, and nothing rolled it back. The next read inside it holds on to the database as it
was; as soon as another connection writes — the bridge writes its request log on its own connection
at every cloud request, so every poll does — each write of the poller fails at once, for good. Who
held the lock for those first five seconds is not known; that is what turned a hiccup into hours.

Every poll of a car now starts by ending a transaction a failed write left open.
"""
import sqlite3

import pytest

import mate_api  # noqa: F401 — puts poller/mate_api_runtime on sys.path, as the poller process does
import api_v2_bridge as bridge
from poll_cycle_fixture import NOW, make_poll, poller_main
from poll_cycle_fixture import frame as _frame


@pytest.fixture
def poll(tmp_path, monkeypatch):
    PM = poller_main("poller_main_failed_write")
    run = make_poll(PM, tmp_path, monkeypatch)
    path = str(tmp_path / "poll.db")
    monkeypatch.setattr(bridge, "DB", path)
    # What the poller waits for a busy database is 5 s; the test waits a fifth of a second.
    run.db._conn.execute("PRAGMA busy_timeout = 200")
    # Every cloud request writes a row in the bridge's request log, on the bridge's own connection.
    status = run.client.get_status
    run.audit = True

    def get_status(vehicle=None):
        if run.audit:
            bridge.NewAPIClient._audit(None, "/app/app-signal-service/signal/info/query", "POST", 200, "0")
        return status(vehicle)

    run.client.get_status = get_status
    run.path, run.PM = path, PM
    return run


def _positions(run):
    return run.db._conn.execute("SELECT COUNT(*) FROM positions WHERE vehicle_id = ?",
                                (run.vid,)).fetchone()[0]


def _busy_while(run, poll_once):
    """Another connection holds the write lock for the whole of one poll, then lets go."""
    other = sqlite3.connect(run.path)
    other.execute("BEGIN IMMEDIATE")
    other.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('held_by_another_writer', '1')")
    run.audit = False                 # its request log would wait for the same lock
    try:
        poll_once()
    finally:
        other.commit()
        other.close()
        run.audit = True


def test_a_frame_that_could_not_be_stored_does_not_stop_the_next_ones(poll):
    t = int(NOW * 1000)
    poll(_frame(t))
    assert _positions(poll) == 1
    _busy_while(poll, lambda: poll(_frame(t + 30_000), advance=30))
    assert _positions(poll) == 1                    # that one is lost: the database was busy
    for i in range(2, 7):
        poll(_frame(t + i * 30_000), advance=30)
    assert _positions(poll) == 6                    # every one after it is stored


def test_after_a_daily_prune_that_found_the_database_busy_the_next_frame_is_stored(poll):
    t = int(NOW * 1000)
    poll(_frame(t))
    poll.db.set_setting("last_prune_ts", "0")
    poll.PM._last_prune_attempt = 0
    _busy_while(poll, lambda: poll.PM._prune_daily(poll.db))
    for i in range(1, 4):
        poll(_frame(t + i * 30_000), advance=30)
    assert _positions(poll) == 4


def test_a_poll_with_nothing_left_open_does_not_touch_the_connection(poll, caplog):
    t = int(NOW * 1000)
    for i in range(3):
        poll(_frame(t + i * 30_000), advance=30)
    assert _positions(poll) == 3
    assert "rolled back" not in caplog.text
