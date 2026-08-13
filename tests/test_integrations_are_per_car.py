"""The integrations are per CAR too (multi-car, #186) — ABRP, the charge limit, the poll boost.

@cookingeek listed what he actually uses: *"the actions ventilation, heating, cooling centrally on
the tablet. And of course MQTT for the integration into Home Assistant and EVCC. But I'd think MQTT
should be unproblematic, since it's split by VIN anyway."*

He was right about MQTT and EVCC — `_publish_evcc` writes under `{prefix}/{vin}/evcc/*`, per car
already. But reading only as far as the integration he named would have missed three more, all the
same shape: one value for the install, consumed inside the per-car loop.
→ [[feedback-gate-a-feature-find-every-copy]]

🔴 **ABRP is the bad one.** `abrp.send(token, data)` sits inside the per-car poll, with ONE token.
Two cars would push into the SAME ABRP vehicle — position, SoC and speed of both, interleaved. Not
"it doesn't work": it silently corrupts the record with two cars pretending to be one. ABRP's own
model is one token per vehicle, so the token has to be per car.
"""
import pathlib

import db as D
import db_reader
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
A, B = "LFZT03AAAAAAAAAA1", "LFZC10BBBBBBBBBB2"


@pytest.fixture
def two_cars(tmp_path, monkeypatch):
    database = D.Database(str(tmp_path / "p.db"))
    database._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,?,'T03')", (A,))
    database._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (2,?,'C10')", (B,))
    database._conn.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "p.db"))
    return database


# ── ABRP: one token per CAR ───────────────────────────────────────────────────

def test_each_car_pushes_to_its_own_abrp_vehicle(two_cars):
    two_cars.set_abrp_token("TOKEN_A", A)
    two_cars.set_abrp_token("TOKEN_B", B)
    assert two_cars.get_abrp_token(A) == "TOKEN_A"
    assert two_cars.get_abrp_token(B) == "TOKEN_B"


def test_the_install_wide_token_stops_applying_once_a_second_car_exists(two_cars):
    """🔴 This test used to be called "…still covers a single car install" and assert the opposite,
    on this very two-car fixture. The name is what hid the defect: falling back to the install-wide
    token is right with ONE car, and with two it is the same corruption this file exists to stop,
    wearing the hat of backwards compatibility. Both cars answered to LEGACY and pushed into one
    ABRP vehicle.

    The single-car promise is real and kept — it is asserted in
    test_abrp_token_never_feeds_two_cars.py, on a fixture that actually has one car."""
    two_cars.set_secret("abrp_token", "LEGACY")
    assert two_cars.get_abrp_token(A) == ""
    assert two_cars.get_abrp_token(B) == ""


def test_a_car_without_a_token_does_not_borrow_the_other_cars(two_cars):
    """🔴 The whole point. Falling back to the install token is right; falling back to the OTHER
    CAR's would be the corruption this fixes, wearing a different hat."""
    two_cars.set_abrp_token("TOKEN_A", A)
    assert two_cars.get_abrp_token(B) == "", "no token of its own and none shared → send nothing"


def test_the_poll_loop_sends_each_car_its_own_token():
    src = (ROOT / "poller" / "main.py").read_text()
    assert "abrp.send(db.get_secret(\"abrp_token\"), data)" not in src, "the install-wide token"
    assert "get_abrp_token(" in src


def test_a_car_with_no_token_is_not_pushed_at_all():
    """🔴 The half the first version of this file forgot to assert, and a mutation walked straight
    through: dropping the guard calls `abrp.send("", data)` on every poll of the car that has no
    token. ABRP would be handed a stream authenticated with nothing, once per poll, for ever."""
    src = (ROOT / "poller" / "main.py").read_text()
    block = src.split('if db.get_setting("abrp_enabled") == "1":', 1)[1].split("\n\n", 1)[0]
    send = [ln for ln in block.splitlines() if "abrp.send(" in ln]
    assert len(send) == 1, "one send, or this test is reading the wrong block"
    guard = [ln for ln in block.splitlines() if "if _tok" in ln]
    assert guard, "the send must be guarded by a token that exists"
    assert block.index(guard[0]) < block.index(send[0]), "guarded BEFORE, not after"


# ── the charge limit the car reported is the CAR's ────────────────────────────

def test_the_charge_limit_is_remembered_per_car(two_cars):
    """It is read FROM the car and used to fill in a charge schedule's target SoC. One key for two
    cars means whichever polled last decides — and then Home Assistant sets car A's plan to car B's
    ceiling, which is a number the owner never typed anywhere."""
    two_cars.set_charge_limit_percent(80, A)
    two_cars.set_charge_limit_percent(90, B)
    assert two_cars.get_charge_limit_percent(A) == "80"
    assert two_cars.get_charge_limit_percent(B) == "90"


def test_the_charge_limit_falls_back_to_the_install_wide_one(two_cars):
    two_cars.set_setting("charge_limit_percent", "70")
    assert two_cars.get_charge_limit_percent(A) == "70"


# ── the command boost belongs to the car that was commanded ───────────────────

def test_a_command_to_one_car_does_not_speed_up_the_other(two_cars):
    """A command boosts polling for a minute so the state syncs. Shared, a command to the car in the
    garage would also wake the one on the motorway — harmless in itself, but it spends the other
    car's cloud budget and muddles what the log says was happening."""
    two_cars.set_boost(A, 9_999_999_999.0)
    assert two_cars.boosting(A) is True
    assert two_cars.boosting(B) is False


def test_the_boost_expires(two_cars):
    two_cars.set_boost(A, 1.0)          # long past
    assert two_cars.boosting(A) is False


# ── EVCC needed nothing, and that is worth pinning ────────────────────────────

def test_evcc_was_already_per_car():
    """cookingeek's own guess, confirmed in the code rather than agreed with: the EVCC mirrors are
    published under `{prefix}/{vin}/evcc/*`, so two cars were never going to collide there."""
    src = (ROOT / "poller" / "mqtt.py").read_text()
    body = src.split("def _publish_evcc", 1)[1].split("\n    def ", 1)[0]
    assert "base" in body, "EVCC topics hang off the per-VIN base"
    caller = src.split("self._publish_evcc(", 1)[0]
    assert 'base = f"{self.topic_prefix}/{data.vin}"' in caller, "and that base carries the VIN"
