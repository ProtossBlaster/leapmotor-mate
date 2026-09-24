"""A charge already running does not disappear from the screen when the current dips (#307).

@arzthilfe, C10, v3.18.1. He turns his wallbox down from 11 A to 8 A mid-charge and the Overview
stops showing the charge: only "cable connected", no remaining time, no power. It comes back on the
next charge — until he turns the current down again. His bundle holds the exact moment:

    14:48:13  State: charging | plug=1 chg=1 A=-3.4 | Frame age: 3s
    14:48:43  State: charging | plug=1 chg=1 A=-2.0
    14:49:13  State: charging | plug=1 chg=1 A=-2.3
    14:50:15  State: charging | plug=1 chg=0 A=-1.6 | Frame age: 2s     ← the screen empties

The frame is TWO SECONDS old: the cloud is not frozen and nothing is stale. What flips is Mate's
own per-poll `charging_status`, because `_is_charging` refuses a pack current below
`charge_detect_min_a` — his is the default 2.0 A, and at 8 A on the wallbox the pack draws 1.6.

That threshold's job is to notice that a charge has STARTED. Using it to decide whether a charge
already open is still running is the same "one threshold, two jobs" defect as the 0.00 kW power
reading fixed in v3.18.3 — and this is its other half. The state machine agrees with the car
(it stays in CHARGING, and charge #39 stayed open and kept collecting SoC); only the screen
disagreed, because every charge block reads `status.charging`, the per-poll flag.

So: while a charge session is open and the cable is still connected, the status says charging.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

import db as poller_db
import db_reader
import pytest


def _iso(secs_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=secs_ago)).isoformat()


@pytest.fixture()
def bernd(tmp_path, monkeypatch):
    """His car at 14:50:15: charge #39 open since 14:43, cable in, pack current 1.6 A."""
    path = str(tmp_path / "c307.db")
    poller_db.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1, 'C10', 'C10')")
    con.execute("INSERT INTO charges (id, vehicle_id, started_at, ended_at, start_soc) "
                "VALUES (39, 1, ?, NULL, 87.5)", (_iso(450),))
    con.commit(); con.close()
    return path


def _poll(path, *, amps, charging, plug=1, soc=87.8, secs_ago=2):
    con = sqlite3.connect(path)
    con.execute(
        "INSERT INTO positions (vehicle_id, recorded_at, latitude, longitude, soc, charging,"
        " plug_connected, charge_current_a, frame_ts) VALUES (1,?,45.0,9.0,?,?,?,?,?)",
        (_iso(secs_ago), soc, charging, plug, amps,
         int((datetime.now(timezone.utc) - timedelta(seconds=secs_ago)).timestamp() * 1000)))
    con.commit(); con.close()


def test_the_screen_keeps_the_charge_when_the_current_dips(bernd):
    """1.6 A is under his 2.0 A floor, so the poll says not-charging — but the session is open
    and the cable is in, which is what the screen must follow."""
    _poll(bernd, amps=-1.6, charging=0)
    assert db_reader.get_latest_status()["charging"], \
        "the charge block vanished while charge #39 was still running"


def test_the_charge_still_ends_when_the_cable_comes_out(bernd):
    """The guard is the cable, not the session alone: unplugging mid-session must still read as
    not charging, or a charge left open by any other defect would print 'charging' forever."""
    _poll(bernd, amps=0.0, charging=0, plug=0)
    assert not db_reader.get_latest_status()["charging"]


def test_a_car_with_no_open_charge_is_not_charging(tmp_path, monkeypatch):
    """Plugged in, waiting for a scheduled window: no session, so nothing to keep alive."""
    path = str(tmp_path / "idle.db")
    poller_db.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO vehicles (id, vin) VALUES (1, 'C10')")
    con.commit(); con.close()
    _poll(path, amps=0.1, charging=0, plug=1)
    assert not db_reader.get_latest_status()["charging"]


def test_a_closed_charge_does_not_keep_the_screen_alive(bernd):
    """Once the session is closed the flag is the poll's again."""
    con = sqlite3.connect(bernd)
    con.execute("UPDATE charges SET ended_at = ? WHERE id = 39", (_iso(10),))
    con.commit(); con.close()
    _poll(bernd, amps=-1.6, charging=0)
    assert not db_reader.get_latest_status()["charging"]


def test_a_poll_that_says_charging_is_untouched(bernd):
    """Nothing changes on the path everybody is on."""
    _poll(bernd, amps=-3.4, charging=1)
    assert db_reader.get_latest_status()["charging"]
