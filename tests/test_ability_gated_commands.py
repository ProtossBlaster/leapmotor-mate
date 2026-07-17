"""Ability-gated command buttons — issue #142 (chengler, T03).

The 'Unlock Charge Cable' button was shown on every model, but the T03 can't unlock the charge
cable: it never declares ability code 53 (UNLOCK_CHARGE_GUN), the official app hides the option,
and the button no-ops on the car. Mate already had a capability system (command_shown) — the button
just wasn't wired to it.

The fix gates command buttons on the car's OWN declared abilities (the VehicleAbility set it reports,
the ground truth for what THIS model supports), via a whitelist COMMAND_ABILITY. This is model-blind:
any car that doesn't declare the code hides the button, present and future models alike.

Pinned here, on real declared-ability sets from diagnostics bundles:
- T03 (chengler, #142): [...] no 53  → hidden
- B10 (Wartopia, #128):  [...] has 53 → shown
And the crucial NON-regression: climate stays OUT of ability gating. The T03 omits AC_ON (code 6)
yet cools (#67), so gating A/C on the ability would wrongly hide it — command_shown must not.
"""
import json

import pytest

import capability_profile as cp

# Real declared-ability code sets, straight from the diagnostics bundles.
T03_ABILITIES = [1, 2, 3, 5, 7, 10, 11, 14, 15, 17, 18, 20, 30, 31, 34, 35, 36, 52, 61]        # no 53, no 6
B10_ABILITIES = [1, 2, 3, 5, 6, 7, 10, 11, 12, 13, 14, 15, 19, 20, 21, 23, 24, 29, 30, 31,
                 32, 34, 35, 38, 42, 43, 47, 48, 51, 52, 53, 57, 59, 60, 61, 69, 70]           # has 53 and 6


# ── the gate itself ──────────────────────────────────────────────────────────

def test_unlock_charge_cable_hidden_on_t03():
    """The crux of #142: the T03 doesn't declare code 53, so the button must be hidden."""
    assert cp.command_shown("VIN", "unlock_charger", abilities=T03_ABILITIES) is False


def test_unlock_charge_cable_shown_on_b10():
    """The B10 declares 53 → the button stays."""
    assert cp.command_shown("VIN", "unlock_charger", abilities=B10_ABILITIES) is True


def test_unknown_abilities_never_hide():
    """A car that hasn't reported its abilities yet → shown. Never hide on a guess."""
    assert cp.command_shown("VIN", "unlock_charger", abilities=None) is True


def test_climate_is_never_ability_gated():
    """NON-regression for #67: the T03 omits AC_ON (6) yet cools. Climate must NOT be gated on the
    declared ability, or we'd hide working A/C. climate_off is gated elsewhere (COMMAND_FEATURE),
    never by COMMAND_ABILITY."""
    assert "climate_off" not in cp.COMMAND_ABILITY
    assert "climate_cool" not in cp.COMMAND_ABILITY
    # With the T03 set (no AC_ON), the ability gate must be inert for climate.
    assert cp.ability_supported("climate_off", T03_ABILITIES) is True
    assert cp.ability_supported("climate_cool", T03_ABILITIES) is True


def test_commands_without_an_ability_requirement_are_unaffected():
    """A command not in COMMAND_ABILITY passes the ability gate regardless of what's declared."""
    assert cp.ability_supported("find_car", T03_ABILITIES) is True
    assert cp.ability_supported("open_trunk", []) is True


def test_ability_gate_is_model_blind():
    """No per-model table: a hypothetical future car that omits 53 hides the button too."""
    assert cp.command_shown("VIN", "unlock_charger", abilities=[1, 2, 3]) is False
    assert cp.command_shown("VIN", "unlock_charger", abilities=[53]) is True


# ── parse_abilities (shared normaliser) ──────────────────────────────────────

def test_parse_abilities_variants():
    assert cp.parse_abilities("[1,2,53]") == [1, 2, 53]      # JSON string (DB column)
    assert cp.parse_abilities([1, 2, 53]) == [1, 2, 53]      # already a list
    assert cp.parse_abilities(None) is None                  # not reported
    assert cp.parse_abilities("") is None                    # empty
    assert cp.parse_abilities("{bad") is None                # unparseable → None (→ shown)


def test_unparseable_abilities_do_not_hide():
    """A corrupt abilities value must fail open (show), not silently hide everything."""
    assert cp.ability_supported("unlock_charger", "not-a-list") is True


# ── poller DB getter ─────────────────────────────────────────────────────────

def test_poller_get_abilities_roundtrip(tmp_path):
    import db as D
    db = D.Database(str(tmp_path / "t.db"))
    db.ensure_vehicle("VINT03", "T03", abilities=T03_ABILITIES)
    got = db.get_abilities()
    assert got is not None and 53 not in got and 34 in got


def test_poller_get_abilities_none_when_absent(tmp_path):
    import db as D
    db = D.Database(str(tmp_path / "t.db"))
    db.ensure_vehicle("VINX", "B10")            # no abilities passed
    assert db.get_abilities() is None


# ── MQTT discovery (both builds share one image; this is the poller side) ─────

def _discover(abilities):
    pytest.importorskip("paho.mqtt.client", reason="poller MQTT bridge needs paho")
    import types
    import mqtt as M

    class _FakeClient:
        def __init__(self):
            self.published = {}

        def publish(self, topic, payload, retain=False):
            self.published[topic] = payload

    svc = M.MqttService("broker", 1883, get_setting=lambda k, d="": d, abilities=abilities)
    svc.client = _FakeClient()
    svc.publish_discovery(types.SimpleNamespace(vin="VINTEST"))
    return svc.client.published


_UNLOCK_TOPIC = "homeassistant/button/leapmotor_mate_vintest/unlock_charger/config"


def test_mqtt_unlock_button_absent_on_t03():
    """T03: the discovery config for the unlock button is cleared (empty retained payload)."""
    pub = _discover(T03_ABILITIES)
    assert pub.get(_UNLOCK_TOPIC) == ""       # empty = HA drops the button


def test_mqtt_unlock_button_present_on_b10():
    """B10: the button is published with a real config."""
    pub = _discover(B10_ABILITIES)
    assert _UNLOCK_TOPIC in pub and pub[_UNLOCK_TOPIC], "B10 must keep the unlock button"
    assert "unlock_charger" in json.loads(pub[_UNLOCK_TOPIC])["command_topic"] or \
           json.loads(pub[_UNLOCK_TOPIC]).get("payload_press") == "unlock_charger"


def test_mqtt_unlock_button_present_when_abilities_unknown():
    """No abilities reported yet → shown (never hide on a guess)."""
    pub = _discover(None)
    assert _UNLOCK_TOPIC in pub and pub[_UNLOCK_TOPIC]


# ── web wiring (the real chain the /charges context + /api/command gate use) ─

def _web_show_unlock(tmp_path, monkeypatch, car_type, abilities):
    """Exactly what web computes: get_vehicle() → parse_abilities(column) → command_shown. Catches a
    mismatch between how the DB stores abilities and how the web reads them (the integration risk)."""
    import db as D
    import db_reader
    dbf = str(tmp_path / "t.db")
    db = D.Database(dbf)
    db.ensure_vehicle("VINWEB", car_type, abilities=abilities)
    monkeypatch.setattr(db_reader, "DB_PATH", dbf)
    veh, _ = db_reader.get_vehicle()
    return cp.command_shown((veh or {}).get("vin", ""), "unlock_charger",
                            abilities=cp.parse_abilities((veh or {}).get("abilities")))


def test_web_hides_unlock_on_t03(tmp_path, monkeypatch):
    assert _web_show_unlock(tmp_path, monkeypatch, "T03", T03_ABILITIES) is False


def test_web_shows_unlock_on_b10(tmp_path, monkeypatch):
    assert _web_show_unlock(tmp_path, monkeypatch, "B10", B10_ABILITIES) is True


def test_web_shows_unlock_when_abilities_absent(tmp_path, monkeypatch):
    assert _web_show_unlock(tmp_path, monkeypatch, "B10", None) is True


# ── the command route refuses it server-side (defence in depth) ──────────────

def test_run_command_refuses_unlock_on_t03(monkeypatch):
    """Even a hand-crafted POST (the hidden button aside) is refused on a car that can't do it —
    without bouncing a no-op off the car."""
    import asyncio
    import types
    pytest.importorskip("fastapi", reason="web.main needs fastapi")
    import main
    from fastapi import BackgroundTasks
    monkeypatch.setattr(main.db_reader, "get_vehicle",
                        lambda: ({"vin": "V", "abilities": json.dumps(T03_ABILITIES)}, {}))
    monkeypatch.setattr(main.db_reader, "get_language", lambda: "en")
    resp = asyncio.run(main.run_command("unlock_charger",
                                        types.SimpleNamespace(headers={"accept": "application/json"}),
                                        BackgroundTasks()))
    body = json.loads(resp.body)
    assert resp.status_code == 400 and body.get("unsupported") is True


def test_run_command_gate_ignores_non_ability_commands(monkeypatch):
    """A command with no ability requirement must NOT trigger the vehicle lookup — the reason the
    gate is keyed on COMMAND_ABILITY. get_vehicle raising here proves it's never called."""
    import asyncio
    import types
    pytest.importorskip("fastapi", reason="web.main needs fastapi")
    import main
    from fastapi import BackgroundTasks

    def _boom():
        raise AssertionError("get_vehicle must not be called for a non-ability command")

    monkeypatch.setattr(main.db_reader, "get_vehicle", _boom)
    monkeypatch.setattr(main.db_reader, "get_latest_status", lambda: {})
    monkeypatch.setattr(main.db_reader, "get_language", lambda: "en")
    # unknown command exits before the gate anyway; use a real non-ability command key present in the
    # map so we reach (and pass) the gate. find_car has no ability requirement.
    monkeypatch.setitem(main._COMMANDS, "find_car", lambda: (True, "ok"))
    monkeypatch.setattr(main.db_reader, "set_setting", lambda *a, **k: None)
    resp = asyncio.run(main.run_command("find_car",
                                        types.SimpleNamespace(headers={"accept": "application/json"}),
                                        BackgroundTasks()))
    # Reached execution (ok) without get_vehicle blowing up.
    assert json.loads(resp.body).get("ok") is True
