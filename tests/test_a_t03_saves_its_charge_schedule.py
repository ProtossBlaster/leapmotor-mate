"""A car with the simple charge scheduler saves its schedule again (#380).

@ViriatusOG's T03 on 4.7.13: saving the charge schedule answered "Command not sent: complete current
charging configuration is required". Command 190 rewrites the car's whole plan, so Mate reads it first
and stops when the car leaves a field out — the guard that keeps a B10's own `circulation` and
`recharge` as the car sent them (#343). His T03 has the simple scheduler, a start time and a target
with no window and no days (none of the weekly abilities 25, 26, 47, 51 among the 19 it declares),
and its plan does not carry every field. Until 4.7.7 the T03 saved through the earlier library, which
filled the rest itself; his bundle has the plan it sent on 27/09 and the cloud's yes:

    {"chargeEnable":1,"chargesoc":100,"circulation":0,"cycles":"1,1,1,1,1,1,1","endtime":"08:00",
     "recharge":0,"starttime":"23:30"}

A car with the simple scheduler now sends the same where it reports nothing, and keeps what it does
report. A car with the full scheduler still refuses a plan it could not read whole.
"""
import json
import sqlite3

import pytest

cc = pytest.importorskip("command_client", reason="needs the web runtime")

T03_ABILITIES = [1, 2, 3, 5, 7, 10, 11, 14, 15, 17, 18, 20, 30, 31, 34, 35, 36, 52, 61]   # his bundle
B10_ABILITIES = [10, 11, 25, 26, 35, 47, 51, 53]


def _session(monkeypatch, plan):
    sent = []

    class FakeApi:
        def set_charge_schedule(self, vin, **kw):
            sent.append(kw)

    class FakeSession:
        def get_charge_schedule(self):
            return plan

        def execute(self, fn):
            fn(FakeApi(), "VINTEST")
            return True, "OK"

    monkeypatch.setattr(cc, "_session", FakeSession())
    return sent


@pytest.mark.parametrize("plan", [None, {}, {"chargeEnable": 1, "starttime": "23:30"}])
def test_the_simple_scheduler_sends_what_the_earlier_library_sent(monkeypatch, plan):
    sent = _session(monkeypatch, plan)
    ok, _ = cc.save_charge_schedule(enabled=True, soc_limit=100, start_time="23:30", end_time="08:00",
                                    cycles="1,1,1,1,1,1,1", simple=True)
    assert ok
    assert sent == [dict(enabled=True, soc_limit=100, start_time="23:30", end_time="08:00",
                         cycles="1,1,1,1,1,1,1", circulation=0, recharge=0)]


def test_what_the_simple_car_does_report_goes_back_as_it_came(monkeypatch):
    sent = _session(monkeypatch, {"circulation": 2, "recharge": 1, "cycles": "1,0,1,0,1,0,1"})
    ok, _ = cc.save_charge_schedule(enabled=True, soc_limit=90, start_time="22:00", end_time="08:00",
                                    simple=True)
    assert ok
    assert (sent[0]["circulation"], sent[0]["recharge"], sent[0]["cycles"]) == (2, 1, "1,0,1,0,1,0,1")


def test_the_full_scheduler_still_refuses_a_plan_it_could_not_read(monkeypatch):
    sent = _session(monkeypatch, {"chargeEnable": 1, "starttime": "23:30"})
    ok, message = cc.save_charge_schedule(enabled=True, soc_limit=90, start_time="22:00", end_time="08:00")
    assert not ok and "complete current charging configuration" in message
    assert sent == []


@pytest.mark.parametrize("abilities, simple", [(T03_ABILITIES, True), (B10_ABILITIES, False), (None, False)])
def test_the_page_says_which_scheduler_the_car_has(tmp_path, monkeypatch, abilities, simple):
    """The car's own declared abilities decide, as they decide which form the page shows; a car that
    declared none gets the full scheduler's guard, never a guess."""
    pytest.importorskip("httpx", reason="Starlette TestClient needs httpx")
    from starlette.testclient import TestClient
    import db as D
    import db_reader
    import main

    path = str(tmp_path / "t.db")
    D.Database(path).close()
    con = sqlite3.connect(path)
    con.execute("INSERT INTO vehicles (id, vin, car_type, abilities) VALUES (1, 'VINTEST', 'T03', ?)",
                (json.dumps(abilities) if abilities is not None else None,))
    for key, value in (("setup_complete", "1"), ("language", "en"), ("timezone", "UTC")):
        con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    con.commit()
    con.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setattr(db_reader, "_current_vehicle_id", lambda: 1)
    asked = []
    monkeypatch.setattr(cc, "save_charge_schedule", lambda **kw: (asked.append(kw), (True, "OK"))[1])
    r = TestClient(main.app).post("/api/charge-schedule", data={
        "enabled": "1", "start_time": "23:30", "end_time": "08:00", "soc_limit": "100"})
    assert r.status_code == 200
    assert asked and asked[0]["simple"] is simple
