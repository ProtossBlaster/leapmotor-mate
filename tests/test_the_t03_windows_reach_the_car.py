"""A T03's windows reach the cloud again, and a refused command says why in the log (#400).

@dilianpenchev-a11y's European T03 declares ability 36 and not 12. Every window command on it
stopped inside Mate: «Command not sent: ability_absent for 230» on the page, «API v2 command failed
or was blocked (MateAPIError)» in the log, nothing sent. His bundle shows cmd 230 sent for the same
car on 21-22/09 with 4, 11, 0, 66, 99, 0, 72, 100 and 0, each answered by the cloud with code 0,
before the gate existed. mate-api 0.1.0a16 takes 12 or 36, and the 0-100 scale where the car declares 36.

What this file holds: the slider, the button and Home Assistant send the T03's own percent; a car
the contract refuses offers neither the buttons nor the slider (the slider posts from script, so
the page never hid it); and the log keeps the contract's own words, which were on screen only.
"""
import json
import logging
import threading
import types

import pytest

import mate_api  # noqa: F401 — puts poller/mate_api_runtime on sys.path, as the poller process does
import api_v2_bridge as bridge
from leapmotor_cloud.transport import Response
import ui_command_access
from ui_command_access import allowed, hidden_controls_css, snapshot_key
import command_client as cc
from leapmotor_cloud.mate_compat import MateAPIError

VIN = 'LFZT03TEST00000400'
USER = 'synthetic@example.invalid'
T03 = [1, 2, 3, 5, 7, 10, 11, 14, 15, 17, 18, 20, 30, 31, 34, 35, 36, 52, 61]   # #400, as declared


def _api(monkeypatch, abilities, car_type='T03'):
    api = object.__new__(bridge.NewAPIClient)
    api._mutex = threading.RLock()
    api.operation_password = '123456'
    api.token = 'token'
    api.login = lambda: None
    api.route = lambda vin: {'appRegion': 'region', 'appCenter': 'center'}
    vehicle = bridge.Vehicle.from_dict({'vin': VIN, 'carType': car_type, 'abilities': abilities}, False)
    api.get_vehicle_list = lambda: [vehicle]
    api._get_vehicle_raw_status = lambda v: {'data': {'signal': {
        '1': str(int(bridge.time.time() * 1000)), '1319': '0', '1258': '0'}}}
    sent = []

    def wire(origin, path, body, **kwargs):
        sent.append((path, body))
        return {'code': 0}, Response(200, json.dumps(
            {'code': 0, 'data': {'eventId': 'synthetic', 'timeout': 30}}).encode())
    api._wire = wire
    monkeypatch.setattr(bridge, 'encrypt_operate_password', lambda pw, token: 'encrypted')
    return api, sent


def _windows_sent(sent):
    return [json.loads(body['state']) for path, body in sent
            if 'appremotectl' in path and body.get('cmdid') == '230']


@pytest.mark.parametrize('value', ['0', '9', '20', '66', '100'])
def test_a_t03_window_command_leaves_mate(monkeypatch, value):
    api, sent = _api(monkeypatch, T03)
    api.windows(VIN, value=value)
    assert _windows_sent(sent) == [{'value': value}]


def test_the_t03_buttons_ask_for_its_own_percent(monkeypatch):
    monkeypatch.setattr(cc, '_session_car_type', lambda: 'T03')
    assert cc._windows_native(20) == '20', 'Open is 20 % of the T03’s 0–100'
    monkeypatch.setattr(cc, '_session_car_type', lambda: 'B10')
    assert cc._windows_native(20) == '2', 'and 2 of the B10’s ten steps'


def _snapshot(abilities, car_type='T03'):
    return {'account': ui_command_access.account_hash(USER), 'at': 1000.0, 'shared': False,
            'vehicle': {'vin': VIN, 'carType': car_type, 'abilities': list(abilities)}}


def test_the_page_offers_a_t03_its_windows():
    for name in ('open_windows', 'close_windows', 'set_windows'):
        assert allowed(_snapshot(T03), USER, VIN, name, now=1000.0), name


def test_a_refused_car_hides_the_slider_with_the_buttons():
    import crypto
    values = {'leapmotor_user': crypto.encrypt(USER),
              snapshot_key(VIN): json.dumps(dict(_snapshot([1, 2, 3]), at=__import__('time').time()))}
    css = hidden_controls_css(VIN, lambda key, default='': values.get(key, default))
    assert '[hx-post="api/command/open_windows"]' in css
    assert '[data-post="api/windows"]' in css, 'the slider posts from script and carries data-post'


def test_the_slider_sits_inside_what_the_css_hides():
    from pathlib import Path
    grid = (Path(__file__).resolve().parents[1] / 'web/templates/partials/cmd_grid.html').read_text()
    wrapper = grid.index('<div data-post="api/windows"')
    slider = grid.index('onchange="winsSet(this.value, this)"')
    result = grid.index('id="wins-res"')
    closing = grid.index('</div>', result)
    assert wrapper < slider < result < closing


def _session(monkeypatch, failure):
    sess = cc.LeapmotorSession()
    sess._api = types.SimpleNamespace(last_new_command_receipt=None)
    sess._vehicle = types.SimpleNamespace(vin='SYNTHETIC')
    monkeypatch.setattr(sess, '_connect', lambda: None)
    monkeypatch.setattr(sess, '_use_pin_of', lambda vin: None)

    def action(client, vin):
        raise failure
    return sess, action


def test_the_log_keeps_the_contracts_own_words(monkeypatch, caplog):
    sess, action = _session(monkeypatch, MateAPIError('Command not sent: ability_absent for 230'))
    with caplog.at_level(logging.WARNING):
        ok, said = sess._execute_inner(action)
    assert not ok and said == 'Command not sent: ability_absent for 230'
    assert 'blocked (Command not sent: ability_absent for 230)' in caplog.text


def test_any_other_failure_still_logs_only_its_type(monkeypatch, caplog):
    sess, action = _session(monkeypatch, RuntimeError('token=SECRET-SYNTHETIC'))
    with caplog.at_level(logging.WARNING):
        sess._execute_inner(action)
    assert 'blocked (RuntimeError)' in caplog.text
    assert 'SECRET' not in caplog.text
