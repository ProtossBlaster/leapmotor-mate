"""Bounded read-only qualification of a staged installation before live migration.

Only the child touches the snapshot. Its timeout covers copying, provisioning,
login and telemetry together; no cloud control method is called. The caller
holds the installation lock and owns promotion/cleanup of the staging directory.
"""
from contextlib import closing
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

RUNTIME = Path(__file__).resolve().parent
POLLER = RUNTIME.parent
SESSION_SETTINGS = ('api_v2_shared_session', 'mate_device_id', 'device_id',
                    'api_v2_backend', 'api_v2_login_attempt', 'api_v2_login_failure')
MATERIAL_DIRECTORIES = ('certs', 'api-v2-private', 'api-v2-account-material')


def qualify_installation(database, stage_directory, *, timeout=15):
    """Return safe status; qualified session/settings remain in the private stage.

    Frozen Desktop 1.0 discards child argv, so worker configuration is carried
    by environment. subprocess.run kills and reaps the worker on timeout.
    """
    database, stage = Path(database).absolute(), Path(stage_directory).absolute()
    if os.environ.get('MATE_DEMO', '').lower() in ('1', 'true') or not database.exists():
        return {'state': 'not_required'}
    command = [sys.executable]
    if getattr(sys, 'frozen', False):
        command += ['--mate-child', 'poller']
    command.append(str(Path(__file__).resolve()))
    result_file = stage / 'qualification.json'
    env = dict(os.environ, DB_PATH=str(stage / database.name),
               MATE_PREFLIGHT_SOURCE=str(database), MATE_PREFLIGHT_RESULT=str(result_file),
               MATE_PREFLIGHT_CERT_DIR=os.environ.get('DATA_CERT_DIR') or os.environ.get('CERT_DIR') or str(database.parent / 'certs'),
               MATE_PREFLIGHT_CERT_PATH=os.environ.get('CERT_PATH', ''),
               MATE_PREFLIGHT_KEY_PATH=os.environ.get('KEY_PATH', ''),
               DATA_CERT_DIR=str(stage / 'certs'), CERT_DIR=str(stage / 'certs'))
    # Never let explicit certificate paths route new account material to live data.
    for name in ('CERT_PATH', 'KEY_PATH', 'MATE_LAB_LOGIN_ONCE'):
        env.pop(name, None)
    try:
        run = subprocess.run(command, env=env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             timeout=max(.01, min(float(timeout), 15)))
        if run.returncode != 0:
            return {'state': 'failed', 'reason': 'qualification_failed'}
        result = json.loads(result_file.read_text())
        if result == {'state': 'not_required'}:
            return result
        if result == {'state': 'qualified', 'capabilities': ['B10']}:
            return result
    except subprocess.TimeoutExpired:
        return {'state': 'failed', 'reason': 'timeout'}
    except (OSError, ValueError, TypeError):
        pass
    return {'state': 'failed', 'reason': 'qualification_failed'}


def _copy_private(source, target):
    from leapmotor_cloud.private_storage import ensure_private_directory
    if source.is_symlink():
        raise ValueError('Unsafe migration source')
    if source.is_dir():
        ensure_private_directory(target)
        for child in source.iterdir():
            _copy_private(child, target / child.name)
    elif source.is_file():
        if source.stat().st_nlink != 1:
            raise ValueError('Unsafe migration source')
        ensure_private_directory(target.parent)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with source.open('rb') as src, os.fdopen(fd, 'wb') as dst:
            while block := src.read(1024 * 1024):
                dst.write(block)
    else:
        raise ValueError('Unsafe migration source')


def _snapshot(source, stage):
    from leapmotor_cloud.private_storage import ensure_private_directory
    if source.is_symlink() or source.parent.is_symlink() or stage.is_symlink():
        raise ValueError('Unsafe migration path')
    ensure_private_directory(stage)
    if any(stage.iterdir()):
        raise ValueError('Migration stage must be empty')
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=1)) as src:
        with closing(sqlite3.connect(stage / source.name)) as dst:
            src.backup(dst, pages=256, sleep=.01)
            if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Invalid migration snapshot')
    if os.name != 'nt':
        os.chmod(stage / source.name, 0o600)
    for name in ('secret.key', 'api-v2-private', 'api-v2-account-material'):
        path = source.parent / name
        if path.exists() or path.is_symlink():
            _copy_private(path, stage / name)
    cert_dir = Path(os.environ.get('MATE_PREFLIGHT_CERT_DIR') or source.parent / 'certs')
    for name, override in [('app.crt', 'MATE_PREFLIGHT_CERT_PATH'), ('app.key', 'MATE_PREFLIGHT_KEY_PATH')]:
        certificate = Path(os.environ[override]) if os.environ.get(override) else cert_dir / name
        if certificate.exists() or certificate.is_symlink():
            _copy_private(certificate, stage / 'certs' / name)
    _relocate_staged_session(source, stage)


def _relocate_staged_session(source, stage):
    import crypto
    with sqlite3.connect(stage / source.name) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'").fetchone():
            return
        row = db.execute("SELECT value FROM settings WHERE key='api_v2_shared_session'").fetchone()
        if not row or not row[0]:
            return
        saved = json.loads(crypto.decrypt(row[0]))
        for name in ('cert', 'private_key'):
            original = Path(saved[name]).absolute()
            relative = original.relative_to(source.parent)
            copied = stage / relative
            if relative.parts[0] == '.api-migration-generations' and len(relative.parts) > 3 and relative.parts[2] == 'api-v2-account-material':
                if any(parent.is_symlink() for parent in (original, *original.parents)):
                    raise ValueError('Unsafe account session material')
                copied = stage / 'api-v2-account-material' / 'reused-session' / (name + '.pem')
                _copy_private(original, copied)
            elif relative.parts[0] != 'api-v2-account-material' or not copied.is_file():
                raise ValueError('Account session material cannot be staged')
            saved[name] = str(copied)
        db.execute("UPDATE settings SET value=? WHERE key='api_v2_shared_session'",
                   (crypto.encrypt(json.dumps(saved)),))


def _qualify_staged():
    import crypto
    from automatic_material import provision_automatic
    from api_v2_bridge import NewAPIClient
    database = Path(os.environ['DB_PATH'])
    with closing(sqlite3.connect(database)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'").fetchone():
            return {'state': 'not_required'}
        values = dict(db.execute('SELECT key, value FROM settings'))
    username = crypto.decrypt(values.get('leapmotor_user', '')) or os.environ.get('LEAPMOTOR_USER', '')
    stored_password = values.get('leapmotor_pass', '')
    password = crypto.decrypt(stored_password) if stored_password else os.environ.get('LEAPMOTOR_PASS', '')
    if not username and not stored_password and not password:
        return {'state': 'not_required'}
    if not username or not password:
        raise ValueError('Existing account credentials unavailable')
    provision_automatic(database.parent)
    client = NewAPIClient(username=username, password=password,
                          app_cert_path=database.parent / 'certs' / 'app.crt',
                          app_key_path=database.parent / 'certs' / 'app.key', timeout=8)
    try:
        client.login()
        vehicles = client.get_vehicle_list()
        if not vehicles:
            raise ValueError('No account vehicle available for qualification')
        for vehicle in vehicles:
            if str(vehicle.car_type).upper() != 'B10':
                raise ValueError('Vehicle command compatibility unavailable')
            _validate_telemetry(client._get_vehicle_raw_status(vehicle), vehicle.vin)
        return {'state': 'qualified', 'capabilities': ['B10']}
    finally:
        client.close()


def _validate_telemetry(response, vin):
    # Match the live poller's empty/partial SoC rejection without importing
    # client.py, which would recursively activate this migration in the child.
    data = response.get('data') or {}
    signals = data.get('signal')
    if data.get('vin') != vin or not isinstance(signals, dict) or not signals:
        raise ValueError('Vehicle telemetry unavailable')
    raw = signals.get('100003')
    if raw is None:
        raw = signals.get('1204')
    try:
        soc = float(raw)
        remaining = float(signals.get('3260') or 0)
    except (TypeError, ValueError):
        raise ValueError('Usable state of charge unavailable') from None
    if not math.isfinite(soc) or not 0 <= soc <= 100 or (soc == 0 and remaining > 5):
        raise ValueError('Usable state of charge unavailable')


def worker():
    sys.path[:0] = [str(RUNTIME), str(POLLER / 'vendor'), str(POLLER)]
    try:
        from migration_state import backup_before_migration
        source = Path(os.environ['MATE_PREFLIGHT_SOURCE'])
        backup_before_migration(source)
        _snapshot(source, Path(os.environ['DB_PATH']).parent)
        result = _qualify_staged()
        code = 0
    except Exception:
        result = {'state': 'failed', 'reason': 'qualification_failed'}
        code = 1
    try:
        target = Path(os.environ['MATE_PREFLIGHT_RESULT'])
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(result, stream)
    except OSError:
        return 1
    return code


if __name__ == '__main__':
    raise SystemExit(worker())
