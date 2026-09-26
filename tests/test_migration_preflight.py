"""Migration probes run against snapshots; failed probes cannot rewrite live secrets."""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'poller' / 'mate_api_runtime'
sys.path[:0] = [str(RUNTIME), str(ROOT / 'poller' / 'vendor')]


def module():
    import migration_preflight
    return migration_preflight


def database(tmp_path):
    root = tmp_path / 'live'
    root.mkdir()
    db = root / 'mate.db'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT)')
        conn.executemany('INSERT INTO settings VALUES (?,?)', [('leapmotor_user', 'fixture@example.invalid'), ('leapmotor_pass', 'fixture-password')])
    (root / 'secret.key').write_bytes(b'original-key')
    return db


def test_failed_probe_keeps_original_database_and_key(tmp_path, monkeypatch):
    migration = module()
    db = database(tmp_path)
    before = db.read_bytes()
    def failed(command, **kwargs):
        stage = Path(kwargs['env']['DB_PATH'])
        migration._snapshot(db, stage.parent)
        with sqlite3.connect(stage) as conn:
            conn.execute("UPDATE settings SET value='changed' WHERE key='leapmotor_pass'")
        return subprocess.CompletedProcess(command, 1)
    monkeypatch.setattr(migration.subprocess, 'run', failed)
    result = migration.qualify_installation(db, tmp_path / 'stage')
    assert result['state'] == 'failed'
    assert result['reason'] in {'qualification_failed', 'timeout'}
    assert db.read_bytes() == before
    assert (db.parent / 'secret.key').read_bytes() == b'original-key'


def test_timeout_is_bounded_and_frozen_child_dispatch_supported(tmp_path, monkeypatch):
    migration = module()
    db = database(tmp_path)
    calls = []
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    def timeout(command, **kwargs):
        calls.append((command, kwargs))
        raise subprocess.TimeoutExpired(command, kwargs['timeout'])
    monkeypatch.setattr(migration.subprocess, 'run', timeout)
    assert migration.qualify_installation(db, tmp_path / 'stage', timeout=4) == {'state': 'failed', 'reason': 'timeout'}
    command, args = calls[0]
    assert command[1:3] == ['--mate-child', 'poller']
    assert 0 < args['timeout'] <= 4
    assert Path(args['env']['DB_PATH']).parent == tmp_path / 'stage'


def test_empty_installation_skips_without_stage_or_network(tmp_path, monkeypatch):
    migration = module()
    monkeypatch.setattr(migration.subprocess, 'run', lambda *a, **k: (_ for _ in ()).throw(AssertionError('unexpected worker')))
    assert migration.qualify_installation(tmp_path / 'absent.db', tmp_path / 'stage') == {'state': 'not_required'}
    assert not (tmp_path / 'stage').exists()


def test_worker_failure_does_not_disclose_exception_or_password(tmp_path, monkeypatch):
    migration = module()
    def fail():
        raise ValueError('fixture-password token secret')
    monkeypatch.setattr(migration, '_qualify_staged', fail)
    monkeypatch.setattr(migration, '_snapshot', lambda *args: None)
    monkeypatch.setenv('MATE_PREFLIGHT_SOURCE', str(tmp_path / 'original.db'))
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'staged.db'))
    output = tmp_path / 'result.json'
    monkeypatch.setenv('MATE_PREFLIGHT_RESULT', str(output))
    assert migration.worker() == 1
    assert json.loads(output.read_text()) == {'state': 'failed', 'reason': 'qualification_failed'}
    assert 'fixture-password' not in output.read_text()


def test_real_worker_failure_preserves_original_bytes(tmp_path):
    migration = module()
    db = database(tmp_path)
    original = db.read_bytes()
    result = migration.qualify_installation(db, tmp_path / 'stage', timeout=4)
    assert result['state'] == 'failed'
    assert result['reason'] in {'qualification_failed', 'timeout'}
    assert db.read_bytes() == original
    assert (db.parent / 'secret.key').read_bytes() == b'original-key'
    assert (tmp_path / 'stage' / 'mate.db').is_file()


def test_staged_qualification_decrypts_key_and_reads_every_vehicle(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from cryptography.fernet import Fernet
    import crypto
    import automatic_material
    import api_v2_bridge
    migration = module()
    db = database(tmp_path)
    key = Fernet.generate_key()
    (db.parent / 'secret.key').write_bytes(key)
    encrypted = 'enc:v1:' + Fernet(key).encrypt(b'fixture-password').decode()
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE settings SET value=? WHERE key='leapmotor_pass'", (encrypted,))
    monkeypatch.setenv('DB_PATH', str(db))
    monkeypatch.delenv('MATE_SECRET_KEY', raising=False)
    monkeypatch.setattr(crypto, '_fernet', None)
    monkeypatch.setattr(automatic_material, 'provision_automatic', lambda *args: None)
    reads = []
    class Cloud:
        def __init__(self, **kwargs):
            assert kwargs['username'] == 'fixture@example.invalid'
            assert kwargs['password'] == 'fixture-password'
        def login(self):
            pass
        def get_vehicle_list(self):
            return [SimpleNamespace(car_type='B10', vin='fixture-1'), SimpleNamespace(car_type='B10', vin='fixture-2')]
        def _get_vehicle_raw_status(self, vehicle):
            reads.append(vehicle.vin)
            return {'data': {'vin': vehicle.vin, 'signal': {'1204': '65'}}}
        def close(self):
            pass
    monkeypatch.setattr(api_v2_bridge, 'NewAPIClient', Cloud)
    assert migration._qualify_staged() == {'state': 'qualified', 'capabilities': ['B10']}
    assert reads == ['fixture-1', 'fixture-2']


def test_staged_qualification_rejects_unsupported_account_without_commands(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import automatic_material
    import api_v2_bridge
    import pytest
    migration = module()
    db = database(tmp_path)
    monkeypatch.setenv('DB_PATH', str(db))
    monkeypatch.setattr(automatic_material, 'provision_automatic', lambda *args: None)
    class Cloud:
        def __init__(self, **kwargs): pass
        def login(self): pass
        def get_vehicle_list(self): return [SimpleNamespace(car_type='C10')]
        def close(self): pass
    monkeypatch.setattr(api_v2_bridge, 'NewAPIClient', Cloud)
    with pytest.raises(ValueError, match='compatibility'):
        migration._qualify_staged()


def test_timeout_covers_sqlite_snapshot_before_any_cloud_call(tmp_path):
    migration = module()
    db = database(tmp_path)
    with sqlite3.connect(db) as writer:
        writer.execute('BEGIN EXCLUSIVE')
        result = migration.qualify_installation(db, tmp_path / 'stage', timeout=.15)
        assert result == {'state': 'failed', 'reason': 'timeout'}
        writer.rollback()
    assert (db.parent / 'secret.key').read_bytes() == b'original-key'


def test_existing_empty_database_is_a_new_installation(tmp_path):
    migration = module()
    db = tmp_path / 'blank.db'
    sqlite3.connect(db).close()
    assert migration.qualify_installation(db, tmp_path / 'stage') == {'state': 'not_required'}


def test_snapshot_copies_explicit_certificate_directory(tmp_path, monkeypatch):
    migration = module()
    db = database(tmp_path)
    external = tmp_path / 'custom-certs'
    external.mkdir()
    for name in ('app.crt', 'app.key'):
        (external / name).write_bytes(b'fixture-material')
    monkeypatch.setenv('MATE_PREFLIGHT_CERT_DIR', str(external))
    migration._snapshot(db, tmp_path / 'stage')
    assert (tmp_path / 'stage' / 'certs' / 'app.crt').read_bytes() == b'fixture-material'
    assert (external / 'app.key').read_bytes() == b'fixture-material'
    if sys.platform != 'win32':
        assert (tmp_path / 'stage' / 'mate.db').stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("material_root", ["api-v2-account-material", ".api-migration-generations/prior/api-v2-account-material"])
def test_snapshot_relocates_only_copied_account_session_material(tmp_path, monkeypatch, material_root):
    from cryptography.fernet import Fernet
    import crypto
    migration = module()
    db = database(tmp_path)
    key = Fernet.generate_key()
    (db.parent / 'secret.key').write_bytes(key)
    certs = db.parent / material_root
    certs.mkdir(parents=True)
    (certs / 'account.crt').write_bytes(b'fixture-cert')
    (certs / 'account.key').write_bytes(b'fixture-key')
    saved = dict(cert=str(certs / 'account.crt'), private_key=str(certs / 'account.key'), token='fixture-token')
    raw = 'enc:v1:' + Fernet(key).encrypt(json.dumps(saved).encode()).decode()
    with sqlite3.connect(db) as conn:
        conn.execute('INSERT INTO settings VALUES (?, ?)', ('api_v2_shared_session', raw))
    original = db.read_bytes()
    stage = tmp_path / 'stage'
    monkeypatch.setenv('DB_PATH', str(stage / db.name))
    monkeypatch.delenv('MATE_SECRET_KEY', raising=False)
    monkeypatch.setattr(crypto, '_fernet', None)
    migration._snapshot(db, stage)
    with sqlite3.connect(stage / db.name) as conn:
        staged_raw = conn.execute("SELECT value FROM settings WHERE key='api_v2_shared_session'").fetchone()[0]
    result = json.loads(Fernet(key).decrypt(staged_raw[len('enc:v1:'):].encode()))
    if material_root.startswith('.api-'):
        expected = stage / 'api-v2-account-material' / 'reused-session'
        assert result['cert'] == str(expected / 'cert.pem')
        assert result['private_key'] == str(expected / 'private_key.pem')
    else:
        assert result['cert'] == str(stage / 'api-v2-account-material' / 'account.crt')
        assert result['private_key'] == str(stage / 'api-v2-account-material' / 'account.key')
    assert db.read_bytes() == original


def test_real_frozen_worker_dispatch(tmp_path, monkeypatch):
    import os
    import pytest
    executable = os.environ.get('MATE_FROZEN_EXECUTABLE')
    if not executable:
        pytest.skip('requires released Desktop executable')
    migration = module()
    db = database(tmp_path)
    monkeypatch.setattr(sys, 'executable', executable)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    result = migration.qualify_installation(db, tmp_path / 'stage')
    assert result['state'] == 'failed'
    assert result['reason'] in {'qualification_failed', 'timeout'}
    # Missing certificates prevent any network calls, but the frozen worker
    # must have completed the real snapshot and reported its safe failure.
    assert (tmp_path / 'stage' / db.name).is_file()
    assert json.loads((tmp_path / 'stage' / 'qualification.json').read_text()) == result


def test_empty_or_unusable_telemetry_cannot_qualify():
    import pytest
    migration = module()
    for signals in ({}, {'1204': 'NaN'}, {'1204': '0', '3260': '40'}, {'1178': '1'}):
        with pytest.raises(ValueError):
            migration._validate_telemetry({'data': {'vin': 'fixture', 'signal': signals}}, 'fixture')
    migration._validate_telemetry({'data': {'vin': 'fixture', 'signal': {'1204': '65'}}}, 'fixture')
