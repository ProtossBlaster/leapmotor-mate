"""Closed read-only API surface for observed EU cloud contracts."""
import json
import re
from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType
from urllib.parse import urlencode

from .errors import ValidationError
from .session import AuthenticationRequired, SessionStore
from .signing import sign_authenticated
from .transport import MAX_RESPONSE_BYTES, Request, Response, TransportError, validate_url

GLOBAL_ORIGIN = "https://app-gw-global-master.leapmotor-international.de"
CENTER_ORIGIN = "https://appgateway.leapmotor-international.de"
ALLOWED_HOSTS = frozenset((GLOBAL_ORIGIN.split("//")[1], CENTER_ORIGIN.split("//")[1]))
ROUTE_PATH = "/app/app-global-service/v1/vehicle/getCarRoute"
READ_PATHS = frozenset((
    "/carownerservice/charge/vail/startTime/query",
    "/carownerservice/mileage/vail/startTime/query",
    "/carownerservice/charge/statistic/days",
    "/carownerservice/charge/daily/detail/page",
    "/carownerservice/mileage/daily/detail/page",
))


class ProtocolError(RuntimeError):
    def __init__(self, reason="invalid_response"):
        self.reason = reason
        super().__init__("Invalid cloud response")


class ApiError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__("Cloud API rejected request")


def freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(freeze(v) for v in value)
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("duplicate_json_key")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ProtocolError("nonfinite_json_number")


def _code(value):
    if type(value) is int:
        return value
    if isinstance(value, str) and re.fullmatch(r"-?[0-9]{1,12}", value):
        return int(value)
    raise ProtocolError("invalid_result_code")


def _vin(vin):
    if not isinstance(vin, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", vin):
        raise ValidationError("Invalid VIN")


def validate_dates(start, end):
    if type(start) is not date or type(end) is not date or end < start:
        raise ValidationError("Expected an ordered calendar date range")


@dataclass(frozen=True, slots=True)
class VehicleRoute:
    vin: str = field(repr=False)
    app_center: str
    app_region: str | None = None


@dataclass(frozen=True, slots=True)
class _ReadContext:
    session: object = field(repr=False)
    route: VehicleRoute = field(repr=False)


class CloudReadClient:
    def __init__(self, transport, sessions, *, clock, nonce_factory):
        if not isinstance(sessions, SessionStore) or not callable(clock) or not callable(nonce_factory):
            raise ValidationError("Explicit session store, clock and nonce factory required")
        self._transport, self._sessions = transport, sessions
        self._clock, self._nonce = clock, nonce_factory

    def _send(self, session, origin, path, body, *, method="POST"):
        origin = validate_url(origin, ALLOWED_HOSTS, origin=True)
        if not ((method == "GET" and origin == GLOBAL_ORIGIN and path == ROUTE_PATH)
                or (method == "POST" and path in READ_PATHS)):
            raise ValidationError("Read endpoint not allowed")
        now = self._clock()
        session.ensure_valid(now)
        nonce = self._nonce()
        if not isinstance(nonce, str) or not re.fullmatch(r"[0-9]{1,32}(?:\.[0-9]{1,16})?", nonce):
            raise ValidationError("Invalid nonce")
        if not isinstance(body, dict) or any(type(v) not in (str, int) for v in body.values()):
            raise ValidationError("Invalid read parameters")
        core = dict(source="leapmotor", channel="1", acceptLanguage="en-US",
                    version="V1.16.4-1", deviceType="android", nonce=nonce,
                    timestamp=str(int(now.timestamp() * 1000)), deviceId=session.device_id)
        headers = dict(core, sign=sign_authenticated(session.key, core, {k: str(v) for k, v in body.items()}))
        headers.update({"token": session.token, "userId": session.user_id,
                        "carvin": body.get("vin", ""), "Content-Type": "application/json",
                        "x-region": "EU", "x-api-signature-version": "2.0", "X-P12_ENC_ALG": "1"})
        url = origin + path
        payload = None
        if method == "GET":
            url += "?" + urlencode(body)
        else:
            payload = json.dumps(body, separators=(",", ":"), ensure_ascii=True).encode()
        response = self._transport.send(Request(method, url, headers, payload), client_cert=session.client_cert)
        if not isinstance(response, Response):
            raise ProtocolError("invalid_transport_response")
        if response.status in (401, 403):
            if response.status==401:self._sessions.invalidate(session)
            raise AuthenticationRequired()
        if not 200 <= response.status < 300:
            raise TransportError("http_status")
        if len(response.body) > MAX_RESPONSE_BYTES:
            raise ProtocolError("response_too_large")
        try:
            envelope = json.loads(response.body.decode("utf-8"), object_pairs_hook=_unique_object,
                                  parse_constant=_invalid_constant)
        except (ValueError, RecursionError):
            raise ProtocolError("invalid_json") from None
        if not isinstance(envelope, dict):
            raise ProtocolError("invalid_envelope")
        codes = [_code(envelope[k]) for k in ("code", "result") if k in envelope]
        if not codes:
            raise ProtocolError("missing_result_code")
        for code in codes:
            if code != 0:
                raise ApiError(code)
        if not isinstance(envelope.get("data"), dict):
            raise ProtocolError("invalid_data")
        return envelope["data"]

    def _resolve(self, vin, session):
        data = self._send(session, GLOBAL_ORIGIN, ROUTE_PATH, {"vin": vin}, method="GET")
        if data.get("vin") != vin:
            raise ProtocolError("route_vin_mismatch")
        try:
            center = validate_url(data.get("appCenter"), ALLOWED_HOSTS, origin=True)
            region = data.get("appRegion")
            if region is not None:
                region = validate_url(region, ALLOWED_HOSTS, origin=True)
        except ValidationError:
            raise ProtocolError("route_origin_rejected") from None
        return VehicleRoute(vin, center, region)

    def resolve_vehicle_route(self, vin):
        _vin(vin)
        return self._resolve(vin, self._sessions.get(self._clock()))

    def _history_context(self, vin):
        _vin(vin)
        session = self._sessions.get(self._clock())
        return _ReadContext(session, self._resolve(vin, session))

    def get_history_availability(self, vin, *, kind):
        if kind not in ("charge", "mileage"):
            raise ValidationError("Unknown history kind")
        context = self._history_context(vin)
        return freeze(self._send(context.session, context.route.app_center,
                      f"/carownerservice/{kind}/vail/startTime/query", {"vin": vin}))

    def get_charge_statistics(self, vin, start, end):
        validate_dates(start, end)
        context = self._history_context(vin)
        return freeze(self._send(context.session, context.route.app_center,
                      "/carownerservice/charge/statistic/days",
                      {"vin": vin, "startTime": start.isoformat(), "endTime": end.isoformat()}))

    def _history_page(self, context, kind, start, end, page):
        if kind not in ("charge", "mileage") or type(page) is not int or page < 1:
            raise ValidationError("Invalid history page")
        return self._send(context.session, context.route.app_center,
                          f"/carownerservice/{kind}/daily/detail/page",
                          {"vin": context.route.vin, "startTime": str(start), "endTime": str(end),
                           "pageNum": str(page) if kind == "charge" else page,
                           "pageSize": "20" if kind == "charge" else 20})

    def list_trips(self, vin, start, end, *, timezone, max_pages=10):
        from .history import list_history
        return list_history(self, vin, start, end, timezone=timezone, max_pages=max_pages, kind="mileage")

    def list_charges(self, vin, start, end, *, timezone, max_pages=10):
        from .history import list_history
        return list_history(self, vin, start, end, timezone=timezone, max_pages=max_pages, kind="charge")
