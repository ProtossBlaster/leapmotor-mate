"""The cloud client can ask which software a car runs.

The vehicle-update endpoint (`hotspot/fetch`) answers with the installed version, when it was
installed, and — while a campaign is open — the version waiting, its release notes and the
packages. Checked on a B10 owner account on 25 and 28.09.2026: an account the car is only shared
with is refused with code 40. It is a form read like `getAppointment`, on the same origin, so the
adapter's allowlist has to know it, and the adapter hands the `data` object back as is.
"""
import types

import mate_api  # noqa: F401 — puts poller/mate_api_runtime on sys.path, as the poller process does
import pytest
from api_v2_bridge import READ_PATHS, LeapmotorApiError, NewAPIClient

PATH = "/carownerservice/oversea/hotspot/fetch"


def _adapter(data):
    """Only what the method touches: `read`, recorded."""
    calls = []

    def read(path, body, **kwargs):
        calls.append((path, body, kwargs))
        return {"code": 0, "result": 0, "data": data}
    return types.SimpleNamespace(read=read, calls=calls)


def test_it_is_a_form_read_of_the_update_endpoint_for_one_car():
    data = {"currentVersion": {"generalVersion": "3.41.30", "time": 1790224725000, "logJsonMultiLang": []}}
    fake = _adapter(data)
    assert NewAPIClient.get_software_version(fake, "LFZB10TEST0000001") == data
    assert fake.calls == [(PATH, {"vin": "LFZB10TEST0000001"}, {"form": True})]


def test_the_adapter_allows_the_read():
    assert PATH in READ_PATHS


def test_an_answer_that_is_not_an_object_is_an_error():
    with pytest.raises(LeapmotorApiError):
        NewAPIClient.get_software_version(_adapter("3.41.30"), "LFZB10TEST0000001")
