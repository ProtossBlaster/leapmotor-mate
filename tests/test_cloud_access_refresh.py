"""Offline regression coverage for renewed access hints and charge read contracts."""
import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest
import mate_api  # configure the pinned independent runtime
import api_v2_bridge as bridge
from command_contracts import charge
from ui_command_access import allowed, snapshot_key

VIN = 'LFZB10TEST00000001'


def client():
    api = object.__new__(bridge.NewAPIClient)
    api._mutex = threading.RLock()
    api._access_refresh_attempt = None
    api.username = 'synthetic@example.invalid'
    api._ensure_token = lambda: None
    api.route = lambda vin: {'appRegion': 'region', 'appCenter': 'center'}
    return api


def test_healthy_telemetry_renews_access_before_expiry(monkeypatch, tmp_path):
    db = tmp_path / 'test.db'
    monkeypatch.setattr(bridge, 'DB', str(db))
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)')
    clock = [1000.0]
    monkeypatch.setattr(bridge.time, 'time', lambda: clock[0])
    monkeypatch.setattr(bridge.time, 'monotonic', lambda: clock[0])
    api = client()
    lists = []
    def read(path, body, **kwargs):
        if path.endswith('/list'):
            lists.append(clock[0])
            return {'data': {'bindcars': [{'vin': VIN, 'carType': 'B10', 'abilities': [10]}]}}
        if path.endswith('/query'):
            return {'data': {'vin': VIN, 'signalMap': {'1': 1}}}
        return {'data': {'vin': VIN, 'config': {}}}
    api.read = read
    vehicle = SimpleNamespace(vin=VIN)
    for clock[0] in (1000.0, 1100.0, 1240.0, 1320.0):
        assert api._get_vehicle_raw_status(vehicle)['data']['vin'] == VIN
    assert lists == [1000.0, 1240.0]
    with sqlite3.connect(db) as conn:
        snapshot = json.loads(bridge.setting(conn, snapshot_key(VIN)))
    assert allowed(snapshot, api.username, VIN, 'lock', now=1320)
    assert not allowed(snapshot, api.username, VIN, 'lock', now=1541)


def test_failed_access_refresh_is_throttled_without_stopping_telemetry(monkeypatch):
    api = client()
    clock = [1000.0]
    monkeypatch.setattr(bridge.time, 'monotonic', lambda: clock[0])
    attempts = []
    def failed():
        attempts.append(clock[0])
        raise RuntimeError('synthetic unavailable')
    api.get_vehicle_list = failed
    api.read = lambda path, body, **kwargs: {'data': {'vin': VIN, 'signalMap': {}, 'config': {}}}
    for clock[0] in (1000.0, 1001.0, 1239.0, 1240.0):
        assert api._get_vehicle_raw_status(SimpleNamespace(vin=VIN))['data']['vin'] == VIN
    assert attempts == [1000.0, 1240.0]


def configuration(**changes):
    config = dict(isEnable='1', percent='80', beginTime='22:00', endTime='06:00',
                  cycles='1,0,1,0,1,0,1', circulation='1', recharge='0')
    config.update(changes)
    return config


def test_charge_limit_normalizes_cloud_strings_and_preserves_plan():
    api = client()
    api.read = lambda *args, **kwargs: {'data': {'vin': VIN, 'config': {'3': configuration()}}}
    sent = []
    api._remote_control_raw = lambda **kwargs: sent.append(kwargs)
    api.set_charge_limit(VIN, 90)
    state = json.loads(sent[0]['cmd_content'])
    assert state == dict(chargeEnable=1, chargesoc=90, starttime='22:00', endtime='06:00',
                         cycles='1,0,1,0,1,0,1', circulation=1, recharge=0)
    charge(state)


@pytest.mark.parametrize('changes', [{'isEnable': True}, {'percent': '80.5'}, {'recharge': None}])
def test_invalid_charge_state_never_sends(changes):
    api = client()
    api.read = lambda *args, **kwargs: {'data': {'vin': VIN, 'config': {'3': configuration(**changes)}}}
    api._remote_control_raw = lambda **kwargs: pytest.fail('invalid configuration must not send')
    with pytest.raises(bridge.LeapmotorApiError):
        api.set_charge_limit(VIN, 90)


def test_charge_configuration_requires_matching_vin():
    api = client()
    api.read = lambda *args, **kwargs: {'data': {'vin': 'OTHER', 'config': {'3': configuration()}}}
    with pytest.raises(bridge.LeapmotorApiError, match='mismatch'):
        api._get_charge_appointment(VIN)


@pytest.mark.parametrize("production_profile", [False, True])
def test_mqtt_discovery_recovers_revokes_and_ignores_timestamp_only_refresh(monkeypatch, production_profile):
    import mqtt
    if production_profile:
        import importlib.util
        from pathlib import Path
        path = Path(__file__).resolve().parents[1] / "poller" / "capability_profile.py"
        spec = importlib.util.spec_from_file_location("production_poller_capabilities", path)
        profile = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(profile)
        monkeypatch.setattr(mqtt, "capability_profile", profile)
    import crypto
    import ui_command_access
    from ui_command_access import account_hash
    monkeypatch.setenv('MATE_API_V2', '1')
    monkeypatch.setattr(crypto, 'decrypt', lambda value: value)
    clock = [1000.0]
    monkeypatch.setattr(ui_command_access.time, 'time', lambda: clock[0])
    settings = {'leapmotor_user': 'synthetic@example.invalid'}
    class FakeBroker:
        def __init__(self):
            self.messages = []
        def is_connected(self):
            return True
        def publish(self, topic, payload, retain=False):
            self.messages.append((topic, payload, retain))
    service = mqtt.MqttService('unused', 1883, get_setting=lambda key, default='': settings.get(key, default))
    service.client = FakeBroker()
    service._publish_sensors = lambda data: None
    data = SimpleNamespace(vin=VIN, climate_on=False)
    topic = f'homeassistant/button/leapmotor_mate_{VIN.lower()}/climate_auto/config'
    lock_topic = f'homeassistant/lock/leapmotor_mate_{VIN.lower()}/door_lock/config'
    schedule_topic = f'homeassistant/text/leapmotor_mate_{VIN.lower()}/charge_schedule/config'
    def latest(target):
        return [payload for name, payload, retained in service.client.messages if name == target][-1]
    def snapshot(at, abilities):
        return json.dumps(dict(account=account_hash(settings['leapmotor_user']), at=at, shared=False,
                               vehicle=dict(vin=VIN, carType='B10', abilities=abilities)))
    service.publish_status(data)
    assert latest(topic) == latest(lock_topic) == latest(schedule_topic) == ''
    settings[snapshot_key(VIN)] = snapshot(1000, [6, 10, 35])
    service.publish_status(data)
    assert json.loads(latest(topic))['payload_press'] == 'climate_auto'
    assert json.loads(latest(lock_topic))['payload_lock'] == 'LOCK'
    assert json.loads(latest(schedule_topic))['command_topic'].endswith('/charge_schedule/set')
    count = len(service.client.messages)
    clock[0] = 1100
    settings[snapshot_key(VIN)] = snapshot(1100, [6, 10, 35])
    service.publish_status(data)
    assert len(service.client.messages) == count
    settings[snapshot_key(VIN)] = snapshot(1100, [10])
    service.publish_status(data)
    assert latest(topic) == latest(schedule_topic) == ''
    assert latest(lock_topic) != ''
    clock[0] = 1401
    service.publish_status(data)
    assert latest(lock_topic) == ''
    settings[snapshot_key(VIN)] = snapshot(1401, [6, 10])
    service.publish_status(data)
    assert latest(topic) != ''
    assert latest(lock_topic) != ''
