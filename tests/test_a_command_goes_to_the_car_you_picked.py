"""A command must reach the car in the picker, not the first one on the account (#253).

@cookingeek, the only install with two real Leapmotors, on v3.14.1: *"Live Stats and Picture Not
switching."* His screenshot shows the heading correctly reading "C10 Live-Status" over the picture
of his **T03** — and the picture was the visible half of something much worse.

The session that talks to the cloud binds ONE vehicle at login:

    self._vehicle = vehicles[0]          # _connect

and the picker never touches it. Every read on the page is scoped to the selected car, but every
WRITE went to `vehicles[0]`: lock and unlock, trunk, windows, climate, charge start and stop,
unlock-charger — carrying that car's PIN. Pick the second car, press Unlock, and the first one
opens.

Three call sites, all frozen at connection time and all wrong for the same reason:

  * `_execute_inner` — the VIN the command is sent to, and the PIN it is authorised with;
  * `log_command(vin=…)` — which car the responsiveness badge credits the command to;
  * `get_car_picture_package` — which car's image the Overview shows, the visible symptom.

🔑 `_use_pin_of` already carried the whole insight in its docstring — *"the session is built once
and the user can change the selected car under it, so a PIN frozen at connection time would send
car A's four digits with car B's command"* — and then took its VIN from the frozen vehicle. The
right idea, applied to the wrong source.

So the car is resolved on EVERY use, not once: the picker can move between two commands without a
reconnection, and a session that re-resolves only on login would keep firing at the old car for as
long as the token lives.
"""
import pytest

import command_client


class _Vehicle:
    def __init__(self, vin, car_type):
        self.vin, self.car_type = vin, car_type


class _Api:
    """Enough of the client for a command to be dispatched."""
    def __init__(self):
        self.operation_password = ""


VIN_FIRST = "LFZFIRST000000001"      # what the cloud lists first — his T03
VIN_PICKED = "LFZSECOND00000002"     # what the owner is looking at — his C10


@pytest.fixture
def session(tmp_path, monkeypatch):
    """A logged-in session over a two-car account, with no network anywhere."""
    import db as D
    import db_reader

    path = str(tmp_path / "t.db")
    pdb = D.Database(path)
    c = pdb._conn
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,?,'T03')", (VIN_FIRST,))
    c.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (2,?,'C10')", (VIN_PICKED,))
    c.commit()
    pdb._conn.close()
    monkeypatch.setattr(db_reader, "DB_PATH", path)

    s = command_client.LeapmotorSession()
    s._api = _Api()
    s._vehicles = [_Vehicle(VIN_FIRST, "T03"), _Vehicle(VIN_PICKED, "C10")]
    s._vehicle = s._vehicles[0]
    monkeypatch.setattr(s, "_connect", lambda: None)
    return s, db_reader


def _sent(session_obj):
    """The VIN a command actually reaches."""
    got = []
    session_obj.execute(lambda api, vin: got.append(vin))
    return got[0] if got else None


def test_the_command_reaches_the_car_in_the_picker(session):
    """His case: the C10 is selected, so Unlock must unlock the C10."""
    s, db_reader = session
    db_reader.set_active_vehicle(VIN_PICKED)
    assert _sent(s) == VIN_PICKED


def test_switching_car_moves_the_target_without_a_new_login(session):
    """The picker can move between two commands; the token lives on. A car resolved once at login
    would keep firing at the old one for as long as the session lasts."""
    s, db_reader = session
    db_reader.set_active_vehicle(VIN_PICKED)
    assert _sent(s) == VIN_PICKED
    db_reader.set_active_vehicle(VIN_FIRST)
    assert _sent(s) == VIN_FIRST


def test_with_nothing_selected_it_stays_the_first_car(session):
    """Every single-car install in the world: unchanged."""
    s, _ = session
    assert _sent(s) == VIN_FIRST


def test_a_selection_naming_a_car_we_do_not_have_falls_back(session):
    """A stale setting must never leave a command with no target, or crash one."""
    s, db_reader = session
    db_reader.set_setting(db_reader.ACTIVE_VEHICLE_SETTING, "LFZGHOST000000000")
    assert _sent(s) == VIN_FIRST


def test_the_pin_is_the_picked_car_s_own(session):
    """The PIN travels with the command — sending car A's four digits with car B's command is the
    failure `_use_pin_of` was written to prevent."""
    s, db_reader = session
    db_reader.set_active_vehicle(VIN_PICKED)
    db_reader.set_operate_pin("4321", VIN_PICKED)
    db_reader.set_operate_pin("1111", VIN_FIRST)
    s.execute(lambda api, vin: None)
    assert s._api.operation_password == "4321"


def test_the_command_log_credits_the_car_it_went_to(session):
    """The responsiveness badge is per car since v3.13.0; crediting the wrong one would make one
    car's silence read as the other's."""
    s, db_reader = session
    db_reader.set_active_vehicle(VIN_PICKED)
    s.execute(lambda api, vin: None)
    rows = db_reader._get().execute(
        "SELECT vin FROM command_log ORDER BY id DESC LIMIT 1").fetchall()
    assert rows and (rows[0]["vin"] or "").lower() == VIN_PICKED.lower()


def test_the_car_picture_follows_the_picker_too(session, monkeypatch):
    """The visible symptom: his C10 showing the T03's render."""
    s, db_reader = session
    db_reader.set_active_vehicle(VIN_PICKED)
    asked = []
    s._api.get_car_picture = lambda vehicle: (asked.append(vehicle.vin), {"data": {"key": "k"}})[1]
    s._api.download_car_picture_package = lambda picture_key: b"PK\x03\x04zip"
    s.get_car_picture_package()
    assert asked == [VIN_PICKED], f"the package was fetched for {asked}"


def test_no_call_is_left_on_the_frozen_vehicle():
    """The whole point: seventeen call sites took their car from the login, and fixing three of them
    is how the picture kept coming back wrong. Only the assignments and the login line may name
    `self._vehicle` — everything that ASKS the cloud for something must resolve per use.

    This is the guard, not the test above it: a new endpoint added next month will copy the line
    beside it, and the line beside it must be right."""
    import pathlib
    import re
    src = (pathlib.Path(__file__).resolve().parent.parent / "web" / "command_client.py").read_text()
    offenders = []
    for n, line in enumerate(src.split("\n"), start=1):
        if "self._vehicle" not in line or "_vehicles" in line:
            continue
        if re.match(r"\s*self\._vehicle = ", line):      # the fallback itself
            continue
        if "Session started" in line or "log.info" in line:
            continue
        offenders.append(f"{n}: {line.strip()}")
    assert not offenders, "these still take the car from the login:\n  " + "\n  ".join(offenders)
