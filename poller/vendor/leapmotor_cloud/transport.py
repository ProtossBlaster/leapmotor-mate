"""Explicit TLS transport. No redirects, ambient proxy, cookies, or retries."""
import math
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from http.client import HTTPException
from pathlib import Path
from types import MappingProxyType
from typing import Protocol

from .errors import ValidationError

MAX_RESPONSE_BYTES = 1024 * 1024


class TransportError(RuntimeError):
    def __init__(self, reason="transport_failure"):
        self.reason = reason
        super().__init__("Cloud transport failed")


def validate_url(url, allowed_hosts, *, origin=False):
    if (not isinstance(url, str) or len(url) > 8192 or not url
            or any(ord(c) <= 32 or ord(c) >= 127 for c in url)
            or "#" in url or "\\" in url):
        raise ValidationError("Invalid cloud URL")
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        raise ValidationError("Invalid cloud URL") from None
    if (parts.scheme != "https" or parts.hostname not in allowed_hosts
            or parts.username is not None or parts.password is not None
            or port not in (None, 443)):
        raise ValidationError("Cloud origin not allowed")
    if origin:
        if parts.path not in ("", "/") or "?" in url:
            raise ValidationError("Expected a cloud origin")
        return "https://" + parts.hostname
    return url


def validate_cert_paths(paths):
    if not isinstance(paths, tuple) or len(paths) != 2 or not all(isinstance(p, Path) for p in paths):
        raise ValidationError("Explicit PEM certificate and key paths required")


@dataclass(frozen=True, slots=True)
class Request:
    method: str
    url: str = field(repr=False)
    headers: Mapping = field(repr=False)
    body: bytes | None = field(default=None, repr=False)

    def __post_init__(self):
        if self.method not in ("GET", "POST") or not isinstance(self.headers, Mapping) or len(self.headers) > 64:
            raise ValidationError("Invalid request")
        seen = set()
        for key, value in self.headers.items():
            if (not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9!#$%&'*+.^_`|~-]+", key)
                    or not isinstance(value, str) or len(value) > 32768
                    or any(ord(c) < 32 or ord(c) >= 127 for c in value)
                    or key.lower() in seen):
                raise ValidationError("Invalid request headers")
            seen.add(key.lower())
        if self.body is not None and (not isinstance(self.body, bytes) or len(self.body) > MAX_RESPONSE_BYTES or self.method == "GET"):
            raise ValidationError("Invalid request body")
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))


@dataclass(frozen=True, slots=True)
class Response:
    status: int
    body: bytes = field(repr=False)

    def __post_init__(self):
        if type(self.status) is not int or not 100 <= self.status <= 599 or not isinstance(self.body, bytes):
            raise ValidationError("Invalid HTTP response")


class Transport(Protocol):
    def send(self, request: Request, *, client_cert: tuple[Path, Path]) -> Response: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise TransportError("redirect_refused")


class UrllibTransport:
    def __init__(self, ca_file, allowed_hosts, *, timeout=15, max_response_bytes=MAX_RESPONSE_BYTES):
        if not isinstance(ca_file, Path) or not isinstance(allowed_hosts, frozenset) or not allowed_hosts:
            raise ValidationError("Explicit CA and host allowlist required")
        if any(not isinstance(h, str) or not re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)*", h) for h in allowed_hosts):
            raise ValidationError("Invalid host allowlist")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValidationError("Invalid timeout")
        if type(max_response_bytes) is not int or not 1 <= max_response_bytes <= 16 * MAX_RESPONSE_BYTES:
            raise ValidationError("Invalid response limit")
        self.ca_file, self.allowed_hosts = ca_file, allowed_hosts
        self.timeout, self.max_response_bytes = timeout, max_response_bytes

    def send(self, request, *, client_cert):
        if not isinstance(request, Request):
            raise ValidationError("Expected Request")
        validate_url(request.url, self.allowed_hosts)
        validate_cert_paths(client_cert)
        if any(k.lower() in ("host", "cookie", "proxy-authorization") for k in request.headers):
            raise ValidationError("Forbidden transport header")
        try:
            context = ssl.create_default_context(cafile=str(self.ca_file))
            context.check_hostname = True
            context.verify_mode = ssl.CERT_REQUIRED
            context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
            context.load_cert_chain(str(client_cert[0]), str(client_cert[1]))
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}), _NoRedirect(),
                urllib.request.HTTPSHandler(context=context),
            )
            wire = urllib.request.Request(request.url, data=request.body,
                                          headers=dict(request.headers), method=request.method)
            try:
                stream = opener.open(wire, timeout=self.timeout)
            except urllib.error.HTTPError as error:
                stream = error
            with stream:
                status = stream.getcode()
                if 300 <= status < 400:
                    raise TransportError("redirect_refused")
                body = stream.read(self.max_response_bytes + 1)
                if len(body) > self.max_response_bytes:
                    raise TransportError("response_too_large")
                return Response(status, body)
        except TransportError:
            raise
        except (OSError, urllib.error.URLError, HTTPException, ValueError):
            raise TransportError() from None
