"""A new installation of Mate 4 could not be set up from its own page (D #328).

@gonzalocav, 27/09/2026, fresh Docker on 4.1.0: the instructions say to upload `app.crt` and
`app.key`, the page offered only a `.zip` box, and a zip of those two files answered "Invalid
application bundle; existing material preserved".

Measured on the released `:4.2.0` image with an empty volume, before this test existed:

    GET /api/setup/cert-status
    {"present":false,"managed":true,"state":"provisioning_required","manual_upload_required":false}

`setup.html` forks on `managed`, which `readiness()` returns as `True` in *every* branch under the
independent client, so `cert-step` was unreachable from 4.0.0 on. The bundle it offered instead has
to carry exactly three members (`bootstrap_independent.NAMES`), one of them
`api-v2-private/p12-parameters.json` — private parameters a user cannot produce.

Nothing was actually missing: on the same empty container `POST /api/setup/cert` with the public
certificate pair answered `{"ok":true}` and cert-status turned to `state: ready`, because
`provision_automatic` installs the parameters from the profile packaged in the image. Only the
page's fork was wrong, and `manual_upload_required` — the field that should decide it — was
hardcoded `False` and read by nobody.
"""
import pathlib
import shutil
import subprocess
from datetime import datetime, timedelta, timezone

import jinja2
import pytest

import battery_packs
import mate_api  # puts poller/mate_api_runtime on sys.path, as the poller process does

ROOT = pathlib.Path(__file__).resolve().parent.parent


class _Request:
    headers: dict[str, str] = {}


def _rendered_script(tmp_path):
    """The page's own script, Jinja-rendered exactly as the browser receives it."""
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(ROOT / "web" / "templates")))
    html = env.get_template("setup.html").render(
        request=_Request(),
        battery_options={ct: [o for o in opts if not o.get("reev")]
                         for ct, opts in battery_packs.EU_BATTERY_MAP.items()},
        research=False, tz_options=[], tz_detected="Europe/Rome", prefill=None,
    )
    body = html[html.rindex("<script>") + len("<script>"):html.rindex("</script>")]
    assert "async function chooseSetup" in body, "the wizard's script is not where this test looks"
    out = tmp_path / "setup_wizard.js"
    out.write_text(body)
    return out


def test_the_page_offers_the_step_that_can_finish_the_install(tmp_path):
    """Four readiness answers through the real `chooseSetup()` — see setup_wizard_fresh_install.cjs."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required to run the wizard's own script")
    subprocess.run([node, str(ROOT / "tests/setup_wizard_fresh_install.cjs"),
                    str(_rendered_script(tmp_path))], check=True)


def test_the_fork_reads_the_field_that_says_a_bundle_is_needed():
    """Runs with or without node: `managed` is true for every 4.x install, so a fork on it sends
    every new user to the bundle box. The question the page has to ask is whether a manual upload
    is required at all."""
    script = (ROOT / "web" / "templates" / "setup.html").read_text()
    fork = script[script.index("async function chooseSetup"):]
    fork = fork[:fork.index("function showManagedSetupError")]
    assert "d.manual_upload_required" in fork, "the page still decides on `managed`"
    assert "else if (d.managed)" not in fork


def _certificate_pair(directory):
    """A synthetic leaf pair `certificate_usable` accepts, so nothing real is copied into a test.

    Not `openssl req -x509`: that stamps BasicConstraints CA:TRUE, which the validator refuses.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    directory.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic-only")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
            .sign(key, hashes.SHA256()))
    (directory / "app.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (directory / "app.key").write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))


def test_a_fresh_install_does_not_need_a_supplied_bundle(tmp_path):
    """An empty installation can finish from the certificate form, so it must not be told to
    upload material it has no way of building."""
    import setup_readiness
    answer = setup_readiness.readiness(tmp_path / "certs",
                                       parameters_directory=tmp_path / "api-v2-private")
    assert answer["state"] == "provisioning_required"
    assert answer["manual_upload_required"] is False


def test_manual_upload_is_required_when_the_packaged_profile_cannot_be_used(tmp_path, monkeypatch):
    """The only case the ZIP box is honest for: this build cannot provision the parameters itself."""
    import automatic_material
    import setup_readiness
    monkeypatch.setattr(automatic_material, "PROFILE_SHA256", "0" * 64)
    answer = setup_readiness.readiness(tmp_path / "certs",
                                       parameters_directory=tmp_path / "api-v2-private")
    assert answer["state"] == "provisioning_required"
    assert answer["manual_upload_required"] is True


def test_missing_parameters_next_to_a_good_certificate_still_point_at_the_form(tmp_path):
    """Certificates readable, parameters absent: re-running the certificate step installs them,
    so this is not a case for the supplied bundle either."""
    import setup_readiness
    _certificate_pair(tmp_path / "certs")
    answer = setup_readiness.readiness(tmp_path / "certs",
                                       parameters_directory=tmp_path / "api-v2-private")
    assert answer["state"] == "application_parameters_required"
    assert answer["manual_upload_required"] is False


def test_the_page_and_the_installer_agree_about_the_packaged_profile(tmp_path, monkeypatch):
    """The page must not offer a form the installer would then refuse.

    `readiness()` says "no bundle needed" exactly when `provision_automatic` can install the
    packaged parameters. Pinned here so the two checks cannot drift apart: with the profile
    unusable, readiness asks for the bundle AND provisioning refuses the certificate pair.
    """
    import automatic_material
    import setup_readiness
    monkeypatch.setattr(automatic_material, "PROFILE_SHA256", "0" * 64)
    _certificate_pair(tmp_path / "certs")
    assert setup_readiness.readiness(
        tmp_path / "certs", parameters_directory=tmp_path / "api-v2-private"
    )["manual_upload_required"] is True
    with pytest.raises(ValueError):
        automatic_material.provision_automatic(tmp_path)
