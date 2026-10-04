"""A setting no signal shows waits for the car's word, and every command logs what it carried (#395).

@ViriatusOG's T03 charged at the plug while Mate's page read its charge plan "On". What Mate showed
was the cloud's copy of the last plan sent; whether the car had taken it, Mate never knew: since the
independent client every accepted command reads "Cloud accepted; physical execution not confirmed",
while the earlier library asked the cloud for the car's answer after each command, and the command
log said `confirmed`. Nor did the log say what a save had sent: his bundle could not show it.

Measured on a B10, 04/10/2026: the command's answer carries an `eventId` and how long the cloud
waits for the car (`timeout`: 30 s for a car asleep, 5 s for one awake). Asked with that id as
`msgID`, `appremotectl/query` answers `data` 0, then 1 when the car has carried the command out — in
1.3 s awake and in 15 s from 14 minutes asleep, each time the second the car's own configuration
showed the plan.

So the charge plan (190) and a navigator destination (180), which no signal of the car shows, wait for
that 1 until the cloud's time is up; a physical command keeps what it had, since the page reads the
car's signals after it. And each command writes one line: what it carried, a place left out, and
what the cloud said.
"""
import json
import logging
import sqlite3
import threading
import time as real_time
import types

import pytest

import mate_api  # noqa: F401 — puts poller/mate_api_runtime on sys.path, as the poller process does
import api_v2_bridge as bridge
from leapmotor_cloud.transport import Response
import command_client as cc

VIN = 'LFZC10TEST00000395'
PLAN = {'chargeEnable': 1, 'chargesoc': 100, 'starttime': '23:30', 'endtime': '08:00',
        'cycles': '1,1,1,1,1,1,1', 'circulation': 0, 'recharge': 0}
DESTINATION = {'address': '10 Downing Street, London', 'addressname': 'Home',
               'latitude': '51.503396', 'longitude': '-0.127640', 'linenum': '0'}
# Where the B10 told whether it had carried a command out.
RESULT = bridge.CONTROL_PATH + '/query'


class Clock:
    """Seconds that pass only when the code under test waits."""
    def __init__(self):
        self.now = 1000.0
        self.waited = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.waited.append(seconds)
        self.now += seconds


def _api(monkeypatch, answers, *, timeout=30, event='43949800'):
    """The client on one parked car. The command is accepted with `event` and `timeout`; each
    question about it gets the next of `answers` (an exception: raised), the last one repeating."""
    clock = Clock()
    monkeypatch.setattr(bridge, 'time', types.SimpleNamespace(
        time=real_time.time, monotonic=clock.monotonic, sleep=clock.sleep))
    api = object.__new__(bridge.NewAPIClient)
    api._mutex = threading.RLock()
    api.operation_password = '123456'
    api.token = 'token'
    api.login = lambda: None
    api.route = lambda vin: {'appRegion': 'region', 'appCenter': 'center'}
    vehicle = bridge.Vehicle.from_dict({'vin': VIN, 'carType': 'T03', 'abilities': [13, 35, 52]}, False)
    api.get_vehicle_list = lambda: [vehicle]
    api._get_vehicle_raw_status = lambda v: {'data': {'signal': {
        '1': str(int(real_time.time() * 1000)), '1319': '0', '1258': '0'}}}
    api.sent, api.asked = [], []
    left = list(answers)

    def wire(origin, path, body, **kwargs):
        if path == RESULT:
            api.asked.append((origin, body, kwargs.get('method'), clock.now))
            answer = left.pop(0) if len(left) > 1 else left[0]
            if isinstance(answer, Exception):
                raise answer
            return {'code': 0, 'result': 0, 'message': 'Request successful', 'data': answer}, None
        api.sent.append((path, body))
        data = {'eventId': event, 'timeout': timeout} if event else None
        return {'code': 0}, Response(200, json.dumps({'code': 0, 'data': data}).encode())

    api._wire = wire
    monkeypatch.setattr(bridge, 'encrypt_operate_password', lambda pw, token: 'encrypted')
    api.clock = clock
    return api


def _send_plan(api):
    return api._remote_control_raw(vin=VIN, cmd_id='190', cmd_content=json.dumps(PLAN),
                                   action_label='set_charge_schedule')


def test_a_charge_plan_waits_for_the_cars_yes(monkeypatch):
    api = _api(monkeypatch, [0, 0, 1])
    _send_plan(api)
    assert api.last_new_command_receipt.outcome == 'confirmed'
    assert [(origin, body, method) for origin, body, method, _ in api.asked] == \
        [('region', {'msgID': '43949800'}, 'GET')] * 3, 'asked by its eventId, where it was sent'
    assert [at - 1000.0 for *_, at in api.asked] == [0.0, 1.0, 2.0], 'once a second'


def test_no_yes_in_the_clouds_time_is_said(monkeypatch):
    api = _api(monkeypatch, [0], timeout=30)
    _send_plan(api)
    assert api.last_new_command_receipt.outcome == 'unconfirmed'
    assert api.clock.now - 1000.0 < 30, 'not a second past the time the cloud gave the car'
    assert len(api.asked) == 30


def test_an_awake_car_is_given_the_five_seconds_the_cloud_says(monkeypatch):
    api = _api(monkeypatch, [0], timeout=5)
    _send_plan(api)
    assert api.last_new_command_receipt.outcome == 'unconfirmed'
    assert len(api.asked) == 5


def test_a_question_that_fails_leaves_the_answer_open(monkeypatch):
    api = _api(monkeypatch, [bridge.LeapmotorApiError('Read timed out'), 1])
    _send_plan(api)
    assert api.last_new_command_receipt.outcome == 'confirmed'
    assert len(api.asked) == 2


def test_a_physical_command_keeps_what_it_had(monkeypatch):
    api = _api(monkeypatch, [1])
    api._remote_control_raw(vin=VIN, cmd_id='240', cmd_content='{"value":"10"}',
                            action_label='open_sunshade')
    assert api.last_new_command_receipt.outcome == 'accepted'
    assert api.asked == [], 'the page reads the car signals after a physical command'


def test_an_answer_with_nothing_to_follow_is_not_asked_about(monkeypatch):
    api = _api(monkeypatch, [1], event=None)
    _send_plan(api)
    assert api.last_new_command_receipt.outcome == 'accepted_untracked'
    assert api.asked == []


def test_a_destination_waits_too(monkeypatch):
    api = _api(monkeypatch, [0, 1])
    api._remote_control_raw(vin=VIN, cmd_id='180', cmd_content=json.dumps(DESTINATION),
                            action_label='send_destination')
    assert api.last_new_command_receipt.outcome == 'confirmed'


def test_the_log_says_what_was_sent_and_what_the_cloud_said(monkeypatch, caplog):
    api = _api(monkeypatch, [1])
    with caplog.at_level(logging.INFO, logger='mate.cloud'):
        _send_plan(api)
    text = caplog.text
    assert 'Command set_charge_schedule (190) sent:' in text
    assert '"chargeEnable":1' in text and '"starttime":"23:30"' in text and '"chargesoc":100' in text
    assert 'accepted by the cloud, which waits 30 s for the car' in text
    assert 'the car carried it out' in text
    assert VIN not in text


def test_a_place_stays_out_of_the_log(monkeypatch, caplog):
    api = _api(monkeypatch, [1])
    with caplog.at_level(logging.INFO, logger='mate.cloud'):
        api._remote_control_raw(vin=VIN, cmd_id='180', cmd_content=json.dumps(DESTINATION),
                                action_label='send_destination')
    text = caplog.text
    assert 'Command send_destination (180) sent:' in text
    for private in ('Downing', 'Home', '51.503', '-0.127'):
        assert private not in text, private


def test_a_refused_command_logs_the_clouds_code(monkeypatch, caplog):
    api = _api(monkeypatch, [1])

    def refused(origin, path, body, **kwargs):
        if path != bridge.CONTROL_PATH:
            return {'code': 0}, None                # the PIN check passes
        error = bridge.LeapmotorApiError('New API rejected request: HTTP 200, code [40, 40]')
        error.api_codes = (40, 40)
        raise error
    api._wire = refused
    with caplog.at_level(logging.INFO, logger='mate.cloud'), pytest.raises(bridge.LeapmotorApiError):
        _send_plan(api)
    assert 'Command set_charge_schedule (190) sent:' in caplog.text
    assert 'refused by the cloud (code 40); not retried' in caplog.text


@pytest.mark.parametrize('outcome, ok, logged', [
    ('confirmed', True, 'confirmed'),
    ('unconfirmed', False, 'timeout_car'),
    ('accepted', True, 'accepted_unconfirmed'),
])
def test_the_page_tells_the_cars_yes_from_its_silence(monkeypatch, outcome, ok, logged):
    """The car's yes is a done; its silence, the amber «the car did not confirm in time» the page
    already had for the earlier library — not «Schedule saved»."""
    api = types.SimpleNamespace(last_new_command_receipt=None)
    sess = cc.LeapmotorSession()
    sess._api = api
    sess._vehicle = types.SimpleNamespace(vin='SYNTHETIC')
    monkeypatch.setattr(sess, '_connect', lambda: None)
    monkeypatch.setattr(sess, '_use_pin_of', lambda vin: None)

    def action(client, vin):
        client.last_new_command_receipt = types.SimpleNamespace(outcome=outcome)
    answer, message = sess.execute(action)
    assert answer is ok
    assert cc._classify_outcome(answer, message) == logged


@pytest.mark.parametrize('ok, message, shown, kept', [
    (True, 'The car carried it out', '✓ Schedule saved', True),
    (False, 'Remote control result unconfirmed; not retried', '⏱️', False),
])
def test_the_save_button_says_what_the_car_said(tmp_path, monkeypatch, ok, message, shown, kept):
    """What the Save button shows. When the car stays silent the page shows the amber notice the
    earlier library had, and the Overview's copy of the window keeps the old times: those times went
    out, but the car never said it took them."""
    pytest.importorskip('httpx', reason='Starlette TestClient needs httpx')
    from starlette.testclient import TestClient
    import db as D
    import db_reader
    import i18n
    import main

    path = str(tmp_path / 't.db')
    D.Database(path).close()
    con = sqlite3.connect(path)
    con.execute("INSERT INTO vehicles (id, vin, car_type, abilities) VALUES (1, 'VINTEST', 'B10', ?)",
                (json.dumps([10, 11, 25, 26, 35, 47, 51, 53]),))
    for key, value in (('setup_complete', '1'), ('language', 'en'), ('timezone', 'UTC'),
                       ('charge_sched_start', '01:50')):
        con.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, value))
    con.commit()
    con.close()
    monkeypatch.setattr(db_reader, 'DB_PATH', path)
    monkeypatch.setattr(db_reader, '_current_vehicle_id', lambda: 1)
    monkeypatch.setattr(cc, 'save_charge_schedule', lambda **kw: (ok, message))
    page = TestClient(main.app).post('/api/charge-schedule', data={
        'enabled': '1', 'start_time': '23:30', 'end_time': '08:00', 'soc_limit': '100'}).text
    assert shown in page
    if not ok:
        assert i18n.get_t('en')('cmd_timeout_car') in page
    assert db_reader.get_setting('charge_sched_start') == ('23:30' if kept else '01:50')
