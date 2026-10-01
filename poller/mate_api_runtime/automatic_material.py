"""Provision application material while preserving installation identity.

The common parameters come from the integrity-checked packaged profile. The certificate
pair an installation already holds is kept; a new installation takes the Leapmotor app
certificate packaged with this build (application_certificate/, hash-pinned), so nobody
is ever asked for it. No discovery of other users' sessions or network requests.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

from bootstrap_independent import bootstrap, NAMES
from leapmotor_cloud.account_password import AccountPasswordResolver
from leapmotor_cloud.private_storage import ensure_private_directory
from process_lock import exclusive
from provision_application import load_material

MAX_FILE_BYTES = 32768
PARAMETERS = NAMES[2]
PACKAGED_PROFILE = 'application_profile.json'
DEFAULT_PROFILE_DIRECTORY = Path(__file__).parent
PROFILE_SHA256 = "c856adee9057ae48893e6dd65fdc7fe9bb5f5956bdfb64d90a3daa2ec2b053f6"
# The Leapmotor app's own TLS pair (V1.16.4-1, serial 7FFAB6B2EEC9, valid to 06/03/2029): one for
# every user, the one inside every copy of the app. Shipped with the build instead of asked of the
# user (01/10/2026). When Leapmotor replaces it, a release replaces these two files and hashes.
# → APPLICATION-CERTIFICATE-NOTICE, tests/test_a_fresh_install_is_never_asked_for_a_certificate.py
PACKAGED_CERTIFICATE = 'application_certificate'
CERTIFICATE_SHA256 = {
    'app.crt': "922875fc9a14d948ce9c213046936ef4b27db4cc5ad76bdd1bc712b9146a9dd0",
    'app.key': "362307aaff20a3122bbc20531cad62efa828c1abb6b6a21e42ad236c8d201a7d",
}


def _safe_path(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or (part.exists() and getattr(part.lstat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('Unsafe application material path')
    return path


def _read(path):
    path = _safe_path(path)
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_FILE_BYTES:
            raise ValueError('Invalid application material file')
        data = stream.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise ValueError('Application material too large')
        return data


def _private_owned(path, *, directory=False):
    """Repair destination permissions only for current-user-owned objects."""
    path = _safe_path(path)
    if not path.exists():
        if directory:
            path.mkdir(mode=0o700, parents=True)
        else:
            return
    info = path.stat()
    if directory != stat.S_ISDIR(info.st_mode) or (not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1)):
        raise ValueError('Unsafe private application path')
    if os.name != 'nt':
        if info.st_uid != os.geteuid():
            raise ValueError('Application material must be owned by the current user')
        path.chmod(0o700 if directory else 0o600)
    elif directory:
        ensure_private_directory(path)
    else:
        from leapmotor_cloud.private_storage import _windows_directory
        _windows_directory(path, protect=True, directory=False)


def packaged_profile_usable(profile_directory=DEFAULT_PROFILE_DIRECTORY):
    """Whether this build can install the common parameters out of its own packaged profile.

    The setup page asks this to know what to offer a new installation: with a usable profile a
    certificate pair is enough on its own, because saving it runs `provision_automatic`, and the
    three-file bundle is only the answer where there is no profile to complete the pair.
    Deliberately the same checks `provision_automatic` makes before accepting that file, so the
    page never offers a form that would then be refused.
    → tests/test_a_fresh_install_is_offered_the_certificate_form.py
    """
    try:
        payload = _read(_safe_path(profile_directory) / PACKAGED_PROFILE)
        if hashlib.sha256(payload).hexdigest() != PROFILE_SHA256:
            return False
        parameters = json.loads(payload)
        if set(parameters) != {'round_keys', 'sbox'}:
            return False
        AccountPasswordResolver(**parameters)
    except Exception:
        return False
    return True


def _packaged_certificate():
    """The build's certificate pair, refused unless both files are exactly the pinned ones."""
    directory = _safe_path(DEFAULT_PROFILE_DIRECTORY / PACKAGED_CERTIFICATE)
    payloads = {name: _read(directory / Path(name).name) for name in NAMES[:2]}
    if any(hashlib.sha256(payloads[name]).hexdigest() != CERTIFICATE_SHA256.get(Path(name).name)
           for name in NAMES[:2]):
        raise ValueError('Packaged application certificate integrity check failed')
    return payloads


def packaged_certificate_usable():
    """Whether this build can give a new installation its certificate pair by itself.

    The same integrity check `provision_automatic` makes, then the pair loaded as the login loads it,
    so an expired or damaged packaged pair is caught here and not at a user's first login.
    """
    try:
        _packaged_certificate()
        from session_material import certificate_usable
        directory = DEFAULT_PROFILE_DIRECTORY / PACKAGED_CERTIFICATE
        return bool(certificate_usable(directory / 'app.crt', directory / 'app.key'))
    except Exception:
        return False


def _the_packaged_certificate_in_other_bytes(payloads):
    """Whether a pair is this build's certificate and key, written differently.

    Pairs uploaded through the old setup step were exports carrying their PKCS#12 bag attributes in front of the
    PEM: the same certificate and key in another file. Those are rewritten as the packaged copy, so
    every installation runs on the one recovered from the app. Any other pair is left alone.
    """
    try:
        packaged = _packaged_certificate()
        if all(payloads[name] == packaged[name] for name in NAMES[:2]):
            return False
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization

        def identity(pair):
            certificate = x509.load_pem_x509_certificate(pair[NAMES[0]])
            key = serialization.load_pem_private_key(pair[NAMES[1]], password=None)
            return (certificate.public_bytes(serialization.Encoding.DER),
                    key.public_key().public_bytes(serialization.Encoding.DER,
                                                  serialization.PublicFormat.SubjectPublicKeyInfo))
        return identity(payloads) == identity(packaged)
    except Exception:
        return False


def provision_automatic(destination, *, profile_directory=DEFAULT_PROFILE_DIRECTORY, certificate_directory=None):
    """Reuse local certificates and install missing material atomically.

    Existing complete material wins over anything packaged. A destination with no
    certificate pair takes the one from `certificate_directory` when given, else the
    pair packaged with this build. Invalid or partial destination certificate pairs
    fail closed. Source files are never modified. Bootstrap's durable transaction
    resumes safely following interruption.
    """
    destination = _safe_path(destination)
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    with exclusive(destination / '.automatic-material.lock'):
        targets = {name: _safe_path(destination / name) for name in NAMES}
        transaction = _safe_path(destination / '.application-transaction')
        if (transaction / 'ready').exists():
            _read(transaction / 'ready')
            for name in NAMES:
                _read(transaction / 'staged' / name)
            result = bootstrap(source=transaction / 'staged', destination=destination)
            return {**result, 'account_data_copied': False, 'automatic': True}
        local_pair = [targets[name].exists() for name in NAMES[:2]]
        if any(local_pair) and not all(local_pair):
            raise ValueError('Incomplete existing application certificate pair')
        replace = False
        if all(local_pair) or certificate_directory is not None:
            cert_dir = destination / 'certs' if all(local_pair) else _safe_path(certificate_directory)
            payloads = {name: _read(cert_dir / Path(name).name) for name in NAMES[:2]}
            if _the_packaged_certificate_in_other_bytes(payloads):
                payloads = _packaged_certificate()
                replace = all(local_pair)
        else:
            payloads = _packaged_certificate()
        existing_parameters = targets[PARAMETERS].exists()
        packaged = Path(profile_directory) == DEFAULT_PROFILE_DIRECTORY
        parameter_path = targets[PARAMETERS] if existing_parameters else _safe_path(profile_directory) / (
            PACKAGED_PROFILE if packaged else PARAMETERS)
        payloads[PARAMETERS] = _read(parameter_path)
        if packaged and not existing_parameters and hashlib.sha256(payloads[PARAMETERS]).hexdigest() != PROFILE_SHA256:
            raise ValueError('Packaged application profile integrity check failed')
        try:
            parameters = json.loads(payloads[PARAMETERS])
            if not existing_parameters and set(parameters) != {'round_keys', 'sbox'}:
                raise ValueError('Packaged profile must contain only common parameters')
            AccountPasswordResolver(**parameters)
        except (TypeError, KeyError, ValueError):
            raise ValueError('Invalid application parameters') from None
        # Validate a bounded snapshot before any destination material is changed.
        with tempfile.TemporaryDirectory(prefix='mate-local-application-') as staging:
            source = Path(staging)
            for name, content in payloads.items():
                path = source / name
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(content)
            load_material(source)
            for directory in (destination / 'certs', destination / 'api-v2-private'):
                _private_owned(directory, directory=True)
            for target in targets.values():
                _private_owned(target)
            result = bootstrap(source=source, destination=destination, replace=replace)
        return {**result, 'account_data_copied': False, 'automatic': True}
