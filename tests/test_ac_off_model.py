"""A/C full-OFF is model-specific (#67). B10/C10/B05 stay EXACTLY on `ac_switch operate=off` — the
path confirmed on-car. The T03 wants the opposite: `operate=off` inside the FULL cmd-170 body.

🔴 The T03 test below used to assert `api.ac_off()` — the dedicated action, i.e. `operate=close`. It
was green for a month and it was asserting the **defect**: @derekzoli measured that exact call as
accepted-and-ignored by the car. A test can pin a bug in place as firmly as it pins a fix.
→ [[feedback-a-green-test-can-assert-the-bug]]

CI-safe: _session and _session_car_type are stubbed, no network."""
import json

import command_client as cc


class _FakeApi:
    def __init__(self):
        self.calls = []

    def ac_off(self, vin):
        self.calls.append(("ac_off", vin, None))
        return {"code": 0}

    def ac_switch(self, vin, *, params=None):
        self.calls.append(("ac_switch", vin, params))
        return {"code": 0}

    def _remote_control(self, *, vin, action, cmd_content):
        self.calls.append((action, vin, json.loads(cmd_content)))
        return {"code": 0}


class _FakeSession:
    def __init__(self):
        self.api = _FakeApi()

    def execute(self, fn):
        fn(self.api, "VIN")
        return True, "ok"


def _stub(monkeypatch, car_type):
    fake = _FakeSession()
    monkeypatch.setattr(cc, "_session", fake)
    monkeypatch.setattr(cc, "_session_car_type", lambda: car_type)
    return fake


def test_t03_sends_operate_off_inside_the_full_body(monkeypatch):
    """On the T03 it is not the VALUE of operate that decides, it is the SHAPE of the payload — it
    needs `off`, and only with the other six fields present. Verified on-car by @derekzoli
    (markoceri/leapmotor-api#9), watching acSwitch go false rather than trusting the cloud's code:0."""
    fake = _stub(monkeypatch, "T03")
    cc.ac_off()
    action, vin, body = fake.api.calls[-1]
    assert action == "ac_on"           # the library's name for cmd 170
    assert vin == "VIN"
    assert body["operate"] == "off"
    assert set(body) == {"circle", "mode", "operate", "position",
                         "temperature", "windlevel", "wshld"}, "the shape is the whole point"
    assert not any(c[0] == "ac_off" for c in fake.api.calls), "operate=close is what the car ignores"


def test_b10_stays_on_ac_switch_off(monkeypatch):
    fake = _stub(monkeypatch, "B10")
    cc.ac_off()
    action, _vin, params = fake.api.calls[-1]
    assert action == "ac_switch"       # UNCHANGED — confirmed working path
    assert params == {"operate": "off"}


def test_c10_b05_and_unknown_stay_on_ac_switch_off(monkeypatch):
    # Every non-T03 model (including an unknown/empty car_type) must keep the B10/C10 behaviour.
    for model in ("C10", "B05", "", "b10"):   # note: matching is on the exact upper "T03" only
        fake = _stub(monkeypatch, model)
        cc.ac_off()
        action, _vin, params = fake.api.calls[-1]
        assert action == "ac_switch", f"{model!r} must stay on ac_switch, got {action}"
        assert params == {"operate": "off"}, f"{model!r} lost its operate=off payload"
