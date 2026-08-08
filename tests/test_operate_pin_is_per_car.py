"""The operation PIN belongs to the CAR, not to the install (multi-car, #186).

@cookingeek found this in an afternoon, reading the design — before his second car had even arrived:

    "For performing actions on the car, I'd think you'd need to store two PINs. The T03 has a
     4-digit one, the C10 probably doesn't — or rather, I don't really know."

He is right, and it was a hole in eight steps of multi-car work: battery capacity, the REEV flag,
abilities, model, MQTT entities all became facts about a car — the PIN stayed one secret for the
whole install. With two cars whose PINs differ, every command to the second one would have failed,
and only the commands: reads never carry a PIN, so the interface would have looked perfectly healthy.

🔑 The cloud checks it PER VIN — `/operPwd/verify` takes `operatePassword` **and** `vin`. That does
not prove two cars must differ, but it proves the API is built so they can.

⚠️ Fallback is the whole safety of it: no per-car PIN stored → the install-wide one, which is what
every existing install has. One car, or two cars sharing a PIN, must see no change whatsoever.
"""
import importlib.util
import pathlib

import db as D                 # poller/db.py
import db_reader
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
T03, C10 = "LFZT03AAAAAAAAAA1", "LFZC10BBBBBBBBBB2"


@pytest.fixture
def two_cars(tmp_path, monkeypatch):
    database = D.Database(str(tmp_path / "p.db"))
    database._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,?,'T03')", (T03,))
    database._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (2,?,'C10')", (C10,))
    database._conn.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "p.db"))
    return database


# ── the reader, on both sides ─────────────────────────────────────────────────

def test_each_car_gets_its_own_pin(two_cars):
    db_reader.set_operate_pin("1234", T03)
    db_reader.set_operate_pin("9876", C10)
    assert db_reader.get_operate_pin(T03) == "1234"
    assert db_reader.get_operate_pin(C10) == "9876"


def test_a_car_without_its_own_falls_back_to_the_install_pin(two_cars):
    """Every install that exists today has exactly this: one PIN, no per-car ones. It must keep
    working for both cars without anybody typing anything."""
    db_reader.set_secret("leapmotor_pin", "5555")
    assert db_reader.get_operate_pin(T03) == "5555"
    assert db_reader.get_operate_pin(C10) == "5555"


def test_one_car_overriding_does_not_disturb_the_other(two_cars):
    db_reader.set_secret("leapmotor_pin", "5555")
    db_reader.set_operate_pin("1234", T03)
    assert db_reader.get_operate_pin(T03) == "1234"
    assert db_reader.get_operate_pin(C10) == "5555", "the other car keeps the install PIN"


def test_clearing_a_car_pin_returns_it_to_the_install_one(two_cars):
    db_reader.set_secret("leapmotor_pin", "5555")
    db_reader.set_operate_pin("1234", T03)
    db_reader.set_operate_pin("", T03)
    assert db_reader.get_operate_pin(T03) == "5555"


def test_no_vin_means_the_selected_car(two_cars):
    """The web asks without a vin — every page is already scoped to the picked car, and the PIN has
    to follow the same choice or a command goes to the car you are not looking at."""
    db_reader.set_operate_pin("1234", T03)
    db_reader.set_operate_pin("9876", C10)
    db_reader.set_active_vehicle(C10)
    assert db_reader.get_operate_pin() == "9876"
    db_reader.set_active_vehicle(T03)
    assert db_reader.get_operate_pin() == "1234"


def test_the_poller_reads_the_same_pins(two_cars):
    """Two processes, two modules, one stored secret. If they disagreed, a command from Home
    Assistant and the same command from the page would authenticate differently."""
    db_reader.set_operate_pin("1234", T03)
    db_reader.set_secret("leapmotor_pin", "5555")
    assert two_cars.get_operate_pin(T03) == "1234"
    assert two_cars.get_operate_pin(C10) == "5555"


def test_the_pin_is_stored_encrypted(two_cars):
    """It is a secret, and it must be as encrypted at rest as the install-wide one. A per-car key
    that quietly bypassed `set_secret` would put four digits in the clear in the database."""
    db_reader.set_operate_pin("1234", T03)
    raw = db_reader.get_setting(f"leapmotor_pin_{T03.lower()}", "")
    assert raw and "1234" not in raw, "the stored value must not contain the PIN"


def test_a_lost_encryption_key_names_the_per_car_pins_too(two_cars, monkeypatch):
    """`check_decryption` tells people, in words, which secrets belong to a key they no longer have
    (#227). A per-car PIN that is not in that list would go missing in silence."""
    db_reader.set_operate_pin("1234", T03)
    monkeypatch.setattr(db_reader.crypto, "can_decrypt", lambda _v: False)
    lost = db_reader.check_decryption()
    assert f"leapmotor_pin_{T03.lower()}" in lost


# ── the two command paths actually use it ─────────────────────────────────────

class _Api:
    def __init__(self):
        self.operation_password = "INSTALL"
        self.seen = []

    def lock_vehicle(self, vin):
        self.seen.append((vin, self.operation_password))


@pytest.mark.parametrize("picked,expected", [(C10, "9876"), (T03, "1234")])
def test_the_web_command_uses_the_selected_cars_pin(two_cars, monkeypatch, picked, expected):
    """⚠️ Driven through the REAL session object, not a stand-in with an `execute` of its own: the
    first version of this test replaced `_session` wholesale, so it called its own fake and proved
    nothing about the code that ships. → [[feedback-a-successful-call-is-not-a-correct-result]]"""
    import command_client as cc
    db_reader.set_operate_pin("1234", T03)
    db_reader.set_operate_pin("9876", C10)
    db_reader.set_active_vehicle(picked)

    session = cc.LeapmotorSession()
    session._api = _Api()
    session._vehicle = type("_V", (), {"vin": picked})()
    monkeypatch.setattr(session, "_connect", lambda: None)

    ok, _msg = session._execute_inner(lambda api, vin: api.lock_vehicle(vin))
    assert ok
    assert session._api.seen[-1] == (picked, expected), "the command carries the picked car's PIN"


def test_the_home_assistant_command_uses_the_pin_of_the_car_it_names(two_cars, monkeypatch):
    """🔑 MQTT is not scoped to the picked car — the topic names the VIN, and a command can arrive
    for either. Using the *selected* car's PIN here would authenticate car A's command with car B's
    four digits, which fails only sometimes and never says why."""
    spec = importlib.util.spec_from_file_location("poller_main_pin", ROOT / "poller" / "main.py")
    pmain = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pmain)

    db_reader.set_operate_pin("1234", T03)
    db_reader.set_operate_pin("9876", C10)
    api = _Api()
    client = type("_C", (), {"_api": api, "_vehicle": None})()
    service = type("_S", (), {"last_climate_on": None})()
    pmain._handle_mqtt_command(client, service, two_cars, T03, "lock", None)
    assert api.seen[-1] == (T03, "1234")
    pmain._handle_mqtt_command(client, service, two_cars, C10, "lock", None)
    assert api.seen[-1] == (C10, "9876")
