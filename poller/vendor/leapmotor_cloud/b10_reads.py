"""Opt-in B10 reads using the verified v2 session and explicit regional routes."""
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlencode

from .errors import ValidationError
from .session import SessionStore
from .signing import sign_authenticated
from .transport import Request, validate_url, MAX_RESPONSE_BYTES
from .vehicle_data import normalize_configuration, normalize_telemetry

HOSTS = frozenset({'appgateway.leapmotor-international.de'})
SIGNALS = '/app/app-signal-service/signal/info/query'
CONFIG = '/carownerservice/v3/api/vehicleinfo/commonConfig'


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError('Duplicate response key')
        result[key] = value
    return result


@dataclass(frozen=True)
class VehicleReadBundle:
    telemetry: object
    configuration: object


class B10ReadClient:
    def __init__(self, transport, sessions, *, center, region, clock=None):
        if not isinstance(sessions, SessionStore):
            raise ValidationError('Expected session store')
        self.center = validate_url(center, HOSTS, origin=True)
        self.region = validate_url(region, HOSTS, origin=True)
        self.transport, self.sessions = transport, sessions
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _read(self, vin, *, configuration):
        if not isinstance(vin, str) or not vin or len(vin) > 64 or not vin.isascii() or not vin.isalnum():
            raise ValidationError('Invalid vehicle identifier')
        now = self.clock()
        session = self.sessions.get(now)
        params = {'vin': vin}
        if configuration:
            params.update(osType='Android', appVersion='V1.16.4-1')
        core = dict(source='leapmotor', channel='1', acceptLanguage='en-US',
                    version='V1.16.4-1', deviceType='android',
                    nonce=str(secrets.randbelow(90000)+10000)+'.0',
                    timestamp=str(int(now.timestamp()*1000)), deviceId=session.device_id)
        headers = dict(core, sign=sign_authenticated(session.key, core, params),
                       token=session.token, userId=session.user_id, carvin=vin, cartype='B10')
        headers.update({'Content-Type':'application/json','x-region':'EU',
                        'x-api-signature-version':'2.0','X-P12_ENC_ALG':'1'})
        if configuration:
            request = Request('GET', self.center+CONFIG+'?'+urlencode(params), headers)
        else:
            request = Request('POST', self.region+SIGNALS, headers,
                              json.dumps(params,separators=(',',':')).encode())
        response = self.transport.send(request, client_cert=session.client_cert)
        if response.status != 200 or len(response.body) > MAX_RESPONSE_BYTES:
            raise ValidationError('Vehicle read failed')
        try:
            data = json.loads(response.body, object_pairs_hook=_unique)
        except (ValueError, UnicodeError, RecursionError):
            raise ValidationError('Invalid vehicle read response') from None
        if not isinstance(data, dict) or 'data' not in data:
            raise ValidationError('Expected cloud response envelope')
        normalizer = normalize_configuration if configuration else normalize_telemetry
        return normalizer(data, expected_vin=vin, received_at=self.clock())

    def telemetry(self, vin):
        return self._read(vin, configuration=False)

    def configuration(self, vin):
        return self._read(vin, configuration=True)

    def read(self, vin):
        # Separate source timestamps; this is not an atomic vehicle snapshot.
        return VehicleReadBundle(self.telemetry(vin), self.configuration(vin))
