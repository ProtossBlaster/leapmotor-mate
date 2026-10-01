"""Everything runs on Mate's own cloud client, and nothing in Mate names the old library.

01/10/2026, Silvio's order: from 4.0 the client is ours (MATE-API, V3 commands), yet an
installation whose qualification did not finish was still put back on the bundled third-party SDK,
and its name sat in comments, docs and the changelog. The SDK, the switch that selected it
(`MATE_API_V2`) and the qualification that decided between the two are gone: every installation
runs the new client, and a decision stored by an older version changes nothing.
"""
import importlib
import pathlib
import subprocess

import mate_api  # puts poller/mate_api_runtime on sys.path, as the poller process does

ROOT = pathlib.Path(__file__).resolve().parents[1]
# Spelled in pieces so this file does not match itself.
OLD_LIBRARY = ("leapmotor" + "-api", "leapmotor" + "_api")
OLD_AUTHOR = "marko" + "ceri"
# The packaged application profile still comes from that library: while it ships, its notice and
# licence must travel with it. They go with the profile, not before it.
ATTRIBUTION = {"poller/mate_api_runtime/APPLICATION-PROFILE-NOTICE",
               "poller/mate_api_runtime/APPLICATION-PROFILE-LICENSE"}


def test_the_backend_is_the_new_client_whatever_an_old_decision_said(monkeypatch):
    monkeypatch.setenv("MATE_API_V2", "0")  # what a stored `legacy` decision used to set
    import api_backend
    from api_v2_bridge import NewAPIClient
    assert importlib.reload(api_backend).LeapmotorApiClient is NewAPIClient


def test_no_requirement_installs_the_old_library():
    for name in ("poller/requirements.txt", "web/requirements.txt"):
        text = (ROOT / name).read_text().lower()
        assert not any(old in text for old in OLD_LIBRARY), name


def test_nothing_in_mate_names_the_old_library_or_its_author():
    files = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True,
                           check=True).stdout.decode().split("\0")
    found = []
    for name in filter(None, files):
        if name in ATTRIBUTION:
            continue
        try:
            text = (ROOT / name).read_text(encoding="utf-8").lower()
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if OLD_AUTHOR in line or any(old in line for old in OLD_LIBRARY):
                found.append(f"{name}:{number}")
    assert not found, f"{len(found)} lines still name it: " + ", ".join(found[:40])


def test_a_refused_history_page_is_not_taken_for_data():
    """A refusal can still carry a page that looks valid: a vehicle without the right answers
    `result 40` around an empty list. Read as data, that month's trips would vanish without an error.
    (Kept from the test of the old client's history reader, now on the only client there is.)"""
    import json
    import api_v2_bridge as bridge
    from leapmotor_cloud.transport import Response
    page = {'result': 40, 'code': 40, 'message': 'No such permission',
            'data': {'pageNum': 1, 'pageSize': 20, 'total': 0, 'totalPage': 0, 'list': []}}

    class Transport:
        def send(self, request, client_cert=None):
            return Response(200, json.dumps(page).encode())

    api = object.__new__(bridge.NewAPIClient)
    api.language, api.device_id, api._new_key = 'en-US', 'synthetic-device', b'k' * 32
    api.account_cert_file, api.account_key_file = 'cert.pem', 'key.pem'
    api.token, api.user_id, api._transport = 'token', '1', Transport()
    api._audit = lambda *a, **k: None
    import pytest
    with pytest.raises(bridge.LeapmotorApiError) as refused:
        api._wire(bridge.CENTER_ORIGIN, '/carownerservice/mileage/daily/detail/page',
                  {'vin': 'V', 'pageNum': 1, 'pageSize': 20, 'startTime': '0', 'endTime': '1'})
    assert refused.value.api_codes == (40, 40)
