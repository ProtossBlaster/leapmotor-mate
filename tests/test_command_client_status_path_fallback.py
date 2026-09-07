"""The web client must use the status path that actually serves a B05 (#278).

The poller already discovers that these cars answer through the ``c10`` status
path.  Vehicle-page and charge-plan reads use a separate session, so exercise
their public methods here with a deterministic stand-in for the cloud.
"""
import dataclasses

import command_client
import pytest


@dataclasses.dataclass
class _Vehicle:
    vin: str = "VIN-B05"
    car_type: str = "B05"


class _Api:
    def __init__(self, serves):
        self.serves = serves
        self.asked = []

    def get_vehicle_raw_status(self, vehicle):
        self.asked.append(vehicle.car_type)
        if vehicle.car_type.lower() not in self.serves:
            raise RuntimeError("Leapmotor vehicle status failed: No message available")
        return {
            "data": {
                "signal": {"1204": 55, "2667": 241},
                "config": {
                    "3": {
                        "percent": 80,
                        "isEnable": True,
                        "beginTime": "23:00",
                        "endTime": "07:00",
                    }
                },
            }
        }


def _session(car_type="B05", serves=frozenset({"c10"})):
    session = command_client.LeapmotorSession()
    session._api = _Api(serves)
    session._vehicle = _Vehicle(car_type=car_type)
    session._vehicles = [session._vehicle]
    session._connect = lambda: None
    session._reset = lambda: None
    return session


def test_b05_vehicle_page_falls_back_once_then_remembers_the_working_path():
    session = _session()

    assert session.get_fresh_signals() == {"1204": 55, "2667": 241}
    assert session.get_fresh_signals() == {"1204": 55, "2667": 241}
    assert session._api.asked == ["B05", "c10", "c10"]
    assert session._vehicle.car_type == "B05"


def test_b05_charge_plan_uses_the_same_status_fallback():
    session = _session()

    assert session.get_charge_plan() == {
        "charge_limit_percent": 80,
        "charge_enabled": True,
        "start_time": "23:00",
        "end_time": "07:00",
    }
    assert session._api.asked == ["B05", "c10"]


@pytest.mark.parametrize("car_type", ["B10", "C10", "T03"])
def test_a_model_whose_status_path_works_is_not_redirected(car_type):
    session = _session(car_type=car_type, serves={car_type.lower()})

    assert session.get_fresh_signals() == {"1204": 55, "2667": 241}
    assert session._api.asked == [car_type]


def test_a_failed_fallback_is_not_repeated_on_every_outer_retry():
    session = _session(serves=set())

    assert session.get_fresh_signals() is None
    assert session._api.asked == ["B05", "c10", "B05"]


def test_a_failing_c10_is_not_retried_through_its_own_path():
    session = _session(car_type="C10", serves=set())

    assert session.get_fresh_signals() is None
    assert session._api.asked == ["C10", "C10"]


def test_the_remembered_path_is_scoped_to_the_vehicle_vin():
    session = _session(serves={"c10", "t03"})
    b05 = session._vehicle
    t03 = _Vehicle(vin="VIN-T03", car_type="T03")
    selected = [b05]
    session._target = lambda: selected[0]

    assert session.get_fresh_signals() == {"1204": 55, "2667": 241}
    selected[0] = t03
    assert session.get_fresh_signals() == {"1204": 55, "2667": 241}
    selected[0] = b05
    assert session.get_charge_plan()["charge_limit_percent"] == 80

    assert session._api.asked == ["B05", "c10", "T03", "c10"]
