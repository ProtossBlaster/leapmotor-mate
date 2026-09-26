"""History reads follow authenticated bindings, never leftover account vehicles."""
import json
import sqlite3
from types import SimpleNamespace

import pytest
import mate_api
import history_worker as worker


@pytest.fixture
def history(monkeypatch, tmp_path):
    path = tmp_path / 'history.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)')
        db.execute('CREATE TABLE vehicles(id INTEGER PRIMARY KEY,vin TEXT)')
        db.executemany('INSERT INTO vehicles(vin) VALUES (?)', [('OLD',), ('OWNER',), ('SHARED',)])
    monkeypatch.setattr(worker, 'DB', str(path))
    monkeypatch.setattr(worker, 'connect_db', lambda: sqlite3.connect(path))
    monkeypatch.setattr(worker.crypto, 'decrypt', lambda value: value)
    monkeypatch.setenv('TZ', 'UTC')
    monkeypatch.setenv('LEAPMOTOR_USER', 'environment-user')
    monkeypatch.setenv('LEAPMOTOR_PASS', 'environment-password')
    monkeypatch.setattr(worker, 'import_trips', lambda db, importer: {'state': 'disabled'})
    monkeypatch.setattr(worker, 'migrate_charges', lambda db: {'inserted': 0})
    state = SimpleNamespace(path=path, credentials=None, routes=[], reads=[], fail=None,
                            bindings=[SimpleNamespace(vin='OWNER', is_shared=False),
                                      SimpleNamespace(vin='SHARED', is_shared=True),
                                      SimpleNamespace(vin='NOT_REGISTERED', is_shared=False)])
    class API:
        def __init__(self, **kwargs):
            state.credentials = kwargs
        def get_vehicle_list(self):
            return state.bindings
        def route(self, vin):
            state.routes.append(vin)
            if state.fail == ('route', vin):
                raise worker.LeapmotorApiError('private account detail must not appear')
            return {'appCenter': 'synthetic-origin'}
        def read(self, path, body, **kwargs):
            state.reads.append(body['vin'])
            if state.fail == ('read', body['vin']):
                raise worker.LeapmotorApiError('private account detail must not appear')
            return {'data': dict(pageNum=int(body['pageNum']), pageSize=int(body['pageSize']),
                                 totalPage=0, total=0, list=[])}
    monkeypatch.setattr(worker, 'NewAPIClient', API)
    return state


def test_environment_credentials_and_current_owner_shared_bindings(history):
    worker.sync_once()
    assert history.credentials['username'] == 'environment-user'
    assert history.credentials['password'] == 'environment-password'
    assert history.routes == ['OWNER', 'SHARED']
    assert history.reads == ['OWNER', 'OWNER', 'SHARED', 'SHARED']


def test_saved_credentials_take_precedence_over_environment(history):
    with sqlite3.connect(history.path) as db:
        db.executemany('INSERT INTO settings VALUES (?,?)',
                       [('leapmotor_user', 'saved-user'), ('leapmotor_pass', 'saved-password')])
    worker.sync_once()
    assert history.credentials['username'] == 'saved-user'
    assert history.credentials['password'] == 'saved-password'


@pytest.mark.parametrize('stage', ['route', 'read'])
def test_one_current_vehicle_losing_access_does_not_block_another(history, capsys, stage):
    history.fail = (stage, 'OWNER')
    worker.sync_once()
    assert history.reads[-2:] == ['SHARED', 'SHARED']
    output = capsys.readouterr().out
    assert 'private account detail' not in output
    assert 'OWNER' not in output and 'SHARED' not in output
    with sqlite3.connect(history.path) as db:
        summary = json.loads(db.execute("SELECT value FROM settings WHERE key='api_v2_history_sync'").fetchone()[0])
    assert summary['unavailable_vehicles'] == 1


def test_old_local_vehicles_alone_never_authorize_history_reads(history):
    history.bindings = [SimpleNamespace(vin='NOT_REGISTERED', is_shared=False)]
    worker.sync_once()
    assert history.routes == history.reads == []


def test_missing_credentials_do_not_attempt_cloud(history, monkeypatch):
    monkeypatch.delenv('LEAPMOTOR_PASS')
    worker.sync_once()
    assert history.credentials is None
    assert history.routes == history.reads == []
