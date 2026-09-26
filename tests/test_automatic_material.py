from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

import pytest
import mate_api
import automatic_material as mod
from leapmotor_cloud.private_storage import validate_private_file
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


@pytest.fixture
def material(tmp_path):
    root = tmp_path / 'source'
    certs = root / 'certs'
    certs.mkdir(parents=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'synthetic-only')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=1))
            .sign(key, hashes.SHA256()))
    (certs/'app.crt').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (certs/'app.key').write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    params = root / mod.PARAMETERS
    params.parent.mkdir()
    params.write_text(json.dumps({'round_keys': list(range(32)), 'sbox': list(range(256))}))
    return root


def test_reuses_exact_local_pair_and_preserves_source(material, tmp_path):
    destination = tmp_path/'data'
    source = {p:p.read_bytes() for p in material.rglob('*') if p.is_file()}
    modes = {p:p.stat().st_mode for p in source}
    result = mod.provision_automatic(destination, profile_directory=material, certificate_directory=material/'certs')
    assert result['automatic'] and result['account_data_copied'] is False
    for name in mod.NAMES:
        assert (destination/name).read_bytes() == (material/name).read_bytes()
        validate_private_file(destination/name)
    assert all(p.read_bytes()==data and p.stat().st_mode==modes[p] for p,data in source.items())


def test_existing_pair_repaired_without_identity_change(material):
    before = [(material/name).read_bytes() for name in mod.NAMES]
    result = mod.provision_automatic(material, profile_directory=material/'missing')
    assert result['state'] == 'existing_material_preserved'
    assert before == [(material/name).read_bytes() for name in mod.NAMES]
    for name in mod.NAMES:
        validate_private_file(material/name)


def test_default_packaged_profile_needs_no_operator_input(material):
    (material/mod.PARAMETERS).unlink()
    result = mod.provision_automatic(material)
    assert result['automatic']
    assert (material/mod.PARAMETERS).read_bytes() == (mod.DEFAULT_PROFILE_DIRECTORY/'application_profile.json').read_bytes()


@pytest.mark.parametrize('problem', ['symlink','oversize','mismatch','extra'])
def test_invalid_material_fails_before_install(material, tmp_path, problem):
    if problem == 'symlink':
        key=material/'certs/app.key'; actual=material/'private.key';key.rename(actual);key.symlink_to(actual)
    elif problem == 'oversize':
        (material/mod.PARAMETERS).write_bytes(b'x'*(mod.MAX_FILE_BYTES+1))
    elif problem == 'mismatch':
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        (material/'certs/app.key').write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    else:
        path=material/mod.PARAMETERS;params=json.loads(path.read_text());params['password_candidates']=['do-not-copy'];path.write_text(json.dumps(params))
    destination=tmp_path/'data'
    with pytest.raises(ValueError):
        mod.provision_automatic(destination,profile_directory=material,certificate_directory=material/'certs')
    assert not any((destination/name).exists() for name in mod.NAMES)


def test_restart_recovers_transaction_without_sources(material, tmp_path, monkeypatch):
    import bootstrap_independent as boot
    destination=tmp_path/'data'
    original=boot._install_file
    calls=0
    def fail(target,payload):
        nonlocal calls
        calls+=1
        if calls == 2: raise OSError('synthetic interruption')
        original(target,payload)
    monkeypatch.setattr(boot,'_install_file',fail)
    with pytest.raises(OSError):
        mod.provision_automatic(destination,profile_directory=material,certificate_directory=material/'certs')
    monkeypatch.setattr(boot,'_install_file',original)
    result=mod.provision_automatic(destination,profile_directory=tmp_path/'missing')
    assert result['state']=='migration_recovered'
    assert all((destination/name).read_bytes()==(material/name).read_bytes() for name in mod.NAMES)


def test_foreign_owner_not_repaired(material, monkeypatch):
    if os.name == 'nt': pytest.skip('POSIX ownership')
    mode=(material/'certs').stat().st_mode
    monkeypatch.setattr(mod.os,'geteuid',lambda: -1)
    with pytest.raises(ValueError,match='owned'):
        mod.provision_automatic(material)
    assert (material/'certs').stat().st_mode==mode


def test_packaged_profile_integrity_fails_closed(material, monkeypatch):
    (material/mod.PARAMETERS).unlink()
    monkeypatch.setattr(mod,'PROFILE_SHA256','0'*64)
    with pytest.raises(ValueError,match='integrity'):
        mod.provision_automatic(material)
    assert not (material/mod.PARAMETERS).exists()


def test_partial_existing_pair_never_replaced(material, tmp_path):
    destination=tmp_path/'data'
    (destination/'certs').mkdir(parents=True)
    target=destination/'certs/app.crt';target.write_bytes(b'existing identity')
    with pytest.raises(ValueError,match='Incomplete'):
        mod.provision_automatic(destination,profile_directory=material,certificate_directory=material/'certs')
    assert target.read_bytes()==b'existing identity'
    assert not (destination/'certs/app.key').exists()


def test_symlinked_destination_ancestor_rejected(material,tmp_path):
    real=tmp_path/'real';real.mkdir();alias=tmp_path/'alias';alias.symlink_to(real,target_is_directory=True)
    with pytest.raises(ValueError,match='Unsafe'):
        mod.provision_automatic(alias/'data',profile_directory=material,certificate_directory=material/'certs')
    assert not list(real.iterdir())


@pytest.mark.parametrize('setting', ['DATA_CERT_DIR', 'CERT_DIR', None])
def test_startup_automatically_provisions_existing_certificate_install(material,tmp_path,monkeypatch,setting):
    import runtime_paths
    (material/mod.PARAMETERS).unlink()
    data=material if setting is None else tmp_path/'database'
    monkeypatch.setenv('DB_PATH',str(data/'mate.db'))
    for name in ('DATA_CERT_DIR','CERT_DIR','MATE_APPLICATION_BUNDLE','MATE_DEMO'):
        monkeypatch.delenv(name,raising=False)
    if setting:
        monkeypatch.setenv(setting,str(material/'certs'))
    result=runtime_paths.prepare_installation()
    assert result['automatic']
    assert (data/mod.PARAMETERS).is_file()
    assert (data/'migration-backups/mate-4.0.0/complete.json').is_file()
    if not setting:
        assert not (data/'migration-backups/mate-4.0.0'/mod.PARAMETERS).exists()


def test_fresh_startup_keeps_setup_available(tmp_path,monkeypatch):
    import runtime_paths
    monkeypatch.setenv('DB_PATH',str(tmp_path/'mate.db'))
    for name in ('DATA_CERT_DIR','CERT_DIR','MATE_APPLICATION_BUNDLE','MATE_DEMO'):
        monkeypatch.delenv(name,raising=False)
    assert runtime_paths.prepare_installation()['state']=='provisioning_required'
    assert not (tmp_path/mod.PARAMETERS).exists()


def test_explicit_bundle_remains_supported(material,tmp_path,monkeypatch):
    import runtime_paths
    monkeypatch.setenv('DB_PATH',str(tmp_path/'data/mate.db'))
    monkeypatch.setenv('MATE_APPLICATION_BUNDLE',str(material))
    monkeypatch.delenv('MATE_DEMO',raising=False)
    result=runtime_paths.prepare_installation()
    assert result['state']=='application_material_migrated'
    assert (tmp_path/'data'/mod.PARAMETERS).read_bytes()==(material/mod.PARAMETERS).read_bytes()


def test_startup_uses_certificate_fallback_when_data_cert_dir_is_empty(material,tmp_path,monkeypatch):
    import runtime_paths
    from setup_readiness import readiness
    data=tmp_path/'data'
    monkeypatch.setenv('DB_PATH',str(data/'mate.db'))
    monkeypatch.setenv('DATA_CERT_DIR',str(data/'certs'))
    monkeypatch.setenv('CERT_DIR',str(material/'certs'))
    for name in ('MATE_APPLICATION_BUNDLE','MATE_DEMO'):
        monkeypatch.delenv(name,raising=False)
    assert runtime_paths.prepare_installation()['automatic']
    assert readiness(material/'certs',parameters_directory=data/'api-v2-private')['present']
    assert (data/'certs/app.crt').read_bytes()==(material/'certs/app.crt').read_bytes()
