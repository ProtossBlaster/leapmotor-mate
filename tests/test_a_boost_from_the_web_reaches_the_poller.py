"""A boost asked for by the web is one the poller sees.

A command is followed by a minute of fast polling, so the car's answer reaches the page in seconds
rather than at the next 30 s poll; `/api/boost` (a "getting in the car" shortcut) asks for the same
thing for a few minutes. Both write a setting the poller reads. v3.10.0 moved the poller to one key
per car, `boost_until_<vin>` (a command to the car in the garage must not wake the one on the
motorway), and the web went on writing the old shared `boost_until`, which nothing reads any more.
From then on a command from the web never sped anything up, and the shortcut did nothing at all.

The command boosts the car it went to — the one selected in the interface, which is where
command_client sends it. The shortcut cannot know which car you got into, so it boosts every one,
as the single shared boost did before v3.10.0.
"""
import pytest

pytest.importorskip("fastapi", reason="web/main.py needs fastapi (absent in the minimal CI env)")
pytest.importorskip("httpx", reason="Starlette TestClient needs httpx")

import db as D
import db_reader
import main
from starlette.testclient import TestClient

A, B = "LFZT03AAAAAAAAAA1", "LFZC10BBBBBBBBBB2"


@pytest.fixture
def two_cars(tmp_path, monkeypatch):
    """Two cars on one install, the poller's own view of the settings, and a web that answers."""
    poller = D.Database(str(tmp_path / "p.db"))
    poller._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,?,'T03')", (A,))
    poller._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (2,?,'C10')", (B,))
    poller._conn.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "p.db"))
    db_reader.set_setting("setup_complete", "1")
    return poller, TestClient(main.app)


def test_a_command_speeds_up_the_car_it_went_to(two_cars, monkeypatch):
    poller, client = two_cars
    db_reader.set_setting(db_reader.ACTIVE_VEHICLE_SETTING, B)
    monkeypatch.setitem(main._COMMANDS, "find_car", lambda: (True, ""))   # no cloud
    monkeypatch.setattr(main, "_post_command_refresh", lambda *a, **k: None)
    monkeypatch.setattr(main, "_last_command_at", 0.0)                     # no cooldown from an earlier test

    r = client.post("/api/command/find_car")
    assert r.status_code == 200 and "✓" in r.text, r.text

    assert poller.boosting(B) is True
    assert poller.boosting(A) is False


def test_the_get_in_the_car_boost_speeds_up_every_car(two_cars):
    poller, client = two_cars
    r = client.post("/api/boost?seconds=300")
    assert r.status_code == 200, r.text
    assert poller.boosting(A) is True
    assert poller.boosting(B) is True
