"""The Charges page's "Charging status" says at what time the charge ends (D#444, @banolka).

The car sends the minutes left; the panel showed only those. The time is the moment the car
measured them plus the minutes: the car's own clock (`frame_ts`) when it is not after our reading,
otherwise when Mate read them. Counting from "now" would push the end later by however old the
reading is. Shown in the zone every other time is shown in, with its day when that is not today.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

import db as poller_db
import db_reader
import pytest

T0 = datetime(2026, 10, 28, 9, 0, tzinfo=timezone.utc)          # 10:00 in Rome


@pytest.fixture
def car(tmp_path, monkeypatch):
    path = str(tmp_path / "d444.db")
    poller_db.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setattr(db_reader, "_current_vehicle_id", lambda: 1)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1, 'LB10000000000001', 'B10')")
    con.execute("INSERT INTO charges (id, vehicle_id, started_at, start_soc) VALUES (1, 1, ?, 40)",
                (T0.isoformat(),))
    con.commit()
    con.close()
    db_reader.set_setting("language", "en")
    db_reader.set_setting("timezone", "Europe/Rome")
    return path


def _reading(path, recorded, remaining, frame=None, charging=1):
    con = sqlite3.connect(path)
    con.execute("INSERT INTO positions (vehicle_id, recorded_at, soc, charging, plug_connected, "
                "charge_voltage_v, charge_current_a, remaining_charge_min, frame_ts, latitude, longitude) "
                "VALUES (1, ?, 50, ?, 1, 400, -16, ?, ?, 52.0, 7.0)",
                (recorded.isoformat(), charging, remaining,
                 int(frame.timestamp() * 1000) if frame else None))
    con.commit()
    con.close()


def _panel():
    pytest.importorskip("httpx", reason="Starlette's TestClient is built on httpx")
    import main
    from starlette.testclient import TestClient
    return TestClient(main.app).get("/api/charging-live").text


def _finish():
    import main
    return main._charge_finish(db_reader.get_latest_status())


def test_the_end_is_the_reading_plus_the_minutes_in_local_time(car):
    _reading(car, T0, 135)                                   # 10:00 + 2h15 → 12:15 in Rome
    assert _finish() == "12:15"
    assert "Ends at 12:15" in _panel()


def test_an_older_frame_counts_from_when_the_car_measured(car):
    """Read at 09:20 UTC a frame the car took at 09:00: its minutes are from 09:00."""
    _reading(car, T0 + timedelta(minutes=20), 135, frame=T0)
    assert _finish() == "12:15"


def test_a_car_clock_ahead_of_ours_is_not_taken(car):
    """A frame stamped after we read it cannot be when it was measured: our reading is."""
    _reading(car, T0, 135, frame=T0 + timedelta(minutes=7))
    assert _finish() == "12:15"


def test_an_end_on_another_day_carries_its_day(car):
    _reading(car, T0.replace(hour=22, minute=30), 100)       # 23:30 Rome + 1h40 → 01:10 on the 29th
    assert _finish() == "29 Oct 01:10"


def test_no_minutes_no_end(car):
    _reading(car, T0, None)
    assert _finish() is None
    assert "Ends at" not in _panel()


def test_not_charging_no_end(car):
    """Unplugged: the panel says nothing is charging, so it gives no end either, minutes or not."""
    _reading(car, T0, 135, charging=0)
    con = sqlite3.connect(car)
    con.execute("UPDATE positions SET plug_connected = 0, charge_current_a = 0")
    con.commit()
    con.close()
    assert "Ends at" not in _panel()
