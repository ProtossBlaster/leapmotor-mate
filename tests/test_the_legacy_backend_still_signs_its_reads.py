"""An installation left on the bundled SDK must still sign its consumption reads (#327).

@arzthilfe (C10) and @Tommy73LMB05: since updating to 4.x the Trips page shows
"no data" where the consumption chart was, the Monthly Report's driving energy is empty, and
the average and consumed energy print as dashes. His own web log says who refused:

    2026-09-27 07:54:53 command_client: Energy range: non-data response
    {'code': 39, 'result': 39, 'message': 'Information verification failed. Please try again later.'}

65 of them in one morning, the first one nineteen minutes after the update restarted his add-on.
Before that restart the same query had been answering all week.

Mate has two backends. `api_backend` picks the bundled SDK when the activation decided this
account does not qualify (`MATE_API_V2=0`, migration_activation._select) and the independent
client otherwise. These six raw endpoints are POSTed by Mate itself, so the signed headers are
the caller's job — on the SDK. The independent client signs its own wire request and its
`adapter_owned_headers` marker returns nothing on purpose.

4.0.0 replaced the SDK's builders with that marker at all six call sites at once, for both
backends. Measured in the container, no cloud call involved:

    adapter_owned_headers(...)               -> {}
    build_consumption_last_week_headers(...) -> Content-Type, X-P12_ENC_ALG, acceptLanguage,
                                                channel, deviceId, deviceType, nonce, sign,
                                                source, timestamp, version

So an SDK installation sends these reads with no `sign` at all, and "Information verification
failed" is exactly what an unsigned request earns.
"""
import pathlib
import re

import pytest

import command_client

ROOT = pathlib.Path(__file__).resolve().parent.parent
NAMES = ("build_consumption_last_week_headers", "build_consumption_weekly_rank_headers",
         "build_signed_headers")


@pytest.mark.parametrize("name", NAMES)
def test_the_bundled_sdk_builds_real_signed_headers(monkeypatch, name):
    monkeypatch.setenv("MATE_API_V2", "0")
    builder = command_client._signed_headers_builder(name)
    import leapmotor_api.crypto as crypto
    assert builder is getattr(crypto, name)


def test_on_the_sdk_the_headers_actually_carry_a_signature(monkeypatch):
    """Not just 'a different function': the thing the cloud checks has to be in there."""
    monkeypatch.setenv("MATE_API_V2", "0")
    builder = command_client._signed_headers_builder("build_consumption_last_week_headers")
    headers = builder(sign_key=b"k" * 32, device_id="d", carvin="SYNTHETIC",
                      begintime="1", endtime="2", language="en-US").to_dict()
    assert "sign" in headers and headers["sign"]


@pytest.mark.parametrize("name", NAMES)
def test_the_independent_client_still_signs_for_itself(monkeypatch, name):
    """Its marker returns nothing, and that is correct: the adapter signs its own request."""
    monkeypatch.setenv("MATE_API_V2", "1")
    builder = command_client._signed_headers_builder(name)
    assert builder(sign_key=b"k" * 32, device_id="d", carvin="SYNTHETIC").to_dict() == {}


def test_no_endpoint_reaches_for_the_marker_by_itself():
    """The mistake was six copies of one import. A seventh must not be able to repeat it."""
    source = (ROOT / "web" / "command_client.py").read_text(encoding="utf-8")
    direct = [line.strip() for line in source.splitlines()
              if re.search(r"import\s+adapter_owned_headers", line)]
    assert len(direct) == 1, f"the marker is imported outside the chooser: {direct}"
    assert "def _signed_headers_builder" in source
