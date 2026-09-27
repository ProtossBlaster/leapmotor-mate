"""Every poll, and every login attempt, leaves one row in `poll_log` saying how it went.

In discussion #300 the question "is Mate getting data from the cloud at all?" was answered by
counting accepted and refused logins by hand out of a week of poller log, and the count still could
not say which of the two processes — the poller or the web page the user had just opened — made
the login that brought the readings back. A table answers both without counting.

Two facts per poll, deliberately kept apart: what the REQUEST did (answer / empty / failed /
refused) and how old the frame was when it arrived, measured against the host clock at poll
time. An answer can carry a frame that is hours old, and a repeated frame can still be current;
a frame without a clock, or with a clock ahead of the host, has no age a row may claim.

One request, one row — and the row is written once the frame is on record, so a frame the poller
could not store is a failed fetch with our error beside it, not an answer. A login row is written
by the backend from the one place it authenticates, whichever call needed the login.
"""
from datetime import datetime, timedelta, timezone

import db as D
import pytest
import session_share
from poll_cycle_fixture import NOW, VIN, make_poll, make_startup, poller_main
from poll_cycle_fixture import frame as _frame

PM = poller_main("poller_main_poll_log")


@pytest.fixture
def poll(tmp_path, monkeypatch):
    return make_poll(PM, tmp_path, monkeypatch)


def _rows(db, kind="poll"):
    return [dict(r) for r in db._conn.execute(
        "SELECT * FROM poll_log WHERE kind=? ORDER BY id", (kind,)).fetchall()]


# ── the request's outcome ─────────────────────────────────────────────────────

def test_an_answer_leaves_a_row_with_the_frames_age_at_poll_time(poll):
    db = poll(_frame(int((NOW - 12) * 1000)))
    (row,) = _rows(db)
    assert row["outcome"] == "answer" and row["vehicle_id"] == poll.vid
    assert row["frame_age_s"] == 12
    assert row["reason"] is None


def test_the_age_is_measured_when_the_poll_runs_not_when_it_is_read(poll):
    """Yesterday's green cell must stay green: the age is a fact about the poll, stored with it."""
    db = poll(_frame(int((NOW - 12) * 1000)))
    poll(_frame(int((NOW - 12) * 1000)), advance=3600)     # the same frame, an hour later
    first, second = _rows(db)
    assert first["frame_age_s"] == 12
    assert second["frame_age_s"] == 3612


def test_a_frame_without_a_clock_has_no_age(poll):
    db = poll(_frame(0))
    (row,) = _rows(db)
    assert row["outcome"] == "answer" and row["frame_age_s"] is None


def test_a_car_clock_ahead_of_the_host_has_no_age_either(poll):
    """A clock 48 s ahead was measured in the wild; that is drift, not freshness to be claimed."""
    db = poll(_frame(int((NOW + 48) * 1000)))
    (row,) = _rows(db)
    assert row["frame_age_s"] is None


def test_an_empty_answer_is_recorded_as_empty(poll):
    import client as _client
    db = poll(_client.EmptyStatusError("vehicle status has no live signals"))
    (row,) = _rows(db)
    assert row["outcome"] == "empty" and row["frame_age_s"] is None


def test_a_timeout_is_a_failed_fetch_with_the_error_kept(poll):
    db = poll(ConnectionError("HTTPSConnectionPool: Read timed out"))
    (row,) = _rows(db)
    assert row["outcome"] == "failed"
    assert row["reason"].startswith("ConnectionError: ") and "timed out" in row["reason"]


def test_a_rejected_session_is_refused_not_failed(poll):
    db = poll(RuntimeError("Session rejected; command/read not retried"))
    (row,) = _rows(db)
    assert row["outcome"] == "refused"


@pytest.mark.parametrize("message, outcome", [
    ("Session rejected; command/read not retried", "refused"),
    ("Session unavailable; invalidated without replay", "refused"),
    ("401 Unauthorized", "refused"),
    ("Leapmotor login failed: Error occurred", "refused"),
    # the 4.x bridge names the stage a sign-in failed at: only the cloud's own rejection is a refusal
    ("New API sign-in unavailable; stage=cloud_rejection; HTTP=200; API=39; no automatic retry", "refused"),
    ("New API sign-in unavailable; stage=transport; no automatic retry", "failed"),
    ("New API sign-in unavailable; stage=account_certificate; HTTP=200; API=0; no automatic retry", "failed"),
    # put off locally by the bridge, inside a minute of the last attempt: the cloud saw nothing
    ("Login temporarily deferred after a recent attempt", "failed"),
    ("Could not find the TLS certificate file", "failed"),
    ("('Connection aborted.', RemoteDisconnected(...))", "failed"),
    ("New API response could not be decoded", "failed"),
])
def test_what_counts_as_refused(message, outcome):
    """One rule for both processes: the poller's rows and the web's come from the same list."""
    got, reason = session_share.error_outcome(RuntimeError(message))
    assert got == outcome
    assert reason == f"RuntimeError: {message}"


# ── logins, by whoever made them ──────────────────────────────────────────────

def test_a_refused_relogin_leaves_a_login_row_from_the_poller(poll):
    poll.client.relogin_raises = RuntimeError("Leapmotor login failed: Error occurred")
    db = poll(ConnectionError("Read timed out"), advance=120)
    assert poll.client.relogin_calls == 1
    (row,) = _rows(db, "login")
    assert (row["outcome"], row["process"]) == ("refused", "poller")
    assert "login failed" in row["reason"]


def test_a_relogin_that_logged_in_leaves_an_ok_row(poll):
    db = poll(ConnectionError("Read timed out"), advance=120)
    (row,) = _rows(db, "login")
    assert (row["outcome"], row["process"], row["reason"]) == ("ok", "poller", None)


def test_a_refresh_that_held_is_not_a_login(poll):
    """relogin() spends a token refresh first; only a real login is one the cloud rationed."""
    poll.client.relogin_authenticates = False
    db = poll(ConnectionError("Read timed out"), advance=120)
    assert poll.client.relogin_calls == 1 and _rows(db, "login") == []


def test_a_frame_the_recorder_could_not_store_is_one_failed_fetch_with_our_error(poll, monkeypatch):
    """The cloud answered, but nothing of it reached the database: the page would go on showing
    the previous frame as current. One request, one row — `failed`, with the error that is ours,
    and the cloud's error counter untouched (no re-login heals a bug of our own)."""
    def _broken(data):
        raise KeyError("soc")
    monkeypatch.setattr(poll.ctx.recorder, "process", _broken)
    db = poll(_frame(int((NOW - 12) * 1000)))
    (row,) = _rows(db)
    assert row["outcome"] == "failed" and row["reason"] == "KeyError: 'soc'"
    assert poll.ctx.poll_error_count == 0 and poll.client.relogin_calls == 0


def test_a_failure_after_the_frame_is_on_record_leaves_the_answer_standing(poll, monkeypatch):
    """What breaks after the recorder — comfort state, the ABRP push, the bridge — is a failure
    of ours the log names; the frame is stored and current, so the request was an answer."""
    def _broken(db, data):
        raise RuntimeError("comfort state")
    monkeypatch.setattr(PM, "_write_comfort_state", _broken)
    db = poll(_frame(int((NOW - 12) * 1000)))
    assert [(r["outcome"], r["frame_age_s"]) for r in _rows(db)] == [("answer", 12)]


@pytest.mark.parametrize("then, rows", [(True, ["ok"]), (False, [])])
def test_the_startup_login_is_a_row_only_when_the_cloud_was_asked(tmp_path, monkeypatch, then, rows):
    _, db, _ = make_startup(PM, tmp_path, monkeypatch)(0, then=then)
    assert [r["outcome"] for r in _rows(db, "login")] == rows


def test_a_login_the_web_makes_lands_in_the_same_table(tmp_path, monkeypatch):
    """Through the client the web builds, with only the cloud faked: the backend's `on_login`
    has to be wired where the client is made, or a login the web makes leaves no row."""
    import types

    import command_client
    import db_reader
    path = str(tmp_path / "web.db")
    D.Database(path)                               # the schema
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setenv("DB_PATH", path)            # session_share looks for the shared token here

    class _Cloud:                                  # the legacy client, as session_share wraps it
        def __init__(self, **kw):
            self.logins = 0

        def login(self):
            self.logins += 1

        def get_vehicle_list(self):
            return [types.SimpleNamespace(vin="VIN1", car_type="c10")]
    monkeypatch.setattr(command_client, "LeapmotorApiClient", _Cloud)
    monkeypatch.setattr(command_client, "_get_credentials", lambda: ("u", "p", "1234"))
    session = command_client.LeapmotorSession()
    session._connect()
    assert session._api.logins == 1
    rows = [tuple(r) for r in db_reader._conn_rw().execute(
        "SELECT outcome, process, reason FROM poll_log ORDER BY id").fetchall()]
    assert rows == [("ok", "web", None)]


def test_a_login_the_history_sync_makes_is_the_pollers(tmp_path, monkeypatch):
    """The cloud-history worker builds a client of its own, in the poller's process: a login it
    spends is the poller's row, and a session it resumes is no login at all. Through the real
    bridge, with only the cloud replaced."""
    import sqlite3
    import types

    import api_v2_bridge as bridge  # the module the worker imports, not a second copy
    import crypto
    import history_worker as worker
    path = str(tmp_path / "history.db")
    db = D.Database(path)
    db.ensure_vehicle(VIN, "B10")
    db.set_setting("leapmotor_user", crypto.encrypt("u"))
    db.set_setting("leapmotor_pass", crypto.encrypt("p"))
    db.set_setting("mate_device_id", "dev")
    for mod in (bridge, worker):
        monkeypatch.setattr(mod, "DB", path)
        monkeypatch.setattr(mod, "connect_db", lambda: sqlite3.connect(path))
    monkeypatch.setattr(bridge, "certificate_usable", lambda cert, key: True)
    monkeypatch.setenv("MATE_API_V2", "1")
    monkeypatch.setenv("MATE_LAB_LOGIN_ONCE", "1")
    # a token shaped like the cloud's (header.payload.signature) with no device binding inside
    session = types.SimpleNamespace(token="h.e30.s", user_id="U", device_id="D", key=b"k" * 32, client_cert=("c", "k"),
                                    expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    monkeypatch.setattr(bridge.NewAPIClient, "_authenticate_session", lambda self: session)
    # the wire answers an account with no cars: the sync stops right after the login it needed
    monkeypatch.setattr(bridge.NewAPIClient, "_wire",
                        lambda self, *a, **k: ({"data": {"bindcars": [], "sharedcars": []}}, None))

    def _rows():
        return [tuple(r) for r in db._conn.execute(
            "SELECT outcome, process FROM poll_log WHERE kind='login' ORDER BY id").fetchall()]
    worker.sync_once(on_login=lambda exc: PM._note_login(path, exc))
    assert _rows() == [("ok", "poller")]
    worker.sync_once(on_login=lambda exc: PM._note_login(path, exc))
    assert _rows() == [("ok", "poller")], "the second sync resumed the session it had saved"


def test_a_login_row_from_another_thread_does_not_share_the_poll_loops_connection(tmp_path):
    """The cloud-history worker logs in from its own thread while the poll loop may be
    mid-transaction on the poller's connection. One connection under two threads interleaved
    them — commits with no transaction, rows lost — so the login row takes a connection of its
    own, and both sides write every row."""
    import threading
    path = str(tmp_path / "threads.db")
    db = D.Database(path)
    vid = db.ensure_vehicle(VIN, "B10")

    def _logins():
        for _ in range(1000):
            PM._note_login(path, None)
    other = threading.Thread(target=_logins)
    other.start()
    for _ in range(1000):
        db.log_poll(vid, "answer", 12)
    other.join()
    counts = dict(db._conn.execute("SELECT kind, COUNT(*) FROM poll_log GROUP BY kind").fetchall())
    assert counts == {"login": 1000, "poll": 1000}


# ── the table is kept a week ──────────────────────────────────────────────────

def test_rows_older_than_a_week_are_pruned(tmp_path):
    db = D.Database(str(tmp_path / "prune.db"))
    now = datetime.now(timezone.utc)
    for days in (8, 6):
        db._conn.execute("INSERT INTO poll_log (at, kind, outcome) VALUES (?, 'poll', 'answer')",
                         ((now - timedelta(days=days)).isoformat(),))
    db._conn.commit()
    assert db.prune_poll_log(7) == 1
    assert db._conn.execute("SELECT COUNT(*) FROM poll_log").fetchone()[0] == 1
