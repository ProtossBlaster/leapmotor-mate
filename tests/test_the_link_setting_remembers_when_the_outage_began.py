"""The poller keeps the state of its cloud link in settings, for the web and the MQTT bridge to
read: the SESSION in `poll_link` (ok / refused, since when, the error, when the next attempt is,
whether the password was blamed), and what the last fetch of EACH car came to in
`poll_link_<vin>` (ok / empty / failed). The session is the account's; two cars are two
requests, and one that keeps failing must not be reported healthy because the other answered.

`since` moves only when the STATE changes. The per-poll rows are kept a week, and an outage that
has lasted nine days (discussion #300) must still say when it began — a setting is where that
survives both the pruning and a restart of the poller.
"""
import json

import pytest
from poll_cycle_fixture import NOW, VIN, make_poll, make_startup, poller_main
from poll_cycle_fixture import frame as _frame

PM = poller_main("poller_main_poll_link")


@pytest.fixture
def poll(tmp_path, monkeypatch):
    return make_poll(PM, tmp_path, monkeypatch)


@pytest.fixture
def startup(tmp_path, monkeypatch):
    return make_startup(PM, tmp_path, monkeypatch)


def _link(db):
    return json.loads(db.get_setting("poll_link", "{}"))


def _fetch(db, vin=VIN):
    return json.loads(db.get_setting(f"poll_link_{vin.lower()}", "{}"))


def _refused(poll, advance=0.0, message="Leapmotor login failed: Error occurred"):
    poll.client.relogin_raises = RuntimeError(message)
    return poll(ConnectionError("Read timed out"), advance=advance)


def test_the_first_refusal_starts_the_clock(poll):
    db = _refused(poll, advance=120)
    link = _link(db)
    assert link["state"] == "refused"
    assert link["since"] == PM._utc_iso()
    assert link["next_retry_ts"] == poll.acct.last_relogin + poll.acct.relogin_wait_s
    assert link["bad_creds"] is False
    assert "login failed" in link["reason"]


def test_further_refusals_move_the_next_attempt_and_leave_since_alone(poll):
    db = _refused(poll, advance=120)
    began = _link(db)["since"]
    db = _refused(poll, advance=3600)
    link = _link(db)
    assert link["since"] == began, "the outage began once"
    assert link["next_retry_ts"] == poll.acct.last_relogin + poll.acct.relogin_wait_s
    assert link["next_retry_ts"] > NOW + 120 + 60


def test_a_frame_that_arrives_ends_the_outage(poll):
    _refused(poll, advance=120)
    db = poll(_frame(int((NOW + 3600 - 5) * 1000)), advance=3600)
    link = _link(db)
    assert link["state"] == "ok"
    assert link["since"] == PM._utc_iso()
    assert link["next_retry_ts"] is None and link["reason"] is None
    assert _fetch(db)["state"] == "ok"


def test_an_empty_answer_is_the_cars_state_not_the_sessions(poll):
    import client as _client
    db = poll(_client.EmptyStatusError("no live signals"))
    assert _fetch(db)["state"] == "empty"
    assert _link(db)["state"] == "ok", "the cloud let the poll in; the car said nothing"


def test_a_verdict_the_database_would_not_take_is_written_by_the_next_one(poll, monkeypatch):
    """The web reads the setting, not the poller's memory: a write that failed (the database was
    locked for a moment) leaves `failed` on record, and the next good frame must write `ok`
    again rather than find nothing changed."""
    db = poll(ConnectionError("Read timed out"), advance=120)
    assert _fetch(db)["state"] == "failed"
    real = db.set_setting

    def _locked(key, value):
        if key.startswith("poll_link"):
            raise RuntimeError("database is locked")
        real(key, value)
    monkeypatch.setattr(db, "set_setting", _locked)
    poll(_frame(int((NOW + 150) * 1000)), advance=30)
    assert _fetch(db)["state"] == "failed", "the write failed; the record still says so"
    monkeypatch.setattr(db, "set_setting", real)
    poll(_frame(int((NOW + 180) * 1000)), advance=30)
    assert _fetch(db)["state"] == "ok" and _link(db)["state"] == "ok"


def test_since_survives_a_restart_of_the_poller(poll):
    db = _refused(poll, advance=120)
    began = _link(db)["since"]
    restarted = PM.AccountState(link=PM._read_link(db))
    assert restarted.link["since"] == began
    restarted.note_link(db, "refused", "RuntimeError: still refused", next_retry_ts=NOW + 9999)
    assert _link(db)["since"] == began
    assert _link(db)["reason"] == "RuntimeError: still refused"


def test_a_refusal_worded_as_a_password_problem_says_so(poll):
    db = _refused(poll, advance=120, message="Leapmotor login failed: account or password error")
    assert _link(db)["bad_creds"] is True


def test_a_timeout_with_no_refusal_is_this_cars_failed_fetch(poll):
    db = poll(ConnectionError("Read timed out"), advance=120)
    fetch = _fetch(db)
    assert fetch["state"] == "failed"
    assert fetch["reason"].startswith("ConnectionError")
    assert fetch["next_retry_ts"] == NOW + 120 + poll.acct.relogin_wait_s
    assert _link(db)["state"] == "ok", "the re-login held; the session is fine"


def test_a_refusal_survives_the_timeouts_between_attempts(poll):
    """timeout → refused for the password → timeout before the next attempt → refused again:
    the session stays refused from the first refusal on, and the password stays blamed."""
    db = _refused(poll, advance=120, message="Leapmotor login failed: account or password error")
    began = _link(db)["since"]
    db = poll(ConnectionError("Read timed out"), advance=30)      # inside the re-login wait
    assert poll.client.relogin_calls == 1
    assert _link(db)["state"] == "refused" and _link(db)["since"] == began
    assert _link(db)["bad_creds"] is True
    assert _fetch(db)["state"] == "failed"
    db = _refused(poll, advance=3600, message="Leapmotor login failed: Error occurred")
    assert _link(db)["since"] == began and _link(db)["bad_creds"] is True


def test_a_frame_the_recorder_could_not_store_is_this_cars_failed_fetch(poll, monkeypatch):
    """The cloud let the request in — the session is fine — but the car's frame never reached
    the database, and the page must not show the previous one as current."""
    def _broken(data):
        raise KeyError("soc")
    monkeypatch.setattr(poll.ctx.recorder, "process", _broken)
    db = poll(_frame(int((NOW - 12) * 1000)))
    fetch = _fetch(db)
    assert fetch["state"] == "failed" and fetch["reason"] == "KeyError: 'soc'"
    assert fetch["next_retry_ts"] == NOW + poll.ctx.interval
    assert _link(db)["state"] == "ok"


def test_a_relogin_the_bridge_put_off_leaves_the_sessions_verdict_alone(poll):
    """Inside a minute of the last attempt the 4.x bridge does not ask the cloud: the session
    is exactly as refused as it was, since when it was, and the car's fetch is what failed."""
    db = _refused(poll, advance=120)
    began = _link(db)["since"]
    poll.client.relogin_raises = RuntimeError("Login temporarily deferred after a recent attempt")
    poll.client.relogin_authenticates = False
    db = poll(ConnectionError("Read timed out"), advance=3600)
    assert poll.client.relogin_calls == 2
    assert _link(db)["state"] == "refused" and _link(db)["since"] == began
    assert _fetch(db)["state"] == "failed"


def test_one_cars_failure_is_not_hidden_by_the_others_answer(poll):
    class _Second:
        vin, car_type, year, abilities, is_shared = "LFZB10LINK0000002", "B10", 2025, None, False
    other = PM.VehicleContext(poll.db, _Second(), poll.db.ensure_vehicle(_Second.vin, "B10"))
    poll(ConnectionError("Read timed out"), advance=120)
    poll.client.next = _frame(int((NOW + 120) * 1000))
    PM._poll_vehicle(poll.db, poll.client, other, poll.acct)
    assert _fetch(poll.db)["state"] == "failed", "the first car's timeout stands"
    assert _fetch(poll.db, _Second.vin)["state"] == "ok"
    assert _link(poll.db)["state"] == "ok"


def test_a_refused_startup_login_is_written_before_the_first_poll(startup):
    """The setting must exist while the poller is still waiting to get in — that is exactly the
    stretch where the Overview has nothing else to go on."""
    _, db, now = startup(refusals=2)
    link = _link(db)
    assert link["state"] == "refused"
    assert link["next_retry_ts"] is not None and link["next_retry_ts"] <= now + 5
    assert link["bad_creds"] is False


def test_a_startup_login_that_timed_out_is_the_sessions_failed_state(startup):
    """A refusal is the cloud's word; a login that never reached it is `failed` — and the web and
    the bridge read that as every car's fetch failing, not as an unknown age."""
    _, db, _ = startup(refusals=1, error=ConnectionError("Read timed out"))
    link = _link(db)
    assert link["state"] == "failed" and link["reason"] == "ConnectionError: Read timed out"


def test_the_poller_records_when_it_started(startup):
    """Uptime for the Overview's link tile: a stopped-and-restarted poller says so."""
    _, db, now = startup(refusals=1)
    assert abs(float(db.get_setting("poller_started_ts", "0")) - NOW) < 1
