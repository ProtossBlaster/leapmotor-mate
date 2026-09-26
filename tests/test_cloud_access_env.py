import json
import time
import mate_api
from ui_command_access import account_hash, command_allowed, snapshot_key


def test_environment_account_matches_snapshot(monkeypatch):
    monkeypatch.setenv('LEAPMOTOR_USER', 'env@example.invalid')
    snapshot = {'account': account_hash('env@example.invalid'), 'at': time.time(),
                'shared': False, 'vehicle': {'vin': 'VINTEST', 'carType': 'B10', 'abilities': [10]}}
    settings = {snapshot_key('VINTEST'): json.dumps(snapshot)}
    assert command_allowed('VINTEST', 'lock', lambda k, d='': settings.get(k, d))


def test_database_account_takes_precedence(monkeypatch):
    import crypto
    monkeypatch.setenv('LEAPMOTOR_USER', 'env@example.invalid')
    snapshot = {'account': account_hash('env@example.invalid'), 'at': time.time(),
                'shared': False, 'vehicle': {'vin': 'VINTEST', 'carType': 'B10', 'abilities': [10]}}
    settings = {'leapmotor_user': crypto.encrypt('db@example.invalid'),
                snapshot_key('VINTEST'): json.dumps(snapshot)}
    assert not command_allowed('VINTEST', 'lock', lambda k, d='': settings.get(k, d))
