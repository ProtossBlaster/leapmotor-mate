"""The age of the DATA, next to the age of the row (#178 @riri19).

He described the failure exactly: the car drops out of cloud coverage, the cloud keeps answering
every poll with the last frame it received, and Mate's "Last seen" cheerfully says 13s while the
position and battery on screen are half an hour old. `frame_ts` is the car's own clock on that
frame, so the difference is finally visible.

What these tests really pin is the SILENCE, which two rules protect.

A parked car legitimately carries hours-old data, so the age is announced only when the last frame
had the car DRIVING or CHARGING. That's #130's lesson: we turned down a red/green cloud light
because a car asleep in a garage also stops reporting, and "a light that cries wolf every night
isn't an alert, it's noise".

And it must have fallen behind THE ROW, not merely be old. If Mate itself hasn't polled for forty
minutes then both ages read forty minutes — the same fact twice, under the same label, which is the
duplicate-number defect this project has been shown before. The demo container caught that one:
`9 min ago · data 9m old`.
"""
import sqlite3
import time

import re

import pytest

import db_reader


def _db(tmp_path, monkeypatch, **row):
    """A positions table with a single row — the one the Overview reads."""
    p = tmp_path / "m.db"
    if p.exists():
        p.unlink()                 # a test may build two different rows in a row
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE vehicles (id INTEGER PRIMARY KEY, vin TEXT)")
    con.execute("INSERT INTO vehicles (id, vin) VALUES (1,'V')")
    con.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("CREATE TABLE command_log (id INTEGER PRIMARY KEY, outcome TEXT, latency_ms REAL)")
    con.execute("CREATE TABLE positions (id INTEGER PRIMARY KEY, vehicle_id INTEGER, "
                "recorded_at TEXT, latitude REAL DEFAULT 45.0, longitude REAL DEFAULT 9.0, "
                "charge_current_a REAL, charge_voltage_v REAL, "
                "gear TEXT, speed_kmh REAL, charging INTEGER, frame_ts INTEGER)")
    con.execute("INSERT INTO positions (vehicle_id, recorded_at, gear, speed_kmh, charging, frame_ts)"
                " VALUES (1,?,?,?,?,?)",
                (row.get("recorded_at") or _now_iso(), row.get("gear", "P"),
                 row.get("speed_kmh", 0), row.get("charging", 0), row.get("frame_ts")))
    con.commit()
    con.close()
    monkeypatch.setattr(db_reader, "DB_PATH", str(p))
    db_reader._get.cache_clear() if hasattr(db_reader._get, "cache_clear") else None
    return p


def _now_iso():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _ago(seconds):
    return int((time.time() - seconds) * 1000)


def test_driving_on_a_frozen_frame_says_how_old_it_is(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, gear="D", speed_kmh=50, frame_ts=_ago(1800))
    s = db_reader.get_latest_status()
    assert s["data_age"] == "30m"
    assert 1795 <= s["data_age_s"] <= 1805


def test_a_sleeping_car_stays_quiet(tmp_path, monkeypatch):
    """Eight hours in a garage: true, and precisely the noise #130 refused."""
    _db(tmp_path, monkeypatch, gear="P", speed_kmh=0, charging=0, frame_ts=_ago(8 * 3600))
    s = db_reader.get_latest_status()
    assert s["data_age"] is None
    assert s["data_age_s"] >= 8 * 3600 - 5      # still measured — just not announced


def test_a_charge_that_stopped_reporting_does_speak(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, gear="P", charging=1, frame_ts=_ago(3 * 3600 + 25 * 60))
    assert db_reader.get_latest_status()["data_age"] == "3h 25m"


def test_a_fresh_frame_while_driving_says_nothing(tmp_path, monkeypatch):
    """Below the threshold there is nothing to report — the usual case, and it must stay silent."""
    _db(tmp_path, monkeypatch, gear="D", speed_kmh=90, frame_ts=_ago(20))
    s = db_reader.get_latest_status()
    assert s["data_age"] is None
    assert s["data_age_s"] == pytest.approx(20, abs=3)


def test_threshold_is_not_crossed_early(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, gear="D", speed_kmh=90, frame_ts=_ago(db_reader.DATA_AGE_STALE_S - 30))
    assert db_reader.get_latest_status()["data_age"] is None
    _db(tmp_path, monkeypatch, gear="D", speed_kmh=90, frame_ts=_ago(db_reader.DATA_AGE_STALE_S + 30))
    assert db_reader.get_latest_status()["data_age"] is not None


def test_a_car_that_never_reports_its_clock_is_not_guessed_at(tmp_path, monkeypatch):
    """No frame_ts (older rows, or a model that doesn't send `sts`) → no invented number."""
    _db(tmp_path, monkeypatch, gear="D", speed_kmh=50, frame_ts=None)
    s = db_reader.get_latest_status()
    assert s["data_age"] is None and s["data_age_s"] is None


def test_every_language_carries_the_new_strings():
    """A missing key renders as the key itself — the row would read `data_age` at the user. And
    `ago_*` now carries what used to be hardcoded English, so a gap is a visible regression."""
    import json
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / "web" / "locales"
    for loc in ("it", "en", "de", "fr", "pl", "pt-PT"):
        flat = {}

        def walk(o):
            for k, v in o.items():
                walk(v) if isinstance(v, dict) else flat.setdefault(k, v)

        walk(json.loads((root / f"{loc}.json").read_text(encoding="utf-8")))
        for key in ("ago_s", "ago_m", "ago_h", "data_age", "data_age_hint"):
            assert key in flat, f"{loc}.json is missing {key}"
        assert "{n}" in flat["ago_m"] and "{age}" in flat["data_age"], loc


def test_both_places_that_show_a_time_ago_go_through_the_translator():
    """find-every-copy: `last seen` is rendered in TWO templates — the status card and the map
    popup. Translating one and forgetting the other is exactly how the Overview ends up speaking
    two languages at once, which is what this whole change is here to stop."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / "web" / "templates"
    for rel in ("partials/status_card.html", "overview.html"):
        html = (root / rel).read_text(encoding="utf-8")
        # ⚠️ This asserted the literal `ago(status.last_seen_s)` until v3.8.8, and went red the day
        # #232 changed WHICH seconds go in — the frame's age instead of the row's. The rule it
        # exists for is unchanged and is the one pinned here: the figure goes through the
        # translator. Pinning the argument as well made it a test of a decision it has no opinion
        # about. Which seconds are correct is test_last_seen_is_when_the_car_spoke.py's job.
        assert re.search(r"\bago\(\s*status\.", html), f"{rel} still renders a raw English string"
        assert "status.last_seen }}" not in html, f"{rel} still prints the untranslated last_seen"


def test_a_car_clock_ahead_of_the_host_is_not_staleness(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch, gear="D", speed_kmh=50, frame_ts=_ago(-600))
    s = db_reader.get_latest_status()
    assert s["data_age"] is None and s["data_age_s"] is None


def test_a_stalled_poller_does_not_print_the_same_number_twice(tmp_path, monkeypatch):
    """Mate itself hasn't polled for 40 minutes, so the row is 40m old and so is the data. Those
    are the SAME fact, and printing "40 min ago · data 40m old" is the duplicate-number defect.
    Nothing has fallen behind anything — say nothing."""
    import datetime
    old = (datetime.datetime.now(datetime.timezone.utc)
           - datetime.timedelta(minutes=40)).isoformat()
    _db(tmp_path, monkeypatch, recorded_at=old, gear="D", speed_kmh=70, frame_ts=_ago(40 * 60 + 5))
    s = db_reader.get_latest_status()
    assert s["data_age"] is None
    assert s["data_age_s"] >= 40 * 60          # measured, just not worth saying


def test_it_is_the_divergence_that_speaks(tmp_path, monkeypatch):
    """Same stalled poller, but the car stopped reporting half an hour BEFORE that — the row is
    40m old and the data 70m. Thirty minutes of it is genuinely the car's silence."""
    import datetime
    old = (datetime.datetime.now(datetime.timezone.utc)
           - datetime.timedelta(minutes=40)).isoformat()
    _db(tmp_path, monkeypatch, recorded_at=old, gear="D", speed_kmh=70, frame_ts=_ago(70 * 60))
    assert db_reader.get_latest_status()["data_age"] == "1h 10m"
