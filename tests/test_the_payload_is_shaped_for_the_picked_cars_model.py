"""The command must be SHAPED for the picked car's model, not the login car's (#253, follow-up).

v3.14.3 fixed *where* a command goes: `_target()` resolves the picked car on every use. It did not
fix *what* is in the command. Three commands change their PAYLOAD by model, and all three read the
model from the frozen login vehicle:

    def _session_car_type() -> str:
        v = getattr(_session, "_vehicle", None)      # ← the car the account listed FIRST

  * `_windows_native` — the window scale. LEAP cars (B10/C10/B05) take 0–10, the T03 takes 0–100.
  * `_t03_force_manual` — every climate write is rewritten auto→manual on a T03 (#67).
  * `ac_off` — the T03 gets a full cmd-170 body; the others get `ac_switch(operate=off)`.

@cookingeek's account lists the **T03 first** and he drives the **C10** (his screenshot: a C10
heading over the T03's picture). So on v3.14.3 his C10 commands now carry the right VIN and the
wrong shape: "windows to 50%" sends the T03's `50`, which a C10 **silently ignores** — the car does
nothing at all — and his climate is rewritten to the T03's manual path.

🔑 Why the v3.14.3 guard did not catch it: it forbids `self._vehicle`, and this is a MODULE-level
function reading `getattr(_session, "_vehicle", …)`. Same defect, different spelling — so the guard
here is on the string `"_vehicle"`, not on one way of writing it.
"""
import pytest

import command_client


class _Vehicle:
    def __init__(self, vin, car_type):
        self.vin, self.car_type = vin, car_type


class _Api:
    """Records what a command actually put on the wire."""
    def __init__(self):
        self.operation_password = ""
        self.sent = []

    def windows(self, vin, value):
        self.sent.append(("windows", vin, value))

    def ac_switch(self, vin, params=None):
        self.sent.append(("ac_switch", vin, params))

    def _remote_control(self, vin, action, cmd_content):
        self.sent.append(("raw", vin, action, cmd_content))

    def ac_on(self, vin, params=None):
        self.sent.append(("ac_on", vin, params))


VIN_T03 = "LFZT03000000000001"     # first on the account
VIN_C10 = "LFZC10000000000002"     # the one he drives


@pytest.fixture
def two_cars(tmp_path, monkeypatch):
    """A live session over a T03 + C10 account, with the C10 selected and no network anywhere."""
    import db as D
    import db_reader

    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,?,'T03')", (VIN_T03,))
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (2,?,'C10')", (VIN_C10,))
    c.commit()
    pdb._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)

    s = command_client.LeapmotorSession()
    s._api = _Api()
    s._vehicles = [_Vehicle(VIN_T03, "T03"), _Vehicle(VIN_C10, "C10")]
    s._vehicle = s._vehicles[0]
    monkeypatch.setattr(s, "_connect", lambda: None)
    monkeypatch.setattr(command_client, "_session", s)
    return s, db_reader


def test_the_window_scale_is_the_picked_cars_own(two_cars):
    """His exact case. 50% on a C10 is native 5; the T03's 50 is above the C10's full-open 10 and
    the car silently ignores it — the windows do not move at all."""
    s, db_reader = two_cars
    db_reader.set_active_vehicle(VIN_C10)
    command_client.set_windows(50)
    assert s._api.sent == [("windows", VIN_C10, "5")], s._api.sent


def test_the_mirror_case_is_wrong_too(two_cars):
    """A T03 selected under a C10-first account: the LEAP scale would send 5 for half-open, and the
    T03 reads that as 5%."""
    s, db_reader = two_cars
    s._vehicles = [_Vehicle(VIN_C10, "C10"), _Vehicle(VIN_T03, "T03")]
    s._vehicle = s._vehicles[0]
    db_reader.set_active_vehicle(VIN_T03)
    command_client.set_windows(50)
    assert s._api.sent == [("windows", VIN_T03, "50")], s._api.sent


def test_the_t03_climate_rewrite_follows_the_picked_car(two_cars):
    """#67 rewrites auto→manual for the T03 only. Applied to the C10 it changes a command that
    works into one the C10 was never measured on."""
    _, db_reader = two_cars
    db_reader.set_active_vehicle(VIN_C10)
    assert command_client._t03_force_manual("auto", "cold") == ("auto", "cold")

    db_reader.set_active_vehicle(VIN_T03)
    assert command_client._t03_force_manual("auto", "cold") == ("manual", "cold")


def test_ac_off_sends_the_body_of_the_car_it_is_going_to(two_cars):
    """The T03 needs its full cmd-170 body; sending it to a C10 replaces a call that works."""
    s, db_reader = two_cars
    db_reader.set_active_vehicle(VIN_C10)
    command_client.ac_off()
    assert s._api.sent == [("ac_switch", VIN_C10, {"operate": "off"})], s._api.sent

    s._api.sent.clear()
    db_reader.set_active_vehicle(VIN_T03)
    command_client.ac_off()
    assert s._api.sent == [("raw", VIN_T03, "ac_on", command_client.T03_AC_OFF_BODY)], s._api.sent


# ── the Home Assistant path ───────────────────────────────────────────────────
# The poller learned this lesson ONCE and applied it to one command. `_mqtt_windows_native` carries
# the scar in its own docstring — *"it used to read the model off `client._vehicle`, the FIRST car
# on the account, while the command carries its own VIN in the MQTT topic"* — and A/C-off, eight
# lines away, still does exactly that. → [[feedback-gate-a-feature-find-every-copy]]

def _poller_main():
    """poller/main.py under its own name (a bare `main` is the WEB's)."""
    import importlib.util
    import pathlib
    path = pathlib.Path(__file__).parents[1] / "poller" / "main.py"
    spec = importlib.util.spec_from_file_location("poller_main_shape", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _mqtt(tmp_path, cars):
    """A poller MQTT bridge over `cars`, the first one being what the account listed first."""
    import types
    import db as D
    api = types.SimpleNamespace(calls=[])
    api.ac_switch = lambda vin, params=None: api.calls.append(("ac_switch", vin, params))
    api._remote_control = lambda **kw: api.calls.append(("raw", kw["vin"], kw["cmd_content"]))
    client = types.SimpleNamespace(_api=api, _vehicles=cars, _vehicle=cars[0])
    state = {v.vin: True for v in cars}          # A/C on, so the "already off" guard never fires
    service = types.SimpleNamespace(
        climate_on_for=state.get,
        set_climate_on=state.__setitem__,
        publish_state=lambda vin, k, v: None)
    return _poller_main(), api, client, service, D.Database(str(tmp_path / "t.db"))


def test_the_ha_ac_off_button_asks_the_model_of_the_car_it_is_for(tmp_path):
    """His pair, from Home Assistant: the C10's A/C-off must not carry the T03's seven-field body."""
    pytest.importorskip("paho.mqtt.client", reason="the MQTT bridge needs paho")
    pm, api, client, service, db = _mqtt(
        tmp_path, [_Vehicle(VIN_T03, "T03"), _Vehicle(VIN_C10, "C10")])
    pm._handle_mqtt_command(client, service, db, VIN_C10, "climate_off", None)
    assert api.calls == [("ac_switch", VIN_C10, {"operate": "off"})], api.calls


def test_and_the_t03_still_gets_its_own_body_when_it_is_second(tmp_path):
    """The mirror, and the worse half: a T03 under a C10-first account got `ac_switch operate=off`,
    the one form #67 proved the T03 ignores — so its A/C never switched off from Home Assistant."""
    pytest.importorskip("paho.mqtt.client", reason="the MQTT bridge needs paho")
    pm, api, client, service, db = _mqtt(
        tmp_path, [_Vehicle(VIN_C10, "C10"), _Vehicle(VIN_T03, "T03")])
    pm._handle_mqtt_command(client, service, db, VIN_T03, "climate_off", None)
    assert api.calls == [("raw", VIN_T03, command_client.T03_AC_OFF_BODY)], api.calls


def test_nothing_reads_the_model_from_the_login_car():
    """The guard v3.14.3 should have had: it forbade `self._vehicle` and this defect was spelled
    `getattr(_session, "_vehicle", …)` — a module-level function, invisible to it.

    Only the session's own assignments and its `_target()` may name the frozen vehicle at all."""
    import pathlib
    import re
    src = (pathlib.Path(__file__).resolve().parent.parent / "web" / "command_client.py").read_text()
    inside_target = False
    offenders = []
    for n, line in enumerate(src.split("\n"), start=1):
        if re.match(r"\s*def \w+", line):
            inside_target = line.strip().startswith("def _target(")
        # The frozen attribute, however it is spelled — `._vehicle` or `getattr(…, "_vehicle")`.
        # NOT `_vehicles` (the list) and NOT `unlock_vehicle` / `detect_vehicle` (API calls).
        if not re.search(r"\._vehicle\b|[\"']_vehicle[\"']", line):
            continue
        if inside_target or re.match(r"\s*self\._vehicle = ", line):
            continue
        if "log.info" in line or line.strip().startswith("#"):
            continue
        offenders.append(f"{n}: {line.strip()}")
    assert not offenders, "these read the car from the login:\n  " + "\n  ".join(offenders)
