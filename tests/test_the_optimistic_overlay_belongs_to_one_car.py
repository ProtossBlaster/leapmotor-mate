"""A command sent to one car was replayed onto the next car you selected (30/08 audit).

When you press a command, Mate shows the result immediately instead of waiting for the cloud to
confirm — an optimistic overlay, held in memory for 30 seconds so the poller cannot overwrite the
row underneath it and flash the pre-command state back (#34).

The row written to the database was scoped to the current car; the in-memory copy was one dict for
the whole process. So: send "climate on" to the C10, switch to the T03 within the TTL, and the T03
draws itself with the climate on. Nothing was wrong in the database — the picture was a lie for
thirty seconds, on the car you did not command.

CI-safe: db_reader only, no fastapi.
"""
import sqlite3
import time

import db as poller_db
import db_reader
import pytest


@pytest.fixture()
def two_cars(tmp_path, monkeypatch):
    path = str(tmp_path / "c.db")
    poller_db.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    con = sqlite3.connect(path)
    for vid, vin in ((1, "CAR-ONE"), (2, "CAR-TWO")):
        con.execute("INSERT INTO vehicles (id, vin) VALUES (?, ?)", (vid, vin))
        con.execute("INSERT INTO positions (vehicle_id, recorded_at, soc, climate_on) "
                    "VALUES (?, ?, ?, 0)", (vid, "2026-08-31T10:00:00+00:00", 50.0))
    con.commit(); con.close()
    db_reader._opt_by_vehicle.clear()
    return path


def _as_car(monkeypatch, vid):
    monkeypatch.setattr(db_reader, "_current_vehicle_id", lambda: vid)


def test_the_other_car_is_not_shown_the_command_you_sent(two_cars, monkeypatch):
    _as_car(monkeypatch, 1)
    db_reader.write_optimistic_status({"climate_on": 1})
    assert db_reader.get_latest_status()["climate_on"] == 1      # the car you commanded

    _as_car(monkeypatch, 2)
    assert db_reader.get_latest_status()["climate_on"] == 0, \
        "the second car was drawn with the first car's command applied"


def test_clearing_one_car_leaves_the_other_alone(two_cars, monkeypatch):
    """A command the API refuses clears ITS overlay — not everybody's.

    Asserted on the overlay itself rather than on the rendered status: `write_optimistic_status`
    also inserts a real row, scoped to its own car, so the status would keep showing the value
    whatever the in-memory copy says. The overlay is the thing that used to be shared.
    """
    _as_car(monkeypatch, 1)
    db_reader.write_optimistic_status({"climate_on": 1})
    _as_car(monkeypatch, 2)
    db_reader.write_optimistic_status({"climate_on": 1})

    db_reader.clear_optimistic_status()                          # car two's command failed
    assert 2 not in db_reader._opt_by_vehicle
    assert 1 in db_reader._opt_by_vehicle, "car one's overlay was cleared too"


def test_extending_the_ttl_extends_only_the_car_you_are_on(two_cars, monkeypatch):
    """The re-arm exists so a command still being verified does not flash the old state back
    (#34). It must re-arm one car, not both."""
    _as_car(monkeypatch, 1)
    db_reader.write_optimistic_status({"climate_on": 1})
    _as_car(monkeypatch, 2)
    db_reader.write_optimistic_status({"climate_on": 1})
    db_reader._opt_by_vehicle[1] = (db_reader._opt_by_vehicle[1][0], time.time() + 1)

    db_reader.extend_optimistic_status()                          # while on car two

    assert db_reader._opt_by_vehicle[2][1] > time.time() + 20
    assert db_reader._opt_by_vehicle[1][1] < time.time() + 20, "car one's TTL was re-armed too"


def test_the_overlay_still_expires(two_cars, monkeypatch):
    """The TTL must keep working: it is what stops a refused command from showing forever. Read
    through the applier, on a car whose stored row does NOT carry the override."""
    _as_car(monkeypatch, 1)
    db_reader._opt_by_vehicle[1] = ({"climate_on": 1}, time.time() + 30)
    assert db_reader.get_latest_status()["climate_on"] == 1

    db_reader._opt_by_vehicle[1] = ({"climate_on": 1}, time.time() - 1)
    assert db_reader.get_latest_status()["climate_on"] == 0
