"""I comandi V3 valgono per ogni modello, e il permesso lo dice il cloud.

La 4.0.0 aveva aperto il client nuovo solo agli account di sole B10, perché solo sulla B10 i
comandi erano stati provati in auto. Ma `appremotectl` v3 è il percorso unico del cloud
aggiornato e dei client nuovi distribuiti sugli store per tutta la gamma, e la risposta porta
il verdetto del cloud stesso: un comando che quel modello non ha torna con `result: 40`
(无此权限) senza che l'auto si muova.

Quindi il cancello è il dato che il cloud pubblica per quel veicolo — `abilities`, `rightList`,
`moduleRights` — più il suo rifiuto, e non il nome del modello. Resta modellata sul modello solo
la FORMA del payload dove le auto sono state misurate in disaccordo (lo spegnimento del clima).

Cosa NON afferma questo file: che un comando accettato venga eseguito fisicamente. Quello resta
provato solo sulla B10, e il cloud risponde `code: 0` anche a un comando che l'auto ignora.
"""
import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest
import mate_api  # noqa: F401 — mette vendor/ e mate_api_runtime/ in sys.path
import api_v2_bridge as bridge
from leapmotor_cloud.transport import Response
import ui_command_access
from ui_command_access import allowed, snapshot_key

VIN = 'LFZC10TEST00000001'
# Ability e diritto del comando 240 (tendina): namespace diversi, 161 è il diritto.
SUNSHADE, SUNSHADE_RIGHT, SUNSHADE_ABILITY = '240', 161, 13
MODELS = ('B10', 'C10', 'C11', 'T03', 'B05', 'UNRECOGNISED')


def snapshot(*, car_type, abilities=(SUNSHADE_ABILITY,), rights=None, at=1000.0, shared=False,
             user='synthetic@example.invalid'):
    raw = {'vin': VIN, 'carType': car_type, 'abilities': list(abilities)}
    if rights is not None:
        raw['rightList'] = rights
    return {'account': ui_command_access.account_hash(user),
            'at': at, 'vehicle': raw, 'shared': shared}


class TestTheAccountQualifies:
    """La qualifica dice QUALE client, non quali comandi."""

    def _cloud(self, monkeypatch, vehicles, signals=None):
        import automatic_material
        monkeypatch.setattr(automatic_material, 'provision_automatic', lambda *args: None)
        read = []

        class Cloud:
            def __init__(self, **kwargs): pass
            def login(self): pass
            def get_vehicle_list(self): return vehicles
            def _get_vehicle_raw_status(self, vehicle):
                read.append(vehicle.vin)
                return {'data': {'vin': vehicle.vin,
                                 'signal': {'1204': '65'} if signals is None else signals}}
            def close(self): pass
        monkeypatch.setattr(bridge, 'NewAPIClient', Cloud)
        return read

    def test_an_account_without_a_single_b10_qualifies(self, tmp_path, monkeypatch, preflight):
        vehicles = [SimpleNamespace(car_type='C10', vin='fixture-1'),
                    SimpleNamespace(car_type='T03', vin='fixture-2')]
        read = self._cloud(monkeypatch, vehicles)
        result = preflight._qualify_staged()
        assert result['state'] == 'qualified'
        assert sorted(result['capabilities']) == ['C10', 'T03'], \
            'la qualifica deve registrare i modelli che ha visto, non una costante'
        assert read == ['fixture-1', 'fixture-2'], 'ogni veicolo va comunque letto'

    def test_unreadable_telemetry_still_refuses_on_every_model(self, monkeypatch, preflight):
        for model in MODELS:
            self._cloud(monkeypatch, [SimpleNamespace(car_type=model, vin='fixture-1')], signals={})
            with pytest.raises(ValueError):
                preflight._qualify_staged()


class TestTheBridgeSends:
    def _api(self, monkeypatch, car_type):
        api = object.__new__(bridge.NewAPIClient)
        api._mutex = threading.RLock()
        api.operation_password = '123456'
        api.token = 'token'
        api.login = lambda: None
        api.route = lambda vin: {'appRegion': 'region', 'appCenter': 'center'}
        vehicle = bridge.Vehicle.from_dict(
            {'vin': VIN, 'carType': car_type, 'abilities': [SUNSHADE_ABILITY]}, False)
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

    def test_the_bridge_sends_for_every_model(self, monkeypatch):
        for model in MODELS:
            api, sent = self._api(monkeypatch, model)
            api._remote_control_raw(vin=VIN, cmd_id=SUNSHADE, cmd_content='{"value":"10"}',
                                   action_label='open_sunshade')
            command = [body for path, body in sent if 'appremotectl' in path]
            assert command, model
            assert command[0]['cmdid'] == SUNSHADE
            assert json.loads(command[0]['state']) == {'value': '10'}
            assert api.last_new_command_receipt.outcome == 'accepted', model

    def test_a_cloud_refusal_is_reported_with_its_code(self, monkeypatch):
        api, sent = self._api(monkeypatch, 'T03')

        def refused(origin, path, body, **kwargs):
            sent.append((path, body))
            if 'appremotectl' in path:
                # 无此权限: il cloud dice che quell'auto non ha quel comando.
                return {'code': 40}, Response(200, json.dumps(
                    {'code': 40, 'message': 'No such permission'}).encode())
            return {'code': 0}, Response(200, b'{"code":0}')
        api._wire = refused
        api._remote_control_raw(vin=VIN, cmd_id=SUNSHADE, cmd_content='{"value":"10"}',
                               action_label='open_sunshade')
        receipt = api.last_new_command_receipt
        assert receipt.outcome == 'rejected'
        assert str(receipt.api_code) == '40', \
            'il codice del rifiuto è il verdetto del cloud: va conservato, non riassunto'


class TestTheUiGate:
    def test_the_gate_opens_for_every_model(self):
        for model in MODELS:
            assert allowed(snapshot(car_type=model), 'synthetic@example.invalid', VIN,
                           'open_sunshade', now=1000.0), model

    def test_the_gate_still_follows_the_cloud_on_every_model(self):
        for model in MODELS:
            assert not allowed(snapshot(car_type=model, abilities=[]),
                               'synthetic@example.invalid', VIN, 'open_sunshade', now=1000.0), model
            assert not allowed(snapshot(car_type=model, rights=[]),
                               'synthetic@example.invalid', VIN, 'open_sunshade', now=1000.0), model
            assert not allowed(snapshot(car_type=model, shared=True),
                               'synthetic@example.invalid', VIN, 'open_sunshade', now=1000.0), model

    def test_the_sentinel_is_offered_where_its_right_is_declared(self):
        for model in ('B10', 'T03', 'C10'):
            assert allowed(snapshot(car_type=model, rights=[220]), 'synthetic@example.invalid',
                           VIN, 'sentry_on', now=1000.0), model
            assert not allowed(snapshot(car_type=model, rights=[]), 'synthetic@example.invalid',
                               VIN, 'sentry_off', now=1000.0), model


class TestARefusalIsRemembered:
    """«Se non è permesso vuol dire che per quel modello quel comando non è presente.»

    Il rifiuto del cloud è più informativo del suo stesso elenco di diritti: se il cloud dice 40
    su un comando che la sua istantanea dichiarava permesso, quel comando su quell'auto non c'è.
    Va ricordato per VIN, altrimenti l'utente resta con un pulsante che fallisce ogni volta.
    """
    USER = 'synthetic@example.invalid'

    def store(self):
        values = {}
        return values, (lambda key, default='': values.get(key, default)), values.__setitem__

    def refused(self, get_setting):
        return ui_command_access.load_refusals(VIN, self.USER, get_setting=get_setting)

    def test_a_refusal_closes_that_command_for_that_car(self):
        values, get_setting, set_setting = self.store()
        assert allowed(snapshot(car_type='T03'), self.USER, VIN, 'open_sunshade', now=1000.0)
        ui_command_access.remember_refusal(VIN, self.USER, 'open_sunshade', 40,
                                          get_setting=get_setting, set_setting=set_setting)
        assert not allowed(snapshot(car_type='T03'), self.USER, VIN, 'open_sunshade', now=1000.0,
                           refusals=self.refused(get_setting))
        # Un altro comando della stessa auto non viene toccato.
        assert allowed(snapshot(car_type='T03'), self.USER, VIN, 'close_sunshade', now=1000.0,
                       refusals=self.refused(get_setting))

    def test_only_an_explicit_no_permission_is_remembered(self):
        values, get_setting, set_setting = self.store()
        for code in (0, 1, 39, '302010205', None, True):
            ui_command_access.remember_refusal(VIN, self.USER, 'open_sunshade', code,
                                              get_setting=get_setting, set_setting=set_setting)
        assert allowed(snapshot(car_type='T03'), self.USER, VIN, 'open_sunshade', now=1000.0,
                       refusals=self.refused(get_setting)), \
            'solo il 40 dice «questo modello non ha questo comando»: un altro errore no'

    def test_a_change_of_account_forgets_the_refusals(self):
        values, get_setting, set_setting = self.store()
        ui_command_access.remember_refusal(VIN, self.USER, 'open_sunshade', 40,
                                          get_setting=get_setting, set_setting=set_setting)
        assert not allowed(snapshot(car_type='T03'), self.USER, VIN, 'open_sunshade', now=1000.0,
                           refusals=self.refused(get_setting))
        other = ui_command_access.load_refusals(VIN, 'other@example.invalid',
                                               get_setting=get_setting)
        assert allowed(snapshot(car_type='T03', user='other@example.invalid'),
                       'other@example.invalid', VIN, 'open_sunshade',
                       now=1000.0, refusals=other), \
            "i rifiuti sono di quell'abbinamento account-auto: un altro account riparte pulito"


@pytest.fixture
def preflight(tmp_path, monkeypatch):
    import importlib
    module = importlib.import_module('migration_preflight')
    root = tmp_path / 'live'
    root.mkdir()
    db = root / 'mate.db'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT)')
        conn.executemany('INSERT INTO settings VALUES (?,?)',
                         [('leapmotor_user', 'fixture@example.invalid'),
                          ('leapmotor_pass', 'fixture-password')])
    (root / 'secret.key').write_bytes(b'original-key')
    monkeypatch.setenv('DB_PATH', str(db))
    return module


class TestTheRefusalTravelsFromTheCloudToThePage:
    """Il rifiuto deve arrivare fino al pulsante, altrimenti resta un dato che nessuno legge."""

    def test_the_command_layer_exposes_the_refusal_of_the_last_command(self, monkeypatch):
        import command_client
        session = command_client._session
        monkeypatch.setenv('MATE_API_V2', '1')
        monkeypatch.setattr(session, '_connect', lambda: None)
        monkeypatch.setattr(session, '_target', lambda: SimpleNamespace(vin=VIN))
        monkeypatch.setattr(session, '_use_pin_of', lambda vin: None)
        session._api = SimpleNamespace(last_new_command_receipt=None)

        def refused(api, vin):
            api.last_new_command_receipt = SimpleNamespace(outcome='rejected', api_code=40)
        ok, _ = session._execute_inner(refused)
        assert not ok
        assert command_client.last_cloud_refusal() == (VIN, 40)

        def accepted(api, vin):
            api.last_new_command_receipt = SimpleNamespace(outcome='accepted', api_code=0)
        ok, _ = session._execute_inner(accepted)
        assert ok
        assert command_client.last_cloud_refusal() is None, \
            'un comando accettato non deve lasciare in giro il rifiuto del precedente'


@pytest.mark.usefixtures('web')
class TestThePageStopsOfferingARefusedCommand:
    def test_one_refusal_removes_the_button(self, web, monkeypatch):
        import main
        import command_client
        import db_reader
        monkeypatch.setenv('MATE_API_V2', '1')
        monkeypatch.setitem(main._COMMANDS, 'open_sunshade', lambda: (False, 'refused'))
        monkeypatch.setattr(command_client, 'last_cloud_refusal',
                            lambda: (web['vin'], 40))
        first = web['client'].post('/api/command/open_sunshade')
        assert first.status_code == 200
        assert db_reader.get_setting('api_v2_refused_' + web['vin'].lower(), ''), \
            'il rifiuto del cloud va registrato per quell auto'
        # La seconda pressione non arriva più al cloud: il comando non c e su quel modello.
        monkeypatch.setitem(main._COMMANDS, 'open_sunshade',
                            lambda: pytest.fail('un comando rifiutato dal cloud non va rimandato'))
        second = web['client'].post('/api/command/open_sunshade')
        assert second.status_code == 400
        assert 'unsupported' in second.text or 'supporta' in second.text.lower() \
            or 'support' in second.text.lower()


@pytest.fixture
def web(tmp_path, monkeypatch):
    pytest.importorskip('fastapi', reason='web/main.py needs fastapi')
    pytest.importorskip('httpx', reason='Starlette TestClient needs httpx')
    import db as D
    import db_reader
    import main
    from starlette.testclient import TestClient
    from cloud_access_fixture import settings
    D.Database(str(tmp_path / 't.db')).ensure_vehicle(VIN, 'T03', 2025)
    monkeypatch.setattr(db_reader, 'DB_PATH', str(tmp_path / 't.db'))
    db_reader.set_setting('setup_complete', '1')
    access = settings(VIN, car_type='T03')
    for key in ('leapmotor_user', snapshot_key(VIN)):
        db_reader.set_setting(key, access(key))
    monkeypatch.setattr(main, '_post_command_refresh', lambda *a, **k: None)
    monkeypatch.setattr(main, '_last_command_at', 0.0)
    return {'client': TestClient(main.app), 'vin': VIN}


class TestWhatWeShowKeepsWhatWeMeasured:
    """Aprire l'invio non apre l'interfaccia a un pulsante che non può funzionare.

    Il cloud dice cosa è PERMESSO; quello che MOSTRIAMO tiene conto anche di ciò che è stato
    misurato in auto. La T03 europea dichiara STEERING_WHEEL (codice 15) e sedili riscaldati che
    non ha (#144): con il solo dato del cloud le comparirebbero pulsanti che non possono agire.
    Per questo `MODEL_ABSENT` resta davanti al cancello del cloud — e vale su tutte e due le
    superfici, perché la pagina e Home Assistant non devono dire cose diverse sulla stessa auto.
    """
    # `prepare_car` è il suo stesso nome di funzione in MODEL_ABSENT: non passa da
    # COMMAND_FEATURE, e un cancello che guarda solo quella mappa lo lascerebbe passare.
    ABSENT = ('steering_heat_on', 'steering_heat_off', 'seat_heat_driver_on', 'seat_vent_driver_on',
              'prepare_car')
    # La T03 dichiara 15 (STEERING_WHEEL) e 21 (sedili) che non ha, e NON dichiara AC_ON (6)
    # pur raffreddando (#67): entrambe le bugie, nello stesso elenco.
    DECLARED = [15, 21, 42, 10, SUNSHADE_ABILITY]

    def access(self):
        from cloud_access_fixture import settings
        return settings(VIN, abilities=self.DECLARED, car_type='T03')

    def _web_shows(self, monkeypatch):
        """La pagina passa da capability_profile.command_shown."""
        import importlib.util
        from pathlib import Path as P
        path = P(__file__).resolve().parents[1] / 'web' / 'capability_profile.py'
        spec = importlib.util.spec_from_file_location('capability_profile_web', path)
        cp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cp)
        access = self.access()
        return [c for c in self.CHECKED
                if cp.command_shown(VIN, c, access, car_type='T03')]

    def _home_assistant_shows(self):
        """Home Assistant passa da mqtt._command_visible: è quello il suo imbuto, non
        command_shown — che nel poller non ha nemmeno il ramo del client nuovo."""
        import mqtt
        service = mqtt.MqttService('broker', 1883, get_setting=self.access(),
                                   abilities=self.DECLARED, car_type='T03')
        return [c for c in self.CHECKED if service._command_visible(VIN, c)]

    CHECKED = ('lock', 'open_sunshade', 'ac_off', 'sentry_on', 'steering_heat_on',
               'steering_heat_off', 'seat_heat_driver_on', 'seat_vent_driver_on', 'prepare_car')

    def test_a_t03_keeps_hidden_what_it_has_no_hardware_for(self, monkeypatch):
        pytest.importorskip('paho.mqtt.client', reason='il ponte MQTT del poller vuole paho')
        monkeypatch.setenv('MATE_API_V2', '1')
        monkeypatch.delenv('MATE_DEMO', raising=False)
        page = self._web_shows(monkeypatch)
        home_assistant = self._home_assistant_shows()
        assert page == home_assistant, \
            'la pagina e Home Assistant non devono dire cose diverse sulla stessa auto'
        for command in self.ABSENT:
            assert command not in page, command
        # Ciò che la T03 ha davvero resta — clima compreso, che non dichiara (#67).
        for command in ('lock', 'open_sunshade', 'ac_off'):
            assert command in page, command

    def test_the_css_that_hides_controls_uses_the_same_rule_as_the_page(self, monkeypatch):
        """La pagina nasconde i comandi con un CSS iniettato: se quello guardasse solo il cloud,
        la T03 vedrebbe comparire i pulsanti che `command_shown` dichiara nascosti."""
        import importlib.util
        from pathlib import Path as P
        monkeypatch.setenv('MATE_API_V2', '1')
        monkeypatch.delenv('MATE_DEMO', raising=False)
        path = P(__file__).resolve().parents[1] / 'web' / 'capability_profile.py'
        spec = importlib.util.spec_from_file_location('capability_profile_css', path)
        cp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cp)
        access = self.access()
        css = ui_command_access.hidden_controls_css(
            VIN, access, shown=lambda command: cp.command_shown(VIN, command, access, car_type='T03'))
        for command in self.ABSENT:
            assert '"api/command/' + command + '"' in css, command
        assert '"api/command/open_sunshade"' not in css
