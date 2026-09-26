"""A certificate Mate cannot read is refused when it is uploaded, not at every login after — #283.

@fabiodim's install: the wizard took `app.crt`, said nothing, and from then on every login died
with `SSLError(524297, '[SSL] PEM lib (_ssl.c:3855)')` — OpenSSL's way of saying the CERTIFICATE
file cannot be read (reproduced in the add-on image: 3855 is the certificate, 3876 the key). The
issue title was "no connect", body "no login": nothing on screen pointed at the file.

Two holes let it through, and both are closed here:

1. `/api/setup/cert` checked for the substring `-----BEGIN CERTIFICATE-----`. A certificate cut
   short, flattened onto one line, or a saved web page that shows the PEM all contain that line.
   Now the pair is loaded exactly as the login loads it, before anything is written.
2. `/api/setup/cert-status` answered "present" when the two files merely existed, so the wizard
   skipped the certificate step for good and a broken file could never be replaced from it.

Certificates here are generated on the spot — the real app pair is not in the repository.
"""
import datetime
import pathlib
import shutil
import subprocess

import pytest

pytest.importorskip("fastapi", reason="web.main needs the production web dependencies")
pytest.importorskip("httpx", reason="Starlette TestClient needs httpx")

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from starlette.testclient import TestClient

import command_client
import main

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _key_pem(key, password: bytes | None = None) -> str:
    enc = (serialization.BestAvailableEncryption(password) if password
           else serialization.NoEncryption())
    fmt = serialization.PrivateFormat.PKCS8 if password else serialization.PrivateFormat.TraditionalOpenSSL
    return key.private_bytes(serialization.Encoding.PEM, fmt, enc).decode()


def _cert_pem(key) -> str:
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "mate-test")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now).not_valid_after(now + datetime.timedelta(days=30))
            .sign(key, hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.PEM).decode()


KEY = _key()
CRT = _cert_pem(KEY)
KEY_PEM = _key_pem(KEY)
CRT_LINES = CRT.strip().splitlines()


@pytest.fixture
def web(tmp_path, monkeypatch):
    """The web app with its certificate directory pointed at an empty folder."""
    for var in ("MATE_AUTH_PASSWORD", "SUPERVISOR_TOKEN", "HASSIO_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    data = tmp_path / "certs"
    monkeypatch.setattr(main, "_DATA_CERT_DIR", str(data))
    monkeypatch.setattr(command_client, "_DATA_CERT_DIR", str(data))
    monkeypatch.setattr(command_client, "_FALLBACK_CERT_DIR", str(tmp_path / "no-fallback"))
    return TestClient(main.app), data


def _paste(client, crt: str, key: str):
    return client.post("/api/setup/cert", data={"crt_pem": crt, "key_pem": key})


def _refused(response, code: str, data: pathlib.Path):
    assert response.status_code == 400, response.text
    assert response.json()["code"] == code
    assert not (data / "app.crt").exists() and not (data / "app.key").exists(), \
        "a pair that cannot be read was written anyway"
    assert not list(data.glob("*.new")), "the refused pair was left lying beside the real one"


def test_a_good_pair_is_saved_and_the_wizard_counts_it_as_present(web):
    client, data = web
    response = _paste(client, CRT, KEY_PEM)
    assert response.status_code == 200, response.text
    assert (data / "app.crt").exists() and (data / "app.key").exists()
    assert client.get("/api/setup/cert-status").json()["state"] == "application_parameters_required"


def test_a_web_page_saved_in_place_of_the_certificate_is_refused(web):
    """The likeliest #283 shape: "Save as" on the GitHub page. Every PEM line is there, each inside
    markup — so the old substring check passed it."""
    client, data = web
    page = "<html><body><table>\n" + "\n".join(
        f'<tr><td class="blob-code">{line}</td></tr>' for line in CRT_LINES) + "\n</table></body></html>\n"
    response = client.post("/api/setup/cert", files={
        "crt_file": ("app.crt", page.encode(), "text/html"),
        "key_file": ("app.key", KEY_PEM.encode(), "application/octet-stream")})
    _refused(response, "cert_unreadable", data)


def test_a_certificate_cut_short_is_refused(web):
    client, data = web
    _refused(_paste(client, "\n".join(CRT_LINES[:4] + CRT_LINES[-1:]) + "\n", KEY_PEM),
             "cert_unreadable", data)


def test_a_certificate_flattened_onto_one_line_is_refused(web):
    client, data = web
    _refused(_paste(client, " ".join(CRT_LINES), KEY_PEM), "cert_unreadable", data)


def test_a_key_cut_short_is_refused(web):
    client, data = web
    lines = KEY_PEM.strip().splitlines()
    _refused(_paste(client, CRT, "\n".join(lines[:4] + lines[-1:]) + "\n"), "key_unreadable", data)


def test_a_key_from_another_pair_is_refused(web):
    client, data = web
    _refused(_paste(client, CRT, _key_pem(_key())), "key_mismatch", data)


def test_a_password_protected_key_is_refused(web):
    """The login has no password to give it either, so an encrypted key is as unusable as a broken
    one — and it passed the old check, having both `-----BEGIN` and `PRIVATE KEY`."""
    client, data = web
    _refused(_paste(client, CRT, _key_pem(KEY, password=b"secret")), "key_unreadable", data)


def test_a_refused_upload_leaves_the_pair_already_saved_untouched(web):
    client, data = web
    assert _paste(client, CRT, KEY_PEM).status_code == 200
    response = _paste(client, " ".join(CRT_LINES), KEY_PEM)
    assert response.status_code == 400
    assert (data / "app.crt").read_text().strip() == CRT.strip()


def test_a_windows_file_is_accepted(web):
    """CRLF line ends and a byte-order mark: what Notepad saves. Not broken, so not refused."""
    client, _ = web
    response = client.post("/api/setup/cert", files={
        "crt_file": ("app.crt", ("﻿" + CRT.replace("\n", "\r\n")).encode(), "application/x-x509-ca-cert"),
        "key_file": ("app.key", ("﻿" + KEY_PEM.replace("\n", "\r\n")).encode(), "application/octet-stream")})
    assert response.status_code == 200, response.text
    assert client.get("/api/setup/cert-status").json()["state"] == "application_parameters_required"


def test_the_wizard_offers_the_certificate_step_again_when_the_saved_one_cannot_be_read(web):
    """@fabiodim's install as it stands: two files on disk, one of them unreadable. "Present" made
    the wizard skip straight to the login, so the broken file could never be replaced from it."""
    client, data = web
    data.mkdir(parents=True)
    (data / "app.crt").write_text(" ".join(CRT_LINES) + "\n")
    (data / "app.key").write_text(KEY_PEM)
    assert client.get("/api/setup/cert-status").json()["present"] is False


def test_the_wizard_shows_each_refusal_in_the_owner_language(tmp_path):
    """The server answers in English with a `code`; the wizard must turn the code into the page's
    own language — run for real in node, like the two-car test does."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required to run the wizard's own script")
    from test_the_wizard_lets_a_two_car_account_finish import _rendered_script
    subprocess.run([node, str(ROOT / "tests/setup_wizard_cert_refusal.cjs"),
                    str(_rendered_script(tmp_path))], check=True)
