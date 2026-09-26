"""Preservation and shared backend decisions at the installation boundary."""
import json
import sqlite3
from pathlib import Path
import pytest
import mate_api
import migration_activation as activation
import migration_preflight


@pytest.fixture
def installation(tmp_path, monkeypatch):
    db = tmp_path / 'mate.db'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)')
        conn.executemany('INSERT INTO settings VALUES (?,?)', [
            ('leapmotor_user', 'owner'), ('leapmotor_pass', 'stored-password'),
            ('leapmotor_pin', 'stored-pin'), ('custom', 'keep')])
        conn.execute('CREATE TABLE trips(id INTEGER PRIMARY KEY, distance REAL)')
        conn.execute('INSERT INTO trips VALUES (7,123.5)')
    (tmp_path / 'secret.key').write_bytes(b'existing-key')
    monkeypatch.setenv('DB_PATH', str(db))
    monkeypatch.delenv('MATE_DEMO', raising=False)
    return db


def values(db):
    with sqlite3.connect(db) as conn:
        return dict(conn.execute('SELECT key,value FROM settings'))


def test_failed_qualification_retains_account_and_history(installation, monkeypatch):
    original = values(installation)
    monkeypatch.setattr(migration_preflight, 'qualify_installation',
                        lambda *args: {'state': 'failed', 'reason': 'timeout'})
    result = activation.activate_installation()
    assert result['backend'] == 'legacy'
    assert {k: values(installation)[k] for k in original} == original
    assert (installation.parent / 'secret.key').read_bytes() == b'existing-key'
    with sqlite3.connect(installation) as db:
        assert db.execute('SELECT * FROM trips').fetchall() == [(7, 123.5)]
    assert not list((installation.parent / '.api-migration-generations').iterdir())


def test_decision_reused_without_second_login(installation, monkeypatch):
    calls = []
    def qualify(*args):
        calls.append(1)
        return {'state': 'failed', 'reason': 'timeout'}
    monkeypatch.setattr(migration_preflight, 'qualify_installation', qualify)
    first = activation.activate_installation()
    assert activation.activate_installation() == first
    assert calls == [1]


def test_success_promotes_only_session_settings(installation, monkeypatch):
    def qualify(source, stage):
        with sqlite3.connect(source) as src, sqlite3.connect(stage / source.name) as dst:
            src.backup(dst)
            dst.executemany('INSERT OR REPLACE INTO settings VALUES (?,?)', [
                ('api_v2_shared_session', 'encrypted-session'), ('api_v2_access_test', 'rights'),
                ('leapmotor_pin', 'must-not-promote'), ('custom', 'must-not-promote')])
            dst.execute('DELETE FROM trips')
        (stage / 'secret.key').write_bytes(b'existing-key')
        return {'state': 'qualified'}
    monkeypatch.setattr(migration_preflight, 'qualify_installation', qualify)
    assert activation.activate_installation()['backend'] == 'independent'
    stored = values(installation)
    assert stored['api_v2_shared_session'] == 'encrypted-session'
    assert stored['leapmotor_pin'] == 'stored-pin'
    assert stored['custom'] == 'keep'
    with sqlite3.connect(installation) as db:
        assert db.execute('SELECT COUNT(*) FROM trips').fetchone()[0] == 1
    assert len(list((installation.parent / '.api-migration-generations').iterdir())) == 1


def test_changed_account_requalifies(installation, monkeypatch):
    calls = []
    def qualify(*args):
        calls.append(1)
        return {'state': 'failed'}
    monkeypatch.setattr(migration_preflight, 'qualify_installation', qualify)
    activation.activate_installation()
    with sqlite3.connect(installation) as db:
        db.execute("UPDATE settings SET value='new-owner' WHERE key='leapmotor_user'")
    activation.activate_installation()
    assert len(calls) == 2


def test_new_install_does_not_create_database(tmp_path, monkeypatch):
    db = tmp_path / 'empty.db'
    monkeypatch.setenv('DB_PATH', str(db))
    monkeypatch.delenv('LEAPMOTOR_USER', raising=False)
    assert activation.activate_installation()['state'] == 'new_installation'
    assert not db.exists()


def test_env_credentials_on_fresh_install_do_not_create_settings(tmp_path, monkeypatch):
    db = tmp_path / 'fresh.db'
    monkeypatch.setenv('DB_PATH', str(db))
    monkeypatch.setenv('LEAPMOTOR_USER', 'env-owner')
    monkeypatch.setenv('LEAPMOTOR_PASS', 'env-password')
    assert activation.activate_installation()['state'] == 'new_installation'
    assert not db.exists()


def test_identity_stable_when_old_plaintext_secrets_are_encrypted(tmp_path, monkeypatch):
    import crypto
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'identity.db'))
    monkeypatch.setattr(crypto, '_fernet', None)
    plain = {'leapmotor_user': 'owner', 'leapmotor_pass': 'secret'}
    encrypted = {key: crypto.encrypt(value) for key, value in plain.items()}
    assert activation._identity(plain) == activation._identity(encrypted)
