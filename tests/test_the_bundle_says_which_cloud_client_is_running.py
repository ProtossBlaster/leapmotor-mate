"""The first question every triage asks, and the bundle did not answer it (#327).

Mate 4 runs one of two cloud clients: the independent one, or the bundled SDK for an account
the activation decided does not qualify. They fail differently — #327 was a refusal that only
happens on the SDK — and @arzthilfe's bundle never says which one he had. It had to be worked
out from the shape of a log line, which is guesswork dressed as evidence.
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


def _info_lines(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("MATE_API_V2", raising=False)
    else:
        monkeypatch.setenv("MATE_API_V2", value)
    return [l for l in diagnostics.build_bundle("9.9.9", parts=("info",)).splitlines()
            if l.startswith("Cloud client")]


def test_the_independent_client_is_named(car, monkeypatch):
    assert _info_lines(monkeypatch, "1") == ["Cloud client : independent (mate-api)"]


def test_the_bundled_sdk_is_named(car, monkeypatch):
    assert _info_lines(monkeypatch, "0") == ["Cloud client : bundled SDK (leapmotor-api)"]


def test_an_unset_backend_says_so_instead_of_guessing(car, monkeypatch):
    """Absent is not the same as legacy: `api_backend` treats anything but '0' as independent."""
    assert _info_lines(monkeypatch, None) == ["Cloud client : independent (mate-api)"]
