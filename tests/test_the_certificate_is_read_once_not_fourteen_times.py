"""Checking the account certificate must not re-parse the private key every time.

Profiled on a real database on 28/09/2026: ONE request to `/api/vehicle-status` — what the Vehicle
page fetches after it loads — called `load_pem_private_key` **fourteen times**, 21 ms each on a Mac
and far worse on the aarch64 add-on this came from, where RSA parsing is the slowest thing the box
does. 0.732 s of the profile for five requests was that one call.

Every one of them comes from our own adapter: `_apply_session` and the saved-session check in
`login()` both ask `certificate_usable(cert, key)`, and both run several times per request as reads
and commands resume the session. The library function is honest — it reads two files and parses
them — but the ANSWER only changes when the files do.

So it is remembered per (path, size, mtime), for both files. A re-issued certificate has a new
mtime and is re-read; nothing is held across a change.
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
for _p in (ROOT / "poller" / "vendor", ROOT / "poller" / "mate_api_runtime"):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))   # APPEND: web/ must stay ahead, both have a main.py

CERT = """-----BEGIN CERTIFICATE-----
not a certificate, and it does not need to be: what is counted here is how many times the
adapter goes to the disk for it, and the library's own answer to rubbish is False.
-----END CERTIFICATE-----
"""


@pytest.fixture
def material(tmp_path):
    cert = tmp_path / "app.crt"
    key = tmp_path / "app.key"
    cert.write_text(CERT, encoding="utf-8")
    key.write_text(CERT, encoding="utf-8")
    return str(cert), str(key)


def test_the_same_pair_is_read_once(material, monkeypatch):
    bridge = pytest.importorskip("api_v2_bridge",
                                 reason="the 4.x adapter needs the vendored cloud library")
    cert, key = material
    reads = []
    real = bridge._certificate_usable_uncached

    def counting(cert_path, key_path, **kwargs):
        reads.append((cert_path, key_path))
        return real(cert_path, key_path, **kwargs)

    monkeypatch.setattr(bridge, "_certificate_usable_uncached", counting)
    monkeypatch.setattr(bridge, "_certificate_answer_cache", {}, raising=False)

    for _ in range(14):
        bridge.certificate_usable(cert, key)
    assert len(reads) == 1, f"the pair was read {len(reads)} times for one unchanged certificate"


def test_a_new_certificate_is_read_again(material, monkeypatch):
    """Held per (path, size, mtime): a re-issued certificate must not be answered from memory."""
    bridge = pytest.importorskip("api_v2_bridge",
                                 reason="the 4.x adapter needs the vendored cloud library")
    cert, key = material
    reads = []
    real = bridge._certificate_usable_uncached

    def counting(cert_path, key_path, **kwargs):
        reads.append((cert_path, key_path))
        return real(cert_path, key_path, **kwargs)

    monkeypatch.setattr(bridge, "_certificate_usable_uncached", counting)
    monkeypatch.setattr(bridge, "_certificate_answer_cache", {}, raising=False)

    bridge.certificate_usable(cert, key)
    path = pathlib.Path(cert)
    path.write_text(CERT + "re-issued\n", encoding="utf-8")
    import os
    os.utime(path, (path.stat().st_atime + 5, path.stat().st_mtime + 5))
    bridge.certificate_usable(cert, key)
    assert len(reads) == 2, "a changed certificate was answered from memory"
