"""A T03 is asked for its status as a T03, and read at the old address if the signal service has nothing.

01/10/2026, #368 (@gody01, @DJ-Elo-Ostfriesland) and #338 (@dommi1966): three T03s stopped reading at
the update that put every installation on Mate's own cloud client (4.7.7). Until then the old library
read them at `/carownerservice/oversea/vehicle/v1/status/get/t03`, sending no `cartype`; Mate's client
asks `/app/app-signal-service/signal/info/query` for every car and says `cartype: B10` in every request.
@dommi1966's account holds a B03X and a T03: from the same session, in the same minute, the B03X read and
the T03 got code 100, "No data found", at every poll.

Measured the same day on a B10, read only: the signal service answers 100 "No data found" when `cartype`
names a T03, and reads its 96 signals with B10, C10 or nothing; the old address answers Mate's client too
(94 signals, every one equal to the new call's), and with another car's model in the path it answers 100
as well. In 27 hours the B10's 3,526 signal reads never got a 100.

So a car the B10 header has never read is asked as what the cloud lists it as, then at the old address;
the way that answers is kept for that car. A car that has read once keeps its way, and a refusal that is
not "No data found" is not answered with another one.
"""
import sqlite3
import threading
from types import SimpleNamespace

import pytest

import mate_api  # noqa: F401 — puts poller/mate_api_runtime on sys.path, as the poller process does
import api_v2_bridge as bridge
import client as poller_client

VIN = "LFZT03TEST0000001"
SIGNAL = "/app/app-signal-service/signal/info/query"
CONFIG = "/carownerservice/v3/api/vehicleinfo/commonConfig"
OLD = "/carownerservice/oversea/vehicle/v1/status/get/"
SIGNALS = {"1204": 62, "1318": 2296}
NAMED = {"vin": VIN, "soc": 62.0, "totalMileage": 2296}


def _no_data():
    raise bridge._rejection(200, [100, 100])


def _api(tmp_path, monkeypatch, car_type, answer):
    """The client on a synthetic account holding one car the cloud lists as `car_type`. `answer(path,
    cartype)` is what the cloud says to a status call; every call is noted as (path, cartype)."""
    db = tmp_path / "bridge.db"
    monkeypatch.setattr(bridge, "DB", str(db))
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT)")
    api = object.__new__(bridge.NewAPIClient)
    api._mutex = threading.RLock()
    api._access_refresh_attempt = None
    api.username = "synthetic@example.invalid"
    api._ensure_token = lambda: None
    api.route = lambda vin: {"appRegion": "region", "appCenter": "center"}
    asked = []

    def read(path, body, **kwargs):
        if path.endswith("/vehicle/list"):
            return {"data": {"sharedcars": [{"vin": VIN, "carType": car_type}]}}
        cartype = kwargs.get("cartype", "B10")
        asked.append((path, cartype))
        if path == CONFIG:
            return {"data": {"vin": VIN, "config": {}}}
        return {"data": answer(path, cartype)}

    api.read = read
    return api, asked


def _read(api, asked):
    asked.clear()
    return api._get_vehicle_raw_status(SimpleNamespace(vin=VIN, car_type="T03"))["data"]


@pytest.mark.parametrize("car_type", ["B10", "C10", "A10"])
def test_a_car_the_b10_header_reads_is_asked_as_today(tmp_path, monkeypatch, car_type):
    """B10, C10 and the B03X (`A10`) read with the B10 header today: nothing changes for them."""
    api, asked = _api(tmp_path, monkeypatch, car_type, lambda path, cartype: {"vin": VIN, "signalMap": SIGNALS})
    assert _read(api, asked)["signal"] == SIGNALS
    assert asked == [(SIGNAL, "B10"), (CONFIG, "B10")]


def test_a_t03_the_signal_service_does_not_find_as_a_b10_is_asked_as_a_t03(tmp_path, monkeypatch):
    def answer(path, cartype):
        if path == SIGNAL and cartype == "T03":
            return {"vin": VIN, "signalMap": SIGNALS}
        _no_data()

    api, asked = _api(tmp_path, monkeypatch, "T03", answer)
    assert _read(api, asked)["signal"] == SIGNALS
    assert asked == [(SIGNAL, "B10"), (SIGNAL, "T03"), (CONFIG, "T03")]
    _read(api, asked)
    assert asked == [(SIGNAL, "T03"), (CONFIG, "T03")], "the way that answered is kept for the car"


def test_a_t03_the_signal_service_has_nothing_for_is_read_at_the_old_address(tmp_path, monkeypatch):
    """The address the old library read every T03 at, answered in named fields — which the poller maps."""
    def answer(path, cartype):
        if path == OLD + "t03":
            return dict(NAMED)
        _no_data()

    api, asked = _api(tmp_path, monkeypatch, "T03", answer)
    data = _read(api, asked)
    assert asked == [(SIGNAL, "B10"), (SIGNAL, "T03"), (OLD + "t03", "T03"), (CONFIG, "T03")]
    assert "signal" not in data and data["soc"] == 62.0
    assert poller_client._named_fields_to_signal(data)["1204"] == 62.0, "the poller cannot read what came back"
    _read(api, asked)
    assert asked == [(OLD + "t03", "T03"), (CONFIG, "T03")]


@pytest.mark.parametrize("car_type", ["B10", "A10"])
def test_a_car_that_has_read_keeps_its_way_on_an_empty_answer(tmp_path, monkeypatch, car_type):
    """One "No data found" from a car that reads does not send it down another way — not even a B03X,
    which the cloud lists under a model of its own."""
    empty = []

    def answer(path, cartype):
        if empty:
            _no_data()
        return {"vin": VIN, "signalMap": SIGNALS}

    api, asked = _api(tmp_path, monkeypatch, car_type, answer)
    _read(api, asked)
    empty.append(True)
    with pytest.raises(bridge.LeapmotorApiError) as refused:
        _read(api, asked)
    assert refused.value.api_codes == (100, 100)
    assert asked == [(SIGNAL, "B10")]


def test_a_b10_that_finds_nothing_is_not_sent_to_the_old_address(tmp_path, monkeypatch):
    """The B10 header is a B10's own: a "No data found" for it is the cloud's answer, raised as before."""
    api, asked = _api(tmp_path, monkeypatch, "B10", lambda path, cartype: _no_data())
    with pytest.raises(bridge.LeapmotorApiError):
        _read(api, asked)
    assert asked == [(SIGNAL, "B10")]


def test_a_refusal_that_is_not_no_data_is_not_answered_with_another_way(tmp_path, monkeypatch):
    def answer(path, cartype):
        raise bridge._rejection(200, [39, 39])

    api, asked = _api(tmp_path, monkeypatch, "T03", answer)
    with pytest.raises(bridge.LeapmotorApiError) as refused:
        _read(api, asked)
    assert refused.value.api_codes == (39, 39)
    assert asked == [(SIGNAL, "B10")]


def test_a_car_no_way_reads_gets_the_clouds_first_refusal(tmp_path, monkeypatch):
    api, asked = _api(tmp_path, monkeypatch, "T03", lambda path, cartype: _no_data())
    with pytest.raises(bridge.LeapmotorApiError) as refused:
        _read(api, asked)
    assert refused.value.api_codes == (100, 100)
    assert asked == [(SIGNAL, "B10"), (SIGNAL, "T03"), (OLD + "t03", "T03")]


# ── what goes on the wire ─────────────────────────────────────────────────────

class _Transport:
    def __init__(self):
        self.sent = []

    def send(self, request, client_cert=None):
        self.sent.append(request)
        return SimpleNamespace(status=200, body=b'{"code":0,"result":0,"data":{}}')


def _wired():
    api = object.__new__(bridge.NewAPIClient)
    api.language, api.device_id, api.token, api.user_id = "en-US", "d" * 32, "token", "1"
    api._new_key = b"k" * 32
    api.account_cert_file, api.account_key_file = "/nonexistent/cert.pem", "/nonexistent/key.pem"
    api._transport = _Transport()
    api._audit = lambda *args, **kwargs: None
    return api


def test_the_request_says_the_car_type_it_is_given(monkeypatch):
    api = _wired()
    api._wire(bridge.CENTER_ORIGIN, SIGNAL, {"vin": VIN}, cartype="T03")
    api._wire(bridge.CENTER_ORIGIN, SIGNAL, {"vin": VIN})
    assert [request.headers["cartype"] for request in api._transport.sent] == ["T03", "B10"]


def test_only_a_plain_model_name_opens_the_old_status_address():
    api = _wired()
    api._wire(bridge.CENTER_ORIGIN, OLD + "t03", {"vin": VIN}, form=True, cartype="T03")
    for path in (OLD + "t03/../list", OLD, OLD + "T03", OLD + "t03?x=1"):
        with pytest.raises(bridge.LeapmotorApiError):
            api._wire(bridge.CENTER_ORIGIN, path, {"vin": VIN}, form=True)
    assert len(api._transport.sent) == 1


def test_the_poller_records_a_t03_read_at_the_old_address(tmp_path, monkeypatch):
    """From the cloud's answer to the reading the poller records, through the poller's own status read."""
    frame = {"vin": VIN, "soc": 60, "gearStatus": 0, "speed": 0.0, "totalMileage": 2296}   # a T03 answers in names

    def answer(path, cartype):
        if path == OLD + "t03":
            return dict(frame)
        _no_data()

    api, asked = _api(tmp_path, monkeypatch, "T03", answer)
    poller = object.__new__(poller_client.LeapmotorMateClient)
    poller._api, poller._named_mode_logged = api, False
    poller._vehicle = SimpleNamespace(vin=VIN, car_type="T03")
    poller._status_car_type, poller._status_fallback_tried = {}, set()
    reading = poller.get_status()
    assert (reading.soc, reading.odometer_km) == (60, 2296)
    assert not poller._status_fallback_tried, "the poller had to fall back: the bridge did not read the car"
