"""The Overview follows the poller's frozen-drive decision, including across process restarts."""
from datetime import timedelta

import db_reader
import main
import pytest
import state_machine as SM
from test_a_trip_ends_when_the_car_last_spoke import rig, _vd, _ms, T0


@pytest.fixture
def screen(rig, monkeypatch):
    db, rec, poll, wall = rig
    db.set_setting("auto_note", "0")
    db.set_setting("language", "en")
    monkeypatch.setattr(db_reader, "DB_PATH", db._path)
    monkeypatch.setattr(db_reader, "_current_vehicle_id", lambda: rec._vehicle_id)
    for module in (main.charger_locator, main.ec_enrich, main.elevation_enrich):
        monkeypatch.setattr(module, "maybe_sweep", lambda: None)
    return db, rec, poll, wall


def _label():
    status = db_reader.get_latest_status()
    return main._ctx()["state_label"](status), main._state_color(status)


@pytest.mark.parametrize("gear,speed", [("D", 50), ("D", 0), ("R", 4), ("N", 0)])
def test_guard_changes_the_label_and_colour_at_its_boundary(screen, gear, speed):
    db, rec, poll, wall = screen
    data = _vd(gear=gear, speed=speed, ts=_ms(T0))
    poll(0, data)
    poll(SM.FROZEN_DRIVE_LIMIT_S - 1, data)
    assert _label() == ("Driving", "text-blue-400")
    poll(1, data)
    assert rec.state == SM.State.PARKED_ACTIVE
    assert _label() == ("Data stale", "text-amber-400")
    # The trip can be discarded as too short; the decision still survives on disk.
    poll(3600, data)
    assert _label()[0] == "Data stale"
    assert db_reader.get_latest_status()["gear"] == gear


@pytest.mark.parametrize("gear,speed,label", [("D", 50, "Driving"), ("D", 0, "Driving"),
                                               ("P", 0, "Parked"), ("P", 50, "Driving")])
def test_fresh_frame_restores_the_normal_display(screen, gear, speed, label):
    db, rec, poll, wall = screen
    data = _vd(ts=_ms(T0))
    poll(0, data)
    poll(SM.FROZEN_DRIVE_LIMIT_S, data)
    assert _label()[0] == "Data stale"
    poll(10, _vd(gear=gear, speed=speed, ts=_ms(wall["now"] + timedelta(seconds=10))))
    assert _label()[0] == label


def test_backdated_but_advancing_frames_are_still_driving(screen):
    db, rec, poll, wall = screen
    for minute in range(40):
        poll(60, _vd(ts=_ms(T0 - timedelta(days=2) + timedelta(minutes=minute))))
        assert _label()[0] == "Driving"


def test_missing_frame_clock_does_not_invent_staleness(screen):
    db, rec, poll, wall = screen
    poll(0, _vd(ts=0))
    poll(3600, _vd(ts=0))
    assert _label()[0] == "Driving"


def test_the_decision_is_scoped_to_the_car_and_persists_on_disk(screen, monkeypatch):
    db, rec, poll, wall = screen
    data = _vd(ts=_ms(T0))
    poll(0, data)
    poll(SM.FROZEN_DRIVE_LIMIT_S, data)
    assert _label()[0] == "Data stale"
    # A new reader connection sees it without sharing any state machine object.
    db_reader._get().close()
    assert _label()[0] == "Data stale"
    second = db.ensure_vehicle("OTHER", "B10")
    db.save_position(second, data)
    monkeypatch.setattr(db_reader, "_current_vehicle_id", lambda: second)
    assert _label()[0] == "Driving"


def test_garage_replay_keeps_offline_kilometres_separate_from_the_recovered_charge(screen):
    db, rec, poll, wall = screen
    old = _vd(ts=_ms(T0), soc=30, odo=1000)
    poll(0, old)
    poll(SM.FROZEN_DRIVE_LIMIT_S, old)
    poll(4 * 3600 - SM.FROZEN_DRIVE_LIMIT_S - 10, old)
    assert _label()[0] == "Data stale"
    poll(10, _vd(ts=_ms(T0 + timedelta(hours=4)), soc=80, odo=1002))
    assert _label()[0] == "Driving"
    gaps_before = [dict(row) for row in db._conn.execute("SELECT * FROM offline_gaps")]
    assert len(gaps_before) == 1 and gaps_before[0]["distance_km"] == 2
    candidates = db_reader.scan_missed_charges(apply=True)
    assert len(candidates) == 1
    assert (candidates[0]["start_soc"], candidates[0]["end_soc"]) == (30, 80)
    assert candidates[0]["duration_min"] == 240
    assert db_reader.scan_missed_charges(apply=True) == []
    assert [dict(row) for row in db._conn.execute("SELECT * FROM offline_gaps")] == gaps_before
