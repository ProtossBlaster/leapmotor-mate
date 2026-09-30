"""An installation still on the bundled SDK does not say WHY, and the answer is already stored (#338).

4.7.2 stopped pinning a `legacy` decision for the life of a release: a qualification that did not
finish is re-attempted every six hours. @dommi1966's bundle on 4.7.3 still reads `bundled SDK`, so the
re-attempt is happening and failing — and nothing anywhere says what it fails on. The activation
deliberately stores no exception and logs nothing, because the payload could carry credentials; but it
does write a `state` and a `reason` into the decision, and the time it last tried. Those three are not
secrets, and without them a bundle can only say that the installation is on the old client, which is
the question, not the answer.

The identity hash is never printed: it is derived from the account's own credentials, and nothing here
needs it.
"""
import json

import db as PollerDB
import db_reader
import diagnostics
import pytest


@pytest.fixture
def car(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    PollerDB.Database(path).ensure_vehicle("LVIN0000000000001", "C10", 2025)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setenv("DB_PATH", path)
    monkeypatch.setenv("MATE_API_V2", "0")
    return path


def _decide(decision):
    db_reader._conn_rw().execute(
        "INSERT OR REPLACE INTO settings(key,value) VALUES ('mate_api_migration_decision',?)",
        (json.dumps(decision),)).connection.commit()


def _client_line():
    return [l for l in diagnostics.build_bundle("9.9.9", parts=("info",)).splitlines()
            if l.startswith("Cloud client")][0]


def test_a_retained_decision_names_its_state_and_reason(car):
    _decide({"release": "4.4.0", "backend": "legacy", "state": "retained",
             "reason": "qualification_failed", "identity": "deadbeef", "attempted": 0})
    line = _client_line()
    assert "bundled SDK" in line
    assert "retained" in line and "qualification_failed" in line


def test_the_identity_hash_is_not_printed(car):
    _decide({"release": "4.4.0", "backend": "legacy", "state": "retained",
             "reason": "qualification_failed", "identity": "deadbeefcafe", "attempted": 0})
    assert "deadbeefcafe" not in _client_line()


def test_a_decision_from_before_the_retry_rule_says_it_was_never_attempted(car):
    """No `attempted` at all is what 4.4.0 wrote, and it is why such an installation was pinned."""
    _decide({"release": "4.4.0", "backend": "legacy", "state": "retained",
             "reason": "qualification_failed"})
    assert "never attempted" in _client_line()


def test_an_installation_with_no_decision_says_nothing_extra(car):
    """A new install has no row, and inventing a state for it would be a claim nobody made."""
    line = _client_line()
    assert line == "Cloud client : bundled SDK (leapmotor-api)"


def test_a_qualified_decision_is_not_called_never_attempted(car):
    """A qualified decision drops `attempted` on purpose — it succeeded. Reading that absence as
    "never attempted" would print the opposite of what happened, on the one decision that worked."""
    _decide({"release": "4.4.0", "backend": "independent", "state": "qualified",
             "identity": "deadbeef"})
    line = _client_line()
    assert "qualified" in line
    assert "never attempted" not in line
