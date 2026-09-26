"""Pure signing primitives reconstructed from APK V1.16.4-1.

Key derivation does NOT authenticate a JWT. Only supply material from an
authenticated login over a verified transport. No secrets are persisted here.
"""

import base64
import binascii
import hashlib
import hmac
import re
from collections.abc import Mapping

from .errors import ValidationError

_BASE_FIELDS = frozenset({"source", "channel", "acceptLanguage", "version",
                          "deviceType", "nonce", "timestamp", "deviceId"})
_URL_B64 = re.compile(r"[A-Za-z0-9_-]+={0,2}")
_FIELD_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
_MAX_VALUE = 16384


def _text(value):
    if not isinstance(value, str) or not value or len(value) > _MAX_VALUE:
        raise ValidationError("Invalid signing material")
    return value


def _decode_standard(value):
    value = _text(value)
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise ValidationError("Invalid signing material") from None
    if not decoded or base64.b64encode(decoded).decode("ascii") != value:
        raise ValidationError("Invalid signing material")
    return decoded


def derive_v2_key(token: str, r2: str, r3: str) -> bytes:
    """XOR decoded JWT signature, r2 and r3, to the shortest length."""
    parts = _text(token).split(".")
    if len(parts) != 3 or any(not _URL_B64.fullmatch(p) for p in parts):
        raise ValidationError("Invalid signing material")
    signature = parts[2]
    try:
        decoded = base64.b64decode(
            signature + "=" * (-len(signature) % 4), altchars=b"-_", validate=True
        )
    except (ValueError, binascii.Error):
        raise ValidationError("Invalid signing material") from None
    if not decoded or base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != signature.rstrip("="):
        raise ValidationError("Invalid signing material")
    second, third = _decode_standard(r2), _decode_standard(r3)
    return bytes(a ^ b ^ c for a, b, c in zip(decoded, second, third))


def _message(headers: Mapping[str, str], params: Mapping[str, str]) -> bytes:
    if not isinstance(headers, Mapping) or not isinstance(params, Mapping):
        raise ValidationError("Signing fields must be mappings")
    if set(headers) != _BASE_FIELDS or set(headers).intersection(params):
        raise ValidationError("Invalid signing field names")
    if len(params) > 128:
        raise ValidationError("Too many signing parameters")
    combined = dict(headers)
    combined.update(params)
    for name, value in combined.items():
        if not isinstance(name, str) or not _FIELD_NAME.fullmatch(name):
            raise ValidationError("Invalid signing field names")
        if not isinstance(value, str) or len(value) > _MAX_VALUE:
            raise ValidationError("Signing values must be bounded strings")
        if name in _BASE_FIELDS and not value:
            raise ValidationError("Empty base signing field")
    try:
        return "".join(combined[name] for name in sorted(combined)).encode("utf-8")
    except UnicodeError:
        raise ValidationError("Invalid signing text encoding") from None


def sign_authenticated(key: bytes, headers: Mapping[str, str], params: Mapping[str, str]) -> str:
    """Lowercase HMAC-SHA256. Convert API parameters to strings explicitly."""
    if not isinstance(key, bytes) or not key or len(key) > _MAX_VALUE:
        raise ValidationError("Invalid signing key")
    return hmac.new(key, _message(headers, params), hashlib.sha256).hexdigest()


def sign_login(headers: Mapping[str, str], params: Mapping[str, str]) -> str:
    """Uppercase SHA-256 for the reconstructed unauthenticated login route."""
    return hashlib.sha256(_message(headers, params)).hexdigest().upper()

