"""A car whose configuration carries no charge plan reads it where the earlier library did (#380).

@ViriatusOG's T03 on 4.7.15: saving the charge schedule worked — the cloud said yes, and the car itself
shows 23:30, on — but the Charges page read it back empty: "No schedule", the form's defaults, the
schedule unticked right after he ticked it. Mate reads the plan from the car's configuration
(`commonConfig`, key 3) and his T03's carries none. The earlier library read it from `getAppointment`
with cmdId 190; until 4.7.7 his page showed it ("This setting was working before").

Measured on our B10 on 02/10/2026, read only, no login: both answer, with the same seven fields, but not
the same thing — the configuration said the schedule was off (isEnable 0), getAppointment said on (1).
So a car whose configuration carries its plan keeps reading it there, as it always has; getAppointment
is asked only when the configuration does not say whether the schedule is on, or when it starts — the
two things the simple scheduler never fills in (#380, 4.7.15). What each one answered goes to the log
once per car, so the next diagnostic bundle says which address a T03 keeps its plan at.
"""
import json
import threading

import pytest

import mate_api  # noqa: F401 — puts poller/mate_api_runtime on sys.path, as the poller process does
import api_v2_bridge as bridge

VIN = "LFZT03TEST0000380"
CONFIG = "/carownerservice/v3/api/vehicleinfo/commonConfig"
APPOINTMENT = "/carownerservice/oversea/vehicle/v1/app/remote/ctl/getAppointment"
# The plan the earlier library sent for his T03 on 27/09, from his bundle, as getAppointment's answer is
# shaped on our B10: one JSON object, as a string, in `data`.
T03_PLAN = {"chargeEnable": 1, "chargesoc": 100, "circulation": 0, "cycles": "1,1,1,1,1,1,1",
            "endtime": "08:00", "recharge": 0, "starttime": "23:30"}
# Our B10's configuration as the cloud spells it, and what Mate reads out of it.
B10_CONFIG = {"isEnable": "0", "percent": "90", "beginTime": "01:50", "endTime": "12:00",
              "cycles": "1,1,1,1,1,1,1", "circulation": "1", "recharge": "0"}
B10_PLAN = {"chargeEnable": 0, "chargesoc": 90, "starttime": "01:50", "endtime": "12:00",
            "cycles": "1,1,1,1,1,1,1", "circulation": 1, "recharge": 0}


def _api(config3, appointment):
    """The client on one car whose configuration says `config3` under key 3 (None: no key 3 at all)
    and whose getAppointment 190 answers `appointment` (an exception: refuses). Every address asked is
    noted."""
    api = object.__new__(bridge.NewAPIClient)
    api._mutex = threading.RLock()
    api._ensure_token = lambda: None
    api.route = lambda vin: {"appRegion": "region", "appCenter": "center"}
    asked = []

    def read(path, body, **kwargs):
        asked.append(path)
        if path == CONFIG:
            return {"data": {"vin": VIN, "config": {} if config3 is None else {"3": config3}}}
        if path == APPOINTMENT:
            assert body == {"vin": VIN, "cmdId": "190"} and kwargs.get("form") is True
            if isinstance(appointment, Exception):
                raise appointment
            return {"code": 0, "result": 0, "message": "Request successful", "data": appointment}
        raise AssertionError(f"unexpected read {path}")

    api.read = read
    api.asked = asked
    return api


@pytest.mark.parametrize("config3", [None, {}, {"percent": "90"}, {"isEnable": "1"}, {"beginTime": "23:30"}])
def test_a_configuration_that_does_not_say_whether_or_when_reads_the_appointment(config3):
    api = _api(config3, json.dumps(T03_PLAN))
    assert api.get_charge_schedule(VIN) == T03_PLAN
    assert api.asked == [CONFIG, APPOINTMENT]


def test_a_configuration_that_carries_its_plan_is_read_as_it_always_was():
    """Our B10: the configuration says off, getAppointment would say on; the configuration wins, and
    getAppointment is never asked."""
    api = _api(B10_CONFIG, json.dumps(dict(B10_PLAN, chargeEnable=1)))
    assert api.get_charge_schedule(VIN) == B10_PLAN
    assert api.asked == [CONFIG]


@pytest.mark.parametrize("appointment", [None, "", "{}", json.dumps({"chargesoc": 90}), json.dumps({"chargeEnable": 1}),
                                         bridge.LeapmotorApiError("No such permission")])
def test_an_appointment_with_nothing_to_show_leaves_the_answer_as_it_was(appointment):
    api = _api({}, appointment)
    assert api.get_charge_schedule(VIN) == {k: None for k in B10_PLAN}


def test_what_each_address_answered_is_logged_once_per_car(caplog):
    api = _api({"percent": "90"}, json.dumps(T03_PLAN))
    with caplog.at_level("WARNING", logger="mate.cloud"):
        api.get_charge_schedule(VIN)
        api.get_charge_schedule(VIN)
    lines = [r.getMessage() for r in caplog.records if "getAppointment" in r.getMessage()]
    assert len(lines) == 1
    assert "percent" in lines[0] and "starttime=23:30" in lines[0] and "chargeEnable=1" in lines[0]


def test_a_refusal_is_logged_with_what_the_cloud_said(caplog):
    api = _api({}, bridge.LeapmotorApiError("No such permission"))
    with caplog.at_level("WARNING", logger="mate.cloud"):
        api.get_charge_schedule(VIN)
    assert any("No such permission" in r.getMessage() for r in caplog.records)
