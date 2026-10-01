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


# ── 4.7.12: a T03 answers its signal map in names (#368) ──────────────────────
# @DJ-Elo-Ostfriesland's T03 on 4.7.11, 01/10/2026 20:57 (his bundle): asked as a T03, the signal service
# answered with the car's real readings — 56 %, 2,314 km, 160 km of range — in NAMED fields, the shape the
# old address answered in, with eight numbered keys among them. 4.7.11 handed that map on as `signal`, read
# as numbers by the poller and the web: nothing matched, the poller called the car asleep and the web
# stored 0 % and 0 km. Coordinates and the Bluetooth address below are made up.
T03_MAP = {
    "100010": "0.0", "100011": "0.0", "100012": "0.0", "100013": "0.0", "100014": "0.0",
    "100015": "0.0", "100016": "0.0", "2188": 0,
    "acAirVolume": 3, "acAirVolumeSetting": 0, "acCircleMode": False, "acCoolingAndHeating": 0,
    "acSetting": 23, "acSwitch": False, "acTempMode": False, "acWindDirection": 4,
    "batteryCurrent": 0.0, "batteryVoltage": 368.2, "bbcmBackDoorStatus": False,
    "bcmDoorCtrlAllow": False, "bcmKeyPositionOn1": False, "bcmKeyPositionOn3": False,
    "bluetoothAddr": "02:00:00:00:00:00", "bluetoothState": True, "chargeRemainTime": 0,
    "chargeState": 0, "chargeTimeSetting": "00:00", "chargesocSetting": 80,
    "collectTime": "2026-10-01 16:57:04", "collectTimeMs": 1790873824581, "createTime": "2026-10-01 16:57:05",
    "dcInputFastCharge": 0, "driverDoorLockStatus": True, "driverWindowStatus": False, "dumpEnergy": 21000,
    "expectedMileage": 160, "expectedMileageMile": "99.4", "gearStatus": 0, "hotspotState": False,
    "isSupportWindowsRemoteControl": 2, "latitude": 45.123456, "lbcmDriverDoorStatus": False,
    "lbcmLeftRearDoorStatus": False, "leftFrontTirePressure": 253, "leftFrontTirePressureState": 0,
    "leftFrontWindowPercent": 0, "leftRearTirePressure": 253, "leftRearTirePressureState": 0,
    "leftRearWindowPercent": 0, "leftRearWindowStatus": False, "longitude": 9.123456, "minSingleTemp": 19,
    "outdoorTemp": 20, "privacyData": 1, "privacyGPS": 1, "ptcPowerSettingValue": 0, "ptcState": 2,
    "rbcmDriverDoorStatus": False, "rbcmRightRearDoorStatus": False, "rightFrontTirePressure": 253,
    "rightFrontTirePressureState": 0, "rightFrontWindowPercent": 0, "rightFrontWindowStatus": False,
    "rightRearTirePressure": 255, "rightRearTirePressureState": 0, "rightRearWindowPercent": 0,
    "rightRearWindowStatus": False, "soc": 56, "speed": 0, "sunShade": 10, "totalMileage": 2314,
}


def _t03_answering_in_names(path, cartype):
    if path == SIGNAL and cartype == "T03":
        return {"vin": VIN, "signalMap": dict(T03_MAP)}
    _no_data()


def test_a_t03_map_in_names_reaches_the_poller_as_its_readings(tmp_path, monkeypatch):
    api, asked = _api(tmp_path, monkeypatch, "T03", _t03_answering_in_names)
    poller = object.__new__(poller_client.LeapmotorMateClient)
    poller._api, poller._named_mode_logged = api, False
    poller._vehicle = SimpleNamespace(vin=VIN, car_type="T03")
    poller._status_car_type, poller._status_fallback_tried = {}, set()
    reading = poller.get_status()
    assert (reading.soc, reading.odometer_km, reading.gear) == (56, 2314, "P")


def test_the_web_reads_the_same_t03_map_by_name(tmp_path, monkeypatch):
    import command_client
    api, asked = _api(tmp_path, monkeypatch, "T03", _t03_answering_in_names)
    data = _read(api, asked)
    signals = data.get("signal") or command_client._named_fields_to_signal(data)
    assert (signals["1204"], signals["1318"], signals["3260"]) == (56, 2314, 160)


def test_a_numbered_map_with_a_few_names_is_still_read_as_numbers(tmp_path, monkeypatch):
    """A B10's map is numbered, with three named keys among its 98 (measured 01/10/2026)."""
    numbered = dict({str(1000 + i): i for i in range(93)}, **SIGNALS, privacyData=1, privacyGPS=1, sts=0)
    assert len(numbered) == 98
    api, asked = _api(tmp_path, monkeypatch, "B10", lambda path, cartype: {"vin": VIN, "signalMap": numbered})
    assert _read(api, asked)["signal"] == numbered


def test_the_bundle_leaves_out_coordinates_that_come_by_name():
    """The bundle is posted in public issues: a named latitude/longitude is as locating as 3725/3724."""
    import diagnostics
    section = diagnostics._signals_section(dict(T03_MAP), None)
    assert "latitude" not in section and "longitude" not in section
    assert "45.123456" not in section and "9.123456" not in section
    assert '"soc": 56' in section, "the rest of the map is still there"


def test_the_rows_written_from_a_misread_map_are_dropped_once(tmp_path):
    """The web stored a position from each misread map: SoC 0 and no odometer, which no real reading
    has. Left in place, the newest seeds the poller's SoC baseline, and the first true reading of a
    parked car (56 %) is taken for a charge from 0 %."""
    import db as D
    database = D.Database(str(tmp_path / "zero.db"))
    vid = database.ensure_vehicle(VIN, "T03")
    rows = [("2026-09-30T08:00:00+00:00", 0.0, None),     # before 4.7.11: not ours to judge
            ("2026-10-01T19:01:00+00:00", 56.0, 2314.0),   # a real reading
            ("2026-10-01T19:02:00+00:00", 0.0, 5000.0),    # an empty battery, really read
            ("2026-10-01T19:03:00+00:00", 0.0, None)]      # misread: SoC 0, no odometer
    for at, soc, odo in rows:
        database._conn.execute("INSERT INTO positions (vehicle_id, recorded_at, soc, odometer_km) VALUES (?,?,?,?)",
                               (vid, at, soc, odo))
    database._conn.commit()
    assert database.get_last_soc(vid)[0] == 0.0
    database._conn.execute("DELETE FROM settings WHERE key = 'positions_misread_map_repair_v1'")
    database._repair_rows_written_from_a_misread_map()
    left = [r[0] for r in database._conn.execute("SELECT recorded_at FROM positions ORDER BY id")]
    assert left == [rows[0][0], rows[1][0], rows[2][0]]
    assert database.get_last_soc(vid)[0] == 0.0, "the empty battery that was really read is the last reading"
    database._conn.execute("INSERT INTO positions (vehicle_id, recorded_at, soc, odometer_km) VALUES (?,?,?,?)",
                           (vid, "2026-10-01T19:04:00+00:00", 0.0, None))
    database._repair_rows_written_from_a_misread_map()
    assert database._conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 4, "it runs once"
