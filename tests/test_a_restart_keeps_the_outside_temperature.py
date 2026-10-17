"""A restart keeps the outside temperature the car had.

While the poller runs, a refused Open-Meteo lookup leaves the last reading in place, and the next
lookup waits as long as a good reading is trusted. That reading lived only in memory: when the first
lookup after a restart was refused (Open-Meteo sheds load with a 503), every position stored during
the wait carried no outside temperature, a hole in the charge's chart.

The sampler now starts from the reading on the newest stored position, when that one is younger than
the trust window. It is only a stand-in for a refused lookup: the first poll still asks.
"""
import db as D
import outside_temp as OT
import poll_cycle_fixture as F
from test_a_parked_car_without_gps_keeps_its_weather import _Spy

T0 = 1_760_000_000_000


def _start_and_poll(PM, db, vid, client, frame):
    """A poller process starting on this database and polling once."""
    class _Vehicle:
        vin, car_type, year, abilities, is_shared = F.VIN, "B10", 2025, None, False

    client.next = frame
    PM._poll_vehicle(db, client, PM.VehicleContext(db, _Vehicle(), vid), PM.AccountState())


def test_a_lookup_refused_after_a_restart_keeps_the_reading_stored_before(tmp_path, monkeypatch):
    PM = F.poller_main("poller_main_restart_weather")
    spy = _Spy(13.5)
    monkeypatch.setattr(PM, "OutsideTempSampler", lambda **kw: OT.OutsideTempSampler(fetch=spy, **kw))
    for name in ("_maybe_refresh_charge_schedule", "_write_comfort_state", "_mqtt_tick"):
        monkeypatch.setattr(PM, name, lambda *a, **k: None)
    monkeypatch.setattr(PM.energy_snapshots, "maybe_sample", lambda *a, **k: None)
    monkeypatch.setattr(PM.ready_automation, "maybe_trigger", lambda *a, **k: None)
    db = D.Database(str(tmp_path / "restart.db"))
    vid = db.ensure_vehicle(F.VIN, "B10")
    db.set_setting("outside_temp_enabled", "1")
    client = F.Client()

    _start_and_poll(PM, db, vid, client, F.frame(T0))
    spy.answer = None                                   # refused
    _start_and_poll(PM, db, vid, client, F.frame(T0 + 30_000))

    row = db._conn.execute("SELECT outside_temp FROM positions ORDER BY id DESC LIMIT 1").fetchone()
    assert row["outside_temp"] == 13.5


def test_a_lookup_that_answers_after_a_restart_replaces_the_stored_reading():
    spy = _Spy(14.0)
    s = OT.OutsideTempSampler(fetch=spy, last_temp=(13.5, 1_000.0))
    assert s.sample(45.4, 9.2, 1_060.0) == 14.0
    assert spy.calls == [(45.4, 9.2)]


def test_a_reading_stored_longer_ago_than_it_is_trusted_is_not_brought_back():
    s = OT.OutsideTempSampler(fetch=_Spy(None), last_temp=(13.5, 1_000.0))
    assert s.sample(45.4, 9.2, 1_000.0 + OT._MAX_AGE_S) is None
