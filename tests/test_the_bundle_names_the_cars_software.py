"""The diagnostics bundle says which software the car runs.

Some reports turn on the firmware — #68 was "some B10 firmware reports the window flag as 2" — and
the bundle never said which firmware a report came from. The poller now keeps the version the cloud
tells the owner's account; the bundle prints it, or why there is none.
"""
import json

import db as PollerDB
import db_reader
import diagnostics
import pytest

VIN = "LVIN0000000000001"


@pytest.fixture
def car(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    PollerDB.Database(path).ensure_vehicle(VIN, "B10", 2025)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setenv("DB_PATH", path)
    return path


def _line(software=None):
    if software is not None:
        db_reader.set_setting(f"software_{VIN.lower()}", json.dumps(software))
    return [ln for ln in diagnostics.build_bundle("9.9.9", parts=("info",)).splitlines()
            if ln.startswith("Car software")]


UP_TO_DATE = {"state": "ok", "installed": "3.41.30", "installed_at_ms": 1790224725000,
              "latest": "3.41.30", "notes": "", "size_bytes": None}


def test_an_up_to_date_car_names_its_version_and_when_it_was_installed(car):
    assert _line(UP_TO_DATE) == ["Car software : 3.41.30 (installed 2026-09-24), up to date"]


def test_a_waiting_update_is_named_with_its_size(car):
    waiting = dict(UP_TO_DATE, latest="3.42.1", notes="Improvements", size_bytes=3 * 2**30 + 2**29)
    assert _line(waiting) == ["Car software : 3.41.30 (installed 2026-09-24), 3.42.1 waiting (3.5 GB)"]


def test_a_shared_account_says_why_there_is_no_version(car):
    assert _line({"state": "shared"})[0].startswith("Car software : not told (the car is shared")


def test_a_refusal_is_not_reported_as_sharing(car):
    assert _line({"state": "refused"}) == ["Car software : not told (the cloud refused, code 40)"]


def test_nothing_known_yet_says_so(car):
    assert _line() == ["Car software : unknown (not checked yet, or the cloud client cannot ask)"]
