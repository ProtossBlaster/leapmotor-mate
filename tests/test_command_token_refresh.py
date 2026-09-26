"""The independent command path never replays an ambiguous or rejected command."""
import types
import pytest
import command_client as cc

@pytest.mark.parametrize('message', [
    'access token expired / unauthorized', 'verification failed',
    'Could not find TLS certificate file', 'connection reset by peer',
])
def test_error_is_single_attempt_without_automatic_refresh_or_relogin(monkeypatch,message):
    api=types.SimpleNamespace(last_new_command_receipt=None)
    sess=cc.LeapmotorSession();sess._api=api
    sess._vehicle=types.SimpleNamespace(vin='SYNTHETIC')
    monkeypatch.setattr(sess,'_connect',lambda:None)
    monkeypatch.setattr(sess,'_use_pin_of',lambda vin:None)
    calls=[]
    def action(client,vin):
        calls.append(vin)
        raise RuntimeError(message)
    ok,_=sess.execute(action)
    assert not ok
    assert calls==['SYNTHETIC']
    assert sess._api is api


def test_cloud_acceptance_is_not_physical_confirmation(monkeypatch):
    api=types.SimpleNamespace(last_new_command_receipt=None)
    sess=cc.LeapmotorSession();sess._api=api
    sess._vehicle=types.SimpleNamespace(vin='SYNTHETIC')
    monkeypatch.setattr(sess,'_connect',lambda:None)
    monkeypatch.setattr(sess,'_use_pin_of',lambda vin:None)
    def action(client,vin):client.last_new_command_receipt=types.SimpleNamespace(outcome='accepted')
    ok,message=sess.execute(action)
    assert ok and 'physical execution not confirmed' in message
