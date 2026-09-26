"""Provision common application parameters while preserving installation identity.

The caller supplies a trusted, integrity-checked packaged profile directory. Only
its api-v2-private/p12-parameters.json is read; certificates always come from
this installation. No discovery of other users' sessions or network requests.
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
DEFAULT_PROFILE_DIRECTORY = Path(__file__).parent
PROFILE_SHA256 = "c856adee9057ae48893e6dd65fdc7fe9bb5f5956bdfb64d90a3daa2ec2b053f6"


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


def provision_automatic(destination, *, profile_directory=DEFAULT_PROFILE_DIRECTORY, certificate_directory=None):
    """Reuse local certificates and install missing common parameters atomically.

    Existing complete material wins over any packaged profile. Invalid or partial
    destination certificate pairs fail closed. Source files are never modified.
    Bootstrap's durable transaction resumes safely following interruption.
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
        cert_dir = destination / 'certs' if all(local_pair) else _safe_path(
            certificate_directory if certificate_directory is not None else destination / 'certs')
        payloads = {name: _read(cert_dir / Path(name).name) for name in NAMES[:2]}
        existing_parameters = targets[PARAMETERS].exists()
        packaged = Path(profile_directory) == DEFAULT_PROFILE_DIRECTORY
        parameter_path = targets[PARAMETERS] if existing_parameters else _safe_path(profile_directory) / (
            'application_profile.json' if packaged else PARAMETERS)
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
            result = bootstrap(source=source, destination=destination)
        return {**result, 'account_data_copied': False, 'automatic': True}
