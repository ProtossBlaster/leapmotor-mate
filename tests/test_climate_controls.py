"""Fan-level + recirculation WRITE controls (command_client.set_fan_level / set_recirc).

They build an ac_on (cmd 170) payload that changes ONE field while PRESERVING the rest of the
climate panel (mode/recirc/fan/temp) read from the latest stored position — no cloud fetch. The
car rejects windlevel 0 (verified on-car 2026-06-20) so it's clamped to 1-7. CI-safe: _session and
db_reader.get_latest_status are stubbed, so no network / DB."""
import json
import command_client as cc
import db_reader


class _FakeApi:
    def __init__(self): self.calls = []
    def _remote_control(self, *, vin, action, cmd_content):
        self.calls.append((action, json.loads(cmd_content)))
        return {"code": 0}


class _FakeSession:
    def __init__(self): self.api = _FakeApi()
    def execute(self, fn):
        fn(self.api, "VIN")
        return True, "ok"
    def _target(self): return None      # no model here — these tests are model-agnostic


def _stub(monkeypatch, status):
    fake = _FakeSession()
    monkeypatch.setattr(cc, "_session", fake)
    monkeypatch.setattr(db_reader, "get_latest_status", lambda: status)
    return fake


def test_set_fan_level_preserves_mode_recirc_temp(monkeypatch):
    fake = _stub(monkeypatch, {"climate_mode": 1, "recirculation": 1,
                               "fan_level": 7, "climate_target_temp": 20})
    cc.set_fan_level(5)
    action, body = fake.api.calls[-1]
    assert action == "ac_on"
    assert body["windlevel"] == "5"     # the requested level
    assert body["mode"] == "cold"       # 3713=1 (cool) preserved
    assert body["circle"] == "in"       # recirc on preserved
    assert body["temperature"] == "20"  # target temp preserved


def test_set_fan_level_clamps_1_to_7(monkeypatch):
    fake = _stub(monkeypatch, {})
    cc.set_fan_level(0);  assert fake.api.calls[-1][1]["windlevel"] == "1"   # 0 rejected → 1
    cc.set_fan_level(99); assert fake.api.calls[-1][1]["windlevel"] == "7"   # cap at max
    ok, _ = cc.set_fan_level("x")
    assert ok is False                                                       # bad input, no send


def test_set_recirc_toggles_circle_preserving_rest(monkeypatch):
    fake = _stub(monkeypatch, {"climate_mode": 4, "fan_level": 3, "climate_target_temp": 24})
    cc.set_recirc(True)
    assert fake.api.calls[-1][1]["circle"] == "in"      # recirculate
    cc.set_recirc(False)
    body = fake.api.calls[-1][1]
    assert body["circle"] == "out"                       # fresh air
    assert body["mode"] == "wind"                        # 3713=4 (vent) preserved
    assert body["windlevel"] == "3"                      # fan preserved
