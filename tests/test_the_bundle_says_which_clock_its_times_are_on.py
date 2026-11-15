"""The bundle says which clock each of its times is on. The tables are UTC, the logs carry the
container's local time and the pages show the zone chosen in Settings; a report of times "off by
seven hours" (#181) is a question of which two of the three disagree, and the bundle did not say.
"""
import os
import time

import db as PollerDB
import db_reader
import diagnostics
import pytest


@pytest.fixture
def car(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    PollerDB.Database(path).ensure_vehicle("LVIN0000000000001", "B10", 2025)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setenv("DB_PATH", path)
    return path


def _line():
    lines = [l for l in diagnostics.build_bundle("9.9.9", parts=("info",)).splitlines()
             if l.startswith("Timezone")]
    assert len(lines) == 1
    return lines[0]


@pytest.fixture
def container_clock():
    """The container runs on UTC, as a bare Docker container does; no zone held from another test."""
    before = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    time.tzset()
    db_reader._TZ_CACHE["key"] = "\x00"
    yield
    db_reader._TZ_CACHE["key"] = "\x00"
    if before is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = before
    time.tzset()


def test_a_zone_chosen_in_settings_is_named_beside_the_log_clock(car, container_clock):
    db_reader.set_setting("timezone", "Asia/Shanghai")
    assert _line() == ("Timezone     : shown in Asia/Shanghai (UTC+08:00 now, Settings: Asia/Shanghai) · "
                       "log clock UTC (UTC+00:00 now) · database UTC")


def test_automatic_shows_the_containers_zone(car, container_clock):
    assert _line() == ("Timezone     : shown in UTC (UTC+00:00 now, Settings: Automatic) · "
                       "log clock UTC (UTC+00:00 now) · database UTC")


def test_an_offset_west_of_greenwich_keeps_its_sign(car, container_clock):
    db_reader.set_setting("timezone", "America/Caracas")       # no daylight saving: always -04:00
    assert "shown in America/Caracas (UTC-04:00 now" in _line()


def test_the_log_clock_is_the_one_the_log_lines_are_stamped_with(car, container_clock, monkeypatch):
    """Mate sets a default TZ after the process clock has started; on Linux that does not move it."""
    monkeypatch.setattr(db_reader, "detected_tz_name", lambda: "Europe/Rome")
    assert "log clock UTC (UTC+00:00 now)" in _line()
