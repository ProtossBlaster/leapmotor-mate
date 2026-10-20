"""The bundle shows what the cloud answered Mate's last requests, from the record the bridge keeps of
each answer: its time, endpoint, HTTP status and the cloud's code. In discussion #431 the cloud ended
Mate's session every few minutes, and the bundle could only be read by its log times: not which code
the cloud refused a renewal with, nor how long after a login a session ended.

Per endpoint, the answers counted by code. Then, oldest first, every login and renewal — where a
session begins — and every answer the bridge refused, by the bridge's own rule, the newest of them
only past a cap that says what it left out.
"""
from types import SimpleNamespace

import pytest

import db as D
import db_reader
import diagnostics
import mate_api  # noqa: F401 — puts poller/mate_api_runtime on sys.path, as the poller process does
import api_v2_bridge as bridge

VIN = "LFZB10ANSWERS0001"
SIGNAL = "/app/app-signal-service/signal/info/query"
CONFIG = "/carownerservice/v3/api/vehicleinfo/commonConfig"
PACKAGE = "/carownerservice/oversea/vehicle/v1/carpicture/package"
HEAD = "----- cloud answers (Mate's last requests, UTC) -----"


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    """The section as the bundle prints it, read after the bridge wrote its requests."""
    path = str(tmp_path / "answers.db")
    poller = D.Database(path)
    poller._conn.execute("INSERT INTO vehicles (id, vin, car_type) VALUES (1, ?, 'B10')", (VIN,))
    poller._conn.commit()
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    monkeypatch.setattr(bridge, "DB", path)
    db_reader.set_setting("setup_complete", "1")

    def section():
        text = diagnostics.build_bundle("test", parts=("info",))
        return text[text.index(HEAD):].split("\n\n", 1)[0].splitlines()[1:]
    return section


def _asked(path, method, status, code, times=1):
    for _ in range(times):
        bridge.NewAPIClient._audit(None, path, method, status, code)


def _read(body):
    """One GET read through the bridge's real request, answered `body`. True when the bridge took it."""
    api = object.__new__(bridge.NewAPIClient)
    api.language, api.device_id, api.token, api.user_id = "en-US", "d" * 32, "synthetic-token", "1"
    api._new_key = b"k" * 32
    api.account_cert_file, api.account_key_file = "/nonexistent/cert.pem", "/nonexistent/key.pem"
    api._transport = SimpleNamespace(send=lambda request, client_cert=None: SimpleNamespace(status=200, body=body))
    try:
        api._wire(bridge.CENTER_ORIGIN, CONFIG, {"vin": VIN}, method="GET")
    except bridge.LeapmotorApiError:
        return False
    return True


def _listed(lines):
    return lines[lines.index(next(line for line in lines if line.startswith("logins, renewals"))) + 1:]


def test_each_endpoint_counts_its_answers_by_code(bundle):
    _asked(SIGNAL, "POST", 200, [0, 0], times=5)
    _asked(SIGNAL, "POST", 200, [302002004], times=2)
    _asked(CONFIG, "GET", 200, [0])
    lines = bundle()
    assert lines[0].startswith("8 requests · ")
    signal = next(line for line in lines if SIGNAL in line and "×" in line)
    assert signal.split()[:3] == ["7", "POST", SIGNAL]
    assert signal.endswith("200 [0, 0] ×5 · 200 [302002004] ×2")
    assert next(line for line in lines if CONFIG in line).endswith("200 [0] ×1")


def test_the_list_holds_the_logins_renewals_and_every_answer_the_bridge_refused(bundle):
    _asked(SIGNAL, "POST", 200, [0, 0], times=3)
    _asked(bridge.LOGIN_PATH, "POST", 200, "0")
    _asked(SIGNAL, "POST", 200, [302002004])
    _asked(bridge.REFRESH_PATH, "POST", 200, "302002003")
    _asked(bridge.LOGIN_PATH, "POST", 0, "login_transport")
    _asked(PACKAGE, "POST", 200, "binary_ok")
    _asked(PACKAGE, "POST", 200, "not_a_zip")
    _asked(CONFIG, "GET", 502, "invalid_json")
    _asked(bridge.REFRESH_PATH, "POST", 200, "0")
    assert [line.split()[1:] for line in _listed(bundle())] == [
        ["POST", bridge.LOGIN_PATH, "200", "0"],
        ["POST", SIGNAL, "200", "[302002004]"],
        ["POST", bridge.REFRESH_PATH, "200", "302002003"],
        ["POST", bridge.LOGIN_PATH, "0", "login_transport"],
        ["POST", PACKAGE, "200", "not_a_zip"],
        ["GET", CONFIG, "502", "invalid_json"],
        ["POST", bridge.REFRESH_PATH, "200", "0"],
    ]


@pytest.mark.parametrize("body", [b'{"code":" 0 "}', b'{"code":[0]}', b'{"code":0,"result":false}',
                                  b'{"code":"0","result":0}'],
                         ids=["padded-zero", "list-in-code", "false-result", "text-zero"])
def test_an_answer_is_listed_exactly_when_the_bridge_refused_it(bundle, body):
    refused = not _read(body)
    assert any(line.split()[1:3] == ["GET", CONFIG] for line in _listed(bundle())) == refused


def test_the_list_keeps_its_newest_lines_and_says_how_many_it_left_out(bundle):
    for i in range(diagnostics._CLOUD_LINES + 5):
        _asked(SIGNAL, "POST", 200, [302002004 if i else 1])
    _asked(SIGNAL, "POST", 200, [9])
    lines = bundle()
    start = lines.index(next(line for line in lines if line.startswith("logins, renewals")))
    assert lines[start + 1] == "  … 6 earlier not shown"
    listed = lines[start + 2:]
    assert len(listed) == diagnostics._CLOUD_LINES
    assert listed[-1].endswith("200 [9]") and not any(line.endswith("200 [1]") for line in listed)


def test_an_install_that_never_asked_the_cloud_says_so(bundle):
    assert bundle() == ["(none recorded)"]


def test_a_read_is_recorded_by_its_endpoint_never_its_car_or_token(bundle):
    """A GET carries the VIN in its query, and the bundle is attached to public issues."""
    assert not _read(b'{"code":40,"result":40}')
    lines = bundle()
    assert any(line.split()[1:] == ["GET", CONFIG, "200", "[40,", "40]"] for line in lines)
    assert not any(VIN in line or "synthetic-token" in line for line in lines)
