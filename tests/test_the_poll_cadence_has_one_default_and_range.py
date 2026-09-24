"""The poller's cadence (Settings ▸ Poll) as the web's Python reads it: one default and one range
per state.

The settings form clamped what it stored, and `_parked_poll_seconds` repeated the same clamp and
default by hand beside it. The form's clamp reads the named constants now, and the READY carry
window reads `poll_seconds`, built on the same constants — so neither can drift from the other.
The settings page and the diagnostics bundle still print the stored values with their own
fallbacks; they are not covered here.
"""
import db as D
import db_reader
import pytest


@pytest.fixture
def settings(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    D.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)


@pytest.mark.parametrize("driving, default", [(True, db_reader.POLL_DRIVING_DEFAULT_S),
                                               (False, db_reader.POLL_PARKED_DEFAULT_S)])
def test_an_unset_cadence_reads_the_default(settings, driving, default):
    assert db_reader.poll_seconds(driving=driving) == default


@pytest.mark.parametrize("key, driving, stored, expected", [
    ("poll_driving", True, "3", db_reader.POLL_DRIVING_RANGE_S[0]),
    ("poll_driving", True, "500", db_reader.POLL_DRIVING_RANGE_S[1]),
    ("poll_parked", False, "1", db_reader.POLL_PARKED_RANGE_S[0]),
    ("poll_parked", False, "99999", db_reader.POLL_PARKED_RANGE_S[1]),
])
def test_a_hand_edited_row_is_held_to_the_forms_range(settings, key, driving, stored, expected):
    db_reader.set_setting(key, stored)
    assert db_reader.poll_seconds(driving=driving) == expected


@pytest.mark.parametrize("stored", ["fast", "inf", "1e999", "nan"])
def test_a_value_that_is_not_a_usable_number_reads_the_default(settings, stored):
    db_reader.set_setting("poll_driving", stored)
    assert db_reader.poll_seconds(driving=True) == db_reader.POLL_DRIVING_DEFAULT_S


def test_the_ready_carry_window_reads_the_same_parked_cadence(settings):
    db_reader.set_setting("poll_parked", "120")
    assert db_reader._parked_poll_seconds() == db_reader.poll_seconds(driving=False) == 120
