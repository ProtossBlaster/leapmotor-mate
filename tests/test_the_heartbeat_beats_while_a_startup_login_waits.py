"""The poller's heartbeat is written once per poll round — and the first round starts only after
the startup login has succeeded.

While the cloud keeps refusing that login the poller is alive and retrying on a growing backoff,
but nothing says so: `last_loop_ts` stays at zero, /healthz answers 503, and anything that reads
the heartbeat to tell "the poller is gone" from "the poller is waiting for the cloud" sees the
same thing in both cases. In discussion #300 the cloud refused an account's logins for nine
days; a poller in that state must read as alive and refused, not as dead.
"""
import pytest
from poll_cycle_fixture import make_startup, poller_main

PM = poller_main("poller_main_startup_beat")


@pytest.fixture
def startup(tmp_path, monkeypatch):
    return make_startup(PM, tmp_path, monkeypatch)


def test_the_heartbeat_is_written_while_the_login_is_refused(startup):
    client, db, now = startup(refusals=3)
    assert client.attempts == 4, "three refusals, then the attempt the test stopped on"
    beat = float(db.get_setting("last_loop_ts", "0"))
    assert beat > 0, "a poller waiting on a refused login left no heartbeat at all"
    assert now - beat <= 5, "and the last beat is from the wait just before the retry"


def test_the_refusal_is_still_recorded_for_the_setup_screen(startup):
    """The heartbeat is added beside the existing status, not instead of it."""
    _, db, _ = startup(refusals=1)
    assert "login failed" in db.get_setting("poller_login_error", "")


def test_only_a_refusal_worded_as_a_credentials_problem_counts_as_one():
    """The startup wait is an hour on bad credentials and seconds on anything else, so the words
    that decide it live in one place."""
    assert PM._is_bad_credentials("Leapmotor login failed: account or password error")
    assert PM._is_bad_credentials("Incorrect password")
    assert not PM._is_bad_credentials("Leapmotor login failed: Error occurred")
    assert not PM._is_bad_credentials("HTTPSConnectionPool: Read timed out")
    assert not PM._is_bad_credentials("")
