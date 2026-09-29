"""An installation kept on the bundled SDK is asked again, instead of being pinned for ever.

@dommi1966, #338. His 4.7.1 bundle is the only one of the public set that reads
`Cloud client : bundled SDK (leapmotor-api)` — every other one reads `independent (mate-api)` —
and his poller log carries 27 `login failed` and 13 `Token is invalid`.

Why he is there: the backend decision is taken once and stored under `RELEASE`, which has read
'4.4.0' since 4.4.0. Whatever it decided then is returned untouched at every start since, through
4.5, 4.6, 4.7 and 4.7.1. His first bundle showed 747 `database is locked` and a skipped schema
check — the installation was in exactly the state that makes a qualification fail, and it copies
the database into a stage and performs a live cloud login inside a 15-second subprocess.

🔑 There is no longer any such thing as an account that does not qualify. Since 4.2.0 every model
does (`_qualify_staged`: "Every model qualifies"), and 4.4.0 removed the literal `['B10']` that had
kept the others out (#327, #330). So EVERY stored `legacy` decision is a qualification that did not
finish — a timeout, a locked database, a login the cloud refused that minute — and not one of them
is a verdict about the account. Caching a missing answer as though it were an answer is the defect.

It is still not retried on every start: a container in a restart loop would hammer a cloud that
rations logins (#296), so a failure is honoured for a while and re-attempted afterwards. A decision
stored before this rule carries no stamp at all and is re-attempted at once, which is what moves
every installation stuck since 4.4.0.
"""
import json
import os
import sqlite3
import time

import pytest

import mate_api  # noqa: F401 — installs the runtime shim the two modules below import through
import migration_activation as activation
import migration_preflight


@pytest.fixture
def installation(tmp_path, monkeypatch):
    db = tmp_path / "mate.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)")
        conn.executemany("INSERT INTO settings VALUES (?,?)", [
            ("leapmotor_user", "owner"), ("leapmotor_pass", "stored-password")])
    (tmp_path / "secret.key").write_bytes(b"existing-key")
    monkeypatch.setenv("DB_PATH", str(db))
    monkeypatch.delenv("MATE_DEMO", raising=False)
    # `activate_installation` picks the backend by WRITING os.environ['MATE_API_V2'] itself, which
    # monkeypatch cannot undo because it never went through monkeypatch. Claiming it here does:
    # monkeypatch remembers what was there (or that nothing was) and puts it back at teardown.
    # Left behind, a test that ends on a failed qualification hands the whole rest of the suite an
    # installation running the bundled SDK — measured: 13 unrelated tests turn red on
    # "Missing local app certificate material".
    monkeypatch.setenv("MATE_API_V2", os.environ.get("MATE_API_V2", "1"))
    return db


def _decision(db):
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?",
                           (activation.DECISION_KEY,)).fetchone()
    return json.loads(row[0]) if row else None


def _counting_qualifier(monkeypatch, outcome):
    calls = []

    def qualify(*args):
        calls.append(1)
        return dict(outcome)

    monkeypatch.setattr(migration_preflight, "qualify_installation", qualify)
    return calls


def test_a_failure_is_re_attempted_at_the_next_start(installation, monkeypatch):
    """The whole of #338 in one assertion: a cloud that was unreachable once is asked again."""
    calls = _counting_qualifier(monkeypatch, {"state": "failed", "reason": "timeout"})
    activation.activate_installation()
    with sqlite3.connect(installation) as conn:   # the machine was simply restarted later
        stored = _decision(installation)
        stored["attempted"] = time.time() - activation.RETRY_AFTER_S - 1
        conn.execute("INSERT OR REPLACE INTO settings VALUES (?,?)",
                     (activation.DECISION_KEY, json.dumps(stored, sort_keys=True)))
    activation.activate_installation()
    assert len(calls) == 2, "the installation was never asked again"


def test_a_restart_loop_does_not_hammer_the_cloud(installation, monkeypatch):
    """A failure is honoured for a while: the qualification logs in, and the cloud rations that."""
    calls = _counting_qualifier(monkeypatch, {"state": "failed", "reason": "timeout"})
    activation.activate_installation()
    for _ in range(5):
        assert activation.activate_installation()["backend"] == "legacy"
    assert len(calls) == 1, f"a restart loop asked the cloud {len(calls)} times"


def test_a_decision_stored_before_this_rule_is_re_attempted_at_once(installation, monkeypatch):
    """What every installation pinned since 4.4.0 actually carries: a decision with no stamp."""
    with sqlite3.connect(installation) as conn:
        conn.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (
            activation.DECISION_KEY,
            json.dumps({"release": activation.RELEASE, "backend": "legacy", "state": "retained",
                        "reason": "qualification_failed",
                        "identity": activation._identity(dict(conn.execute(
                            "SELECT key,value FROM settings")))}, sort_keys=True)))
    calls = _counting_qualifier(monkeypatch, {"state": "failed", "reason": "timeout"})
    activation.activate_installation()
    assert calls == [1], "an installation stuck on the bundled SDK since 4.4.0 was not asked again"


def test_a_qualified_decision_is_never_re_attempted(installation, monkeypatch):
    """A success is expensive and durable — it promotes session material. It stays cached."""
    def qualify(source, stage):
        with sqlite3.connect(source) as src, sqlite3.connect(stage / source.name) as dst:
            src.backup(dst)
            dst.execute("INSERT OR REPLACE INTO settings VALUES ('api_v2_shared_session','s')")
        return {"state": "qualified", "capabilities": ["B03X"]}
    calls = []
    monkeypatch.setattr(migration_preflight, "qualify_installation",
                        lambda *a: (calls.append(1), qualify(*a))[1])
    first = activation.activate_installation()
    assert first["backend"] == "independent"
    for _ in range(3):
        assert activation.activate_installation() == first
    assert calls == [1], "a qualified installation was made to log in again"


def test_the_stamp_is_written_on_a_failure(installation, monkeypatch):
    """Without it the brake above cannot exist, and the retry cannot be bounded."""
    _counting_qualifier(monkeypatch, {"state": "failed", "reason": "timeout"})
    activation.activate_installation()
    stored = _decision(installation)
    assert stored["backend"] == "legacy"
    assert abs(stored.get("attempted", 0) - time.time()) < 60, \
        f"a failed decision carries no usable attempt time: {stored!r}"
