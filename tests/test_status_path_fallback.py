"""A model the library doesn't map still reads its status (#177, @arnolds77).

The status endpoint is the only call in the whole flow with the model in its address —
`…/vehicle/v1/status/get/{car_type}`. leapmotor-api maps B10 and B11 onto `c10` and lets every other
model fall through to its own name, so a model nobody has added asks the backend for an address it
does not serve. @arnolds77's **B05** is exactly that: login fine, vehicle list fine, VIN, model and
abilities all known, the official Leapmotor app on the same account showing live data — and
`No message available` from the very first poll, for ever. Mate already treats a B05 as a B10
everywhere else (pack size, window scale, command path); this was the one place it didn't.

What these pin is not just "it retries" but the three things that keep the retry from being worse
than the bug: it must not fire when the normal call works, it must not repeat for ever (the extra
call would double the load on the session v2.13.2 stopped us exhausting), and when the fallback
fails too it must report the ORIGINAL failure rather than its own.
"""
import dataclasses

import pytest

import client as C


@dataclasses.dataclass
class _Vehicle:
    vin: str = "VINX"
    car_type: str = "B05"


class _Api:
    """Answers only for the car types in `serves`; anything else fails like the real backend."""

    def __init__(self, serves):
        self.serves = serves
        self.asked = []

    def get_vehicle_raw_status(self, vehicle):
        self.asked.append(vehicle.car_type)
        if vehicle.car_type.lower() not in self.serves:
            raise RuntimeError("Leapmotor vehicle status failed: No message available")
        return {"data": {"signal": {"1204": 55}}}


def _client(car_type, serves):
    c = C.LeapmotorMateClient.__new__(C.LeapmotorMateClient)     # no login, no cloud
    c._api = _Api(serves)
    c._vehicle = _Vehicle(car_type=car_type)
    c._status_car_type = None
    c._status_fallback_tried = False
    return c


def test_an_unmapped_model_falls_back_and_gets_its_data():
    c = _client("B05", serves={"c10"})
    raw = c._raw_status()
    assert raw["data"]["signal"]["1204"] == 55
    assert c._api.asked == ["B05", "c10"]                # its own address first, then the family's


def test_a_model_that_works_is_never_retried():
    """The fallback must be invisible to everyone whose car already reads fine."""
    c = _client("T03", serves={"t03"})
    c._raw_status()
    assert c._api.asked == ["T03"]
    assert c._status_car_type is None


def test_the_working_path_is_remembered_so_the_extra_call_happens_once():
    """A retry on every poll would double the cloud calls — the exact failure mode of #177's own
    install before v2.13.2. One extra call, once."""
    c = _client("B05", serves={"c10"})
    for _ in range(5):
        c._raw_status()
    assert c._api.asked == ["B05"] + ["c10"] * 5         # one probe, then straight to the right path


def test_when_the_fallback_fails_too_the_original_error_is_what_surfaces():
    """Otherwise triage would chase our retry instead of the real refusal."""
    c = _client("B05", serves=set())
    with pytest.raises(RuntimeError, match="No message available"):
        c._raw_status()
    assert c._api.asked == ["B05", "c10"]


def test_a_second_failure_does_not_probe_again():
    """A car that is simply unreachable must not pay two calls per poll for ever."""
    c = _client("B05", serves=set())
    for _ in range(4):
        with pytest.raises(RuntimeError):
            c._raw_status()
    assert c._api.asked == ["B05", "c10", "B05", "B05", "B05"]


def test_a_c10_that_fails_is_not_retried_on_itself():
    """The fallback IS its own path — retrying would be a pointless duplicate call."""
    c = _client("C10", serves=set())
    with pytest.raises(RuntimeError):
        c._raw_status()
    assert c._api.asked == ["C10"]


def test_the_real_vehicle_keeps_its_own_model():
    """car_type is read elsewhere for the pack size, the window scale and the command paths, and
    those are already right for a B05 — only the status ADDRESS may differ."""
    c = _client("B05", serves={"c10"})
    c._raw_status()
    assert c._vehicle.car_type == "B05"
