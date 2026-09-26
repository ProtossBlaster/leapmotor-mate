"""Local certificate usability checks; no network or secret-bearing errors."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import ExtendedKeyUsageOID


def certificate_usable(cert_path, key_path, *, margin=90, now=None):
    try:
        now = now or datetime.now(timezone.utc)
        cert = x509.load_pem_x509_certificate(Path(cert_path).read_bytes())
        key = serialization.load_pem_private_key(Path(key_path).read_bytes(), password=None)
        if cert.not_valid_before_utc > now or cert.not_valid_after_utc <= now + timedelta(seconds=margin):
            return False
        encoding, fmt = serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        if cert.public_key().public_bytes(encoding, fmt) != key.public_key().public_bytes(encoding, fmt):
            return False
        try:
            if cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
                return False
        except x509.ExtensionNotFound:
            pass
        try:
            if ExtendedKeyUsageOID.CLIENT_AUTH not in cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value:
                return False
        except x509.ExtensionNotFound:
            pass
        return True
    except Exception:
        return False
