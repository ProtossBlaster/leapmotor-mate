"""Single-attempt login with explicit TLS material; no legacy SDK or persistence.

Application provisioning and account-PKCS12 decoding are caller responsibilities.
A coordinator must serialize logins across processes sharing the same account.
"""
import json
from datetime import timedelta
from threading import Lock
from .cloud import GLOBAL_ORIGIN, _unique_object, _invalid_constant
from .certificate_validation import certificate_usable
from .errors import ValidationError
from .models import require_aware
from .session import CloudSession, _expiry_from_token
from .signing import sign_authenticated, sign_login
from .transport import Request, Response, MAX_RESPONSE_BYTES, validate_cert_paths

LOGIN_PATH = '/base/base-user/account/v1/login'
REFRESH_PATH = '/base/base-user/token/v1/refresh'
DEFAULT_SESSION_SECONDS = 1800
MAX_STATED_SECONDS = 30 * 24 * 3600


def _stated_seconds(value):
    """A lifetime the cloud states, in seconds, or None when it states none.

    Measured on 27/09/2026: a login answers `tokenExpireTime` 7200 and
    `refreshTokenExpireTime` 604799. Anything that is not a plain positive number within a
    month is refused rather than guessed at — a session must not outlive its token because a
    field arrived malformed.
    """
    if value is None:
        return None
    if type(value) is not int or not 0 < value <= MAX_STATED_SECONDS:
        raise ValidationError('Invalid stated lifetime')
    return value


class LoginUnavailable(RuntimeError):
    def __init__(self, stage='unknown', http_status=None, api_code=None):
        allowed={'unknown','cooldown','application_certificate','transport','response',
                 'cloud_rejection','session_metadata','account_certificate','session_validation'}
        self.stage=stage if stage in allowed else 'unknown'
        self.http_status=http_status if type(http_status) is int and 100<=http_status<=599 else None
        self.api_code=api_code if type(api_code) is int and abs(api_code)<=10**12 else None
        super().__init__('Cloud login unavailable; no automatic retry')


class LoginClient:
    def __init__(self, transport, *, application_cert, account_certificate_provider,
                 clock, nonce_factory, language='en-US'):
        validate_cert_paths(application_cert)
        if not all(callable(x) for x in (account_certificate_provider, clock, nonce_factory)):
            raise ValidationError('Explicit certificate provider, clock and nonce required')
        self._transport, self._application_cert = transport, application_cert
        self._provider, self._clock, self._nonce = account_certificate_provider, clock, nonce_factory
        self._language, self._lock = language, Lock()

    def refresh(self, session, *, device_id):
        """Renew a session from its refresh token: a new access token without a login.

        Measured against the live cloud on 27/09/2026 — `POST /base/base-user/token/v1/refresh`
        with `{"refreshToken": …}` answers `code 0` and a whole new session: access token,
        refresh token and the signing parameters with it. The account certificate is NOT
        re-issued, so the session keeps the pair it already holds and the material provider is
        never called. A refresh token the cloud will not take comes back `302010219 Token
        refresh error`, which surfaces as LoginUnavailable rather than as a session quietly
        left as it was.

        The caller decides WHEN: this asks once and never retries, exactly like `login`.
        """
        if not isinstance(session, CloudSession):
            raise ValidationError('Expected CloudSession')
        if not isinstance(device_id, str) or not device_id or len(device_id) > 16384:
            raise ValidationError('Missing or invalid device identifier')
        with self._lock:
            now = self._clock(); require_aware(now)
            if not session.renewable(now):
                # Nothing to send. A login is the caller's answer here, not a request the cloud
                # is certain to refuse.
                raise ValidationError('Session carries no usable refresh token')
            nonce = self._nonce()
            if not isinstance(nonce, str) or not nonce.isascii() or not nonce.isdecimal() or len(nonce) > 32:
                raise ValidationError('Invalid login nonce')
            body = {'refreshToken': session.refresh_token}
            core = dict(source='leapmotor', channel='1', acceptLanguage=self._language,
                        version='V1.16.4-1', deviceType='android', nonce=nonce,
                        timestamp=str(int(now.timestamp() * 1000)), deviceId=session.device_id)
            headers = dict(core, sign=sign_authenticated(session.key, core, body), carvin='', cartype='')
            headers.update({'token': session.token, 'userId': session.user_id,
                            'Content-Type': 'application/json', 'x-region': 'EU',
                            'x-api-signature-version': '2.0', 'X-P12_ENC_ALG': '1'})
            request = Request('POST', GLOBAL_ORIGIN + REFRESH_PATH, headers,
                              json.dumps(body, separators=(',', ':')).encode())
            stage = 'transport'; http_status = None; api_code = None
            try:
                response = self._transport.send(request, client_cert=session.client_cert)
                stage = 'response'
                if isinstance(response, Response): http_status = response.status
                if (not isinstance(response, Response) or response.status != 200
                        or len(response.body) > MAX_RESPONSE_BYTES):
                    raise ValueError()
                envelope = json.loads(response.body.decode(), object_pairs_hook=_unique_object,
                                      parse_constant=_invalid_constant)
                if not isinstance(envelope, dict): raise ValueError()
                codes = [envelope[k] for k in ('code', 'result') if k in envelope]
                if len(codes) == 1 or len(codes) == 2 and codes[0] == codes[1]:
                    value = codes[0]
                    if type(value) is int: api_code = value
                    elif isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) <= 12:
                        api_code = int(value)
                stage = 'cloud_rejection'
                if not codes or any(type(c) is bool or str(c) != '0' for c in codes): raise ValueError()
                stage = 'session_metadata'
                data = envelope.get('data')
                if not isinstance(data, dict): raise ValueError()
                token = data.get('accessToken')
                if not isinstance(token, str) or not token: raise ValueError()
                observed = self._clock(); require_aware(observed)
                stated = _stated_seconds(data.get('tokenExpireTime'))
                bound = observed + timedelta(seconds=stated if stated else DEFAULT_SESSION_SECONDS)
                expiry = _expiry_from_token(token)
                expiry = min(bound, expiry) if expiry else bound
                if expiry <= observed: raise ValueError()
                renewed_for = _stated_seconds(data.get('refreshTokenExpireTime'))
                renewed_until = observed + timedelta(seconds=renewed_for) if renewed_for else None
                refresh_token = data.get('refreshToken')
                if refresh_token is not None and (not isinstance(refresh_token, str) or not refresh_token
                                                  or len(refresh_token) > 16384):
                    raise ValueError()
                stage = 'session_validation'
                renewed = CloudSession.from_login_data(
                    dict(data, token=token), device_id=device_id, client_cert=session.client_cert,
                    expires_at=expiry, refresh_token=refresh_token, refresh_expires_at=renewed_until)
                renewed.ensure_valid(observed)
                return renewed
            except Exception:
                raise LoginUnavailable(stage, http_status, api_code) from None

    def login(self, username, password, *, device_id):
        for value in (username,password,device_id):
            if not isinstance(value,str) or not value or len(value)>16384:
                raise ValidationError('Missing or invalid login input')
        with self._lock:
            now=self._clock();require_aware(now)
            if not certificate_usable(*self._application_cert,now=now):
                raise LoginUnavailable('application_certificate')
            nonce=self._nonce()
            if not isinstance(nonce,str) or not nonce.isascii() or not nonce.isdecimal() or len(nonce)>32:
                raise ValidationError('Invalid login nonce')
            body=dict(identifier=username,identifierType='2',security=password)
            core=dict(source='leapmotor',channel='1',acceptLanguage=self._language,
                      version='V1.16.4-1',deviceType='android',nonce=nonce,
                      timestamp=str(int(now.timestamp()*1000)),deviceId=device_id)
            headers=dict(core,sign=sign_login(core,body),carvin='',cartype='')
            headers.update({'Content-Type':'application/json','x-region':'EU',
                            'x-api-signature-version':'2.0','X-P12_ENC_ALG':'1'})
            request=Request('POST',GLOBAL_ORIGIN+LOGIN_PATH,headers,
                            json.dumps(body,separators=(',',':')).encode())
            stage='transport';http_status=None;api_code=None
            try:
                response=self._transport.send(request,client_cert=self._application_cert)
                stage='response'
                if isinstance(response,Response):http_status=response.status
                if not isinstance(response,Response) or response.status!=200 or len(response.body)>MAX_RESPONSE_BYTES:
                    raise ValueError()
                envelope=json.loads(response.body.decode(),object_pairs_hook=_unique_object,parse_constant=_invalid_constant)
                if not isinstance(envelope,dict):raise ValueError()
                codes=[envelope[k] for k in ('code','result') if k in envelope]
                if len(codes)==1 or len(codes)==2 and codes[0]==codes[1]:
                    value=codes[0]
                    if type(value) is int:api_code=value
                    elif isinstance(value,str) and value.isascii() and value.isdecimal() and len(value)<=12:api_code=int(value)
                stage='cloud_rejection'
                if not codes or any(type(c)is bool or str(c)!='0' for c in codes):raise ValueError()
                stage='session_metadata'
                data=envelope.get('data')
                if not isinstance(data,dict):raise ValueError()
                token=data.get('accessToken')
                if not isinstance(token,str) or not token:raise ValueError()
                observed=self._clock();require_aware(observed)
                # The cloud states how long it means the token to live; until it was measured
                # (7200 s) this client capped every session at half an hour, which is why an
                # installation spent a login every thirty minutes. Where nothing is stated the
                # old bound stands, because then nothing has told us better.
                stated=_stated_seconds(data.get('tokenExpireTime'))
                bound=observed+timedelta(seconds=stated if stated else DEFAULT_SESSION_SECONDS)
                expiry=_expiry_from_token(token)
                expiry=min(bound,expiry) if expiry else bound
                refresh_expiry=_stated_seconds(data.get('refreshTokenExpireTime'))
                refresh_expiry=observed+timedelta(seconds=refresh_expiry) if refresh_expiry else None
                if expiry<=observed:raise ValueError()
                # Validate token/signing structure before invoking the material provider.
                normalized=dict(data,token=token)
                refresh_token=data.get('refreshToken')
                if refresh_token is not None and (not isinstance(refresh_token,str) or not refresh_token
                                                  or len(refresh_token)>16384):
                    raise ValueError()
                CloudSession.from_login_data(normalized,device_id=device_id,
                                             client_cert=self._application_cert,expires_at=expiry,
                                             refresh_token=refresh_token,refresh_expires_at=refresh_expiry)
                stage='account_certificate'
                pair=self._provider(data)
                validate_cert_paths(pair)
                finished=self._clock();require_aware(finished)
                if not certificate_usable(*pair,now=finished):raise ValueError()
                stage='session_validation'
                session=CloudSession.from_login_data(normalized,device_id=device_id,
                                                    client_cert=pair,expires_at=expiry,
                                                    refresh_token=refresh_token,refresh_expires_at=refresh_expiry)
                session.ensure_valid(finished)
                return session
            except Exception:
                raise LoginUnavailable(stage,http_status,api_code) from None
