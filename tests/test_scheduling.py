"""Charge-schedule write must read-modify-write: change only enable/SoC/window and PRESERVE
the car's existing day mask (`cycles`), `circulation` and `recharge` — never guess them.
See command_client.save_charge_schedule.

Skipped where leapmotor_api isn't installed (the CI test env per pytest.ini); verified
in-container against the real client."""
import pytest

cc = pytest.importorskip("command_client", reason="needs leapmotor_api")


def test_charge_schedule_merge_preserves_car_fields(monkeypatch):
    captured = {}

    class FakeApi:
        def set_charge_schedule(self, vin, **kw):
            captured.update(kw)
            captured["vin"] = vin

    class FakeSession:
        def get_charge_schedule(self):
            # car has a custom day mask + circulation that the UI doesn't expose
            return {"chargeEnable": 0, "chargesoc": 80, "circulation": 1,
                    "cycles": "1,0,1,0,1,0,1", "endtime": "08:00",
                    "recharge": 0, "starttime": "22:00"}

        def execute(self, fn):
            fn(FakeApi(), "VINTEST")
            return True, "OK"

    monkeypatch.setattr(cc, "_session", FakeSession())
    ok, _ = cc.save_charge_schedule(enabled=True, soc_limit=90, start_time="23:30", end_time="07:00")
    assert ok
    # user-edited fields applied
    assert captured["enabled"] is True
    assert captured["soc_limit"] == 90
    assert captured["start_time"] == "23:30"
    assert captured["end_time"] == "07:00"
    # car-owned fields preserved, not clobbered/guessed
    assert captured["cycles"] == "1,0,1,0,1,0,1"
    assert captured["circulation"] == 1
    assert captured["recharge"] == 0


def test_charge_schedule_without_current_state_is_not_sent(monkeypatch):
    class FakeSession:
        def get_charge_schedule(self): return {}
        def execute(self, fn): raise AssertionError('Incomplete state must not be sent')
    monkeypatch.setattr(cc, '_session', FakeSession())
    ok, message = cc.save_charge_schedule(enabled=False, soc_limit=80, start_time='00:00', end_time='06:00')
    assert not ok and 'complete current charging configuration' in message


def test_cycles_from_day_flags_position_order():
    # cycles is MONDAY-first (0=Mon..6=Sun), confirmed on-car 2026-06-07. The helper is
    # weekday-agnostic — it just maps flags[i] -> field i.
    assert cc.cycles_from_day_flags([True] * 7) == "1,1,1,1,1,1,1"
    # positions 1,2 set → Tue,Wed (Mon-first)
    assert cc.cycles_from_day_flags([False, True, True, False, False, False, False]) == "0,1,1,0,0,0,0"
    # only Monday (position 0)
    assert cc.cycles_from_day_flags([True, False, False, False, False, False, False]) == "1,0,0,0,0,0,0"
    # empty selection coerces to all-days (a window with no days would never fire)
    assert cc.cycles_from_day_flags([False] * 7) == "1,1,1,1,1,1,1"


def test_day_flags_from_cycles_roundtrip():
    assert cc.day_flags_from_cycles("0,1,1,0,0,0,0") == [False, True, True, False, False, False, False]
    assert cc.day_flags_from_cycles("1,1,1,1,1,1,1") == [True] * 7
    # tolerant of short/garbage input — missing positions are False, never an IndexError
    assert cc.day_flags_from_cycles("1,1") == [True, True, False, False, False, False, False]
    assert cc.day_flags_from_cycles("") == [False] * 7


def test_save_charge_schedule_uses_provided_cycles(monkeypatch):
    captured = {}

    class FakeApi:
        def set_charge_schedule(self, vin, **kw):
            captured.update(kw)

    class FakeSession:
        def get_charge_schedule(self):
            return {"cycles": "1,1,1,1,1,1,1", "circulation": 1, "recharge": 0,
                    "chargeEnable": 1, "chargesoc": 80, "starttime": "22:00", "endtime": "07:00"}

        def execute(self, fn):
            fn(FakeApi(), "VIN")
            return True, "OK"

    monkeypatch.setattr(cc, "_session", FakeSession())
    # an explicit cycles arg (user picked days) must WIN over the car's current mask
    cc.save_charge_schedule(enabled=True, soc_limit=80, start_time="23:00",
                            end_time="07:00", cycles="0,1,1,1,1,1,0")
    assert captured["cycles"] == "0,1,1,1,1,1,0"  # weekdays only, not the car's all-days
    assert captured["circulation"] == 1            # still preserved


def _fake_session_returning(schedule, captured):
    from api_v2_bridge import NewAPIClient
    class FakeApi(NewAPIClient):
        def __init__(self): pass
        def _get_charge_appointment(self, vin): return dict(schedule)
        def _remote_control_raw(self, **kwargs):
            import json
            state=json.loads(kwargs['cmd_content'])
            captured.update(soc_limit=state['chargesoc'], enabled=bool(state['chargeEnable']),
                            start_time=state['starttime'], end_time=state['endtime'], cycles=state['cycles'])
    class FakeSession:
        def execute(self, fn):
            fn(FakeApi(), 'VIN')
            return True, 'OK'
    return FakeSession()


def test_set_charge_limit_preserves_complete_enabled_plan(monkeypatch):
    """#18 regression: setting the charge limit on an ENABLED, start-time-only plan (the cloud
    omits cycles/endtime/recharge) must change ONLY the SoC — never disable the plan or reset the
    start time. The lib's api.set_charge_limit guarded on `cycles` and wiped both; Mate now
    round-trips the plan."""
    captured = {}
    # enabled, only a start time — the exact shape that tripped the lib's `cycles` guard
    monkeypatch.setattr(cc, "_session",
                        _fake_session_returning({"chargeEnable": 1, "chargesoc": 80,
                                                 "starttime": "22:00", "endtime":"08:00", "cycles":"1,1,1,1,1,1,1", "circulation":1, "recharge":0}, captured))
    ok, _ = cc.set_charge_limit(90)
    assert ok
    assert captured["soc_limit"] == 90            # the one field we meant to change
    assert captured["enabled"] is True            # NOT disabled (was the #18 bug)
    assert captured["start_time"] == "22:00"      # NOT reset to 00:00 (was the #18 bug)
    assert captured["cycles"] == "1,1,1,1,1,1,1"  # missing mask → all-days, never empty


def test_set_charge_limit_keeps_a_disabled_plan_disabled(monkeypatch):
    """The inverse guard: changing the limit must not silently ENABLE a plan the user left off."""
    captured = {}
    monkeypatch.setattr(cc, "_session",
                        _fake_session_returning({"chargeEnable": 0, "chargesoc": 80,
                                                 "cycles": "1,0,1,0,1,0,1", "starttime": "01:00",
                                                 "endtime": "06:00", "circulation":1, "recharge":0}, captured))
    cc.set_charge_limit(70)
    assert captured["soc_limit"] == 70
    assert captured["enabled"] is False           # stays off
    assert captured["start_time"] == "01:00"      # window preserved
    assert captured["end_time"] == "06:00"
    assert captured["cycles"] == "1,0,1,0,1,0,1"  # custom mask preserved
