"""ready_automation.maybe_trigger — the Ready-edge "prepare now" automation (design agreed with
Silvio 2026-07-02): fires ONCE per Ready OFF→ON edge (never repeats mid-session), optionally
gated on interior temperature, reusing the exact same command_client.build_prepare_bundle() as
manual "Prepara Ora" plus a direct windows() call — both through the poller's OWN authenticated
session under its existing api_lock.

Contract under test: seed-only first poll (restart safety), single fire per session (no repeat
while still Ready, no re-fire for a later trip in the same session), debounce against a single-
poll `ready` blip (must NOT re-arm), temperature gate (both directions + disabled = always-fire),
disabled automation, and the three action shapes (climate+seats bundle, windows-only, both).

No network — a fake client exposing _api.prepare_car/_api.windows, a tiny in-memory settings dict
standing in for the poller's Database (only get_setting is used)."""
import json

import pytest

import ready_automation as ra


class FakeApi:
    def __init__(self):
        self.prepare_calls = []
        self.windows_calls = []

    def prepare_car(self, vin, *, params):
        self.prepare_calls.append((vin, params))

    def windows(self, vin, *, value):
        self.windows_calls.append((vin, value))


class FakeVehicle:
    vin = "VIN1"
    car_type = "B10"


class FakeClient:
    def __init__(self):
        self._api = FakeApi()
        self._vehicle = FakeVehicle()


class FakeDB:
    def __init__(self, config: dict | None = None):
        self._settings = {}
        if config is not None:
            self._settings["ready_automation"] = json.dumps(config)

    def get_setting(self, key, default=""):
        return self._settings.get(key, default)


class Data:
    def __init__(self, ready: bool, inside_temp: float = 20.0):
        self.ready = ready
        self.inside_temp = inside_temp


class FakeLock:
    def __enter__(self): return self
    def __exit__(self, *a): return False


ENABLED_NO_TEMP = {"enabled": True, "temp_enabled": False, "ac_preset": "cool",
                   "ac_temperature": 22, "windows_pct": 30, "seat_driver": "vent", "seat_copilot": "vent",
                   "steering": False, "mirror": False}


@pytest.fixture(autouse=True)
def _reset_module_state():
    """⚠️ The edge detector's memory is PER VIN now, not three scalars. One car's `_fired` shared
    with another meant getting into the second car after the first found the automation already
    spent, and one `_last_ready` meant car B going Ready read as a rising edge on car A — the
    automation would have preheated the car nobody was in."""
    ra._last_ready.clear(); ra._fired.clear(); ra._off_since.clear()
    yield
    ra._last_ready.clear(); ra._fired.clear(); ra._off_since.clear()


def _run(client, db, ready, temp=20.0, now=0.0):
    return ra.maybe_trigger(db, client, Data(ready, temp), FakeLock(), now=now)


def test_first_poll_never_fires_even_if_already_ready(monkeypatch):
    """Restart safety: the module's memory of 'previous ready' is gone after a restart — the
    first poll must only SEED state, never treat an already-on car as a fresh rising edge."""
    client, db = FakeClient(), FakeDB(ENABLED_NO_TEMP)
    assert _run(client, db, ready=True, now=0.0) is False
    assert client._api.prepare_calls == []
    # a GENUINE later edge (still-ready polls change nothing, this call simulates staying on)
    assert _run(client, db, ready=True, now=1.0) is False


def test_rising_edge_fires_once_and_not_again_while_ready(monkeypatch):
    client, db = FakeClient(), FakeDB(ENABLED_NO_TEMP)
    _run(client, db, ready=False, now=0.0)                    # seed at OFF
    assert _run(client, db, ready=True, now=1.0) is True       # rising edge → fires
    assert len(client._api.prepare_calls) == 1
    assert len(client._api.windows_calls) == 1
    # still Ready on later polls in the SAME session — must not re-fire
    assert _run(client, db, ready=True, now=30.0) is False
    assert _run(client, db, ready=True, now=60.0) is False
    assert len(client._api.prepare_calls) == 1


def test_full_off_on_cycle_fires_again_as_a_new_session(monkeypatch):
    client, db = FakeClient(), FakeDB(ENABLED_NO_TEMP)
    _run(client, db, ready=False, now=0.0)
    _run(client, db, ready=True, now=1.0)                      # session 1 fires
    _run(client, db, ready=False, now=2.0)
    # confirmed off past the debounce window
    _run(client, db, ready=False, now=2.0 + ra.READY_DEBOUNCE_S + 1)
    assert _run(client, db, ready=True, now=2.0 + ra.READY_DEBOUNCE_S + 2) is True   # session 2
    assert len(client._api.prepare_calls) == 2


def test_single_poll_blip_does_not_double_fire(monkeypatch):
    """The exact scenario db_reader._READY_DEBOUNCE_S exists for: `ready` reads 0 for ONE poll
    (a signal blip) then 1 again shortly after — must NOT be treated as a confirmed new session."""
    client, db = FakeClient(), FakeDB(ENABLED_NO_TEMP)
    _run(client, db, ready=False, now=0.0)
    _run(client, db, ready=True, now=1.0)                      # fires
    _run(client, db, ready=False, now=2.0)                     # blip: off for one poll only
    assert _run(client, db, ready=True, now=2.5) is False       # back on WELL before debounce elapses
    assert len(client._api.prepare_calls) == 1


def test_temperature_gate_above_threshold(monkeypatch):
    cfg = {**ENABLED_NO_TEMP, "temp_enabled": True, "temp_comparator": ">", "temp_value": 25.0}
    client, db = FakeClient(), FakeDB(cfg)
    _run(client, db, ready=False, now=0.0)
    assert _run(client, db, ready=True, temp=20.0, now=1.0) is False   # below threshold → no fire
    assert client._api.prepare_calls == []


def test_temperature_gate_below_threshold_negative_value(monkeypatch):
    """Winter scenario: negative threshold, '<' comparator (heating case)."""
    cfg = {**ENABLED_NO_TEMP, "ac_preset": "heat", "temp_enabled": True,
           "temp_comparator": "<", "temp_value": -5.0}
    client, db = FakeClient(), FakeDB(cfg)
    _run(client, db, ready=False, now=0.0)
    assert _run(client, db, ready=True, temp=-10.0, now=1.0) is True   # colder than -5 → fires
    assert client._api.prepare_calls[0][1]["air_condition"]["mode"] == "hot"


def test_condition_not_reevaluated_mid_session(monkeypatch):
    """Once a Ready-on happened, the session is consumed even if the condition later becomes
    true — no surprise mid-drive actuation."""
    cfg = {**ENABLED_NO_TEMP, "temp_enabled": True, "temp_comparator": ">", "temp_value": 25.0}
    client, db = FakeClient(), FakeDB(cfg)
    _run(client, db, ready=False, now=0.0)
    assert _run(client, db, ready=True, temp=20.0, now=1.0) is False   # cold at Ready-on
    assert _run(client, db, ready=True, temp=30.0, now=30.0) is False  # heats up mid-drive — no fire
    assert client._api.prepare_calls == []


def test_disabled_automation_never_fires(monkeypatch):
    client, db = FakeClient(), FakeDB({**ENABLED_NO_TEMP, "enabled": False})
    _run(client, db, ready=False, now=0.0)
    assert _run(client, db, ready=True, now=1.0) is False
    assert client._api.prepare_calls == [] and client._api.windows_calls == []


def test_no_config_at_all_is_a_safe_noop(monkeypatch):
    client, db = FakeClient(), FakeDB(None)   # ready_automation setting missing entirely
    _run(client, db, ready=False, now=0.0)
    assert _run(client, db, ready=True, now=1.0) is False


def test_windows_only_skips_empty_prepare_bundle(monkeypatch):
    cfg = {"enabled": True, "temp_enabled": False, "ac_preset": None, "windows_pct": 50,
           "seat_driver": "off", "seat_copilot": "off", "steering": False, "mirror": False}
    client, db = FakeClient(), FakeDB(cfg)
    _run(client, db, ready=False, now=0.0)
    assert _run(client, db, ready=True, now=1.0) is True
    assert client._api.prepare_calls == []             # nothing to prepare — call skipped
    assert client._api.windows_calls == [("VIN1", "5")]  # 50% -> B10 native scale 0-10 -> "5"


def test_climate_and_seats_only_no_windows(monkeypatch):
    """ac_preset='cool' is the 'Raffreddamento rapido' button — CLIMATE_PRESETS locks its
    temperature to 18 regardless of ac_temperature (same as the official app's rapid-cool/heat;
    the free slider only applies to 'none'/Auto — see test below). Caught by this test on the
    first attempt (expected 20, got 18) — the fix was the test's assumption, not the code."""
    cfg = {"enabled": True, "temp_enabled": False, "ac_preset": "cool", "ac_temperature": 20,
           "windows_pct": None, "seat_driver": "vent", "seat_copilot": "vent", "steering": True, "mirror": True}
    client, db = FakeClient(), FakeDB(cfg)
    _run(client, db, ready=False, now=0.0)
    assert _run(client, db, ready=True, now=1.0) is True
    assert len(client._api.prepare_calls) == 1
    bundle = client._api.prepare_calls[0][1]
    assert bundle["air_condition"]["temperature"] == "18"   # locked, not the configured 20
    assert bundle["seat_setting"]["driver"] == "13"     # vent code
    assert bundle["steeringWheelHeatCtrl"]["enable"] is True
    assert client._api.windows_calls == []


def test_auto_preset_honours_the_configured_temperature(monkeypatch):
    """Unlike cool/heat, 'none' (Auto) has no fixed temperature — ac_temperature applies."""
    cfg = {"enabled": True, "temp_enabled": False, "ac_preset": "none", "ac_temperature": 24,
           "windows_pct": None, "seat_driver": "off", "seat_copilot": "off", "steering": False, "mirror": False}
    client, db = FakeClient(), FakeDB(cfg)
    _run(client, db, ready=False, now=0.0)
    assert _run(client, db, ready=True, now=1.0) is True
    assert client._api.prepare_calls[0][1]["air_condition"]["temperature"] == "24"


def test_nothing_configured_sends_nothing_but_does_not_crash(monkeypatch):
    cfg = {"enabled": True, "temp_enabled": False, "ac_preset": None, "windows_pct": None,
           "seat_driver": "off", "seat_copilot": "off", "steering": False, "mirror": False}
    client, db = FakeClient(), FakeDB(cfg)
    _run(client, db, ready=False, now=0.0)
    assert _run(client, db, ready=True, now=1.0) is False
    assert client._api.prepare_calls == [] and client._api.windows_calls == []


def test_malformed_setting_falls_back_to_safe_defaults(monkeypatch):
    client, db = FakeClient(), FakeDB(None)
    db._settings["ready_automation"] = "{not valid json"
    _run(client, db, ready=False, now=0.0)
    assert _run(client, db, ready=True, now=1.0) is False   # defaults to enabled=False → no crash


def test_command_exception_is_swallowed(monkeypatch):
    cfg = {**ENABLED_NO_TEMP}
    client, db = FakeClient(), FakeDB(cfg)

    def boom(*a, **k):
        raise RuntimeError("cloud unreachable")
    client._api.prepare_car = boom
    _run(client, db, ready=False, now=0.0)
    assert _run(client, db, ready=True, now=1.0) is False   # never raises out of maybe_trigger


# ── two cars ──────────────────────────────────────────────────────────────────

class _Car:
    def __init__(self, vin, car_type="B10"):
        self.vin, self.car_type = vin, car_type


def _run_on(client, db, car, ready, temp=20.0, now=0.0):
    return ra.maybe_trigger(db, client, Data(ready, temp), FakeLock(), now=now, vehicle=car)


def test_the_second_car_gets_its_own_rising_edge(monkeypatch):
    """🔴 One `_fired` for the module meant this: preheat car A in the morning, get into car B in
    the afternoon, and the automation is already spent — nothing happens, and nothing says why."""
    client, db = FakeClient(), FakeDB(ENABLED_NO_TEMP)
    a, b = _Car("VIN_A"), _Car("VIN_B", "T03")
    _run_on(client, db, a, ready=False)                 # seed each car separately
    _run_on(client, db, b, ready=False)
    assert _run_on(client, db, a, ready=True) is True
    assert _run_on(client, db, b, ready=True) is True, "car B has its own edge to fire on"
    assert {v for v, _ in client._api.prepare_calls} == {"VIN_A", "VIN_B"}


def test_one_car_going_ready_is_not_an_edge_on_the_other(monkeypatch):
    """🔴 And one `_last_ready` meant the opposite failure: car B reporting Ready read as a rising
    edge on car A, and Mate preheated the car nobody was getting into."""
    client, db = FakeClient(), FakeDB(ENABLED_NO_TEMP)
    a, b = _Car("VIN_A"), _Car("VIN_B", "T03")
    _run_on(client, db, a, ready=False)
    _run_on(client, db, b, ready=False)
    _run_on(client, db, b, ready=True)                  # only B is switched on
    assert [v for v, _ in client._api.prepare_calls] == ["VIN_B"], "A was never touched"


def test_the_command_goes_to_the_car_that_went_ready(monkeypatch):
    """The commands used to target `client._vehicle` — the FIRST car on the account — whatever car
    the frame had come from."""
    client, db = FakeClient(), FakeDB(ENABLED_NO_TEMP)
    b = _Car("VIN_B", "T03")
    _run_on(client, db, b, ready=False)
    assert _run_on(client, db, b, ready=True) is True
    assert client._api.prepare_calls[0][0] == "VIN_B"
    assert client._api.windows_calls[0][0] == "VIN_B"
    # …and in the T03's own native window scale, not the B10's
    assert client._api.windows_calls[0][1] == "30"
