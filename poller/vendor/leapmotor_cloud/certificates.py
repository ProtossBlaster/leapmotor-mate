"""Opt-in account certificate lifecycle; deliberately no network provisioning.

The provider must return a PKCS#12 and password from an authenticated response
for the same account. This module checks local usability, not issuer trust,
revocation or account ownership. Old generations remain until close so in-flight
requests may finish. Close only after all users of the paths have stopped.
"""
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID
from .private_storage import ensure_private_directory


class CertificateUnavailable(RuntimeError):
    def __init__(self):
        super().__init__("Account certificate unavailable")


@dataclass(frozen=True)
class CertificateLease:
    paths: tuple[Path, Path] = field(repr=False)
    expires_at: datetime


class AccountCertificateManager:
    """Renew on demand, with a serialized provider and bounded failure cooldown."""

    def __init__(self, provider, *, renew_before=timedelta(hours=24),
                 retry_after=timedelta(minutes=1), clock=None):
        if (not callable(provider) or not isinstance(renew_before, timedelta)
                or renew_before < timedelta(0) or not isinstance(retry_after, timedelta)
                or retry_after <= timedelta(0)):
            raise ValueError("Invalid certificate lifecycle configuration")
        self._provider = provider
        self._margin, self._cooldown = renew_before, retry_after
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = Lock()
        self._root = Path(tempfile.mkdtemp(prefix="mate-account-cert-"))
        try:
            ensure_private_directory(self._root)
        except Exception:
            shutil.rmtree(self._root, ignore_errors=True)
            raise CertificateUnavailable() from None
        self._current = None
        self._retry_at = None
        self._closed = False
        self.last_refresh_failed = False

    def _now(self):
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Aware certificate clock required")
        return now

    def ensure(self):
        with self._lock:
            if self._closed:
                raise CertificateUnavailable()
            now = self._now()
            current = self._current
            if current is not None and current.expires_at > now + self._margin:
                return current
            if self._retry_at is not None and now < self._retry_at:
                if current is not None and current.expires_at > now:
                    return current
                raise CertificateUnavailable()
            try:
                bundle, password = self._provider()
                # Re-evaluate time after a potentially slow provisioning request.
                now = self._now()
                lease = self._prepare(bundle, password, now)
            except Exception:
                now = self._now()
                self._retry_at = now + self._cooldown
                self.last_refresh_failed = True
                if current is not None and current.expires_at > now:
                    return current
                raise CertificateUnavailable() from None
            self._current = lease
            self._retry_at = None
            self.last_refresh_failed = False
            return lease

    def _prepare(self, bundle, password, now):
        if (not isinstance(bundle, bytes) or not 0 < len(bundle) <= 1024 * 1024
                or not isinstance(password, bytes) or len(password) > 4096):
            raise ValueError("Invalid certificate material")
        key, cert, chain = pkcs12.load_key_and_certificates(bundle, password)
        if key is None or cert is None:
            raise ValueError("Missing certificate pair")
        if cert.not_valid_before_utc > now or cert.not_valid_after_utc <= now + self._margin:
            raise ValueError("Certificate validity window insufficient")
        encoding, fmt = serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        if key.public_key().public_bytes(encoding, fmt) != cert.public_key().public_bytes(encoding, fmt):
            raise ValueError("Certificate pair mismatch")
        try:
            if cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
                raise ValueError("Expected leaf certificate")
        except x509.ExtensionNotFound:
            pass
        try:
            usage = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
            if ExtendedKeyUsageOID.CLIENT_AUTH not in usage:
                raise ValueError("Certificate excludes client authentication")
        except x509.ExtensionNotFound:
            pass
        cert_bytes = cert.public_bytes(serialization.Encoding.PEM)
        for extra in chain or ():
            cert_bytes += extra.public_bytes(serialization.Encoding.PEM)
        key_bytes = key.private_bytes(serialization.Encoding.PEM,
                                     serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption())
        ensure_private_directory(self._root)
        generation = Path(tempfile.mkdtemp(prefix="generation-", dir=self._root))
        try:
            ensure_private_directory(generation)
            paths = generation / "cert.pem", generation / "key.pem"
            for path, content in zip(paths, (cert_bytes, key_bytes)):
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(content)
            return CertificateLease(paths, cert.not_valid_after_utc)
        except BaseException:
            shutil.rmtree(generation)
            raise

    def close(self):
        with self._lock:
            if not self._closed:
                shutil.rmtree(self._root)
                self._closed = True

    def invalidate(self, expected):
        """Invalidate an explicitly rejected lease, retaining files for readers.

        Caller must supply issuer-confirmed evidence. Local expiry checks alone
        cannot discover revocation. Replacement requires a provider call.
        """
        with self._lock:
            if self._closed or self._current is not expected:return False
            self._current=None
            self._retry_at=None
            return True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
