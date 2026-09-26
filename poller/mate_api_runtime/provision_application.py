"""Offline installer for operator-supplied application material, never user sessions.

No embedded secrets, issuer API, renewal or revocation check.
"""
import argparse
import json
import os
from pathlib import Path
from leapmotor_cloud.private_storage import ensure_private_directory
from leapmotor_cloud.account_password import AccountPasswordResolver
from leapmotor_cloud.certificate_validation import certificate_usable


def load_material(source):
    source = Path(source)
    names = ('certs/app.crt', 'certs/app.key', 'api-v2-private/p12-parameters.json')
    payloads = {}
    for name in names:
        path = source / name
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(source.resolve()):
            raise ValueError('Unsafe or missing application material')
        if path.stat().st_size > 32768:
            raise ValueError('Application material too large')
        payloads[name] = path.read_bytes()
    if not certificate_usable(source / names[0], source / names[1]):
        raise ValueError('Invalid certificate pair')
    AccountPasswordResolver(**json.loads(payloads[names[2]]))
    return payloads


def provision(source, destination):
    destination = Path(destination)
    payloads = load_material(source)
    names = tuple(payloads)
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name in names:
        target = destination / name
        if target.exists() or target.is_symlink() or target.parent.is_symlink():
            raise ValueError('Refusing to overwrite application material')
    created = []
    try:
        for name in names:
            target = destination / name
            ensure_private_directory(target.parent)
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created.append(target)
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(payloads[name])
                stream.flush()
                os.fsync(stream.fileno())
    except Exception:
        for target in reversed(created):
            target.unlink(missing_ok=True)
        raise
    return {'provisioned': True, 'application_files': len(created), 'account_data_copied': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--destination', default='/data')
    args = parser.parse_args()
    try:
        print(json.dumps(provision(args.source, args.destination)))
    except Exception as error:
        print(json.dumps({'provisioned': False, 'error_type': type(error).__name__}))
        raise SystemExit(1)
