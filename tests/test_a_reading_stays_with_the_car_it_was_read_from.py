"""A reading the web writes goes under the car it was read from, whatever the picker says by then.

The web writes a position row in two places: the sidebar Refresh, and the check after a command.
Both asked "which car?" twice — once to read (`command_client._target`), once to write
(`db_reader._current_vehicle_id`) — and the picker can move in between, while the cloud answers.
#338, pack 22 (@dommi1966, an A10 and a shared T03): a command to the T03 on 30/09 at 18:34, and
the T03's 53 % and 9,275 km landed under the A10 — a day "driven 8,527 km" and a standby bar of
39 % a day on a car that lost none. The read now names its car, and the write names the same one.
"""
import dataclasses
import time as _time
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi", reason="web/main.py needs fastapi (absent in the minimal CI env)")
pytest.importorskip("httpx", reason="Starlette TestClient needs httpx")

import command_client
import db as D
import db_reader
import main
from starlette.testclient import TestClient

A10 = "LA10000000000001"
T03 = "LT03000000000002"
READINGS = {
    A10: {"1204": 21.5, "1318": 776, "1298": 0},
    T03: {"1204": 53.0, "1318": 9275, "1298": 1},      # the T03 reports itself locked
}


@dataclasses.dataclass
class _Car:
    vin: str
    car_type: str


class _Cloud:
    """Answers for the car it is asked about; while it answers, the owner picks the A10."""

    def get_vehicle_raw_status(self, vehicle):
        db_reader.set_active_vehicle(A10)
        return {"data": {"signal": dict(READINGS[vehicle.vin])}}


class _Clock:
    """The check after a command sleeps between reads: skip the waiting, keep the real clock."""

    def __init__(self):
        self.skipped = 0.0

    def time(self):
        return _time.time() + self.skipped

    def sleep(self, s):
        self.skipped += s


@pytest.fixture
def web(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    db = D.Database(path)
    db.ensure_vehicle(A10, "A10", 2025)                # vehicle 1, as on @dommi1966's install
    db.ensure_vehicle(T03, "T03", 2024)                # vehicle 2
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    db_reader.set_setting("setup_complete", "1")
    from cloud_access_fixture import settings
    from ui_command_access import snapshot_key
    for vin, car_type in ((A10, "A10"), (T03, "T03")):
        access = settings(vin, car_type=car_type)
        for key in ("leapmotor_user", snapshot_key(vin)):
            db_reader.set_setting(key, access(key))
    session = command_client.LeapmotorSession()
    session._api = _Cloud()
    session._vehicles = [_Car(A10, "A10"), _Car(T03, "T03")]   # the cloud lists the A10 first
    session._vehicle = session._vehicles[0]
    session._connect = lambda: None
    session._reset = lambda: None
    monkeypatch.setattr(command_client, "_session", session)
    clock = _Clock()
    fake_time = SimpleNamespace(**{n: getattr(_time, n) for n in dir(_time) if not n.startswith("_")})
    fake_time.time, fake_time.sleep = clock.time, clock.sleep
    monkeypatch.setattr(main, "time", fake_time)
    monkeypatch.setattr(main, "_last_command_at", 0.0)          # no cooldown from an earlier test
    monkeypatch.setitem(main._COMMANDS, "lock", lambda: (True, ""))   # the cloud takes the command
    db_reader.set_active_vehicle(T03)                  # the owner is on the T03's page
    return SimpleNamespace(client=TestClient(main.app), db=db)


def _rows(db):
    return [tuple(r) for r in db._conn.execute(
        "SELECT vehicle_id, soc, odometer_km FROM positions ORDER BY id").fetchall()]


def test_the_check_after_a_command_files_the_reading_under_the_commanded_car(web):
    r = web.client.post("/api/command/lock")
    assert r.status_code == 200, r.text
    assert _rows(web.db) == [(2, 53.0, 9275.0)]       # the T03's reading, under the T03


def test_the_refresh_button_files_the_reading_under_the_car_it_read(web):
    r = web.client.post("/api/refresh")
    assert r.status_code == 200, r.text
    assert _rows(web.db) == [(2, 53.0, 9275.0)]


GONE = "LGONE00000000003"                              # a car this account no longer lists


def test_a_car_the_account_no_longer_lists_is_not_read_as_the_other_one(web):
    assert command_client.get_fresh_signals(GONE) is None


def test_a_reading_named_after_an_unknown_car_is_not_filed_under_another(web):
    db_reader.save_fresh_signals(dict(READINGS[T03]), GONE)
    assert _rows(web.db) == []
