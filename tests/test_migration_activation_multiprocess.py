"""Real process locks and SQLite crash boundaries; no network or real account data."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'poller' / 'mate_api_runtime'
DECISION = 'mate_api_migration_decision'
SESSION = 'api_v2_shared_session'
NEW_KEY = b'a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s='

CHILD = r'''
import json, os, pathlib, sqlite3, sys, time
sys.path[:0] = [os.environ['TEST_RUNTIME'], os.environ['TEST_POLLER'] + '/vendor', os.environ['TEST_POLLER']]
import migration_activation as activation
import migration_preflight as preflight
root = pathlib.Path(os.environ['DB_PATH']).parent
if os.environ.get('TEST_BARRIER'):
    (root / ('ready-' + str(os.getpid()))).touch()
    deadline = time.monotonic() + 10
    while not (root / 'go').exists():
        if time.monotonic() > deadline: raise RuntimeError('barrier timeout')
        time.sleep(.01)
def qualify(database, stage):
    fd = os.open(root / 'qualification-count', os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, 'wb') as out:
        out.write(b'qualified\n'); out.flush(); os.fsync(out.fileno())
    time.sleep(.15)
    if os.environ.get('TEST_RESULT') == 'failed':
        return {'state': 'failed', 'reason': 'synthetic_failure'}
    with sqlite3.connect(database) as src, sqlite3.connect(stage / database.name) as dst:
        src.backup(dst)
        dst.execute('INSERT OR REPLACE INTO settings VALUES (?, ?)', ('api_v2_shared_session', 'synthetic-qualified-session'))
        dst.execute('INSERT OR REPLACE INTO settings VALUES (?, ?)', ('api_v2_access_synthetic', 'synthetic-rights'))
        dst.execute('UPDATE trips SET distance=999')
    (stage / 'secret.key').write_bytes((root / 'secret.key').read_bytes() if (root / 'secret.key').exists()
                                    else b'a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s=')
    return {'state': 'qualified', 'capabilities': ['B10']}
preflight.qualify_installation = qualify
phase = os.environ.get('TEST_CRASH')
if phase == 'before_commit':
    real_connect = sqlite3.connect
    class CrashConnection(sqlite3.Connection):
        def execute(self, sql, parameters=(), /):
            if sql.startswith('INSERT OR REPLACE INTO settings') and parameters and parameters[0] == activation.DECISION_KEY:
                # Promoted settings have been inserted but the decision and
                # enclosing SQLite transaction have not committed.
                os._exit(73)
            return super().execute(sql, parameters)
    def connect(*args, **kwargs):
        kwargs['factory'] = CrashConnection
        return real_connect(*args, **kwargs)
    activation.sqlite3.connect = connect
elif phase == 'during_key_write':
    real_mkstemp = activation.tempfile.mkstemp
    def mkstemp(*args, **kwargs):
        fd, path = real_mkstemp(*args, **kwargs)
        if kwargs.get('prefix') == '.migration-key-':
            os.write(fd, b'partial-key'); os.fsync(fd); os._exit(75)
        return fd, path
    activation.tempfile.mkstemp = mkstemp
elif phase == 'after_commit':
    real_select = activation._select
    def select(result):
        if result['backend'] == 'independent': os._exit(74)
        return real_select(result)
    activation._select = select
print(json.dumps(activation.activate_installation()), flush=True)
'''


@pytest.fixture
def installation(tmp_path):
    db = tmp_path / 'mate.db'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT)')
        conn.executemany('INSERT INTO settings VALUES (?, ?)', [
            ('leapmotor_user', 'synthetic-owner'), ('leapmotor_pass', 'synthetic-password'),
            ('leapmotor_pin', 'synthetic-pin'), ('custom', 'untouched')])
        conn.execute('CREATE TABLE trips(id INTEGER PRIMARY KEY, distance REAL)')
        conn.execute('INSERT INTO trips VALUES (17, 42.5)')
    (tmp_path / 'secret.key').write_bytes(b'unchanged-synthetic-key')
    return db


def environment(db, **extra):
    env = dict(os.environ, DB_PATH=str(db), TEST_RUNTIME=str(RUNTIME), TEST_POLLER=str(ROOT/'poller'))
    for name in ('MATE_DEMO','LEAPMOTOR_USER','LEAPMOTOR_PASS','MATE_SECRET_KEY','MATE_PREFLIGHT_SOURCE',
                 'MATE_PREFLIGHT_RESULT','TEST_CRASH','TEST_BARRIER','TEST_RESULT'):
        env.pop(name, None)
    env.update(extra)
    return env


def settings(db):
    with sqlite3.connect(db) as conn:
        return dict(conn.execute('SELECT key,value FROM settings'))


def preserved(db, expected_key=b'unchanged-synthetic-key'):
    values=settings(db)
    assert {key:values[key] for key in ('leapmotor_user','leapmotor_pass','leapmotor_pin','custom')} == {
        'leapmotor_user':'synthetic-owner','leapmotor_pass':'synthetic-password',
        'leapmotor_pin':'synthetic-pin','custom':'untouched'}
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT * FROM trips').fetchall()==[(17,42.5)]
        assert conn.execute('PRAGMA integrity_check').fetchone()==('ok',)
    assert (db.parent/'secret.key').read_bytes()==expected_key


@pytest.mark.parametrize('outcome,backend',[('qualified','independent'),('failed','legacy')])
def test_simultaneous_processes_qualify_once_and_share_decision(installation,outcome,backend):
    processes=[subprocess.Popen([sys.executable,'-c',CHILD],
        env=environment(installation,TEST_BARRIER='1',TEST_RESULT=outcome),
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for _ in range(2)]
    try:
        deadline=time.monotonic()+10
        while len(list(installation.parent.glob('ready-*')))<2:
            assert time.monotonic()<deadline, 'child startup exceeded bound'
            assert all(p.poll() is None for p in processes), 'child exited before barrier'
            time.sleep(.01)
        (installation.parent/'go').touch()
        results=[]
        for child in processes:
            output,error=child.communicate(timeout=15)
            assert child.returncode==0,error
            results.append(json.loads(output))
        assert results[0]==results[1]
        assert results[0]['backend']==backend
        assert (installation.parent/'qualification-count').read_bytes()==b'qualified\n'
        assert json.loads(settings(installation)[DECISION])==results[0]
        assert (SESSION in settings(installation))==(backend=='independent')
        preserved(installation)
    finally:
        for child in processes:
            if child.poll() is None:
                child.kill();child.wait(timeout=5)


@pytest.mark.parametrize('phase,exit_code,qualification_count',[
    ('before_commit',73,2),('after_commit',74,1)])
def test_process_crash_never_publishes_partial_session_decision(installation,phase,exit_code,qualification_count):
    crashed=subprocess.run([sys.executable,'-c',CHILD],env=environment(installation,TEST_CRASH=phase),
                           capture_output=True,text=True,timeout=15)
    assert crashed.returncode==exit_code,crashed.stderr
    before=settings(installation)
    assert (SESSION in before)==(DECISION in before)
    assert (SESSION in before)==(phase=='after_commit')
    preserved(installation)
    resumed=subprocess.run([sys.executable,'-c',CHILD],env=environment(installation),
                           capture_output=True,text=True,timeout=15)
    assert resumed.returncode==0,resumed.stderr
    result=json.loads(resumed.stdout)
    assert result['backend']=='independent'
    assert settings(installation)[SESSION]=='synthetic-qualified-session'
    assert json.loads(settings(installation)[DECISION])==result
    assert (installation.parent/'qualification-count').read_bytes()==b'qualified\n'*qualification_count
    preserved(installation)


def test_interrupted_key_staging_never_exposes_partial_live_key(installation):
    key=installation.parent/'secret.key'
    key.unlink()
    crashed=subprocess.run([sys.executable,'-c',CHILD],env=environment(installation,TEST_CRASH='during_key_write'),
                           capture_output=True,text=True,timeout=15)
    assert crashed.returncode==75,crashed.stderr
    assert not key.exists()
    assert len(list(installation.parent.glob('.migration-key-*')))==1
    assert SESSION not in settings(installation)
    assert DECISION not in settings(installation)
    resumed=subprocess.run([sys.executable,'-c',CHILD],env=environment(installation),
                           capture_output=True,text=True,timeout=15)
    assert resumed.returncode==0,resumed.stderr
    assert json.loads(resumed.stdout)['backend']=='independent'
    assert key.read_bytes()==NEW_KEY
    from cryptography.fernet import Fernet
    Fernet(key.read_bytes())  # A real complete key, never the staged partial bytes.
    preserved(installation,NEW_KEY)
