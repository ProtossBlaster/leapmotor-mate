"""Lo storico cloud dei viaggi NON dipende dal client dei comandi.

La qualifica della 4.0.0 parla di comandi — `'Vehicle command compatibility unavailable'` — e
tiene sul client vecchio ogni account che non sia B10 puro. Ma lo storico dei viaggi è una
LETTURA, e il 27/09/2026 è stato misurato che il percorso unificato
`/carownerservice/mileage/daily/detail/page` risponde `result=0` anche firmato dalla libreria
vecchia (183 viaggi, `driveReevOil` compreso). Quindi una C10, una REEV o un T03 possono avere
i viaggi singoli dal cloud **con la sessione che hanno già**, senza spendere un solo login.
"""
import json
import threading

import pytest
import mate_api  # noqa: F401 — mette vendor/ e mate_api_runtime/ in sys.path
from leapmotor_cloud.mate_compat import MateAPIError


def test_the_history_worker_starts_on_an_account_kept_on_the_legacy_client(monkeypatch):
    import history_service
    import history_worker
    monkeypatch.setenv('MATE_API_V2', '0')
    monkeypatch.delenv('MATE_DEMO', raising=False)
    monkeypatch.setattr(history_service, '_thread', None)
    started = threading.Event()
    monkeypatch.setattr(history_worker, 'sync_once', lambda on_login=None: started.set())
    history_service.start_history_worker()
    assert started.wait(5), \
        'un account trattenuto sul client vecchio non scarica nessuno storico cloud'


class _LegacySession:
    """La libreria vecchia: nessun metodo `read`, e una sessione già viva."""
    sign_key = b'k' * 32
    device_id = 'device'
    language = 'en-US'
    account_cert = ('cert.pem', 'key.pem')

    def __init__(self):
        self.calls = []

    def login(self):
        self.calls.append('login')

    def _auth_headers(self):
        return {'token': 'token', 'userId': '1'}

    def _post_json(self, *, path, headers, json_body, cert):
        self.calls.append((path, json_body['pageNum']))
        assert headers.get('token') == 'token', 'la lettura va firmata e autenticata'
        return {'status_code': 200, 'body': json.dumps({'result': 0, 'code': 0, 'data': {
            'pageNum': json_body['pageNum'], 'pageSize': 20, 'total': 1, 'totalPage': 1,
            'list': [{'vin': 'V', 'routeStartTs': 1789000000000, 'routeEndTs': 1789000600000,
                      'totalMileage': 3, 'totalEnergy': 0.6, 'driveReevOil': 1.4}]}})}


def test_the_legacy_session_reads_the_trip_page_without_spending_a_login():
    import history_worker
    api = _LegacySession()
    data = history_worker._reader(api).read('/carownerservice/mileage/daily/detail/page',
                                           dict(vin='V', pageNum=1, pageSize=20,
                                                startTime='0', endTime='1'))['data']
    assert data['total'] == 1
    assert data['list'][0]['driveReevOil'] == 1.4, 'il carburante REEV del viaggio va conservato'
    assert 'login' not in api.calls, \
        'la lettura dello storico non deve spendere un login: il cloud ne concede 5-12 al giorno'


def test_a_refused_page_is_not_taken_for_data():
    import history_worker

    class _Refused(_LegacySession):
        def _post_json(self, **kwargs):
            # Un rifiuto che porta comunque una pagina dall'aspetto valido: se il lettore
            # non guardasse `result`, importerebbe una pagina vuota come se fosse la verità
            # (e il conteggio dei viaggi del mese sparirebbe senza un errore).
            return {'status_code': 200, 'body': json.dumps({
                'result': 40, 'code': 40, 'message': 'No such permission',
                'data': {'pageNum': 1, 'pageSize': 20, 'total': 0, 'totalPage': 0, 'list': []}})}

    # `Exception` non basterebbe: prima che `_reader` esistesse questo test passava
    # sull'AttributeError, cioè affermava il difetto. → feedback-a-green-test-can-assert-the-bug
    with pytest.raises(MateAPIError):
        history_worker._reader(_Refused()).read('/carownerservice/mileage/daily/detail/page',
                                               dict(vin='V', pageNum=1, pageSize=20,
                                                    startTime='0', endTime='1'))
