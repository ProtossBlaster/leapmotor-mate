"""T03-only climate operate fix (#67). The T03 firmware silently IGNORES operate=auto climate writes
(A/C button, temperature, fan, recirc all no-op — cloud returns code:0 but the car does nothing) while
it HONORS operate=manual (the Raffredda/quick_cool path works on-car). So on the T03 ONLY, an auto write
is rewritten to manual (+ mode 'cold' when there is no manual mode to preserve). B10/C10/B05 MUST stay on
operate=auto — the confirmed-working path we don't touch. CI-safe: _session, _session_car_type and
db_reader.get_latest_status are stubbed, no network / DB."""
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


def _stub(monkeypatch, car_type, status):
    fake = _FakeSession()
    monkeypatch.setattr(cc, "_session", fake)
    monkeypatch.setattr(cc, "_session_car_type", lambda: car_type)
    monkeypatch.setattr(db_reader, "get_latest_status", lambda: status)
    monkeypatch.setattr(db_reader, "get_vehicle", lambda: (None, {}))   # no car listed: the fan keeps 1..7
    return fake


# ── T03: operate=auto writes become operate=manual ────────────────────────────────────────────────
def test_t03_ac_on_becomes_manual_cold(monkeypatch):
    fake = _stub(monkeypatch, "T03", {"climate_target_temp": 22})
    cc.ac_on()
    action, body = fake.api.calls[-1]
    assert action == "ac_on"
    assert body["operate"] == "manual"      # was "auto" — T03 ignores auto
    assert body["mode"] == "cold"           # nohotcold has no manual equiv → cold (the proven start)
    assert body["temperature"] == "22"      # target still preserved


def test_t03_temperature_becomes_manual_cold_when_mode_unknown(monkeypatch):
    fake = _stub(monkeypatch, "T03", {"climate_mode": 0, "fan_level": 4, "recirculation": 0})
    cc.set_climate_temp(21)
    _action, body = fake.api.calls[-1]
    assert body["operate"] == "manual"
    assert body["mode"] == "cold"
    assert body["temperature"] == "21"
    assert body["windlevel"] == "4"         # fan preserved


def test_t03_fan_becomes_manual_when_mode_unknown(monkeypatch):
    fake = _stub(monkeypatch, "T03", {"climate_mode": None, "climate_target_temp": 24})
    cc.set_fan_level(6)
    _action, body = fake.api.calls[-1]
    assert body["operate"] == "manual"
    assert body["mode"] == "cold"
    assert body["windlevel"] == "6"


def test_t03_recirc_becomes_manual_when_mode_unknown(monkeypatch):
    fake = _stub(monkeypatch, "T03", {"climate_mode": 0, "fan_level": 3, "climate_target_temp": 24})
    cc.set_recirc(True)
    _action, body = fake.api.calls[-1]
    assert body["operate"] == "manual"
    assert body["circle"] == "in"           # recirc on


def test_t03_preserves_an_existing_manual_mode(monkeypatch):
    # When the car IS in a manual mode (e.g. 3=hot), the helper must NOT flatten it to cold —
    # it only rewrites operate=auto. So heating stays heating.
    fake = _stub(monkeypatch, "T03", {"climate_mode": 3, "fan_level": 5, "climate_target_temp": 30})
    cc.set_climate_temp(28)
    _action, body = fake.api.calls[-1]
    assert body["operate"] == "manual"
    assert body["mode"] == "hot"            # preserved, not forced to cold


# ── B10 / C10 / B05 / unknown: operate=auto path UNCHANGED ─────────────────────────────────────────
def test_b10_ac_on_stays_auto(monkeypatch):
    fake = _stub(monkeypatch, "B10", {"climate_target_temp": 22})
    cc.ac_on()
    _action, body = fake.api.calls[-1]
    assert body["operate"] == "auto"        # UNCHANGED — B10 honors auto
    assert body["mode"] == "nohotcold"


def test_non_t03_temperature_stays_auto_when_mode_unknown(monkeypatch):
    for model in ("B10", "C10", "B05", "", "t03"):   # matching is exact-upper "T03" only
        fake = _stub(monkeypatch, model, {"climate_mode": 0, "fan_level": 4})
        cc.set_climate_temp(21)
        _action, body = fake.api.calls[-1]
        assert body["operate"] == "auto", f"{model!r} must keep operate=auto"
        assert body["mode"] == "nohotcold", f"{model!r} must keep mode=nohotcold"
