"""'Command not sent: invalid charge flag' — which shapes of the car's own `config.3` trigger it?

`NewAPIClient.set_charge_limit` reads the car's charge plan (`_get_charge_appointment`), changes the
SoC and sends the rest back. `charge()` then demands chargeEnable / circulation / recharge be
exactly the int 0 or 1, but the reader only converts digit-only strings — anything else the cloud
publishes reaches the validator untouched. These tests feed the REAL reader a `config.3` in each
shape and watch what happens to the command.

Skipped where leapmotor_api isn't installed (see pytest.ini)."""
import json

import pytest

pytest.importorskip("command_client", reason="needs leapmotor_api")
from api_v2_bridge import NewAPIClient  # noqa: E402

VIN = "VINTEST"
GOOD = {"isEnable": "1", "percent": "80", "beginTime": "22:00", "endTime": "06:00",
        "cycles": "1,1,1,1,1,1,1", "circulation": "1", "recharge": "0"}


def _client(config3, sent):
    class Client(NewAPIClient):
        def __init__(self): pass
        def route(self, vin): return {"appCenter": "https://example.invalid"}
        def read(self, path, params, **kw):
            return {"data": {"vin": VIN, "config": {"3": dict(config3)}}}
        def _remote_control_raw(self, **kw): sent.append(json.loads(kw["cmd_content"]))
    return Client()


def _set_limit(config3):
    sent = []
    _client(config3, sent).set_charge_limit(VIN, 90)
    return sent


def test_a_plan_the_reader_understands_is_sent_with_only_the_soc_changed():
    (state,) = _set_limit(GOOD)
    assert state["chargesoc"] == 90
    assert (state["chargeEnable"], state["circulation"], state["recharge"]) == (1, 1, 0)


# The cloud says the same thing in another spelling: the flag is meaningful, so it must not block
# the command. Each of these is what the car might plausibly publish for a 0/1 flag.
@pytest.mark.parametrize("field", ["isEnable", "circulation", "recharge"])
@pytest.mark.parametrize("raw,expected", [(1, 1), (0, 0), ("01", 1), ("1.0", 1), ("0.0", 0)])
def test_a_flag_in_another_spelling_is_normalised_not_refused(field, raw, expected):
    (state,) = _set_limit(dict(GOOD, **{field: raw}))
    key = {"isEnable": "chargeEnable"}.get(field, field)
    assert state[key] == expected


# A flag the car did not publish. `circulation` and `recharge` get the defaults save_charge_schedule
# already uses (1 and 0), and the raw value is logged so a bug report can say what the car sent.
@pytest.mark.parametrize("field,key,default", [("circulation", "circulation", 1), ("recharge", "recharge", 0)])
@pytest.mark.parametrize("missing", ["absent", None, ""])
def test_an_unpublished_secondary_flag_gets_the_default_and_is_logged(field, key, default, missing, caplog):
    config3 = {k: v for k, v in GOOD.items() if k != field}
    if missing != "absent":
        config3[field] = missing
    with caplog.at_level("WARNING"):
        (state,) = _set_limit(config3)
    assert state[key] == default and state["chargesoc"] == 90
    assert key in caplog.text


# chargeEnable has no default: the whole plan is re-sent, so guessing it would switch the plan on
# or off. And a boolean is refused on purpose (test_cloud_access_refresh): the cloud speaks 0/1.
@pytest.mark.parametrize("missing", ["absent", None, "", True])
def test_an_unpublished_enable_flag_is_refused_and_named(missing):
    config3 = {k: v for k, v in GOOD.items() if k != "isEnable"}
    if missing != "absent":
        config3["isEnable"] = missing
    sent = []
    with pytest.raises(Exception) as err:
        _client(config3, sent).set_charge_limit(VIN, 90)
    assert not sent, "nothing may reach the car"
    assert "chargeEnable" in str(err.value)


# `recharge` is the one flag whose safe value is known: 0 never charges past the programmed window.
# So an unreadable `recharge` (present, but not 0/1) is replaced by 0 and the raw value is logged for
# review. A real 1 is the car's own setting and is left alone.
# (sent by the cloud, what the log shows: the reader already turns the digit string "2" into 2)
@pytest.mark.parametrize("raw,logged", [(2, 2), (-1, -1), (True, True), ("2", 2), ("abc", "'abc'"),
                                        ("true", "'true'"), (1.5, 1.5)])
def test_an_unreadable_recharge_is_sent_as_zero_and_the_raw_value_is_logged(raw, logged, caplog):
    with caplog.at_level("WARNING"):
        (state,) = _set_limit(dict(GOOD, recharge=raw))
    assert state["recharge"] == 0 and state["chargesoc"] == 90
    assert f"recharge={logged}" in caplog.text


def test_a_readable_recharge_of_one_is_the_cars_own_setting_and_is_kept(caplog):
    with caplog.at_level("WARNING"):
        (state,) = _set_limit(dict(GOOD, recharge="1"))
    assert state["recharge"] == 1 and "recharge" not in caplog.text


# circulation has no known-safe value, so a value the car DID send but we cannot read is not ours to
# overwrite: refused and named.
@pytest.mark.parametrize("raw", [2, True, "abc"])
def test_an_unreadable_circulation_is_refused_and_named(raw):
    sent = []
    with pytest.raises(Exception) as err:
        _client(dict(GOOD, circulation=raw), sent).set_charge_limit(VIN, 90)
    assert not sent and "circulation" in str(err.value)


# ── What the person sees on the Charges page ─────────────────────────────────────────────────
# The tests above stop at the bridge. This one goes through the real POST the page fires, because
# the complaint was the sentence in the UI, not an exception in a log.
def _page(tmp_path, monkeypatch, config3):
    pytest.importorskip("fastapi", reason="web/main.py needs fastapi")
    pytest.importorskip("httpx", reason="Starlette TestClient needs httpx")
    import db as D
    import db_reader
    import main
    import command_client
    from starlette.testclient import TestClient

    poller = D.Database(str(tmp_path / "p.db"))
    poller._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1,?,'C10')", (VIN,))
    poller._conn.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "p.db"))
    db_reader.set_setting("setup_complete", "1")
    monkeypatch.setenv("MATE_API_V2", "1")

    sent = []

    class Session:
        # Same contract as command_client's V2 path: a refusal comes back as (False, message).
        def execute(self, fn):
            try:
                fn(_client(config3, sent), VIN)
            except Exception as exc:
                return False, str(exc)
            return True, "Cloud accepted; physical execution not confirmed"

    monkeypatch.setattr(command_client, "_session", Session())
    return TestClient(main.app), sent


def test_the_page_sets_the_limit_when_the_car_spells_a_flag_differently(tmp_path, monkeypatch):
    client, sent = _page(tmp_path, monkeypatch, dict(GOOD, recharge="0.0", circulation="1.0"))
    body = client.post("/api/charge-limit", data={"percent": "90"}).text
    assert "Limit set to 90%" in body
    assert "invalid charge flag" not in body
    assert sent and sent[0]["chargesoc"] == 90


def test_the_page_sets_the_limit_when_the_car_sends_an_unreadable_recharge(tmp_path, monkeypatch):
    client, sent = _page(tmp_path, monkeypatch, dict(GOOD, recharge="2"))
    body = client.post("/api/charge-limit", data={"percent": "90"}).text
    assert "Limit set to 90%" in body and sent[0]["recharge"] == 0


def test_the_page_sets_the_limit_when_the_car_omits_recharge(tmp_path, monkeypatch):
    client, sent = _page(tmp_path, monkeypatch, {k: v for k, v in GOOD.items() if k != "recharge"})
    body = client.post("/api/charge-limit", data={"percent": "90"}).text
    assert "Limit set to 90%" in body and sent[0]["recharge"] == 0


def test_the_page_names_the_field_the_car_did_not_publish(tmp_path, monkeypatch):
    client, sent = _page(tmp_path, monkeypatch, {k: v for k, v in GOOD.items() if k != "isEnable"})
    body = client.post("/api/charge-limit", data={"percent": "90"}).text
    assert "Limit set" not in body and not sent
    assert "chargeEnable" in body


# ── Saving the schedule (issue #343: "Cannot set schedule charge") ───────────────────────────
# The panel's save goes through command_client.save_charge_schedule, not set_charge_limit. It
# re-sends the car's own circulation/recharge; an int the car published outside 0/1 reaches charge()
# and the whole save is refused with "invalid charge flag".
def _save(monkeypatch, cur):
    import command_client as cc
    monkeypatch.setenv("MATE_API_V2", "1")
    captured = {}

    class Api:
        def set_charge_schedule(self, vin, **kw): captured.update(kw)

    class Session:
        def get_charge_schedule(self): return dict(cur)
        def execute(self, fn):
            fn(Api(), VIN)
            return True, "OK"

    monkeypatch.setattr(cc, "_session", Session())
    return cc.save_charge_schedule(enabled=True, soc_limit=90, start_time="14:00", end_time="17:00"), captured


PLAN = {"chargeEnable": 1, "chargesoc": 90, "starttime": "00:00", "endtime": "07:55",
        "cycles": "1,1,1,1,1,1,1", "circulation": 1, "recharge": 0}


@pytest.mark.parametrize("raw", [2, -1, 7])
def test_saving_the_schedule_sends_recharge_zero_when_the_car_published_an_unreadable_one(monkeypatch, caplog, raw):
    from command_contracts import charge
    with caplog.at_level("WARNING"):
        (ok, _), sent = _save(monkeypatch, dict(PLAN, recharge=raw))
    assert ok and sent["recharge"] == 0
    assert f"recharge={raw}" in caplog.text
    # …and what Mate would hand the car is a plan the contract accepts.
    charge(dict(chargeEnable=1, chargesoc=90, starttime="14:00", endtime="17:00",
                cycles=sent["cycles"], circulation=sent["circulation"], recharge=sent["recharge"]))


def test_saving_the_schedule_keeps_a_real_recharge_of_one(monkeypatch):
    (ok, _), sent = _save(monkeypatch, dict(PLAN, recharge=1))
    assert ok and sent["recharge"] == 1


@pytest.mark.parametrize("raw", [2, -1])
def test_saving_the_schedule_refuses_an_unreadable_circulation_by_name(monkeypatch, raw):
    (ok, message), sent = _save(monkeypatch, dict(PLAN, circulation=raw))
    assert not ok and not sent and "circulation" in message
