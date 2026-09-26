"""Explicitly authorized, single-attempt B10 commands. Not wired into Mate yet.

Requires a NEW-protocol session; never logs in or refreshes credentials.
An accepted response is not physical confirmation. Ambiguous outcomes must not
be retried automatically. PIN encryption is supplied by the authenticated caller.
"""
import json
from dataclasses import dataclass, field
from urllib.parse import urlencode

from .b10_planner import plan_b10
from .cloud import _unique_object, _invalid_constant, _code
from .errors import ValidationError
from .models import Availability
from .signing import sign_authenticated
from .transport import Request, Response, MAX_RESPONSE_BYTES, validate_url, TransportError

HOSTS = frozenset({'appgateway.leapmotor-international.de'})
COMMAND_PATH = '/app/app-control-service/v3/api/appremotectl'


@dataclass(frozen=True, slots=True)
class CommandReceipt:
    outcome: str
    event_id: str | None = field(default=None, repr=False)
    api_code: int | None = None
    timeout_seconds: int | None = None
    physical_execution_confirmed: bool = field(default=False, init=False)
    automatic_retry_allowed: bool = field(default=False, init=False)


def interpret(response):
    if not isinstance(response, Response) or response.status != 200:
        return CommandReceipt('unknown')
    if len(response.body) > MAX_RESPONSE_BYTES:
        return CommandReceipt('unknown')
    try:
        envelope = json.loads(response.body.decode('utf-8'), object_pairs_hook=_unique_object,
                              parse_constant=_invalid_constant)
        if not isinstance(envelope, dict):
            return CommandReceipt('unknown')
        codes = [_code(envelope[k]) for k in ('code', 'result') if k in envelope]
        if not codes:
            return CommandReceipt('unknown')
        if len(set(codes)) != 1:
            return CommandReceipt('unknown')
        if codes[0] != 0:
            return CommandReceipt('rejected', api_code=codes[0])
        data = envelope.get('data')
        if not isinstance(data, dict):
            return CommandReceipt('accepted_untracked', api_code=0)
        event = data.get('eventId')
        if not isinstance(event, str) or not event or len(event) > 256:
            event = None
        timeout = data.get('timeout')
        if type(timeout) is not int or not 0 < timeout <= 600:
            timeout = None
        return CommandReceipt('accepted' if event else 'accepted_untracked', event, 0, timeout)
    except Exception:
        # The request may have executed even if the response cannot be parsed.
        return CommandReceipt('unknown')


class B10CommandClient:
    def __init__(self, transport, sessions, *, region, clock, nonce_factory, pin_encryptor):
        self.region = validate_url(region, HOSTS, origin=True)
        if not all(callable(x) for x in (clock, nonce_factory, pin_encryptor)):
            raise ValidationError('Explicit clock, nonce and PIN encryptor required')
        self.transport, self.sessions = transport, sessions
        self.clock, self.nonce, self.encrypt = clock, nonce_factory, pin_encryptor

    def execute(self, snapshot, state, *, action, pin, capability_max_age,
                state_max_age, value=None, position=None, rudder=None, authorized=False,
                allow_stale_parked=False):
        if authorized is not True:
            raise ValidationError('Explicit command authorization required')
        now = self.clock()
        plan = plan_b10(snapshot, state, action=action, now=now,
                        capability_max_age=capability_max_age, state_max_age=state_max_age,
                        value=value, position=position, rudder=rudder,allow_stale_parked=allow_stale_parked)
        if plan.decision.state is not Availability.AVAILABLE or plan.payload is None:
            raise ValidationError('Command unavailable under current capability/state policy')
        if not isinstance(pin, str) or not pin.isascii() or not pin.isdecimal() or not 1 <= len(pin) <= 32:
            raise ValidationError('Vehicle PIN required')
        session = self.sessions.get(now)
        encrypted = self.encrypt(pin, session.token)
        if not isinstance(encrypted, str) or not encrypted or len(encrypted) > 2048:
            raise ValidationError('PIN encryption failed')
        nonce = self.nonce()
        if not isinstance(nonce, str) or not nonce.isascii() or not nonce.isdecimal() or len(nonce) > 32:
            raise ValidationError('Invalid nonce')
        body = dict(carvin=snapshot.vehicle.vin, cmdid=plan.payload.command,
                    state=plan.payload.state, oppwd=encrypted)
        core = dict(source='leapmotor', channel='1', acceptLanguage='en-US',
                    version='V1.16.4-1', deviceType='android', nonce=nonce,
                    timestamp=str(int(now.timestamp()*1000)), deviceId=session.device_id)
        headers = dict(core, sign=sign_authenticated(session.key, core, body))
        headers.update(token=session.token, userId=session.user_id, carvin=snapshot.vehicle.vin,
                       cartype='B10', **{'Content-Type':'application/x-www-form-urlencoded',
                       'x-region':'EU', 'x-api-signature-version':'2.0', 'X-P12_ENC_ALG':'1'})
        request = Request('POST', self.region + COMMAND_PATH, headers, urlencode(body).encode('ascii'))
        try:
            response = self.transport.send(request, client_cert=session.client_cert)
        except (TransportError, TimeoutError, OSError):
            return CommandReceipt('unknown')
        return interpret(response)
