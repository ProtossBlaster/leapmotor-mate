"""Private adapter writes and reads use the same platform security contract."""
import os
from pathlib import Path

import pytest
import mate_api
from leapmotor_cloud.private_storage import validate_private_file


def test_atomic_install_protects_parent_before_writing(tmp_path, monkeypatch):
    import bootstrap_independent as bootstrap
    target = tmp_path / 'private' / 'key.pem'
    target.parent.mkdir(mode=0o700)
    target.write_bytes(b'old-synthetic')
    def refuse(path):
        raise ValueError('Private directory permissions required')
    monkeypatch.setattr(bootstrap, 'ensure_private_directory', refuse)
    with pytest.raises(ValueError):
        bootstrap._install_file(target, b'new-synthetic')
    assert target.read_bytes() == b'old-synthetic'
    assert list(target.parent.iterdir()) == [target]


def test_atomic_install_creates_private_file(tmp_path):
    from bootstrap_independent import _install_file
    target = tmp_path / 'private' / 'key.pem'
    _install_file(target, b'synthetic')
    validate_private_file(target)
    assert target.read_bytes() == b'synthetic'


def test_provision_removes_partial_files_when_protection_fails(tmp_path, monkeypatch):
    import provision_application as provision
    from leapmotor_cloud.private_storage import ensure_private_directory
    destination = tmp_path / 'destination'
    payloads = {'certs/app.crt': b'cert', 'certs/app.key': b'key',
                'api-v2-private/p12-parameters.json': b'parameters'}
    monkeypatch.setattr(provision, 'load_material', lambda source: payloads)
    def protect(path):
        if Path(path).name == 'api-v2-private':
            raise ValueError('Protection unavailable')
        return ensure_private_directory(path)
    monkeypatch.setattr(provision, 'ensure_private_directory', protect)
    with pytest.raises(ValueError):
        provision.provision(tmp_path / 'source', destination)
    assert not any(path.is_file() for path in destination.rglob('*'))


def test_readiness_fails_closed_on_private_file_validation(tmp_path, monkeypatch):
    import setup_readiness as setup
    certs = tmp_path / 'certs'
    certs.mkdir()
    for name in ('app.crt', 'app.key'):
        (certs / name).write_bytes(b'synthetic')
    monkeypatch.setattr(setup, 'certificate_usable', lambda *args: True)
    seen = []
    def reject(path):
        seen.append(path)
        raise ValueError('Unsafe private file')
    monkeypatch.setattr(setup, 'validate_private_file', reject)
    assert setup.readiness(certs)['state'] == 'application_parameters_required'
    assert seen == [tmp_path / 'api-v2-private' / 'p12-parameters.json']


@pytest.mark.skipif(os.name == 'nt', reason='POSIX directory permissions')
def test_existing_public_directory_is_rejected_without_writing(tmp_path):
    from bootstrap_independent import _install_file
    directory = tmp_path / 'private'
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)
    with pytest.raises(ValueError):
        _install_file(directory / 'key.pem', b'synthetic')
    assert list(directory.iterdir()) == []
