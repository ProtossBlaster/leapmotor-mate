"""The first question every triage asks, and the bundle did not answer it (#327).

@arzthilfe's bundle never said which cloud client he had, and it had to be worked out from the
shape of a log line. Since 01/10/2026 there is one, Mate's own; the bundle still says so, whatever
an older version left in the environment.
"""
import db as PollerDB
import db_reader
import diagnostics
import pytest


@pytest.fixture
def car(tmp_path, monkeypatch):
    """An isolated database, and the environment the bundle reads pointed at it as well."""
    path = str(tmp_path / "t.db")
    PollerDB.Database(path).ensure_vehicle("LVIN0000000000001", "C10", 2025)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setenv("DB_PATH", path)
    return path


@pytest.mark.parametrize("left_over", [None, "0", "1"])
def test_the_client_is_named(car, monkeypatch, left_over):
    if left_over is None:
        monkeypatch.delenv("MATE_API_V2", raising=False)
    else:
        monkeypatch.setenv("MATE_API_V2", left_over)  # what an older version set at startup
    lines = [l for l in diagnostics.build_bundle("9.9.9", parts=("info",)).splitlines()
             if l.startswith("Cloud client")]
    assert lines == ["Cloud client : independent (mate-api)"]
