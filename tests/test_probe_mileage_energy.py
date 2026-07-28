"""The research probe captures the whole mileage/energy/detail reply (beta #10 / #11).

Why this probe exists: on a range-extender the "Avg consumption" on the cumulative card is the
electricity divided by a distance that was *partly driven on petrol*. @michapr's own car reports
12.6 kWh/100 km over its electric kilometres where Mate reports 8.9 over all of them, and the split
that would reconcile the two (his dashboard says 58 % electric / 42 % fuel) is in none of the
endpoints we had ever captured. mileage/energy/detail is the only remaining candidate — and the live
call reads `totalEnergy` and `totalmileage` and drops the rest of the reply where it stands, so
nobody has ever seen what else is in there. These tests pin the two things that would silently make
the probe useless: the key going missing from the bundle, and the request losing the signed
begin/end window (without it the cloud answers with mileage only).
"""
import json

import pytest

import command_client


class _FakeApi:
    """Just enough of the leapmotor_api client for the probe helpers."""

    def __init__(self, body):
        self.sign_key, self.device_id, self.language = "K", "DEV", "en"
        self.account_cert = "CERT"
        self.posted = []
        self._body = body

    def _auth_headers(self):
        return {"auth": "t"}

    def _post(self, path, headers, data, cert):
        self.posted.append({"path": path, "headers": headers, "data": data, "cert": cert})
        return {"body": self._body}


class _FakeVehicle:
    vin = "LFZ TEST 0001"          # a space, so the URL-quoting is exercised


@pytest.fixture
def session(monkeypatch):
    s = command_client.LeapmotorSession()
    s._api = _FakeApi(json.dumps({"code": 0, "data": {"totalEnergy": 92, "totalmileage": 1032,
                                                      "someUnknownField": 58}}))
    s._vehicle = _FakeVehicle()
    monkeypatch.setattr(s, "_connect", lambda: None)
    # The signature helper is the library's; the probe only has to feed it the right arguments.
    monkeypatch.setattr("leapmotor_api.crypto.build_signed_headers",
                        lambda **kw: type("H", (), {"to_dict": lambda self: {"sig": kw}})())
    return s


def test_the_probe_carries_the_whole_reply(session):
    """Not the two fields the live call keeps — the point is the fields it throws away."""
    out = session._raw_mileage_energy(1_000_000, 2_000_000)
    assert out["data"]["someUnknownField"] == 58
    assert out["data"]["totalEnergy"] == 92


def test_it_asks_the_right_endpoint_with_a_signed_window(session):
    """The cloud returns mileage ONLY unless begin/end are in the signature as well as the body.
    Losing that would give a reply that looks plausible and is missing exactly what we're after."""
    session._raw_mileage_energy(1_000_000, 2_000_000)
    (call,) = session._api.posted
    assert call["path"] == "/carownerservice/oversea/drivingRecord/v1/mileage/energy/detail"
    assert "begintime=1000000" in call["data"] and "endtime=2000000" in call["data"]
    assert "LFZ%20TEST%200001" in call["data"]          # VIN quoted, not raw
    signed = call["headers"]["sig"]["body_params"]
    assert signed == {"begintime": "1000000", "endtime": "2000000"}


def test_a_dict_body_is_passed_through(session):
    """Some responses arrive already decoded rather than as a JSON string."""
    session._api._body = {"code": 0, "data": {"x": 1}}
    assert session._raw_mileage_energy(1, 2) == {"code": 0, "data": {"x": 1}}


def test_the_bundle_gets_the_new_key_alongside_the_old_ones(session, monkeypatch):
    """A probe that quietly stops being collected is worse than no probe: the bundle still looks
    complete. Pin the whole key set, so removing one is a failing test rather than a silent gap."""
    monkeypatch.setattr(session, "_raw_getec", lambda a, b: {"ec": [a, b]})
    monkeypatch.setattr(session, "_raw_weekly_rank", lambda: {"rank": 1})
    monkeypatch.setattr(session, "_raw_getplugin", lambda: {"plugin": 1})
    probe = session.get_consumption_probe_raw()
    assert set(probe) == {"captured_at_ms", "getEC_last24h", "getEC_last7d",
                          "weekly_rank_6w", "getplugin_100km_6w", "mileage_energy_detail"}
    assert probe["mileage_energy_detail"]["data"]["someUnknownField"] == 58


def test_the_window_is_seven_days_and_in_milliseconds(session, monkeypatch):
    """This endpoint takes milliseconds where its getEC siblings take seconds — mixing the two up
    would ask for a window in 1970 and come back empty."""
    monkeypatch.setattr(session, "_raw_getec", lambda a, b: {})
    monkeypatch.setattr(session, "_raw_weekly_rank", lambda: {})
    monkeypatch.setattr(session, "_raw_getplugin", lambda: {})
    session.get_consumption_probe_raw()
    (call,) = session._api.posted
    begin = int(call["headers"]["sig"]["body_params"]["begintime"])
    end = int(call["headers"]["sig"]["body_params"]["endtime"])
    assert end - begin == 7 * 86400 * 1000
    assert end > 1_700_000_000_000                      # ms, not seconds
