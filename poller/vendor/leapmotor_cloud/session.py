"""Immutable caller-supplied v2 sessions, held in memory only."""
import base64
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from .errors import ValidationError
from .models import require_aware
from .signing import derive_v2_key
from .transport import validate_cert_paths


class AuthenticationRequired(RuntimeError):
    def __init__(self):
        super().__init__("Cloud authentication required")


class SessionExpired(AuthenticationRequired):
    pass


def session_device_id(token, fallback):
    """Resolve the device binding from a token received over verified TLS.

    This decodes metadata, not a signature verification or authentication check.
    Tokens without this optional binding retain the supplied installation ID.
    """
    try:
        payload = token.split('.')[1]
        data = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        name = data.get('user_name')
        parts = name.split(',') if isinstance(name, str) else []
        if len(parts) >= 4 and parts[2]:
            value = parts[2]
            if len(value) > 16384 or any(not 33 <= ord(c) <= 126 for c in value):
                raise ValidationError('Invalid session device binding')
            return value
    except (ValueError, TypeError, IndexError, AttributeError):
        raise ValidationError('Invalid session device metadata') from None
    return fallback


def _expiry_from_token(token):
    try:
        payload = token.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if not isinstance(data, dict):
            raise ValueError()
        if "exp" not in data:
            return None
        value = data["exp"]
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError()
        return datetime.fromtimestamp(value, timezone.utc)
    except (ValueError, TypeError, IndexError, OverflowError, OSError, RecursionError):
        raise ValidationError("Invalid token expiry metadata") from None


@dataclass(frozen=True, slots=True)
class CloudSession:
    token: str = field(repr=False)
    user_id: str = field(repr=False)
    device_id: str = field(repr=False)
    key: bytes = field(repr=False)
    client_cert: tuple[Path, Path] = field(repr=False)
    expires_at: datetime | None = None

    def __post_init__(self):
        for value in (self.token, self.user_id, self.device_id):
            if not isinstance(value, str) or not value or len(value) > 16384 or any(not 33 <= ord(c) <= 126 for c in value):
                raise ValidationError("Invalid session material")
        if not isinstance(self.key, bytes) or len(self.key) != 32:
            raise ValidationError("Expected a 32-byte v2 key")
        validate_cert_paths(self.client_cert)
        if self.expires_at is not None:
            require_aware(self.expires_at)

    @classmethod
    def from_login_data(cls, data, *, device_id, client_cert, expires_at=None):
        if not isinstance(data, Mapping) or not isinstance(data.get("signParam"), Mapping):
            raise ValidationError("Invalid login material")
        account = data.get("accountId")
        if type(account) not in (int, str) or (type(account) is int and account < 0):
            raise ValidationError("Invalid account identifier")
        token = data.get("token")
        params = data["signParam"]
        key = derive_v2_key(token, params.get("r2"), params.get("r3"))
        expiry = expires_at if expires_at is not None else _expiry_from_token(token)
        return cls(token, str(account), session_device_id(token, device_id), key, client_cert, expiry)

    @property
    def expiry_known(self):
        return self.expires_at is not None

    def ensure_valid(self, now):
        require_aware(now)
        if self.expires_at is not None and now >= self.expires_at:
            raise SessionExpired()


class SessionStore:
    def __init__(self):
        self._lock = Lock()
        self._session = None

    def __repr__(self):
        return "SessionStore(<in-memory>)"

    def replace(self, session):
        if not isinstance(session, CloudSession):
            raise ValidationError("Expected CloudSession")
        with self._lock:
            self._session = session

    def get(self, now):
        require_aware(now)
        with self._lock:
            if self._session is None:
                raise AuthenticationRequired()
            self._session.ensure_valid(now)
            return self._session

    def invalidate(self, expected):
        """A late rejection of an old request cannot erase its replacement."""
        if not isinstance(expected,CloudSession):raise ValidationError('Expected CloudSession')
        with self._lock:
            if self._session is not expected:return False
            self._session=None
            return True
