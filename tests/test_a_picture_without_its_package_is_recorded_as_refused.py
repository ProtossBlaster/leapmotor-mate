"""A picture request the cloud answers with an envelope instead of its package is recorded as refused.

The bridge asks for the car's picture package as a ZIP and refuses any other answer ("Picture endpoint
did not return a ZIP"). An envelope with code 0 went into the request log as `[0]`, the record of an
answer accepted, so the log said yes where the bridge said no. It now reads `not_a_zip`; an envelope the
cloud refused keeps its codes.
"""
import sqlite3
from types import SimpleNamespace

import pytest

import mate_api  # noqa: F401 — puts poller/mate_api_runtime on sys.path, as the poller process does
import api_v2_bridge as bridge

PACKAGE = "/carownerservice/oversea/vehicle/v1/carpicture/package"


@pytest.fixture
def ask(tmp_path, monkeypatch):
    """The picture request through the real _wire and _audit, answered `body`; the log's rows after it."""
    path = str(tmp_path / "picture.db")
    monkeypatch.setattr(bridge, "DB", path)

    def asked(body, refusal):
        answer = SimpleNamespace(send=lambda request, client_cert=None: SimpleNamespace(status=200, body=body))
        monkeypatch.setattr(bridge, "UrllibTransport", lambda *a, **k: answer)   # the picture's own transport
        api = object.__new__(bridge.NewAPIClient)
        api.language, api.device_id, api.token, api.user_id = "en-US", "d" * 32, "synthetic-token", "1"
        api._new_key = b"k" * 32
        api.account_cert_file, api.account_key_file = "/nonexistent/cert.pem", "/nonexistent/key.pem"
        api._transport = answer
        with pytest.raises(bridge.LeapmotorApiError, match=refusal):
            api._wire(bridge.CENTER_ORIGIN, PACKAGE, {"key": "k"}, form=True, binary=True)
        with sqlite3.connect(path) as db:
            return db.execute("SELECT http_status, api_code FROM api_v2_http_log").fetchall()
    return asked


def test_an_envelope_in_place_of_the_package_is_recorded_as_refused(ask):
    assert ask(b'{"code":0}', "did not return a ZIP") == [(200, "not_a_zip")]


def test_an_envelope_the_cloud_refused_keeps_its_codes(ask):
    assert ask(b'{"code":40,"result":40}', "rejected") == [(200, "[40, 40]")]
