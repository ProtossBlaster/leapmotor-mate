"""Locked, restartable application-material migration; never copies sessions."""
from process_lock import exclusive
import json
import os
from pathlib import Path
from leapmotor_cloud.private_storage import ensure_private_directory
import tempfile
import uuid
from provision_application import load_material, provision

NAMES = ('certs/app.crt', 'certs/app.key', 'api-v2-private/p12-parameters.json')


def _sync(path):
    if os.name == "nt":
        return  # Windows has no directory fsync
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write(path, payload):
    ensure_private_directory(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    _sync(path.parent)


def _install_file(target, payload):
    ensure_private_directory(target.parent)
    fd, name = tempfile.mkstemp(prefix='.application-', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, target)
        _sync(target.parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _check_paths(destination):
    for name in NAMES:
        target = destination / name
        if target.is_symlink() or target.parent.is_symlink():
            raise ValueError('Unsafe application material path')
        if target.exists() and not target.is_file():
            raise ValueError('Invalid application material path')


def _archive(transaction, destination):
    transaction.rename(destination / ('.application-backup-' + uuid.uuid4().hex))
    _sync(destination)


def bootstrap(source='/opt/mate-application', destination='/data'):
    destination = Path(destination)
    if destination.is_symlink():
        raise ValueError('Unsafe data directory')
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    with exclusive(destination / '.application-migration.lock'):
        _check_paths(destination)
        transaction = destination / '.application-transaction'
        if transaction.is_symlink():
            raise ValueError('Unsafe application transaction')
        recovered = transaction.exists()
        if recovered:
            ensure_private_directory(transaction)
        if recovered and not (transaction / 'ready').is_file():
            # No destination writes occur before the durable ready marker.
            _archive(transaction, destination)
        if not transaction.exists():
            present = [(destination / name).exists() for name in NAMES]
            if all(present):
                load_material(destination)
                return {'state': 'existing_material_preserved', 'downloaded': False}
            # Validate the whole trusted bundle before changing existing material.
            load_material(source)
            ensure_private_directory(transaction)
            _sync(destination)
            provision(source, transaction / 'staged')
            for name in NAMES:
                target = destination / name
                if target.exists():
                    _write(transaction / 'original' / name, target.read_bytes())
            for directory in sorted((p for p in transaction.rglob('*') if p.is_dir()),
                                    key=lambda p: len(p.parts), reverse=True):
                _sync(directory)
            _write(transaction / 'ready', b'1\n')
        payloads = load_material(transaction / 'staged')
        for name in NAMES:
            _install_file(destination / name, payloads[name])
        load_material(destination)
        _archive(transaction, destination)
        return {'state': 'migration_recovered' if recovered else 'application_material_migrated',
                'downloaded': False, 'account_data_copied': False}


if __name__ == '__main__':
    try:
        print(json.dumps(bootstrap(destination=os.environ.get('MATE_DATA_DIR', '/data'))))
    except Exception as error:
        print(json.dumps({'state': 'provisioning_failed', 'error_type': type(error).__name__}))
        raise SystemExit(1)
